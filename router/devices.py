import asyncio
import re
import time

import db
from logger import logger
from services.onboarding import start_onboarding
from state import state

_recently_migrated: dict[str, float] = {}
_recently_removed: dict[str, float] = {}


def _is_recently_guarded(mac: str) -> bool:
    now = time.time()
    for d in (_recently_migrated, _recently_removed):
        for k, exp in list(d.items()):
            if exp < now:
                d.pop(k, None)
    m = mac.upper()
    return m in _recently_migrated or m in _recently_removed


import demjson3

from config import ROUTER_SESSION, ROUTER_URL


def fetch_devlist():
    """Fetch DHCP leases, wireless and ARP lists from router."""
    url = f"{ROUTER_URL}/update.cgi"
    data = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
    r = ROUTER_SESSION.post(url, data=data, timeout=30)

    dhcp_match = re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text)
    wldev_match = re.search(r"wldev\s*=\s*(\[.*?\]);", r.text)
    arp_match = re.search(r"arplist\s*=\s*(\[.*?\]);", r.text)

    if not (dhcp_match and wldev_match and arp_match):
        logger.error(
            f"Router devlist regex failed to match. Response snippet: {r.text[:500]!r}"
        )
        raise RuntimeError(
            "Failed to parse router devlist response (malformed or empty)."
        )

    dhcp_leases = demjson3.decode(dhcp_match.group(1))
    wireless_devs = demjson3.decode(wldev_match.group(1))
    arp_list = demjson3.decode(arp_match.group(1))

    seen_macs = []
    for lease in dhcp_leases:
        ip = lease[1]
        mac = lease[2].upper()
        if not ip or not mac:
            continue
        seen_macs.append(mac)
        old_mac = state.ip_to_mac_cache.get(ip)
        if old_mac and old_mac != mac:
            old_name = state.macs_list.get(old_mac, old_mac)
            new_name = state.macs_list.get(mac, mac)
            logger.warning(
                f"⚠️ IP Reuse: {ip} moved from {old_name} ({old_mac}) "
                f"to {new_name} ({mac}) — daily usage may be inaccurate."
            )
        state.ip_to_mac_cache[ip] = mac

    # Presence comes exclusively from the router: every MAC in this poll
    # counts as "seen right now" for stale-device cleanup.
    db.refresh_last_seen(seen_macs)

    return dhcp_leases, wireless_devs, arp_list


def fetch_devlist_and_discover(bot_instance):
    """Fetch devlist, discover new devices and start onboarding."""
    dhcp_leases, wireless_devs, arp_list = fetch_devlist()
    from config import (
        DISTANCE_ESTIMATION_ENABLED,
        RSSI_AT_1M_DBM,
        RSSI_DISPLAY_ENABLED,
        WIFI_PATH_LOSS_EXPONENT,
    )
    from utils.wireless import clamp_distance, rssi_to_distance_m, rssi_to_quality_pct

    def _build_rssi_map(devs) -> dict[str, int]:
        try:
            return {
                dev[1].upper(): int(dev[2])
                for dev in devs
                if len(dev) > 2 and isinstance(dev[2], (int, float))
            }
        except (ValueError, TypeError, AttributeError):
            return {}

    rssi_by_mac = _build_rssi_map(wireless_devs) if wireless_devs else {}
    new_devices = []
    for lease in dhcp_leases:
        mac = lease[2].upper()
        # TTL guard: skip recently migrated/removed old MACs to prevent immediate ghost recreation
        if _is_recently_guarded(mac):
            logger.debug(
                f"Discovery skipping recently migrated/removed MAC {mac} (TTL guard)"
            )
            continue
        hostname = lease[0].strip() or "Unknown"
        ip = lease[1]

        def _wireless_info(rssi_val: int | None) -> tuple[int | None, float | None]:
            if rssi_val is None:
                return None, None
            q = rssi_to_quality_pct(rssi_val)
            d = None
            if DISTANCE_ESTIMATION_ENABLED:
                raw = rssi_to_distance_m(
                    rssi_val, RSSI_AT_1M_DBM, WIFI_PATH_LOSS_EXPONENT
                )
                if raw is not None:
                    clamped, was_clamped = clamp_distance(raw, 1.0)
                    if was_clamped:
                        logger.debug(
                            f"Distance for {mac} clamped {raw:.2f}m -> 1.0m (RSSI {rssi_val} dBm)"
                        )
                    d = clamped
            return q, d

        rssi = rssi_by_mac.get(mac) if RSSI_DISPLAY_ENABLED else None
        quality, distance = _wireless_info(rssi)
        existing = state.macs_list.get(mac)
        # Protect custom hostname: do not overwrite non-Unknown with Unknown
        if hostname.lower() == "unknown" and existing and existing.lower() != "unknown":
            # Keep existing custom hostname; still ensure device exists in DB (no hostname change)
            if not db.device_exists(mac):
                # New device with Unknown — insert as Unknown (preserves pending flow)
                if db.add_device(mac, hostname):
                    new_devices.append((mac, hostname, ip, rssi, distance, quality))
            # Do not update state.macs_list with Unknown
        else:
            if db.add_device(mac, hostname):
                new_devices.append((mac, hostname, ip, rssi, distance, quality))
            if state.macs_list.get(mac) != hostname:
                state.macs_list[mac] = hostname
    if new_devices and bot_instance.loop.is_running():

        async def _onboard_new_devices():
            # New devices are inserted as PENDING (firewall-dropped) by
            # add_device; this kicks off the interactive review session.
            for mac, hostname, ip, rssi, distance, quality in new_devices:
                await start_onboarding(
                    bot_instance, mac, hostname, ip, rssi, distance, quality
                )

        future = asyncio.run_coroutine_threadsafe(
            _onboard_new_devices(), bot_instance.loop
        )

        def _log_onboard_failure(fut):
            exc = fut.exception()
            if exc:
                logger.error(f"Failed to start onboarding for new devices: {exc}")

        future.add_done_callback(_log_onboard_failure)
    return dhcp_leases, wireless_devs, arp_list
