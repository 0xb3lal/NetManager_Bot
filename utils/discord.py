import discord

from logger import logger
from utils.embeds import error_embed


async def ensure_admin(interaction: discord.Interaction) -> bool:
    """Any administrator may answer onboarding questions (the bot itself
    starts these messages, so there is no single invoker to restrict to).
    Mirrors services/onboarding.py:89 _admin_gate but shared via utils
    to dedup commands/views/bulk_block.py:18 + tasks/anomaly_check.py:144."""
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    await interaction.response.send_message(
        embed=error_embed("`❌` Only administrators can answer onboarding questions."),
        ephemeral=True,
    )
    return False


async def safe_defer(interaction: discord.Interaction, thinking: bool = False) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(thinking=thinking)
        return True
    except discord.errors.HTTPException as e:
        cmd = interaction.command.name if interaction.command else "?"
        if e.code == 40060:
            logger.warning(
                f"Interaction already acknowledged for /{cmd} (40060), continuing."
            )
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
