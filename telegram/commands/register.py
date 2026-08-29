from logger import logger

COMMAND_HANDLERS = {}


def command(name: str):
    """Register Telegram command handler under name."""

    def decorator(func):
        COMMAND_HANDLERS[name] = func
        logger.info(f"Telegram command registered: {name} -> {func.__name__}")
        return func

    return decorator
