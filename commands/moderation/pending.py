import discord
from discord import app_commands

import db
from logger import logger
from utils.discord import safe_defer


def setup(bot):

    @bot.tree.command(
        name="pending",
        description="List devices still awaiting onboarding (blocked until reviewed)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def pending(interaction: discord.Interaction):
        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /pending | User: {interaction.user}")

        try:
            pending_devices = db.get_pending_devices()

            if pending_devices:
                msg = "\n".join(
                    f"`{mac}` : **{hostname}**"
                    for mac, hostname in pending_devices.items()
                )
                embed_color = discord.Color.blue()
            else:
                msg = "*No devices are pending onboarding.*"
                embed_color = discord.Color.light_grey()

            embed = discord.Embed(
                title=f"`📋` Devices Pending Onboarding ({len(pending_devices)})",
                description=msg,
                color=embed_color,
            )

            embed.set_footer(
                text="Pending devices are blocked until allowed via /wl add."
            )

            await interaction.followup.send(embed=embed)

            logger.info(
                f"SUCCESS: /pending | Returned {len(pending_devices)} pending device(s)"
            )

        except Exception as e:
            logger.error(f"FAILURE in /pending command: {e}")
            await interaction.followup.send("`❌` Failed to retrieve pending devices.")
