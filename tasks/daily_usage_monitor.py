import asyncio
import discord
import db
from discord.ext import tasks
from logger import logger
from state import ROUTER_LOCK, state, acquire_router_lock_bounded
from config import CHANNEL_ID
from router.firewall import enable_lockdown
from services.traffic import get_today_usage_by_mac
from utils.traffic import format_data_size
from telegram.alerts import check_and_send_threshold_alerts


def setup_daily_usage_monitor_task(bot):
    """Create and configure the daily usage monitor task."""

    @tasks.loop(minutes=10.0)
    async def daily_usage_monitor_task():
        """Monitor daily usage and auto-block devices exceeding limits."""
        logger.info("Daily usage monitor task TRIGGERED — starting scheduled check...")
        await _run_daily_usage_monitor_check(bot)

    @daily_usage_monitor_task.before_loop
    async def before_daily_usage_monitor():
        await bot.wait_until_ready()
        logger.info("Daily usage monitor task ready. Waiting 30s to avoid startup collision...")
        await asyncio.sleep(30)
        logger.info("Daily usage monitor task started (checks per-device usage every 10 minutes).")

    return daily_usage_monitor_task


async def _run_daily_usage_monitor_check(bot):
    """Check device usage and enforce daily limits."""
    # --- Phase 1: router I/O only, under the lock ---
    if not await acquire_router_lock_bounded("Daily usage monitor"):
        return
    try:
        usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)
    except Exception as e:
        logger.error(f"Error fetching usage snapshot in daily usage monitor: {e}")
        return
    finally:
        ROUTER_LOCK.release()

    try:
        channel = bot.get_channel(CHANNEL_ID)
        devices_to_ban = []

        # --- Phase 2 (no lock): Telegram alerts + classification ---
        for mac, usage_gb in usage_by_mac.items():
            if db.is_exempt_from_daily_limit(mac):
                continue

            effective_limit = db.get_effective_daily_limit(mac)
            if effective_limit <= 0:
                continue

            device_name = state.macs_list.get(mac, mac)

            # --- Telegram threshold alerts (25% / 50% / 75% / 100%) ---
            await check_and_send_threshold_alerts(
                mac, device_name, usage_gb, effective_limit
            )

            if usage_gb < effective_limit:
                continue

            if mac in state.allowed_macs:
                if not db.was_notified_today(mac):
                    db.mark_notified_today(mac)
                    if channel:
                        status_box = (
                            "```\n"
                            f"{'Device:'.ljust(10)} {device_name}\n"
                            f"{'Usage:'.ljust(10)} {format_data_size(usage_gb)}\n"
                            f"{'Limit:'.ljust(10)} {format_data_size(effective_limit)}\n"
                            "```"
                        )
                        embed = discord.Embed(
                            title="`⚠️` Whitelisted Device Over Daily Limit",
                            description=status_box,
                            color=0xe67e22
                        )
                        embed.set_footer(text="Whitelisted devices are never blocked automatically.")
                        await channel.send(embed=embed)
                continue

            if mac in state.banned_macs:
                continue

            devices_to_ban.append((mac, device_name, usage_gb, effective_limit))

        # --- Phase 3 (short lock): re-verify, then apply bans atomically ---
        if devices_to_ban:
            async with ROUTER_LOCK:
                ban_now = [
                    entry for entry in devices_to_ban
                    if entry[0] not in state.banned_macs
                    and entry[0] not in state.allowed_macs
                    and db.device_exists(entry[0])
                ]
                for mac, _, _, _ in ban_now:
                    state.banned_macs.add(mac)
                    db.ban_device(mac, reason="daily_limit")

                if ban_now:
                    await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)

            # --- Phase 4 (no lock): Discord notifications ---
            for mac, device_name, usage_gb, effective_limit in ban_now:
                logger.info(f"Auto-blocked {mac} for exceeding daily limit ({usage_gb:.2f}GB / {effective_limit:.2f}GB).")
                if channel:
                    status_box = (
                        f"```\n"
                        f"{'Device:'.ljust(10)} {device_name}\n"
                        f"{'Usage:'.ljust(10)} {format_data_size(usage_gb)}\n"
                        f"{'Limit:'.ljust(10)} {format_data_size(effective_limit)}\n"
                        f"```"
                    )
                    embed = discord.Embed(
                        title="`🚫` Device Auto-Blocked (Daily Limit)",
                        description=status_box,
                        color=0xff4747
                    )
                    await channel.send(embed=embed)

        logger.info("Scheduled daily usage monitor check completed.")
    except Exception as e:
        logger.error(f"Error in daily_usage_monitor_task: {e}")
