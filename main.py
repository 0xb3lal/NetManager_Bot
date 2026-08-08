import asyncio
import discord
from discord import app_commands
import db
from logger import logger
from state import ROUTER_LOCK, state
from tasks.traffic_check import setup_traffic_check_task
from tasks.device_discovery import setup_device_discovery_task
from tasks.daily_usage_monitor import setup_daily_usage_monitor_task
from tasks.daily_report import setup_daily_network_report_task
from tasks.midnight_reset import setup_midnight_reset_task
from router.firewall import _reapply_firewall_state
from commands.register import setup
import usage_db
import telegram.db as telegram_db
from config import (
    DISCORD_TOKEN,
    GUILD_ID,
)

# DATABASE & STATE INITIALIZATION

db.init_db()
usage_db.init_usage_tables()
telegram_db.init_telegram_tables()
state.reload_from_db()

# BOT CLIENT

class MyBot(discord.Client):
    """Main Discord bot client with command tree."""

    def __init__(self):
        super().__init__(
            intents=discord.Intents.default(),
            heartbeat_timeout=60.0
        )
        self.tree = app_commands.CommandTree(self)

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.errors.CommandInvokeError):
            cause = error.original
        else:
            cause = error

        if isinstance(cause, discord.errors.NotFound) and cause.code == 10062:
            return

        logger.error(
            f"Tree error in /{interaction.command.name if interaction.command else '?'}: {error}"
        )

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)

        traffic_check_task = setup_traffic_check_task(self)
        if not traffic_check_task.is_running():
            traffic_check_task.start()

        device_discovery_task = setup_device_discovery_task(self)
        if not device_discovery_task.is_running():
            device_discovery_task.start()

        daily_network_report = setup_daily_network_report_task(self)
        if not daily_network_report.is_running():
            daily_network_report.start()

        daily_usage_monitor_task = setup_daily_usage_monitor_task(self)
        if not daily_usage_monitor_task.is_running():
            daily_usage_monitor_task.start()

        midnight_reset_task = setup_midnight_reset_task(self)
        if not midnight_reset_task.is_running():
            midnight_reset_task.start()

    async def on_disconnect(self):
        logger.warning(
            "Bot disconnected from Discord Gateway. "
            "Waiting for automatic reconnect..."
        )

    async def on_resumed(self):
        logger.info(
            "Discord Gateway session resumed successfully. "
            "Bot is fully operational."
        )

    async def on_ready(self):
        logger.info(f"Bot ready: {self.user}")

        state.reload_from_db()

        logger.info(
            f"Reapplying firewall rules on startup "
            f"(lockdown_state={state.lockdown_state})..."
        )

        async with ROUTER_LOCK:
            await asyncio.to_thread(_reapply_firewall_state)

# MAIN ENTRY POINT

async def main():
    """Main bot entry point with automatic restart on fatal errors."""
    retry_delay = 5
    max_retry_delay = 300  # cap backoff at 5 minutes
    attempt = 0

    while True:
        attempt += 1
        bot = MyBot()
        setup(bot)

        if attempt > 1:
            logger.info(f"Connection attempt #{attempt}...")

        try:
            async with bot:
                await bot.start(DISCORD_TOKEN)
            logger.info("Bot stopped normally.")
            break

        except asyncio.CancelledError:
            logger.warning("Main coroutine cancelled.")
            break

        except discord.LoginFailure:
            logger.error("Invalid Discord token. Stopping (will not retry).")
            break

        except Exception as e:
            logger.error(
                f"Fatal error in main loop (attempt #{attempt}): {e}. "
                f"Retrying in {retry_delay}s..."
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
            continue

        retry_delay = 5
        attempt = 0

if __name__ == "__main__":
    logger.info("--- Starting NetManager Bot ---")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received (Ctrl+C).")
    finally:
        logger.info("--- Bot has been stopped safely ---")