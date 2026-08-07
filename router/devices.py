import re
import discord

import db

from logger import logger
from state import state

from config import (
    ROUTER_URL,
    ROUTER_SESSION,
    CHANNEL_ID,
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
    
    for lease in dhcp_leases:
        ip = lease[1]
        mac = lease[2].upper()
        if not ip or not mac:
            continue
        old_mac = state.ip_to_mac_cache.get(ip)
        if old_mac and old_mac != mac:
            old_name = state.macs_list.get(old_mac, old_mac)
            new_name = state.macs_list.get(mac, mac)
            logger.warning(
                f"⚠️ IP Reuse: {ip} moved from {old_name} ({old_mac}) "
                f"to {new_name} ({mac}) — daily usage may be inaccurate."
            )
        state.ip_to_mac_cache[ip] = mac
    return dhcp_leases, wireless_devs, arp_list


def fetch_devlist_and_discover(bot_instance):
    """Fetch devlist and notify about new devices."""
    dhcp_leases, wireless_devs, arp_list = fetch_devlist()
    new_devices = []
    for lease in dhcp_leases:
        mac      = lease[2].upper()
        hostname = lease[0].strip() or "Unknown"
        if db.add_device(mac, hostname):
            state.macs_list[mac] = hostname
            new_devices.append((mac, hostname))
    if new_devices and bot_instance.loop.is_running():
        async def _notify():
            channel = bot_instance.get_channel(CHANNEL_ID)
            if not channel:
                return
            for mac, hostname in new_devices:
                embed = discord.Embed(
                    title="` 🆕` New Device Discovered",
                    description=f"**Hostname:** `{hostname}`\n**MAC:** `{mac}`",
                    color=0xf39c12
                )
                await channel.send(embed=embed)
        bot_instance.loop.create_task(_notify())
    return dhcp_leases, wireless_devs, arp_list
