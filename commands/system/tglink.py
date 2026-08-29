import discord
from discord import app_commands

import telegram.db as telegram_db
from logger import logger
from state import state
from utils.autocomplete import all_macs_autocomplete
from utils.discord import safe_defer
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="tglink",
        description="Link a device to a Telegram chat for usage notifications",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mac="The device to link", chat_id="Telegram chat ID (leave empty to unlink)"
    )
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def tglink(interaction: discord.Interaction, mac: str, chat_id: str = None):
        if not await safe_defer(interaction, thinking=True):
            return

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                embed = discord.Embed(
                    title="❌ Invalid MAC Address",
                    description="The MAC address format you entered is not valid.",
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return

            device_name = state.macs_list.get(mac_upper, mac_upper)

            if chat_id is None:
                removed = telegram_db.remove_device_chat_id(mac_upper)

                if removed:
                    embed = discord.Embed(
                        title="✅ Telegram Link Removed",
                        description=f"The Telegram link for **{device_name}** has been removed.",
                        color=discord.Color.green(),
                    )
                else:
                    embed = discord.Embed(
                        title="⚠️ No Link Found",
                        description=f"No Telegram link was set for **{device_name}**.",
                        color=discord.Color.orange(),
                    )

                await interaction.followup.send(embed=embed)
                return

            telegram_db.set_device_chat_id(mac_upper, chat_id)
            logger.info(
                f"User {interaction.user} linked {mac_upper} to Telegram chat {chat_id}"
            )

            embed = discord.Embed(
                title="✅ Device Linked",
                description=(
                    f"**{device_name}** has been linked to Telegram chat.\n\n"
                    f"**MAC Address:** `{mac_upper}`\n"
                    f"**Telegram Chat ID:** `{chat_id}`"
                ),
                color=discord.Color.green(),
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in tglink command: {e}")
            embed = discord.Embed(
                title="❌ Failed to Link Device",
                description="An unexpected error occurred while linking this device.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
