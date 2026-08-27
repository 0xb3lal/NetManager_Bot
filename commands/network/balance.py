import asyncio
import discord
from logger import logger
from state import state
from services.radius import fetch_radius_traffic

def setup(bot):

    @bot.tree.command(
        name="balance",
        description="Check current available traffic"
    )
    async def balance(interaction: discord.Interaction):

        logger.info(
            f"ACTION: /balance | User: {interaction.user}"
        )

        try:
            await interaction.response.defer()

        except Exception as e:
            logger.error(
                f"Failed to defer /balance: {e}"
            )
            return

        try:
            try:
                traffic = await asyncio.to_thread(
                    fetch_radius_traffic,
                    True
                )
            except Exception as e:
                logger.error(
                    f"Error in balance-fetch thread: {e}"
                )
                traffic = None

            if traffic:
                balance_label = "Current Balance:".ljust(17)
                limit_label = "System Limit:".ljust(17)

                status_box = (
                    "```\n"
                    f"{balance_label} {traffic}\n"
                    f"{limit_label} {state.threshold} GB\n"
                    "```\n"
                    "`💡` *Status is updated automatically every hour.*"
                )

                embed = discord.Embed(
                    title="`📊` Network Status",
                    description=status_box,
                    color=0x3498db
                )

                await interaction.followup.send(
                    embed=embed
                )

                logger.info(
                    f"SUCCESS: /balance | Balance sent: {traffic}"
                )

            else:
                embed = discord.Embed(
                    title="`❌` System Error",
                    description=(
                        "`Could not fetch balance. "
                        "Radius server unreachable.`"
                    ),
                    color=0xe74c3c
                )
                await interaction.followup.send(
                    embed=embed
                )
                logger.error(
                    "FAILURE: /balance | Could not fetch traffic"
                )

        except Exception as e:
            logger.error(
                f"FAILURE in /balance command: {e}"
            )
            await interaction.followup.send(
                "`❌` Failed to check balance."
            )
