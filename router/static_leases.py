import asyncio
import re
import time

import requests

from config import ROUTER_SESSION, ROUTER_URL
from logger import logger
from utils.validators import is_valid_mac

# Hostname validation: router dnsmasq compatible
# Keep onboarding limit 32
HOSTNAME_MAX_LEN = 32
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,30}[A-Za-z0-9])?$")


def is_valid_hostname(name: str) -> bool:
    if not name or len(name) > HOSTNAME_MAX_LEN:
        return False
    return bool(HOSTNAME_RE.match(name))


def is_valid_ip(ip: str) -> bool:
    try:
        import ipaddress

        ipaddress.IPv4Address(ip)
        return True
    except Exception:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        for p in parts:
            if not p.isdigit():
                return False
            v = int(p)
            if v < 0 or v > 255:
                return False
            # Reject leading zeros ("010") to match the ipaddress module's
            # strict behavior and avoid octal-interpretation ambiguity.
            if str(v) != p:
                return False
        return True


def _escape_field(s: str) -> str:
    # Tomato dhcpd_static delimiters are < and > ; only they and backslash need escaping.
    # MAC colons must NOT be escaped — canonical form is AA:BB:CC:DD:EE:FF.
    s = s.replace("\\", "\\\\")
    s = s.replace("<", "\\<")
    s = s.replace(">", "\\>")
    return s


def _unescape_field(s: str) -> str:
    # Reverse of _escape_field, plus legacy \: for already-corrupted data.
    res = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in (":", "<", ">", "\\"):
                res.append(nxt)
                i += 2
                continue
        res.append(s[i])
        i += 1
    return "".join(res)


def _normalize_mac(mac: str) -> str:
    """Normalize MAC to AA:BB:CC:DD:EE:FF, healing legacy backslash corruption."""
    if not mac:
        return mac
    import re

    mac = re.sub(r"\\x5c:", ":", mac, flags=re.IGNORECASE)
    mac = re.sub(r"\\x5c", "", mac, flags=re.IGNORECASE)
    mac = mac.replace("\\:", ":")
    mac = mac.replace("\\", "")
    m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", mac)
    if m:
        return m.group(1).upper()
    return mac.upper()


def _split_unescaped(s: str, delim: str):
    parts = []
    cur = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == delim:
            cur.append(delim)
            i += 2
            continue
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "\\":
            cur.append("\\")
            i += 2
            continue
        if s[i] == delim and (delim != "\\"):
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(s[i])
        i += 1
    parts.append("".join(cur))
    return parts


def _parse_raw_entries(raw: str):
    """Parse raw dhcpd_static string into dicts preserving raw substrings."""
    if not raw or not raw.strip():
        return []
    # Since ">" is delimiter between entries, we need to split on ">" not preceded by "\"
    entries = []
    cur = []
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            # keep escaped char as is in raw substring
            cur.append(raw[i])
            cur.append(raw[i + 1])
            i += 2
            continue
        if raw[i] == ">":
            entries.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(raw[i])
        i += 1
    if cur or not entries:
        entries.append("".join(cur))
    parsed = []
    for ent_raw in entries:
        if not ent_raw.strip():
            continue
        parts = []
        cur2 = []
        j = 0
        while j < len(ent_raw):
            if (
                ent_raw[j] == "\\"
                and j + 1 < len(ent_raw)
                and                 ent_raw[j + 1] in ("<", ">", ":", "\\")
            ):
                cur2.append(ent_raw[j])
                cur2.append(ent_raw[j + 1])
                j += 2
                continue
            if ent_raw[j] == "<":
                parts.append("".join(cur2))
                cur2 = []
                j += 1
                continue
            cur2.append(ent_raw[j])
            j += 1
        parts.append("".join(cur2))
        # Expect 4 parts: MAC, IP, HOSTNAME, flag
        if len(parts) < 4:
            logger.warning(f"Malformed dhcpd_static entry skipped: {ent_raw!r}")
            continue
        mac_e, ip_e, host_e, flag_e = parts[0], parts[1], parts[2], parts[3]
        mac_raw = _unescape_field(mac_e)
        mac = _normalize_mac(mac_raw)
        ip = _unescape_field(ip_e)
        hostname = _unescape_field(host_e)
        flag = _unescape_field(flag_e)
        parsed.append(
            {"raw": ent_raw, "mac": mac, "ip": ip, "hostname": hostname, "flag": flag}
        )
    return parsed


def _serialize_entry(mac: str, ip: str, hostname: str, flag: str = "0") -> str:
    mac_e = _escape_field(mac.upper())
    host_e = _escape_field(hostname)
    ip_e = ip
    return f"{mac_e}<{ip_e}<{host_e}<{flag}"


def _fetch_raw_via_nvram(timeout=15):
    """Fetch current dhcpd_static via nvram get."""
    from router.client import run_cmd_output_value

    out = run_cmd_output_value("nvram get dhcpd_static", timeout=timeout)
    if out is None:
        return None
    return out.strip()


