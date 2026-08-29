import discord
from discord import app_commands

import db
from logger import logger
from services.limits import (
    recheck_default_limit_devices,
    recheck_device_after_limit_change,
)
from services.radius import async_check_and_lock
from state import state
from utils.autocomplete import all_macs_autocomplete
from utils.discord import safe_defer
from utils.traffic import format_data_size
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="limit",
        description="View or change the main balance threshold or daily per-device usage limits",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        scope="Which limit to change (defaults to the main balance threshold)",
        value="The new limit value",
        unit="Unit for the value (defaults to GB)",
        mac="Set a custom daily limit for one specific device",
        mode="Persistence of custom limit: persistent (indefinite) or today_only (expires next Cairo day)",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Main Balance", value="main"),
            app_commands.Choice(name="Daily Default", value="daily_default"),
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Reset Custom Limits", value="reset"),
        ]
    )
    @app_commands.choices(
        unit=[
            app_commands.Choice(name="GB", value="GB"),
            app_commands.Choice(name="MB", value="MB"),
        ]
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Persistent", value="persistent"),
            app_commands.Choice(name="Today only", value="today_only"),
        ]
    )
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def set_limit(
        interaction: discord.Interaction,
        scope: app_commands.Choice[str] = None,
        value: float = None,
        unit: app_commands.Choice[str] = None,
        mac: str = None,
        mode: app_commands.Choice[str] = None,
    ):

        if not await safe_defer(interaction, thinking=True):
            return

        try:
            scope_value = scope.value if scope else "main"
            unit_value = unit.value if unit else "GB"

            if scope_value == "list":

                default_limit = db.get_daily_default_limit()
                overrides = db.get_all_device_daily_limits_with_mode()

                lines = [
                    f"{'Main Threshold:'.ljust(18)} {state.threshold} GB",
                    f"{'Daily Default:'.ljust(18)} {format_data_size(default_limit)}",
                ]

                if overrides:
                    lines.append("")
                    lines.append("Custom Daily Limits:")

                    for m, (gb, mode, expires_on) in overrides.items():
                        device_name = state.macs_list.get(m, m)
                        mode_label = (
                            "persistent"
                            if mode == "persistent"
                            else f"today_only (expires {expires_on})"
                        )
                        lines.append(
                            f"  {device_name[:14].ljust(14)} : {format_data_size(gb)} [{mode_label}]"
                        )

                embed = discord.Embed(
                    title="`⚙️` Current Limit Configuration",
                    description="```\n" + "\n".join(lines) + "\n```",
                    color=0xF1C40F,
                )

                await interaction.followup.send(embed=embed)
                return

            if scope_value == "reset":

                cleared_count = db.clear_all_device_daily_limits()

                if not cleared_count:
                    await interaction.followup.send(
                        "`ℹ️` No custom daily limits to reset."
                    )
                    return

                logger.info(
                    f"User {interaction.user} reset {cleared_count} custom daily limit(s) to default."
                )

                await interaction.followup.send(
                    embed=discord.Embed(
                        title="`⚙️` Custom Limits Reset",
                        description=(
                            f"`✅` Cleared {cleared_count} custom daily limit(s). "
                            f"All devices now use the default ({format_data_size(db.get_daily_default_limit())})."
                        ),
                        color=0xF1C40F,
                    )
                )

                await recheck_default_limit_devices(bot)
                return

            if value is None:
                await interaction.followup.send("`⚠️` Please provide a value.")
                return

            value_gb = value / 1024 if unit_value == "MB" else value

            if value_gb <= 0:
                await interaction.followup.send("`❌` Value must be greater than zero.")
                return

            if mac:

                mac_upper = mac.upper()

                if not is_valid_mac(mac_upper):
                    await interaction.followup.send("`❌` Invalid MAC Address format.")
                    return

                mode_value = mode.value if mode else "persistent"
                if mode_value not in ("persistent", "today_only"):
                    mode_value = "persistent"

                db.set_device_daily_limit(mac_upper, value_gb, mode_value)

                logger.info(
                    f"User {interaction.user} set custom daily limit "
                    f"for {mac_upper} to {value_gb} GB mode={mode_value}"
                )

                mode_desc = (
                    "persistent (indefinite)"
                    if mode_value == "persistent"
                    else "today_only (expires next Cairo day)"
                )

                await interaction.followup.send(
                    embed=discord.Embed(
                        title="`⚙️` Daily Limit Update",
                        description=(
                            f"```\n"
                            f"Device:     {state.macs_list.get(mac_upper, mac_upper)}\n"
                            f"New Limit:  {format_data_size(value_gb)}\n"
                            f"Mode:       {mode_desc}\n"
                            f"```"
                            "\n`✅` Settings updated."
                        ),
                        color=0xF1C40F,
                    )
                )

                await recheck_device_after_limit_change(bot, mac_upper)

                return

            if scope_value == "daily_default":

                old_limit = db.get_daily_default_limit()

                db.set_daily_default_limit(value_gb)

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
                        color=0xF1C40F,
                    )
                )

                await recheck_default_limit_devices(bot)

                return

            old_limit = state.threshold

            state.threshold = value_gb

            db.set_threshold(value_gb)

            logger.info(f"User {interaction.user} updated main threshold to {value_gb}")

            await interaction.followup.send(
                embed=discord.Embed(
                    title="`⚙️` System Configuration Update",
                    description=(
                        f"```\n"
                        f"Old Limit: {format_data_size(old_limit)}\n"
                        f"New Limit: {format_data_size(state.threshold)}\n"
                        f"```"
                    ),
                    color=0xF1C40F,
                )
            )

            await async_check_and_lock(bot)

        except Exception as e:

            logger.error(f"Error in limit command: {e}")

            await interaction.followup.send("`❌` Failed to update configuration.")
