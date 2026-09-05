import asyncio

import discord
from discord import app_commands

from logger import logger
from router.firewall import ban_mac
from state import ROUTER_LOCK, state
from utils.autocomplete import mac_autocomplete
from utils.discord import safe_defer
from utils.embeds import error_embed
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(name="blk", description="Ban a MAC address from the list")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.autocomplete(mac=mac_autocomplete)
    async def ban(interaction: discord.Interaction, mac: str):
        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /blk | User: {interaction.user} | Target: {mac}")

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                return await interaction.followup.send(
                    embed=error_embed("`❌` Invalid MAC Address format")
                )

            async with ROUTER_LOCK:
                await asyncio.to_thread(ban_mac, mac_upper, "manual")

            device_name = state.macs_list.get(mac_upper, "Unknown Device")

            lines = [
                f"{i:02d}. {state.macs_list.get(m, 'Unknown Device')}"
                for i, m in enumerate(sorted(state.banned_macs), 1)
            ]

            current_list = (
                "```\n" + "\n".join(lines) + "```"
                if lines
                else "No devices currently banned"
            )

            embed = discord.Embed(
                title="`🚫` Device Blocked",
                description=f"**Target:** `{device_name}`",
                color=0xFF4747,
            )

            embed.add_field(
                name="`📝` Updated Banned List",
                value=current_list,
                inline=False,
            )

            await interaction.followup.send(embed=embed)

            logger.info(
                f"SUCCESS: {mac_upper} blocked. "
                f"Total banned: {len(state.banned_macs)}"
            )

        except Exception as e:
            logger.error(f"FAILURE: {e}")

            try:
                await interaction.followup.send(
                    embed=error_embed(
                        "`❌` Router Error: Connection timed out or failed."
                    )
                )
            except Exception:
                pass
