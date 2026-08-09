import telegram.client as telegram_client
from logger import logger
from telegram.commands.register import command


@command("/start")
async def handle_start_command(chat_id: str, first_name: str = ""):
    """Reply to /start with a short, personalized welcome message."""
    greeting = f"<b>👋 Welcome, {first_name}!\n</b>" if first_name else "<b>👋 Welcome!</b>\n"
    logger.info(f"Telegram /start used by {first_name or chat_id} (chat_id={chat_id}).")
    await telegram_client.send_chat_action(chat_id, "typing")
    await telegram_client.send_message(
        chat_id,
        f"{greeting} Send /usage anytime to check your current daily usage."
    )