def fetch_current_entries():
    raw = _fetch_raw_via_nvram()
    if raw is None:
        raise RuntimeError(
            "Failed to fetch current dhcpd_static from router (nvram get failed)"
        )
    # raw may be empty
    entries = _parse_raw_entries(raw)
    return entries, raw


def _push_dhcpd_static(new_raw: str, timeout=30):
    """Push new dhcpd_static to /tomato.cgi."""
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": f"{ROUTER_URL}/",
        "Origin": ROUTER_URL,
        "User-Agent": "Mozilla/5.0",
    }
    # Need to handle _http_id - existing code hardcodes TIDe5b1505eeac7f67f
    # Preserve that.
    data = (
        "_ajax=1"
        "&_nextpage=/#basic-static.asp"
        "&_service=dhcpd-restart,arpbind-restart,cstats-restart"
        f"&dhcpd_static={new_raw}"
        "&dhcpd_static_only=0"
        "&cstats_include="
        "&arpbind_listed="
        "&_http_id=TIDe5b1505eeac7f67f"
    )
    try:
        resp = ROUTER_SESSION.post(
            f"{ROUTER_URL}/tomato.cgi", headers=headers, data=data, timeout=timeout
        )
        if resp.status_code == 200:
            logger.info("Static lease update submitted successfully.")
            return True, None
        else:
            msg = f"Router returned HTTP {resp.status_code}"
            logger.error(msg)
            return False, msg
    except requests.exceptions.Timeout as e:
        msg = f"Timeout contacting router: {e}"
        logger.error(msg)
        return False, msg
    except requests.exceptions.ConnectionError as e:
        msg = f"Connection error: {e}"
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Unexpected error: {e}"
        logger.error(msg)
        return False, msg


def set_static_hostname_sync(mac: str, ip: str, hostname: str):
    """Single-attempt static lease set; returns (success, error_msg)."""
    if not is_valid_mac(mac):
        return False, "Invalid MAC address format"
    if not is_valid_ip(ip):
        return False, "Invalid IP address format"
    if not is_valid_hostname(hostname):
        return (
            False,
            f"Invalid hostname '{hostname}' (1-32 chars, alphanumeric/hyphen, must start/end alnum)",
        )
    mac = mac.upper()
    try:
        entries, raw = fetch_current_entries()
    except Exception as e:
        return False, f"Failed to read current leases: {e}"
    # Build new raw — re-serialize every entry through canonical form to heal corruption
    found = False
    new_raw_parts = []
    for ent in entries:
        if ent["mac"].upper() == mac.upper():
            new_ent_raw = _serialize_entry(
                mac, ip, hostname, ent["flag"] if ent["flag"] else "0"
            )
            new_raw_parts.append(new_ent_raw)
            found = True
        else:
            new_raw_parts.append(
                _serialize_entry(
                    ent["mac"],
                    ent["ip"],
                    ent["hostname"],
                    ent["flag"] if ent["flag"] else "0",
                )
            )
    if not found:
        new_raw_parts.append(_serialize_entry(mac, ip, hostname, "0"))
    new_raw = ">".join(new_raw_parts)
    success, err = _push_dhcpd_static(new_raw)
    return success, err


async def set_static_hostname(
    mac: str, ip: str, hostname: str, max_attempts=3, base_delay=2
):
    """Bounded retry for static lease set; releases ROUTER_LOCK while sleeping."""
    from state import ROUTER_LOCK

    last_err = None
    for attempt in range(1, max_attempts + 1):
        # Acquire lock per attempt (read-modify-write atomic)
        acquired = False
        try:
            try:
                from state import acquire_router_lock_bounded

                got = await acquire_router_lock_bounded(f"static-lease {mac}")
                if not got:
                    last_err = "Router busy (lock timeout)"
                    logger.warning(
                        f"set_static_hostname attempt {attempt}/{max_attempts} lock busy for {mac}"
                    )
                    # treat as retryable failure
                else:
                    acquired = True
                    success, err = await asyncio.to_thread(
                        set_static_hostname_sync, mac, ip, hostname
                    )
                    if success:
                        return True, None
                    last_err = err
                    logger.warning(
                        f"set_static_hostname attempt {attempt}/{max_attempts} failed for {mac}: {err}"
                    )
            except ImportError:
                async with ROUTER_LOCK:
                    success, err = await asyncio.to_thread(
                        set_static_hostname_sync, mac, ip, hostname
                    )
                    if success:
                        return True, None
                    last_err = err
                    logger.warning(
                        f"set_static_hostname attempt {attempt}/{max_attempts} failed for {mac}: {err}"
                    )
                acquired = False
        finally:
            if acquired:
                try:
                    ROUTER_LOCK.release()
                except Exception:
                    pass
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            await asyncio.sleep(delay)
    return False, last_err


def resolve_ip_for_mac(mac: str):
    """Resolve current IP for MAC from state cache."""
    from state import state

    mac = mac.upper()
    for ip, cached_mac in state.ip_to_mac_cache.items():
        if cached_mac.upper() == mac:
            return ip
    # Returns None without a fresh DHCP lookup by design — the state cache is
    # the best available source here, and macs_list holds no IP mapping.
    return None
