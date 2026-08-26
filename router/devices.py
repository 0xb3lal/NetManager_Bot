import re
import asyncio

import db

from logger import logger
from state import state
from services.onboarding import start_onboarding

from config import (
    ROUTER_URL,
    ROUTER_SESSION,
)

import demjson3


def fetch_devlist():
    """Fetch device list from router (DHCP leases, wireless, ARP)."""
    url  = f"{ROUTER_URL}/update.cgi"
    data = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
    r = ROUTER_SESSION.post(url, data=data, timeout=30)

    dhcp_match  = re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text)
    wldev_match = re.search(r"wldev\s*=\s*(\[.*?\]);", r.text)
    arp_match   = re.search(r"arplist\s*=\s*(\[.*?\]);", r.text)

    if not (dhcp_match and wldev_match and arp_match):
        logger.error(f"Router devlist regex failed to match. Response snippet: {r.text[:500]!r}")
        raise RuntimeError("Failed to parse router devlist response (malformed or empty).")

    dhcp_leases   = demjson3.decode(dhcp_match.group(1))
    wireless_devs = demjson3.decode(wldev_match.group(1))
    arp_list      = demjson3.decode(arp_match.group(1))
    
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
    """Fetch devlist, discover new devices, and start their onboarding flow."""
    dhcp_leases, wireless_devs, arp_list = fetch_devlist()
    new_devices = []
    for lease in dhcp_leases:
        mac      = lease[2].upper()
        hostname = lease[0].strip() or "Unknown"
        ip       = lease[1]
        if db.add_device(mac, hostname):
            new_devices.append((mac, hostname, ip))
        if state.macs_list.get(mac) != hostname:
            state.macs_list[mac] = hostname
    if new_devices and bot_instance.loop.is_running():
        async def _onboard_new_devices():
            # New devices are inserted as PENDING (firewall-dropped) by
            # add_device; this kicks off the interactive review session.
            for mac, hostname, ip in new_devices:
                await start_onboarding(bot_instance, mac, hostname, ip)

        future = asyncio.run_coroutine_threadsafe(_onboard_new_devices(), bot_instance.loop)

        def _log_onboard_failure(fut):
            exc = fut.exception()
            if exc:
                logger.error(f"Failed to start onboarding for new devices: {exc}")

        future.add_done_callback(_log_onboard_failure)
    return dhcp_leases, wireless_devs, arp_list
