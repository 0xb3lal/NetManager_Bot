import discord
from discord import app_commands
from logger import logger
from state import state
from utils.discord import safe_defer
from commands.views.bulk_unblock import BulkUnblockView


def setup(bot):

    @bot.tree.command(
        name="rmall",
        description="Select multiple devices to unblock from the banned list"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def rmall(interaction: discord.Interaction):

        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /rmall | User: {interaction.user}")

        try:
            options = [
                discord.SelectOption(
                    label=state.macs_list.get(
                        mac,
                        f"Unknown ({mac})"
                    ),
                    value=mac,
                    description=f"MAC: {mac}"
                )
                for mac in state.banned_macs
            ]

            if not options:
                await interaction.followup.send(
                    "`⚠️` No devices are currently banned.",
                    ephemeral=True
                )
                return

            view = BulkUnblockView(options[:25])

            await interaction.followup.send(
                "Select the devices you want to unblock:",
                view=view
            )

        except Exception as e:
            logger.error(f"FAILURE in rmall: {e}")

            await interaction.followup.send(
                f"`❌` Error: {str(e)}"
            )