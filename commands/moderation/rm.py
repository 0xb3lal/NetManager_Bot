import asyncio

import discord
from discord import app_commands

from logger import logger
from router.firewall import unban_mac
from state import ROUTER_LOCK, state
from utils.autocomplete import banned_macs_autocomplete
from utils.discord import safe_defer
from utils.embeds import error_embed
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="rm", description="Unban a device from the current banned list"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.autocomplete(mac=banned_macs_autocomplete)
    async def rm(interaction: discord.Interaction, mac: str):

        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /rm | User: {interaction.user} | Target MAC: {mac}")

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                return await interaction.followup.send(
                    embed=error_embed("`❌` Invalid MAC Address format")
                )

            async with ROUTER_LOCK:
                await asyncio.to_thread(unban_mac, mac_upper)

            device_name = state.macs_list.get(mac_upper, "Unknown Device")

            lines = [
                f"{i:02d}. {state.macs_list.get(m, 'Unknown')}"
                for i, m in enumerate(sorted(state.banned_macs), 1)
            ]

            current_list = (
                "```\n" + "\n".join(lines) + "```"
                if lines
                else "✨ *No devices currently banned*"
            )

            embed = discord.Embed(
                title="`✅` Device Unblocked",
                description=f"**Target:** `{device_name}`",
                color=0x2ECC71,
            )

            embed.add_field(
                name="`📝` Updated Banned List",
                value=current_list,
                inline=False,
            )

            await interaction.followup.send(embed=embed)

            logger.info(
                f"SUCCESS: {mac_upper} unblocked. "
                f"New list size: {len(state.banned_macs)}"
            )

        except Exception as e:
            logger.error(f"FAILURE: {e}")

            try:
                await interaction.followup.send(
                    embed=error_embed("`❌` Router Error: Failed to remove block.")
                )
            except Exception:
                pass
