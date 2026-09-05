import discord

COLOR_SUCCESS = 0x2ECC71
COLOR_ERROR = 0xFF4747
COLOR_WARNING = 0xE67E22
COLOR_INFO = 0x3498DB


def _embed(color, title, description=None, **kwargs):
    return discord.Embed(title=title, description=description, color=color, **kwargs)


def success_embed(title, description=None, **kwargs):
    """Green embed — an action completed successfully."""
    return _embed(COLOR_SUCCESS, title, description, **kwargs)


def error_embed(title, description=None, **kwargs):
    """Red embed — an error, failure, or rejected action."""
    return _embed(COLOR_ERROR, title, description, **kwargs)


def warning_embed(title, description=None, **kwargs):
    """Orange embed — a warning, caution, or partial success."""
    return _embed(COLOR_WARNING, title, description, **kwargs)


def info_embed(title, description=None, **kwargs):
    """Blue embed — informational or neutral output (listings, status)."""
    return _embed(COLOR_INFO, title, description, **kwargs)
