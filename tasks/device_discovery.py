import asyncio
import requests

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

        # Bounded retry: one immediate retry for transient router failures
        for attempt in range(1, 3):
            try:
                async with ROUTER_LOCK:
                    await asyncio.to_thread(
                        fetch_devlist_and_discover,
                        bot,
                    )

                logger.debug(
                    "Scheduled device discovery check completed."
                )
                break

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, RuntimeError) as e:
                if attempt == 1:
                    logger.warning(
                        f"Device discovery failed (attempt 1/2): {e}, retrying in 2s"
                    )
                    # Release lock before sleep (exited async with), then sleep outside
                    await asyncio.sleep(2)
                    continue
                logger.exception(
                    f"Error in device discovery task after retry: {e}"
                )
                break

            except asyncio.CancelledError:
                raise

            except Exception as e:
                logger.exception(
                    f"Error in device discovery task: {e}"
                )
                break

    @device_discovery_task.before_loop
    async def before_device_discovery():
        await bot.wait_until_ready()

        logger.info(
            "Device discovery task started "
            "(checks for new devices every 5 minutes)."
        )

    return device_discovery_task