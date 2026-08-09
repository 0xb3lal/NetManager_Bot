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


async def send_chat_action(chat_id: str, action: str = "typing") -> bool:
    """Send a chat action (e.g. 'typing') to indicate the bot is processing."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    if not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "action": action,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TELEGRAM_API_BASE}/sendChatAction",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram sendChatAction failed ({resp.status}) to {chat_id}: {body}")
                    return False
                return True
    except Exception as e:
        logger.error(f"Error sending chat action to {chat_id}: {e}")
        return False


async def get_updates(offset: int | None = None, timeout: int = 30) -> list:
    """
    Long-poll Telegram for new incoming updates (messages) since `offset`.
    Blocks up to `timeout` seconds server-side if there's nothing new yet —
    this is the standard Telegram long-polling pattern, so callers should
    just loop and call this repeatedly without extra sleeps.
    """
    if not TELEGRAM_BOT_TOKEN:
        return []

    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TELEGRAM_API_BASE}/getUpdates",
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout + 10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram getUpdates failed ({resp.status}): {body}")
                    return []
                data = await resp.json()
                return data.get("result", [])
    except Exception as e:
        logger.error(f"Error polling Telegram updates: {e}")
        return []


async def set_bot_commands() -> bool:
    """
    Register the bot's command list with Telegram so it shows up in the
    "/" menu button in every chat with the bot. Call this once at startup
    (safe to call every time — Telegram just overwrites the list).
    """
    if not TELEGRAM_BOT_TOKEN:
        return False

    commands = [
        {"command": "usage", "description": "Check your current daily usage"},
        {"command": "start", "description": "Get started / link info"},
    ]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TELEGRAM_API_BASE}/setMyCommands",
                json={"commands": commands},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Telegram setMyCommands failed ({resp.status}): {body}")
                    return False
                logger.info("Telegram bot command menu registered.")
                return True
    except Exception as e:
        logger.error(f"Error setting Telegram bot commands: {e}")
        return False