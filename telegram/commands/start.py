import telegram.client as telegram_client
from telegram.commands.register import command


@command("/start")
async def handle_start_command(chat_id: str, first_name: str = ""):
    """Reply to /start with a short, personalized welcome message."""
    greeting = f"👋 Welcome, {first_name}!" if first_name else "👋 Welcome!"
    await telegram_client.send_message(
        chat_id,
        f"{greeting} Send /usage anytime to check your current daily usage."
    )