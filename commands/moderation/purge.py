import discord
from discord import app_commands

from logger import logger


purge_group = app_commands.Group(
    name="purge",
    description="Commands to delete messages"
)


@purge_group.command(
    name="any",
    description="Delete any messages in the channel"
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    amount="Number of messages to delete"
)
async def purge_any(
    interaction: discord.Interaction,
    amount: int
):
    try:
        await interaction.response.defer(
            ephemeral=True
        )

    except Exception as e:
        logger.warning(
            f"Failed to defer /purge any (ignoring): {e}"
        )

    try:
        deleted = await interaction.channel.purge(
            limit=amount
        )

        try:
            embed = discord.Embed(
                title="`🧹` Purge Completed",
                description=(
                    f"`✅` Deleted **{len(deleted)}** messages "
                    "from the channel."
                ),
                color=0x2ecc71
            )

            embed.set_footer(
                text=f"Requested by {interaction.user}"
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )

        except Exception:
            logger.warning(
                "Could not send followup for purge any "
                "(interaction expired), but messages were deleted."
            )

    except Exception as e:
        logger.error(
            f"Error in purge any: {e}"
        )

        try:
            embed = discord.Embed(
                title="`❌` Purge Failed",
                description=(
                    "Failed to delete messages."
                ),
                color=0xe74c3c
            )

            embed.set_footer(
                text=f"Requested by {interaction.user}"
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )

        except Exception:
            pass


def setup(bot):
    bot.tree.add_command(
        purge_group
    )