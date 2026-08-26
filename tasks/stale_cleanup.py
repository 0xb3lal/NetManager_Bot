import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

import db
from config import (
    CHANNEL_ID,
    STALE_DEVICE_CHECK_INTERVAL_DAYS,
    STALE_DEVICE_THRESHOLD_DAYS,
)
from logger import logger
from state import state, ROUTER_LOCK, acquire_router_lock_bounded
from services.onboarding import drop_session

STALE_RETRY_INTERVAL = 45 * 60  # 45 minutes
STALE_MAX_ATTEMPTS = 3
_stale_retry_running = False


async def _run_stale_cleanup_once():
    """Execute one stale cleanup scan+purge. Returns removed list or raises on failure.
    Returns None if lock busy (not a failure, just skip).
    """
    if not await acquire_router_lock_bounded("Stale device cleanup"):
        return None  # lock busy, not a failure
    removed = []
    try:
        now = datetime.now(timezone.utc)
        max_age = timedelta(days=STALE_DEVICE_THRESHOLD_DAYS)

        for mac, row in db.get_devices_with_last_seen().items():
            raw = row["last_seen"]
            if not raw:
                continue
            try:
                last_seen = datetime.fromisoformat(raw)
            except ValueError:
                logger.warning(f"Unparseable last_seen for {mac} ({raw!r}) — skipped.")
                continue

            if now - last_seen < max_age:
                continue

            if db.get_last_seen(mac) != raw:
                logger.debug(f"Stale skip {mac}: seen again since scan.")
                continue

            drop_session(mac)
            state.pending_macs.discard(mac)
            state.banned_macs.discard(mac)
            if mac in state.allowed_macs:
                state.allowed_macs.remove(mac)
            state.macs_list.pop(mac, None)
            for ip, cached_mac in list(state.ip_to_mac_cache.items()):
                if cached_mac == mac:
                    del state.ip_to_mac_cache[ip]

            if db.delete_device_purge(mac):
                removed.append((mac, row["hostname"], raw))
                logger.info(
                    f"Stale device removed: {row['hostname']} ({mac}) — "
                    f"last seen {raw[:10]}."
                )
        return removed
    except Exception as e:
        logger.error(f"Error in stale cleanup scan: {e}")
        raise
    finally:
        try:
            ROUTER_LOCK.release()
        except Exception:
            pass


async def _send_stale_success(channel, removed):
    """Send success notification once, with bounded retry."""
    try:
        if not removed:
            logger.info("Stale device cleanup completed: nothing to remove.")
            # Optional: still notify? Existing behavior logs only, no embed when nothing. Keep same.
            return True
        if not channel:
            logger.info(f"Stale cleanup success: {len(removed)} removed but no channel found.")
            return True
        lines = "\n".join(
            f"{hostname[:18].ljust(18)} {mac}   last seen {raw[:10]}"
            for mac, hostname, raw in removed
        )
        embed = discord.Embed(
            title=f"`🧹` Stale Devices Removed ({len(removed)})",
            description=f"```\n{lines}\n```",
            color=0xE67E22,
        )
        embed.set_footer(
            text=(
                f"Devices unseen for {STALE_DEVICE_THRESHOLD_DAYS} days "
                f"are removed automatically."
            )
        )
        # Bounded retry for Discord send (3 attempts)
        for attempt in range(3):
            try:
                await channel.send(embed=embed)
                logger.info(f"Stale device cleanup completed: {len(removed)} device(s) removed.")
                return True
            except Exception as e:
                logger.warning(f"Stale cleanup success notification attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
        # Auditability: deletion already succeeded, ensure details survive in logs even though Discord failed
        logger.error(
            f"Failed to send stale cleanup success notification after 3 attempts. "
            f"Deleted {len(removed)} device(s) (audit):\n{lines}"
        )
        return False
    except Exception as e:
        logger.error(f"Error sending stale success notification: {e}")
        return False


async def _send_stale_failure(channel, last_error):
    """Send final failure notification once."""
    try:
        if not channel:
            logger.error(f"Stale cleanup failed after {STALE_MAX_ATTEMPTS} attempts: {last_error} (no channel)")
            return
        embed = discord.Embed(
            title="`❌` Stale Cleanup Failed",
            description=(
                f"Stale device cleanup failed after {STALE_MAX_ATTEMPTS} attempts "
                f"over ~90 minutes.\n"
                f"```\n{str(last_error)[:400]}\n```"
            ),
            color=0xe74c3c,
        )
        embed.set_footer(text="Will retry at next scheduled interval.")
        for attempt in range(3):
            try:
                await channel.send(embed=embed)
                return
            except Exception as e:
                logger.warning(f"Stale failure notification attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
        logger.error("Failed to send stale cleanup failure notification")
    except Exception as e:
        logger.error(f"Error sending stale failure notification: {e}")


async def _retry_stale_cleanup(channel, first_error):
    global _stale_retry_running
    last_error = first_error
    try:
        for attempt in range(2, STALE_MAX_ATTEMPTS + 1):
            await asyncio.sleep(STALE_RETRY_INTERVAL)
            logger.info(f"Retrying stale cleanup (attempt {attempt}/{STALE_MAX_ATTEMPTS})...")
            try:
                removed = await _run_stale_cleanup_once()
                if removed is None:
                    # Lock busy is not failure, treat as success with no removal?
                    logger.info("Stale cleanup retry skipped (lock busy), treating as success")
                    return
                # Success
                await _send_stale_success(channel, removed)
                logger.info(f"Stale cleanup succeeded on retry attempt {attempt}")
                return
            except Exception as e:
                last_error = e
                logger.error(f"Stale cleanup retry attempt {attempt} failed: {e}")
                continue
        # All retries exhausted
        logger.error(f"Stale cleanup failed after {STALE_MAX_ATTEMPTS} attempts over ~90 minutes: {last_error}")
        await _send_stale_failure(channel, last_error)
    finally:
        _stale_retry_running = False


def setup_stale_cleanup_task(bot):
    """Create and configure the stale-device cleanup task."""

    @tasks.loop(hours=24 * STALE_DEVICE_CHECK_INTERVAL_DAYS)
    async def stale_cleanup_task():
        """Delete devices not seen by the router for the configured threshold."""
        global _stale_retry_running
        logger.info("Stale device cleanup TRIGGERED — scanning device table...")
        channel = bot.get_channel(CHANNEL_ID)
        try:
            removed = await _run_stale_cleanup_once()
            if removed is None:
                return
            # Success on first attempt
            await _send_stale_success(channel, removed)
        except Exception as e:
            logger.error(f"Stale cleanup attempt 1 failed: {e}")
            if not _stale_retry_running:
                _stale_retry_running = True
                asyncio.create_task(_retry_stale_cleanup(channel, e))
            else:
                logger.warning("Stale retry already running, skipping duplicate trigger")

    @stale_cleanup_task.before_loop
    async def before_stale_cleanup():
        await bot.wait_until_ready()
        logger.info(
            "Stale device cleanup started "
            f"(checks every {STALE_DEVICE_CHECK_INTERVAL_DAYS} day(s), "
            f"removes devices unseen for {STALE_DEVICE_THRESHOLD_DAYS} day(s))."
        )

    return stale_cleanup_task
