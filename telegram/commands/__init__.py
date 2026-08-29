import telegram.commands.start

# registers itself into COMMAND_HANDLERS. To add a new command later:
# create commands/<name>.py with an @command("/name") handler, and add
# ONE import line below. Nothing else in this file needs to change.
import telegram.commands.usage
import telegram.db as telegram_db
from logger import logger
from telegram.commands.register import COMMAND_HANDLERS
from telegram.discord_bridge import notify_admin_new_telegram_user

# on every message — one notification per chat per process run is enough.
_already_notified_unlinked = set()


async def handle_update(update: dict):
    """Route Telegram update to command handler."""
    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = str(message["chat"]["id"])
    sender = message.get("from", {})
    first_name = sender.get("first_name", "")
    text = message["text"].strip()
    if not text:
        return

    # they can /tglink this chat to a device.
    mac = telegram_db.get_mac_by_chat_id(chat_id)
    if not mac and chat_id not in _already_notified_unlinked:
        _already_notified_unlinked.add(chat_id)
        await notify_admin_new_telegram_user(chat_id, first_name)

    command_name = text.split()[0].split("@")[0]  # strip "@BotName" if present
    handler = COMMAND_HANDLERS.get(command_name)

    logger.info(
        f"Telegram command received: {command_name} from chat_id={chat_id} "
        f"first_name={first_name!r}"
    )

    if handler:
        await handler(chat_id, first_name)
    else:
        logger.info(f"No handler registered for Telegram command: {command_name}")
        # spam replies for random messages sent to the bot.
