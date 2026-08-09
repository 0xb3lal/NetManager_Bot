"""
telegram/commands/register.py

Decorator-based command registry for the Telegram bot, mirroring the
pattern used for Discord commands. Each command file decorates its
handler with @command("/name") and it registers itself automatically —
no need to touch this file or __init__.py when adding a new command.
"""

COMMAND_HANDLERS = {}


def command(name: str):
    """Decorator: registers an async handler(chat_id) under a command name."""
    def decorator(func):
        COMMAND_HANDLERS[name] = func
        return func
    return decorator