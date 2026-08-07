import discord
from discord import app_commands
from logger import logger
from state import state
from utils.discord import safe_defer
from commands.views.bulk_block import BulkBlockView
def setup(bot):

    @bot.tree.command(
        name="blkall",
        description="Select multiple saved devices to block"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def blkall(interaction: discord.Interaction):

        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /blkall | User: {interaction.user}")

        try:
            options = [
                discord.SelectOption(
                    label=hostname,
                    value=mac,
                    description=f"MAC: {mac}"
                )
                for mac, hostname in state.macs_list.items()
                if mac.upper() not in state.banned_macs
            ]

            if not options:
                await interaction.followup.send(
                    "`⚠️` All saved devices are already blocked or list is empty."
                )
                return

            view = BulkBlockView(options[:25])

            await interaction.followup.send(
                "Select the saved devices you want to block:",
                view=view,
            )

        except Exception as e:
            logger.error(f"FAILURE in blkall: {e}")

            await interaction.followup.send(
                f"`❌` Error: {str(e)}"
            )