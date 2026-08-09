import discord
from discord import app_commands
import db
from logger import logger
from state import state
from utils.discord import safe_defer
from utils.traffic import format_data_size
from utils.validators import is_valid_mac
from utils.autocomplete import all_macs_autocomplete
from services.limits import (
    recheck_device_after_limit_change,
    recheck_default_limit_devices,
)

from services.radius import async_check_and_lock


def setup(bot):

    @bot.tree.command(
        name="limit",
        description="View or change the main balance threshold or daily per-device usage limits"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        scope="Which limit to change (defaults to the main balance threshold)",
        value="The new limit value",
        unit="Unit for the value (defaults to GB)",
        mac="Set a custom daily limit for one specific device"
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="Main Balance", value="main"),
        app_commands.Choice(name="Daily Default", value="daily_default"),
        app_commands.Choice(name="List", value="list"),
    ])
    @app_commands.choices(unit=[
        app_commands.Choice(name="GB", value="GB"),
        app_commands.Choice(name="MB", value="MB"),
    ])
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def set_limit(
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] = None,
        value: float = None,
        unit: app_commands.Choice[str] = None,
        mac: str = None
    ):

        if not await safe_defer(interaction, thinking=True):
            return

        try:
            scope_value = scope.value if scope else "main"
            unit_value = unit.value if unit else "GB"

            if scope_value == "list":

                default_limit = db.get_daily_default_limit()
                overrides = db.get_all_device_daily_limits()

                lines = [
                    f"{'Main Threshold:'.ljust(18)} {state.threshold} GB",
                    f"{'Daily Default:'.ljust(18)} {format_data_size(default_limit)}",
                ]

                if overrides:
                    lines.append("")
                    lines.append("Custom Daily Limits:")

                    for m, gb in overrides.items():
                        device_name = state.macs_list.get(m, m)
                        lines.append(
                            f"  {device_name[:14].ljust(14)} : {format_data_size(gb)}"
                        )

                embed = discord.Embed(
                    title="`⚙️` Current Limit Configuration",
                    description="```\n" + "\n".join(lines) + "\n```",
                    color=0xf1c40f
                )

                await interaction.followup.send(embed=embed)
                return


            if value is None:
                await interaction.followup.send(
                    "`⚠️` Please provide a value."
                )
                return


            value_gb = value / 1024 if unit_value == "MB" else value


            if mac:

                mac_upper = mac.upper()

                if not is_valid_mac(mac_upper):
                    await interaction.followup.send(
                        "`❌` Invalid MAC Address format."
                    )
                    return


                db.set_device_daily_limit(
                    mac_upper,
                    value_gb
                )

                logger.info(
                    f"User {interaction.user} set custom daily limit "
                    f"for {mac_upper} to {value_gb} GB"
                )


                await interaction.followup.send(
                    embed=discord.Embed(
                        title="`⚙️` Daily Limit Update",
                        description=(
                            f"```\n"
                            f"Device:     {state.macs_list.get(mac_upper, mac_upper)}\n"
                            f"New Limit:  {format_data_size(value_gb)}\n"
                            f"```"
                            "\n`✅` Settings updated."
                        ),
                        color=0xf1c40f
                    )
                )


                await recheck_device_after_limit_change(
                    bot,
                    mac_upper
                )

                return


            if scope_value == "daily_default":

                old_limit = db.get_daily_default_limit()

                db.set_daily_default_limit(
                    value_gb
                )

                logger.info(
                    f"User {interaction.user} updated daily default limit to {value_gb}"
                )


                await interaction.followup.send(
                    embed=discord.Embed(
                        title="`⚙️` Daily Default Limit Update",
                        description=(
                            f"```\n"
                            f"Old Default: {format_data_size(old_limit)}\n"
                            f"New Default: {format_data_size(value_gb)}\n"
                            f"```"
                        ),
                        color=0xf1c40f
                    )
                )


                await recheck_default_limit_devices(
                    bot
                )

                return


            old_limit = state.threshold

            state.threshold = value_gb

            db.set_threshold(
                value_gb
            )


            logger.info(
                f"User {interaction.user} updated main threshold to {value_gb}"
            )


            await interaction.followup.send(
                embed=discord.Embed(
                    title="`⚙️` System Configuration Update",
                    description=(
                        f"```\n"
                        f"Old Limit: {old_limit} GB\n"
                        f"New Limit: {state.threshold} GB\n"
                        f"```"
                    ),
                    color=0xf1c40f
                )
            )


            await async_check_and_lock(
                bot
            )


        except Exception as e:

            logger.error(
                f"Error in limit command: {e}"
            )

            await interaction.followup.send(
                "`❌` Failed to update configuration."
            )