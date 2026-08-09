"""
telegram/discord_bridge.py

Telegram command handlers run outside the Discord command tree, but
sometimes need to notify the admin on Discord (e.g. a new, unlinked
Telegram user messaging the bot). This module holds a reference to the
Discord bot instance, set once at startup, so any Telegram-side code
can reach the Discord channel without needing the bot passed through
every function call.
"""

import discord

from logger import logger
from config import CHANNEL_ID

_bot_instance = None


def set_bot_instance(bot):
    global _bot_instance
    _bot_instance = bot


async def notify_admin_new_telegram_user(chat_id: str, first_name: str):
    """Post an embed to the admin Discord channel when an unlinked Telegram
    chat messages the bot, so the admin can link it with /tglink."""
    if _bot_instance is None:
        logger.warning("Discord bot instance not set — can't notify admin of new Telegram user.")
        return

    channel = _bot_instance.get_channel(CHANNEL_ID)
    if not channel:
        return

    status_box = (
        "```\n"
        f"{'Name:'.ljust(10)} {first_name or 'Unknown'}\n"
        f"{'Chat ID:'.ljust(10)} {chat_id}\n"
        "```"
    )

    embed = discord.Embed(
        title="`📨` New Telegram User",
        description=status_box + "\nUse `/tglink` to link this chat to a device.",
        color=0x3498db
    )

    await channel.send(embed=embed)
    logger.info(f"Notified admin on Discord about new Telegram user: {first_name} (chat_id={chat_id})")