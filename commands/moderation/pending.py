import discord
from discord import app_commands

import db
from logger import logger
from state import state
from utils.autocomplete import pending_macs_autocomplete
from utils.discord import safe_defer
from utils.embeds import error_embed, success_embed, warning_embed
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="pending",
        description="List devices awaiting onboarding, or re-run the onboarding questions for one",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        action="List pending devices, or review one (re-run its onboarding questions)",
        mac="The pending device to review (only used with Review)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Review", value="review"),
        ]
    )
    @app_commands.autocomplete(mac=pending_macs_autocomplete)
    async def pending(
        interaction: discord.Interaction,
        action: app_commands.Choice[str] = None,
        mac: str = None,
    ):
        if not await safe_defer(interaction, thinking=True):
            return

        action_value = action.value if action else "list"

        if action_value == "review":
            await _handle_review(interaction, mac)
            return

        logger.info(f"ACTION: /pending list | User: {interaction.user}")

        try:
            pending_devices = db.get_pending_devices()

            if pending_devices:
                msg = "\n".join(
                    f"`{mac}` : **{hostname}**"
                    for mac, hostname in pending_devices.items()
                )
                embed_color = discord.Color.blue()
            else:
                msg = "*No devices are pending onboarding.*"
                embed_color = discord.Color.light_grey()

            embed = discord.Embed(
                title=f"`📋` Devices Pending Onboarding ({len(pending_devices)})",
                description=msg,
                color=embed_color,
            )

            embed.set_footer(
                text="Use /pending review to re-run a device's onboarding questions."
            )

            await interaction.followup.send(embed=embed)

            logger.info(
                f"SUCCESS: /pending | Returned {len(pending_devices)} pending device(s)"
            )

        except Exception as e:
            logger.error(f"FAILURE in /pending command: {e}")
            await interaction.followup.send(
                embed=error_embed("`❌` Failed to retrieve pending devices.")
            )


async def _handle_review(interaction: discord.Interaction, mac: str | None):
    # Local import — breaks cycle services ↔ views (same pattern as macs.py).
    from services.onboarding import _sessions, start_onboarding

    if not mac:
        await interaction.followup.send(
            embed=error_embed(
                "`❌` A MAC address is required to review a pending device."
            )
        )
        return

    mac_upper = mac.strip().upper()

    if not is_valid_mac(mac_upper):
        await interaction.followup.send(
            embed=error_embed("`❌` Invalid MAC Address format")
        )
        return

    if mac_upper not in state.pending_macs:
        await interaction.followup.send(
            embed=warning_embed(
                f"`⚠️` `{mac_upper}` is not pending onboarding — nothing to review.",
                "Use /pending to list devices awaiting onboarding.",
            )
        )
        return

    if mac_upper in _sessions:
        await interaction.followup.send(
            embed=warning_embed(
                f"`⚠️` An onboarding session is already open for `{mac_upper}`."
            )
        )
        return

    hostname = state.macs_list.get(mac_upper, "Unknown")

    await start_onboarding(interaction.client, mac_upper, hostname)

    if mac_upper not in _sessions:
        await interaction.followup.send(
            embed=error_embed(
                f"`❌` Could not start onboarding for `{mac_upper}`",
                "Admin channel unavailable.",
            )
        )
        return

    logger.info(
        f"ACTION: /pending review | User: {interaction.user} | {mac_upper}"
    )
    await interaction.followup.send(
        embed=success_embed(
            f"`✅` Onboarding questions re-posted for `{hostname}` (`{mac_upper}`)."
        )
    )
