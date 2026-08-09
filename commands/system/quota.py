import discord
from discord import app_commands
import db
import usage_db
from logger import logger
from state import state, QUOTA_LOCK
from utils.discord import safe_defer
from utils.traffic import format_data_size
from utils.validators import is_valid_mac
from utils.autocomplete import all_macs_autocomplete
from services.limits import (
    recheck_device_after_limit_change,
    add_extra_quota_covering_overage,
)

# Values below this are probably an MB value entered without switching the unit dropdown.
SUSPICIOUS_LOW_GB = 0.1
# Values above this are probably a GB value that was meant to be smaller (e.g. typo).
SUSPICIOUS_HIGH_GB = 1.0


class QuotaConfirmView(discord.ui.View):
    """Confirmation prompt shown when a quota value looks like a unit mistake."""

    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed: bool | None = None  # None = timed out

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "`❌` This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


def setup(bot):

    @bot.tree.command(
        name="quota",
        description="Add to or edit a device's extra daily quota (today only)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mac="The device to change quota for",
        action="Add extra usable data on top of current usage, or edit the extra quota to an absolute value",
        value="Amount of extra data",
        unit="Unit for the value (defaults to GB)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Edit", value="edit"),
    ])
    @app_commands.choices(unit=[
        app_commands.Choice(name="GB", value="GB"),
        app_commands.Choice(name="MB", value="MB"),
    ])
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def quota(
        interaction: discord.Interaction,
        mac: str,
        action: app_commands.Choice[str],
        value: float,
        unit: app_commands.Choice[str] = None
    ):
        if not await safe_defer(interaction, thinking=True):
            return

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                await interaction.followup.send("`❌` Invalid MAC Address format.")
                return

            unit_value = unit.value if unit else "GB"
            value_gb = value / 1024 if unit_value == "MB" else value

            is_suspicious = value_gb < SUSPICIOUS_LOW_GB or value_gb > SUSPICIOUS_HIGH_GB

            if is_suspicious:
                view = QuotaConfirmView(interaction.user.id)
                warning_msg = await interaction.followup.send(
                    embed=discord.Embed(
                        title="`⚠️` Confirm Unusual Quota Value",
                        description=(
                            f"```\n"
                            f"Device: {state.macs_list.get(mac_upper, mac_upper)}\n"
                            f"Value:  {format_data_size(value_gb)}\n"
                            f"Unit:   {unit_value} (entered)\n"
                            f"```"
                            "\nThis looks unusually small or large — did you mean to pick a different unit?\n"
                            "Press **Confirm** to proceed anyway, or **Cancel** to abort."
                        ),
                        color=0xffa500
                    ),
                    view=view
                )

                await view.wait()

                if view.confirmed is None:
                    await warning_msg.edit(
                        content="`⌛` Confirmation timed out — quota change cancelled.",
                        embed=None,
                        view=None
                    )
                    return

                if not view.confirmed:
                    await warning_msg.edit(
                        content="`❌` Quota change cancelled.",
                        embed=None,
                        view=None
                    )
                    return

                # Confirmed — clear the buttons off the warning message and continue below.
                await warning_msg.edit(view=None)

            if action.value == "edit":
                async with QUOTA_LOCK:
                    new_extra_total = usage_db.set_extra_quota(mac_upper, value_gb)
                    new_effective_limit = db.get_effective_daily_limit(mac_upper)
                title = "`✏️` Extra Quota Edited"
                action_line = f"Set To:        {format_data_size(value_gb)}\n"
                log_verb = "edited (set)"

                await recheck_device_after_limit_change(bot, mac_upper)

            else:  # add
                new_extra_total, new_effective_limit = await add_extra_quota_covering_overage(
                    bot, mac_upper, value_gb
                )
                title = "`➕` Extra Quota Added"
                action_line = f"Added:         {format_data_size(value_gb)}\n"
                log_verb = "added"

            logger.info(
                f"User {interaction.user} {log_verb} extra quota for {mac_upper}: "
                f"{value_gb:.2f} GB (total extra today now {new_extra_total:.2f} GB)"
            )

            await interaction.followup.send(
                embed=discord.Embed(
                    title=title,
                    description=(
                        f"```\n"
                        f"Device:        {state.macs_list.get(mac_upper, mac_upper)}\n"
                        f"{action_line}"
                        f"Extra Today:   {format_data_size(new_extra_total)}\n"
                        f"New Effective: {format_data_size(new_effective_limit)}\n"
                        f"```"
                        "\n`✅` Applies for today only — resets automatically at midnight."
                    ),
                    color=0xf1c40f
                )
            )

        except Exception as e:
            logger.error(f"Error in quota command: {e}")
            await interaction.followup.send("`❌` Failed to update extra quota.")