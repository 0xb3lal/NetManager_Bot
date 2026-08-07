import discord

from logger import logger
from state import state


def setup(bot):

    @bot.tree.command(
        name="macs",
        description="List known MAC addresses and their hostnames"
    )
    async def macs(interaction: discord.Interaction):

        logger.info(f"ACTION: /macs | User: {interaction.user}")

        try:
            await interaction.response.defer()

        except Exception as e:
            logger.error(f"Failed to defer /macs: {e}")
            return

        try:

            if state.macs_list:
                msg = "\n".join(
                    f"`{mac}` : **{hostname}**"
                    for mac, hostname in state.macs_list.items()
                )

                embed_color = discord.Color.blue()

            else:
                msg = "*No MAC addresses found.*"
                embed_color = discord.Color.light_grey()

            embed = discord.Embed(
                title="`📋` Known Devices",
                description=msg,
                color=embed_color
            )

            await interaction.followup.send(embed=embed)

            logger.info(
                f"SUCCESS: /macs | Returned {len(state.macs_list)} devices"
            )

        except Exception as e:
            logger.error(f"FAILURE in /macs command: {e}")

            await interaction.followup.send(
                "`❌` Failed to retrieve the MACs list."
            )