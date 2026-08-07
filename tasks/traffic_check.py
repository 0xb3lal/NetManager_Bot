from discord.ext import tasks
from logger import logger
from services.traffic import async_check_and_lock


def setup_traffic_check_task(bot):
    """Create and configure the hourly traffic check task."""

    @tasks.loop(hours=1.0)
    async def traffic_check_task():
        """Hourly traffic check against Radius."""
        logger.info("Starting scheduled traffic check...")

        try:
            await async_check_and_lock(bot)

            logger.info(
                "Scheduled traffic check completed successfully."
            )

        except Exception as e:
            logger.exception(
                f"Unexpected error during traffic check task: {e}"
            )

    @traffic_check_task.before_loop
    async def before_traffic_check():
        logger.info(
            "Waiting for bot to be ready before starting traffic task..."
        )

        await bot.wait_until_ready()

        logger.info("Bot is ready. Traffic task started.")

    return traffic_check_task