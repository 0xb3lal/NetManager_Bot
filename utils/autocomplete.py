import discord
from discord import app_commands

from logger import logger
from state import state


async def mac_autocomplete(interaction: discord.Interaction, current: str):
    try:
        macs_list_snapshot = dict(state.macs_list)
        banned_macs_snapshot = set(state.banned_macs)
        current_lower = current.lower()

        choices = [
            app_commands.Choice(name=hostname, value=mac)
            for mac, hostname in macs_list_snapshot.items()
            if (current_lower in hostname.lower() or current_lower in mac.lower())
            and mac not in banned_macs_snapshot
        ]
        return choices[:25]

    except Exception as e:
        logger.error(f"Error in mac_autocomplete: {e}")
        return []


async def banned_macs_autocomplete(interaction: discord.Interaction, current: str):
    try:
        macs_list_snapshot = dict(state.macs_list)
        banned_macs_snapshot = set(state.banned_macs)
        current_lower = current.lower()

        choices = [
            app_commands.Choice(
                name=macs_list_snapshot.get(mac, "Unknown Device"), value=mac
            )
            for mac in banned_macs_snapshot
            if current_lower in mac.lower()
            or current_lower in macs_list_snapshot.get(mac, "").lower()
        ]
        return choices[:25]

    except Exception as e:
        logger.error(f"Error in banned_macs_autocomplete: {e}")
        return []


async def pending_macs_autocomplete(interaction: discord.Interaction, current: str):
    try:
        macs_list_snapshot = dict(state.macs_list)
        pending_macs_snapshot = set(state.pending_macs)
        current_lower = current.lower()

        choices = [
            app_commands.Choice(
                name=macs_list_snapshot.get(mac, "Unknown Device"), value=mac
            )
            for mac in pending_macs_snapshot
            if current_lower in mac.lower()
            or current_lower in macs_list_snapshot.get(mac, "").lower()
        ]
        return choices[:25]

    except Exception as e:
        logger.error(f"Error in pending_macs_autocomplete: {e}")
        return []


async def all_macs_autocomplete(interaction: discord.Interaction, current: str):
    try:
        macs_list_snapshot = dict(state.macs_list)
        current_lower = current.lower()

        choices = [
            app_commands.Choice(name=hostname, value=mac)
            for mac, hostname in macs_list_snapshot.items()
            if current_lower in hostname.lower() or current_lower in mac.lower()
        ]
        return choices[:25]

    except Exception as e:
        logger.error(f"Error in all_macs_autocomplete: {e}")
        return []


async def wl_macs_autocomplete(interaction: discord.Interaction, current: str):
    try:
        action_value = getattr(interaction.namespace, "action", None)

        macs_list_snapshot = dict(state.macs_list)
        allowed_macs_snapshot = list(state.allowed_macs)

        if action_value == "remove":
            candidates = {
                mac: macs_list_snapshot.get(mac, "Unknown Device")
                for mac in allowed_macs_snapshot
            }
        elif action_value == "add":
            candidates = {
                mac: hostname
                for mac, hostname in macs_list_snapshot.items()
                if mac not in allowed_macs_snapshot
            }
        else:
            candidates = macs_list_snapshot

        current_lower = current.lower()

        choices = [
            app_commands.Choice(name=hostname, value=mac)
            for mac, hostname in candidates.items()
            if current_lower in hostname.lower() or current_lower in mac.lower()
        ]
        return choices[:25]

    except Exception as e:
        logger.error(f"Error in wl_macs_autocomplete: {e}")
        return []
