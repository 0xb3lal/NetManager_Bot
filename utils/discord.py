import discord

from logger import logger


async def safe_defer(interaction: discord.Interaction, thinking: bool = False) -> bool:
    """Safely defer interaction with error handling."""
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(thinking=thinking)
        return True
    except discord.errors.HTTPException as e:
        cmd = interaction.command.name if interaction.command else "?"
        if e.code == 40060:
            logger.warning(f"Interaction already acknowledged for /{cmd} (40060), continuing.")
            return True
        elif e.code == 10062:
            logger.warning(f"Unknown/expired interaction for /{cmd} (10062), aborting.")
            return False
        else:
            logger.error(f"Failed to defer /{cmd}: {e}")
            return False
    except Exception as e:
        logger.error(f"Failed to defer: {e}")
        return False