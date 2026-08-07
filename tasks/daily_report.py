from datetime import datetime, time
from zoneinfo import ZoneInfo

import asyncio
import discord
from discord.ext import tasks

from logger import logger
from state import ROUTER_LOCK, state
from router.devices import fetch_devlist
from router.traffic import get_speed_history, get_daily_history, get_today_combined
from utils.traffic import bytes_to_mb
from config import CHANNEL_ID

REPORT_TIME = time(hour=23, minute=55, tzinfo=ZoneInfo("Africa/Cairo"))


def setup_daily_network_report_task(bot):
    """Create and configure the daily network report task."""

    @tasks.loop(time=REPORT_TIME)
    async def daily_network_report():
        """Generate daily network usage report at 23:55."""
        try:
            async with ROUTER_LOCK:
                speed_history = await asyncio.to_thread(get_speed_history)
                daily_history = await asyncio.to_thread(get_daily_history)
                dhcp_leases, _, _ = await asyncio.to_thread(fetch_devlist)

            devices_info       = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
            combined_usage     = get_today_combined(speed_history, daily_history)
            combined_data      = []
            total_day_usage_mb = 0.0

            for ip, total_bytes in combined_usage.items():
                usage_mb = bytes_to_mb(total_bytes)
                if usage_mb < 0.1:
                    continue

                target_mac = next((mac for mac, info in devices_info.items() if info["ip"] == ip), None)
                if not target_mac:
                    continue

                total_day_usage_mb += usage_mb

                raw_name   = devices_info[target_mac]["name"]
                final_name = state.macs_list.get(target_mac, raw_name)
                combined_data.append({"name": final_name, "usage": usage_mb})

            combined_data.sort(key=lambda x: x["usage"], reverse=True)
            if not combined_data:
                return

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                return

            now   = datetime.now(ZoneInfo("Africa/Cairo"))
            lines = []
            for dev in combined_data[:15]:
                u_str = f"{dev['usage']/1024:.1f}GB" if dev["usage"] >= 1024 else f"{int(dev['usage'])}MB"
                lines.append(f"`{dev['name'][:15].ljust(15)} | 📊{u_str.rjust(8)}`")

            embed = discord.Embed(
                title=f"📅 Daily Usage Report ({now.strftime('%Y-%m-%d')})",
                description="\n".join(lines),
                color=0x3498db,
                timestamp=now
            )
            embed.set_footer(text=f"Total Network Load: {total_day_usage_mb/1024:.2f} GB")
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in daily_network_report: {e}")

    @daily_network_report.before_loop
    async def before_daily_report():
        await bot.wait_until_ready()

    return daily_network_report