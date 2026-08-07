import asyncio

from discord.ext import tasks

from logger import logger
from router.devices import fetch_devlist_and_discover
from state import ROUTER_LOCK


def setup_device_discovery_task(bot):
    """Create and configure the device discovery task."""

    @tasks.loop(minutes=5.0)
    async def device_discovery_task():
        """Periodically discover new network devices."""

        if ROUTER_LOCK.locked():
            logger.debug(
                "Device discovery skipped (router busy with another task)."
            )
            return

        try:
            async with ROUTER_LOCK:
                await asyncio.to_thread(
                    fetch_devlist_and_discover,
                    bot,
                )

            logger.debug(
                "Scheduled device discovery check completed."
            )

        except Exception as e:
            logger.exception(
                f"Error in device discovery task: {e}"
            )

    @device_discovery_task.before_loop
    async def before_device_discovery():
        await bot.wait_until_ready()

        logger.info(
            "Device discovery task started "
            "(checks for new devices every 5 minutes)."
        )

    return device_discovery_task