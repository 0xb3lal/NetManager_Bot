import asyncio
from datetime import time
from zoneinfo import ZoneInfo
import discord
from discord.ext import tasks
import db
from logger import logger
from state import ROUTER_LOCK, state
from router.firewall import enable_lockdown
from router.stats import reset_ip_traffic_stats, reset_bandwidth_stats
from config import CHANNEL_ID
import usage_db
import telegram.db as telegram_db

MIDNIGHT_RESET_TIME     = time(hour=0, minute=0, tzinfo=ZoneInfo("Africa/Cairo"))
MIDNIGHT_RETRY_INTERVAL = 600   # retry every 10 minutes after a failed attempt
MIDNIGHT_MAX_RETRIES    = 40    # safety cap (~6.5 hours)

_midnight_retry_running = False

def setup_midnight_reset_task(bot):

    @tasks.loop(time=MIDNIGHT_RESET_TIME)
    async def midnight_reset_task():
        global _midnight_retry_running
        logger.info("Starting scheduled midnight reset...")
        channel = bot.get_channel(CHANNEL_ID)
        try:
            ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota = await _run_midnight_reset_steps()

            if channel:
                embed = discord.Embed(
                    title="`🌙` Midnight Reset Completed" if (ip_ok and bw_ok) else "`⚠️` Midnight Reset Partially Failed",
                    description=_midnight_status_box(ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota),
                    color=0x2ecc71 if (ip_ok and bw_ok) else 0xe67e22
                )
                if not (ip_ok and bw_ok):
                    embed.set_footer(text="Retrying every 10 minutes until it succeeds...")
                await channel.send(embed=embed)

            logger.info(
                f"Midnight reset completed. ip_ok={ip_ok} bw_ok={bw_ok} "
                f"unbanned={unbanned_count} cleared_overrides={cleared_overrides} "
                f"cleared_extra_quota={cleared_extra_quota}"
            )

            if not (ip_ok and bw_ok):
                if not _midnight_retry_running:
                    _midnight_retry_running = True
                    asyncio.create_task(_retry_midnight_reset(channel))
                else:
                    logger.warning("Midnight retry loop already running, skipping duplicate trigger.")
        except Exception as e:
            logger.exception(f"Critical error in midnight_reset_task: {e}")
            if channel:
                embed = discord.Embed(
                    title="`💥` Midnight Reset Crashed",
                    description=f"```\n{str(e)[:500]}\n```",
                    color=0xe74c3c
                )
                embed.set_footer(text="Retrying every 10 minutes until it succeeds...")
                try:
                    await channel.send(embed=embed)
                except Exception as send_err:
                    logger.error(f"Failed to send midnight-reset crash notification: {send_err}")

            if not _midnight_retry_running:
                _midnight_retry_running = True
                asyncio.create_task(_retry_midnight_reset(channel))
            else:
                logger.warning("Midnight retry loop already running, skipping duplicate trigger.")

    @midnight_reset_task.before_loop
    async def before_midnight_reset():
        await bot.wait_until_ready()
        logger.info("Midnight reset task started (resets router traffic stats, daily-limit bans and custom limits at 00:00 Cairo time, with retry on failure).")

    return midnight_reset_task

async def _run_midnight_reset_steps():
    """Run midnight reset: stats, daily-limit unbans, limits and quota clear."""
    async with ROUTER_LOCK:
        ip_ok = await asyncio.to_thread(reset_ip_traffic_stats)
    await asyncio.sleep(5)

    async with ROUTER_LOCK:
        bw_ok = await asyncio.to_thread(reset_bandwidth_stats)
    await asyncio.sleep(5)

    daily_banned   = db.get_banned_by_reason("daily_limit")
    unbanned_count = 0

    if daily_banned:
        async with ROUTER_LOCK:
            for mac in list(daily_banned):
                if mac in state.banned_macs:
                    state.banned_macs.remove(mac)
                    db.unban_device(mac)
                    unbanned_count += 1
            await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)

    cleared_overrides = await asyncio.to_thread(db.clear_expired_today_only_limits)
    await asyncio.to_thread(db.clear_daily_notifications)

    cleared_extra_quota = await asyncio.to_thread(usage_db.clear_all_extra_quota)
    await asyncio.to_thread(telegram_db.clear_all_threshold_notifications)

    state.ip_to_mac_cache.clear()

    return ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota

def _midnight_status_box(ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota):
    return (
        f"```\n"
        f"{'IP Traffic Reset:'.ljust(20)} {'OK' if ip_ok else 'FAILED'}\n"
        f"{'Bandwidth Reset:'.ljust(20)} {'OK' if bw_ok else 'FAILED'}\n"
        f"{'Devices Unblocked:'.ljust(20)} {unbanned_count}\n"
        f"{'Custom Limits Reset:'.ljust(20)} {cleared_overrides}\n"
        f"{'Extra Quota Reset:'.ljust(20)} {cleared_extra_quota}\n"
        f"```"
    )

async def _retry_midnight_reset(channel):
    """Retry midnight reset on failure until success."""
    global _midnight_retry_running
    attempt = 1
    try:
        while attempt <= MIDNIGHT_MAX_RETRIES:
            await asyncio.sleep(MIDNIGHT_RETRY_INTERVAL)
            logger.info(f"Retrying midnight reset (attempt {attempt})...")

            try:
                ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota = await _run_midnight_reset_steps()
            except Exception as e:
                logger.error(f"Error during midnight reset retry attempt {attempt}: {e}")
                if channel:
                    embed = discord.Embed(
                        title=f"`💥` Midnight Reset Retry #{attempt} Crashed",
                        description=f"```\n{str(e)[:500]}\n```",
                        color=0xe74c3c
                    )
                    embed.set_footer(text="Retrying again in 10 minutes...")
                    try:
                        await channel.send(embed=embed)
                    except Exception as send_err:
                        logger.error(f"Failed to send retry-crash notification: {send_err}")
                attempt += 1
                continue

            if ip_ok and bw_ok:
                if channel:
                    embed = discord.Embed(
                        title="`✅` Midnight Reset Recovered",
                        description=_midnight_status_box(ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota),
                        color=0x2ecc71
                    )
                    embed.set_footer(text=f"Succeeded on retry attempt {attempt}.")
                    await channel.send(embed=embed)
                logger.info(f"Midnight reset succeeded on retry attempt {attempt}.")
                return

            logger.warning(f"Midnight reset retry attempt {attempt} still failing (ip_ok={ip_ok}, bw_ok={bw_ok}).")
            if channel:
                embed = discord.Embed(
                    title=f"`⚠️` Midnight Reset Retry #{attempt} Still Failing",
                    description=_midnight_status_box(ip_ok, bw_ok, unbanned_count, cleared_overrides, cleared_extra_quota),
                    color=0xe67e22
                )
                embed.set_footer(text="Retrying again in 10 minutes...")
                try:
                    await channel.send(embed=embed)
                except Exception as send_err:
                    logger.error(f"Failed to send retry-status notification: {send_err}")

            attempt += 1

        logger.error("Midnight reset retries exhausted without success.")
        if channel:
            embed = discord.Embed(
                title="`❌` Midnight Reset Still Failing",
                description=f"Gave up after {MIDNIGHT_MAX_RETRIES} retries. Please check the router manually.",
                color=0xe74c3c
            )
            await channel.send(embed=embed)
    finally:
        _midnight_retry_running = False
