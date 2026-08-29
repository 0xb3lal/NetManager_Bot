import asyncio

import discord

from logger import logger
from router.devices import fetch_devlist
from state import ROUTER_LOCK, state
from utils.discord import safe_defer
from utils.wireless import rssi_to_quality_pct


def setup(bot):

    @bot.tree.command(
        name="active",
        description="Show currently active devices and their signal strength",
    )
    async def active(interaction: discord.Interaction):

        if not await safe_defer(interaction, thinking=True):
            return

        logger.info(f"ACTION: /active | User: {interaction.user}")

        try:

            async with ROUTER_LOCK:

                dhcp_leases, wireless_devs, arp_list = await asyncio.to_thread(
                    fetch_devlist
                )

            active_signals = {dev[1].upper(): dev[2] for dev in wireless_devs}

            arp_active = {entry[1].upper() for entry in arp_list}

            devices_info = {
                lease[2].upper(): {"name": lease[0], "ip": lease[1]}
                for lease in dhcp_leases
            }

            combined_data = []

            for mac, info in devices_info.items():

                is_online = mac in active_signals and mac in arp_active

                rssi = active_signals.get(mac, None)

                is_banned = mac in state.banned_macs

                if is_banned:

                    status_icon = "⛔"
                    sig_str = "0%"

                elif is_online and rssi is not None:

                    status_icon = "🟢"

                    sig_str = f"{rssi_to_quality_pct(rssi)}%"

                else:

                    status_icon = "🔴"
                    sig_str = "0%"

                combined_data.append(
                    {
                        "name": state.macs_list.get(mac, info["name"]),
                        "signal": sig_str,
                        "icon": status_icon,
                        "online_sort": (1 if is_online and not is_banned else 0),
                    }
                )

            def get_signal_val(sig_text):

                try:
                    return int(sig_text.rstrip("%"))

                except (ValueError, AttributeError):
                    return 0

            combined_data.sort(
                key=lambda x: (x["online_sort"], get_signal_val(x["signal"])),
                reverse=True,
            )

            online = [d for d in combined_data if d["icon"] == "🟢"]

            offline = [d for d in combined_data if d["icon"] == "🔴"]

            banned = [d for d in combined_data if d["icon"] == "⛔"]

            if combined_data:

                def fmt(dev):

                    return (
                        f"{dev['icon']} "
                        f"`{dev['name'][:12].ljust(12)} "
                        f"| 📶{dev['signal'].rjust(4)}`"
                    )

                lines = []

                if online:
                    lines += [fmt(d) for d in online]

                if offline:

                    lines.append("─────────────────────")

                    lines += [fmt(d) for d in offline]

                if banned:

                    lines.append("─────────────────────")

                    lines += [fmt(d) for d in banned]

                embed = discord.Embed(
                    title=f"`📡` Active Devices ({len(arp_active)} Devices)",
                    description="\n".join(lines),
                    color=0x2ECC71,
                )

            else:

                embed = discord.Embed(
                    description="✨ No devices found.", color=0x95A5A6
                )

            await interaction.followup.send(embed=embed)

            logger.info(f"SUCCESS: /active | Sent device status to {interaction.user}")

        except Exception as e:

            logger.error(f"FAILURE in /active command: {e}")

            try:

                await interaction.followup.send(
                    "`❌` Error compiling active devices status."
                )

            except Exception:
                pass
