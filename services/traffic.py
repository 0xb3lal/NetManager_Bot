import asyncio
import discord
import db
from config import CHANNEL_ID
from logger import logger
from state import ROUTER_LOCK, state
from router.traffic import (
    get_speed_history,
    get_daily_history,
    get_today_combined,
)
from router.devices import fetch_devlist
from router.firewall import enable_lockdown
from services.radius import fetch_radius_traffic
from utils.traffic import (
    bytes_to_mb,
    parse_traffic_to_gb,
    format_data_size,
)

def get_today_usage_by_mac():
    """Get today's usage organized by MAC address."""
    speed_history = get_speed_history()
    daily_history = get_daily_history()
    dhcp_leases, _, _ = fetch_devlist()
    ip_to_mac = dict(state.ip_to_mac_cache)
    combined_usage = get_today_combined(speed_history, daily_history)

    usage_by_mac = {}
    for ip, total_bytes in combined_usage.items():
        mac = ip_to_mac.get(ip)
        if not mac:
            continue
        usage_by_mac[mac] = usage_by_mac.get(mac, 0) + bytes_to_mb(total_bytes) / 1024
    return usage_by_mac


def apply_lockdown_for_traffic(traffic_value):
    """Apply lockdown based on traffic threshold."""
    if traffic_value < state.threshold:
        enable_lockdown(force_lock=True)
        state.lockdown_state = True
        db.set_lockdown_state(True)
        return "`❌` System Lockdown", 0xff4747

    enable_lockdown(force_lock=False)
    state.lockdown_state = False
    db.set_lockdown_state(False)
    return "`✅` System Normal", 0x47ff7e

async def async_check_and_lock(bot_instance):
    """Check traffic and apply lockdown asynchronously."""
    try:
        available_traffic = await asyncio.to_thread(fetch_radius_traffic)

        if not available_traffic:
            return

        traffic_value = parse_traffic_to_gb(available_traffic)

        async with ROUTER_LOCK:
            e_title, e_color = await asyncio.to_thread(
                apply_lockdown_for_traffic,
                traffic_value
            )

        balance_label = "Balance:".ljust(9)
        limit_label = "Limit:".ljust(9)

        status_box = (
            f"```\n"
            f"{balance_label} {available_traffic}\n"
            f"{limit_label} {format_data_size(state.threshold)}\n"
            f"```"
        )

        embed = discord.Embed(
            title=e_title,
            description=status_box,
            color=e_color
        )

        try:
            channel = bot_instance.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
        except Exception:
            logger.error("Failed to push status update to Discord")

    except Exception as e:
        logger.error(f"Main Check Error: {e}")