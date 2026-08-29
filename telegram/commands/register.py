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
    """Register Telegram command handler under name."""

    def decorator(func):
        COMMAND_HANDLERS[name] = func
        logger.info(f"Telegram command registered: {name} -> {func.__name__}")
        return func

    return decorator
