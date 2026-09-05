import asyncio

import discord
from discord import app_commands

import db
from logger import logger
from router.devices import fetch_devlist
from router.traffic import get_daily_history, get_speed_history, get_today_combined
from state import ROUTER_LOCK, state
from utils.discord import safe_defer
from utils.embeds import error_embed
from utils.traffic import bytes_to_mb, format_data_size


def setup(bot):

    @bot.tree.command(
        name="netstat", description="Show device network usage or daily limit status"
    )
    @app_commands.describe(
        view="Choose between raw usage (default) or daily limit comparison"
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Usage", value="usage"),
            app_commands.Choice(name="Daily Limit Status", value="daily_limit"),
        ]
    )
    async def netstat(
        interaction: discord.Interaction, view: app_commands.Choice[str] = None
    ):

        if not await safe_defer(interaction, thinking=True):
            return

        view_value = view.value if view else "usage"

        logger.info(
            f"Network usage status requested by {interaction.user} "
            f"(view={view_value})"
        )

        try:

            async with ROUTER_LOCK:

                speed_history = await asyncio.to_thread(get_speed_history)

                daily_history = await asyncio.to_thread(get_daily_history)

                dhcp_leases, _, _ = await asyncio.to_thread(fetch_devlist)

            devices_info = {
                lease[2].upper(): {"name": lease[0], "ip": lease[1]}
                for lease in dhcp_leases
            }

            combined_usage = get_today_combined(speed_history, daily_history)

            if view_value == "daily_limit":

                combined_data = []

                for mac, info in devices_info.items():

                    total_bytes = combined_usage.get(info["ip"], 0)

                    usage_gb = bytes_to_mb(total_bytes) / 1024

                    effective_limit = db.get_effective_daily_limit(mac)

                    combined_data.append(
                        {
                            "name": state.macs_list.get(mac, info["name"]),
                            "usage_gb": usage_gb,
                            "limit_gb": effective_limit,
                            "over": usage_gb >= effective_limit,
                        }
                    )

                combined_data.sort(
                    key=lambda x: (
                        x["usage_gb"] / x["limit_gb"] if x["limit_gb"] else 0
                    ),
                    reverse=True,
                )

                if combined_data:

                    lines = []

                    for dev in combined_data[:15]:

                        icon = "🔴" if dev["over"] else "🟢"

                        lines.append(
                            f"{icon} "
                            f"`{dev['name'][:12].ljust(12)} | "
                            f"📊{format_data_size(dev['usage_gb'])}/"
                            f"{format_data_size(dev['limit_gb'])}`"
                        )

                    embed = discord.Embed(
                        title=f"`📡` Daily Limit Status ({len(combined_data)} Devices)",
                        description="\n".join(lines),
                        color=0x2ECC71,
                    )

                else:

                    embed = discord.Embed(
                        description="✨ No devices found in history.", color=0x95A5A6
                    )

                await interaction.followup.send(embed=embed)

                return

            combined_data = []
            total_traffic_mb = 0.0

            for ip, total_bytes in combined_usage.items():

                usage_mb = bytes_to_mb(total_bytes)

                if usage_mb < 0.1:
                    continue

                target_mac = next(
                    (mac for mac, info in devices_info.items() if info["ip"] == ip),
                    None,
                )

                if not target_mac:
                    continue

                total_traffic_mb += usage_mb

                raw_name = devices_info[target_mac]["name"]

                combined_data.append(
                    {
                        "name": state.macs_list.get(target_mac, raw_name),
                        "usage": usage_mb,
                    }
                )

            combined_data.sort(key=lambda x: x["usage"], reverse=True)

            if combined_data:

                lines = []

                for dev in combined_data[:15]:

                    u_str = (
                        f"{dev['usage']/1024:.1f}GB"
                        if dev["usage"] >= 1024
                        else f"{int(dev['usage'])}MB"
                    )

                    lines.append(
                        f"`📱` "
                        f"`{dev['name'][:12].ljust(12)} | "
                        f"📊{u_str.rjust(6)}`"
                    )

                embed = discord.Embed(
                    title=f"`📡` Network Usage ({len(combined_data)} Devices)",
                    description="\n".join(lines),
                    color=0x2ECC71,
                )

                embed.set_footer(
                    text=f"Total Network Load: {total_traffic_mb/1024:.2f} GB"
                )

            else:

                embed = discord.Embed(
                    description="✨ No devices found in history.", color=0x95A5A6
                )

            await interaction.followup.send(embed=embed)

            logger.info(f"SUCCESS: /netstat | User: {interaction.user}")

        except Exception as e:

            logger.error(f"Error in netstat: {e}")

            try:
                await interaction.followup.send(
                    embed=error_embed("`❌` Error compiling network status.")
                )

            except Exception:
                pass
