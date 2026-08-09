from logger import logger

from telegram.commands.register import COMMAND_HANDLERS

# Import every command module so its @command(...) decorator runs and
# registers itself into COMMAND_HANDLERS. To add a new command later:
# create commands/<name>.py with an @command("/name") handler, and add
# ONE import line below. Nothing else in this file needs to change.
import telegram.commands.usage
import telegram.commands.start


async def handle_update(update: dict):
    """Route a single incoming Telegram update to the right command handler."""
    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = str(message["chat"]["id"])
    first_name = message.get("from", {}).get("first_name", "")
    text = message["text"].strip()
    if not text:
        return

    command_name = text.split()[0].split("@")[0]  # strip "@BotName" if present
    handler = COMMAND_HANDLERS.get(command_name)

    if handler:
        await handler(chat_id, first_name)
    # Unknown commands are ignored on purpose — no need to spam replies
    # for random messages sent to the bot.


async def handle_update(update: dict):
    """Route a single incoming Telegram update to the right command handler."""
    message = update.get("message")
    if not message or "text" not in message:
        return

    chat_id = str(message["chat"]["id"])
    text = message["text"].strip()
    if not text:
        return

    command = text.split()[0].split("@")[0]  # strip "@BotName" if present
    handler = COMMAND_HANDLERS.get(command)

    if handler:
        await handler(chat_id)
    # Unknown commands are ignored on purpose — no need to spam replies
    # for random messages sent to the bot.