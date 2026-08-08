import discord
from discord import app_commands
import telegram.db as telegram_db
from logger import logger
from state import state
from utils.discord import safe_defer
from utils.validators import is_valid_mac
from utils.autocomplete import all_macs_autocomplete


def setup(bot):

    @bot.tree.command(
        name="tglink",
        description="Link a device to a Telegram chat for usage notifications"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mac="The device to link",
        chat_id="Telegram chat ID (leave empty to unlink)"
    )
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def tglink(
        interaction: discord.Interaction,
        mac: str,
        chat_id: str = None
    ):
        if not await safe_defer(interaction, thinking=True):
            return

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                await interaction.followup.send("`❌` Invalid MAC Address format.")
                return

            device_name = state.macs_list.get(mac_upper, mac_upper)

            if chat_id is None:
                removed = telegram_db.remove_device_chat_id(mac_upper)
                msg = "`✅` Telegram link removed." if removed else "`⚠️` No Telegram link was set for this device."
                await interaction.followup.send(msg)
                return

            telegram_db.set_device_chat_id(mac_upper, chat_id)
            logger.info(f"User {interaction.user} linked {mac_upper} to Telegram chat {chat_id}")

            await interaction.followup.send(
                f"`✅` **{device_name}** linked to Telegram chat `{chat_id}`."
            )

        except Exception as e:
            logger.error(f"Error in tglink command: {e}")
            await interaction.followup.send("`❌` Failed to link device.")