import asyncio

import discord
from discord import app_commands

import db
import usage_db
from logger import logger
from services.traffic import get_today_usage_by_mac
from state import ROUTER_LOCK, state
from utils.autocomplete import all_macs_autocomplete
from utils.discord import safe_defer
from utils.traffic import format_data_size
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="device",
        description="Show full status, usage, and quota details for one device",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(mac="The device to look up")
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def device_info(interaction: discord.Interaction, mac: str):
        if not await safe_defer(interaction, thinking=True):
            return

        try:
            mac_upper = mac.upper()

            if not is_valid_mac(mac_upper):
                await interaction.followup.send("`❌` Invalid MAC Address format.")
                return

            async with ROUTER_LOCK:
                usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)

            usage_gb = usage_by_mac.get(mac_upper, 0)

            base_limit, mode, expires_on = db.get_device_daily_limit_with_mode(
                mac_upper
            )
            has_custom_limit = base_limit is not None
            if base_limit is None:
                base_limit = db.get_daily_default_limit()
                mode = None
                expires_on = None

            extra_quota = usage_db.get_extra_quota(mac_upper)
            effective_limit = base_limit + extra_quota
            remaining = max(effective_limit - usage_gb, 0)
            exempt = db.is_exempt_from_daily_limit(mac_upper)

            device_name = state.macs_list.get(mac_upper, mac_upper)
            is_banned = mac_upper in state.banned_macs
            ban_reason = db.get_ban_reason(mac_upper) if is_banned else None
            is_whitelisted = mac_upper in state.allowed_macs

            if is_banned:
                status = f"🚫 Blocked ({ban_reason})" if ban_reason else "🚫 Blocked"
            elif usage_gb >= effective_limit:
                status = "⚠️ Over Limit"
            else:
                status = "✅ Active"

            mode_label = ""
            if has_custom_limit:
                if mode == "today_only":
                    mode_label = f" [today_only expires {expires_on}]"
                else:
                    mode_label = " [persistent]"
            lines = [
                f"{'Device:'.ljust(15)} {device_name}",
                f"{'MAC:'.ljust(15)} {mac_upper}",
                f"{'Status:'.ljust(15)} {status}",
                f"{'Whitelisted:'.ljust(15)} {'Yes' if is_whitelisted else 'No'}",
                "",
                f"{'Used Today:'.ljust(15)} {format_data_size(usage_gb)}",
                f"{'Base Limit:'.ljust(15)} {format_data_size(base_limit)}"
                + (" (custom" + mode_label + ")" if has_custom_limit else " (default)")
                + (" (exempt)" if exempt else ""),
            ]

            if extra_quota > 0:
                lines.append(
                    f"{'Extra Quota:'.ljust(15)} +{format_data_size(extra_quota)}"
                )

            lines.append(
                f"{'Effective Limit:'.ljust(15)} {format_data_size(effective_limit)}"
            )
            lines.append(f"{'Remaining:'.ljust(15)} {format_data_size(remaining)}")

            embed = discord.Embed(
                title="`📋` Device Overview",
                description="```\n" + "\n".join(lines) + "\n```",
                color=0xF1C40F,
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in device command: {e}")
            await interaction.followup.send("`❌` Failed to fetch device info.")
