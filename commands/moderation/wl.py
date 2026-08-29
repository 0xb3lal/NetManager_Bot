import asyncio
from typing import Optional

import discord
from discord import app_commands

import db
from logger import logger
from router.firewall import enable_lockdown
from state import ROUTER_LOCK, state
from utils.autocomplete import wl_macs_autocomplete
from utils.discord import safe_defer
from utils.validators import is_valid_mac


async def wl(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    mac: Optional[str],
):
    if not await safe_defer(interaction, thinking=True):
        return

    action_value = action.value

    if action_value == "list":
        if state.allowed_macs:
            msg = "\n".join(
                f"`{mac}`:**{state.macs_list.get(mac, 'Unknown')}**"
                for mac in state.allowed_macs
            )
            embed_color = discord.Color.blue()
        else:
            msg = "*Whitelist is currently empty.*"
            embed_color = discord.Color.light_grey()

        embed = discord.Embed(
            title="`📋` Whitelisted Devices", description=msg, color=embed_color
        )

        logger.info(f"Whitelist command: listed {len(state.allowed_macs)} device(s).")
        return await interaction.followup.send(embed=embed)

    if not mac:
        logger.warning(
            f"Whitelist command: '{action_value}' called without a MAC address."
        )
        return await interaction.followup.send(
            "`❌` You must provide a MAC address for this action."
        )

    mac = mac.upper()

    if not is_valid_mac(mac):
        logger.warning(f"Whitelist command: invalid MAC format '{mac}'.")
        return await interaction.followup.send("`❌` Invalid MAC Address format.")

    hostname = state.macs_list.get(mac, "Unknown")

    if action_value == "add":
        if mac in state.allowed_macs:
            logger.info(f"Whitelist command: add skipped, {mac} already whitelisted.")
            return await interaction.followup.send(
                f"`⚠️` Device `{hostname}` `({mac})` is already in the whitelist."
            )

        db.add_device(mac, hostname)
        state.macs_list[mac] = hostname

        async with ROUTER_LOCK:
            # Mutate shared whitelist state inside the same lock as the
            # firewall rebuild — enable_lockdown iterates these collections
            # in a worker thread.
            db.set_device_allowed(mac, True)
            state.allowed_macs.append(mac)
            # Whitelisting resolves onboarding explicitly; a whitelisted-but-still-
            db.set_onboarding_confirmed(mac)
            state.pending_macs.discard(mac)
            await asyncio.to_thread(
                enable_lockdown,
                force_lock=state.lockdown_state,
            )

        logger.info(f"Whitelist command: added {hostname} ({mac}) to whitelist.")
        await interaction.followup.send(f"`✅` Device `{hostname}` added to whitelist.")

    elif action_value == "remove":
        if mac in state.allowed_macs:
            async with ROUTER_LOCK:
                # Same lock discipline as add: mutate whitelist state and
                # rebuild the firewall atomically.
                if mac in state.allowed_macs:
                    state.allowed_macs.remove(mac)
                db.set_device_allowed(mac, False)
                await asyncio.to_thread(
                    enable_lockdown,
                    force_lock=state.lockdown_state,
                )

            logger.info(
                f"Whitelist command: removed {hostname} ({mac}) from whitelist."
            )
            await interaction.followup.send(
                f"`✅` Device `{hostname}` removed from whitelist."
            )
        else:
            logger.info(f"Whitelist command: remove skipped, {mac} not in whitelist.")
            await interaction.followup.send("`⚠️` Device is not in the whitelist.")


def setup(bot):
    @bot.tree.command(
        name="wl",
        description="Manage Allowed Devices (Whitelist)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        action="Choose whether to add, remove, or list devices",
        mac="The MAC address of the device (not needed for List)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
            app_commands.Choice(name="List", value="list"),
        ]
    )
    @app_commands.autocomplete(mac=wl_macs_autocomplete)
    async def wl_command(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        mac: Optional[str] = None,
    ):
        await wl(interaction, action, mac)
