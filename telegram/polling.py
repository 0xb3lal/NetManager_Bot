import asyncio

import telegram.client as telegram_client
import telegram.commands as telegram_commands
from logger import logger

_POLL_TIMEOUT = 30  # seconds — Telegram long-polling wait time per request
_ERROR_BACKOFF = 5  # seconds — pause before retrying after a failure
_polling_task = None


async def telegram_polling_loop():
    """Long-poll Telegram and dispatch updates."""
    last_update_id = 0
    logger.info("Telegram command polling started.")

    while True:
        try:
            updates = await telegram_client.get_updates(
                offset=last_update_id + 1 if last_update_id else None,
                timeout=_POLL_TIMEOUT,
            )
            for update in updates:
                update_id = update.get("update_id")
                if update_id is None:
                    logger.warning(
                        f"Skipping malformed Telegram update without update_id: {update}"
                    )
                    continue
                last_update_id = update_id
                try:
                    await telegram_commands.handle_update(update)
                except Exception as e:
                    logger.error(
                        f"Error handling Telegram update {update.get('update_id')}: {e}"
                    )

        except Exception as e:
            logger.error(f"Error in Telegram polling loop: {e}")
            await asyncio.sleep(_ERROR_BACKOFF)


def start_telegram_polling():
    """Start the Telegram polling task (idempotent: one live poller max)."""
    global _polling_task
    if _polling_task is not None and not _polling_task.done():
        return _polling_task
    _polling_task = asyncio.create_task(telegram_polling_loop())
    return _polling_task


async def initialize_telegram_bot():
    """Register Telegram command menu at startup."""
    await telegram_client.set_bot_commands()
