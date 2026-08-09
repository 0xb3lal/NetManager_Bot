import asyncio

from logger import logger
import telegram.client as telegram_client
import telegram.commands as telegram_commands

_POLL_TIMEOUT = 30  # seconds — Telegram long-polling wait time per request
_ERROR_BACKOFF = 5  # seconds — pause before retrying after a failure


async def telegram_polling_loop():
    """
    Continuously long-poll Telegram for new messages and dispatch them.
    Meant to be run as a background asyncio task for the lifetime of the bot.
    """
    last_update_id = 0
    logger.info("Telegram command polling started.")

    while True:
        try:
            updates = await telegram_client.get_updates(
                offset=last_update_id + 1 if last_update_id else None,
                timeout=_POLL_TIMEOUT,
            )
            for update in updates:
                last_update_id = update["update_id"]
                try:
                    await telegram_commands.handle_update(update)
                except Exception as e:
                    logger.error(f"Error handling Telegram update {update.get('update_id')}: {e}")

        except Exception as e:
            logger.error(f"Error in Telegram polling loop: {e}")
            await asyncio.sleep(_ERROR_BACKOFF)


def start_telegram_polling():
    """Call once at bot startup (e.g. in main.py after bot is ready) to launch the polling loop."""
    return asyncio.create_task(telegram_polling_loop())


async def initialize_telegram_bot():
    """
    Call once at startup, before start_telegram_polling(): registers the
    bot's command menu with Telegram (so /usage and /start show up in the
    "/" button in the chat UI).
    """
    await telegram_client.set_bot_commands()