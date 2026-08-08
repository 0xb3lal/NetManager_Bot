import aiohttp

from logger import logger
from config import TELEGRAM_BOT_TOKEN

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def send_message(chat_id: str, text: str) -> bool:
    """Send an HTML-formatted text message to a Telegram chat via the Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping Telegram notification.")
        return False
    if not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TELEGRAM_API_BASE}/sendMessage",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram send failed ({resp.status}) to {chat_id}: {body}")
                    return False
                return True
    except Exception as e:
        logger.error(f"Error sending Telegram message to {chat_id}: {e}")
        return False