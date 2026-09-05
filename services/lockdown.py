import asyncio

import discord

import db
from config import CHANNEL_ID
from logger import logger
from router.firewall import enable_lockdown
from services.radius import fetch_radius_traffic
from state import ROUTER_LOCK, state
from utils.traffic import format_data_size, parse_traffic_to_gb


def apply_lockdown_for_traffic(traffic_value):
    """Apply firewall lockdown based on remaining traffic threshold."""

    if traffic_value < state.threshold:

        enable_lockdown(force_lock=True)

        state.lockdown_state = True
        db.set_lockdown_state(True)

        return "`❌` System Lockdown", 0xFF4747

    else:

        enable_lockdown(force_lock=False)

        state.lockdown_state = False
        db.set_lockdown_state(False)

        return "`✅` System Normal", 0x47FF7E


async def async_check_and_lock(bot_instance):
    """Async wrapper for traffic check and lockdown."""

    try:

        available_traffic = await asyncio.to_thread(fetch_radius_traffic)

        if not available_traffic:
            return

        traffic_value = parse_traffic_to_gb(available_traffic)

        async with ROUTER_LOCK:

            e_title, e_color = await asyncio.to_thread(
                apply_lockdown_for_traffic, traffic_value
            )

        status_box = (
            "```\n"
            f"{'Balance:'.ljust(9)} {available_traffic}\n"
            f"{'Limit:'.ljust(9)} {format_data_size(state.threshold)}\n"
            "```"
        )

        embed = discord.Embed(title=e_title, description=status_box, color=e_color)

        try:

            channel = bot_instance.get_channel(CHANNEL_ID)

            if channel:
                await channel.send(embed=embed)

        except Exception:

            logger.error("Failed to push status update to Discord")

    except Exception as e:

        logger.error(f"Main Check Error: {e}")
