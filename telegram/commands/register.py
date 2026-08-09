"""
telegram/commands/register.py

Decorator-based command registry for the Telegram bot, mirroring the
pattern used for Discord commands. Each command file decorates its
handler with @command("/name") and it registers itself automatically —
no need to touch this file or __init__.py when adding a new command.
"""

from logger import logger

COMMAND_HANDLERS = {}


def command(name: str):
    """Decorator: registers an async handler(chat_id, first_name) under a command name."""
    def decorator(func):
        COMMAND_HANDLERS[name] = func
        logger.info(f"Telegram command registered: {name} -> {func.__name__}")
        return func
    return decorator