import asyncio
import discord
from discord import app_commands
import db
from logger import logger
from state import state, ROUTER_LOCK
from utils.discord import safe_defer
from utils.validators import is_valid_mac
from utils.autocomplete import all_macs_autocomplete
from router.static_leases import set_static_hostname, is_valid_ip, is_valid_hostname
from router.devices import fetch_devlist
from services.onboarding import RenameModal

def setup(bot):

    @bot.tree.command(
        name="rename",
        description="Rename a device's hostname via router static lease"
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        mac="Device MAC address",
        name="New hostname",
        ip="Device IP (optional, will try to resolve)"
    )
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    async def rename(
        interaction: discord.Interaction,
        mac: str,
        name: str,
        ip: str = None
    ):
        if not await safe_defer(interaction, thinking=True):
            return
        try:
            mac_upper = mac.strip().upper()
            if not is_valid_mac(mac_upper):
                await interaction.followup.send("`❌` Invalid MAC address format.")
                return
            if not db.device_exists(mac_upper):
                await interaction.followup.send("`❌` Device not found in database.")
                return
            if not is_valid_hostname(name.strip()):
                await interaction.followup.send("`❌` Invalid hostname. Use 1-32 alphanumeric/hyphen characters, must start/end with alnum.")
                return
            name = name.strip()
            resolved_ip = None
            explicit_ip = ip.strip() if isinstance(ip, str) and ip.strip() else None
            if explicit_ip:
                if not is_valid_ip(explicit_ip):
                    await interaction.followup.send("`❌` Invalid IP address format.")
                    return
                resolved_ip = explicit_ip
            else:
                try:
                    async with ROUTER_LOCK:
                        dhcp_leases, _, _ = await asyncio.to_thread(fetch_devlist)
                    for lease in dhcp_leases:
                        try:
                            lease_mac = str(lease[2]).strip().upper()
                            lease_ip = str(lease[1]).strip() if lease[1] else ""
                        except Exception:
                            continue
                        if lease_mac == mac_upper and lease_ip and is_valid_ip(lease_ip):
                            resolved_ip = lease_ip
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Active DHCP lookup failed for {mac_upper}: {e}")
                if not resolved_ip:
                    try:
                        from router.static_leases import fetch_current_entries
                        async with ROUTER_LOCK:
                            entries, _ = await asyncio.to_thread(fetch_current_entries)
                        for ent in entries:
                            try:
                                ent_mac = str(ent.get("mac", "")).strip().upper()
                                ent_ip = str(ent.get("ip", "")).strip() if ent.get("ip") else ""
                            except Exception:
                                continue
                            if ent_mac == mac_upper and ent_ip and is_valid_ip(ent_ip):
                                resolved_ip = ent_ip
                                break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"Static DHCP lookup failed for {mac_upper}: {e}")
            if not resolved_ip:
                await interaction.followup.send(
                    f"`❌` Could not resolve IP for {mac_upper}.\n\n"
                    f"Please provide it explicitly:\n`/rename mac:{mac_upper} name:{name} ip:192.168.1.x`"
                )
                return

            success, err = await set_static_hostname(mac_upper, resolved_ip, name)

            if not success:
                logger.error(f"/rename failed for {mac_upper} -> {name} ({resolved_ip}): {err}")
                # Do not modify DB/cache
                embed = discord.Embed(
                    title="`❌` Rename Failed",
                    description=f"**Device:** `{state.macs_list.get(mac_upper, mac_upper)}`\n**MAC:** `{mac_upper}`\n**Error:** `{err}`\n\nRouter unreachable, auth failed, or unexpected response. Hostname unchanged.",
                    color=0xe74c3c
                )
                await interaction.followup.send(embed=embed)
                return

            try:
                db.update_hostname(mac_upper, name)
                state.macs_list[mac_upper] = name
            except Exception as e:
                logger.warning(f"Failed to sync DB/state hostname after rename for {mac_upper}: {e}")
            try:
                async with ROUTER_LOCK:
                    await asyncio.to_thread(fetch_devlist)
            except Exception as e:
                logger.warning(f"Post-rename discovery resync failed: {e}")

            embed = discord.Embed(
                title="`✅` Hostname Updated (Router)",
                description=f"**Device:** `{name}`\n**MAC:** `{mac_upper}`\n**IP:** `{resolved_ip}`\n\nRouter accepted the change. Discovery will sync DB/cache shortly.",
                color=0x2ecc71
            )
            await interaction.followup.send(embed=embed)
            logger.info(f"/rename success {mac_upper} -> {name} ({resolved_ip}) by {interaction.user}")

        except Exception as e:
            logger.error(f"Error in rename command: {e}")
            try:
                await interaction.followup.send("`❌` Failed to rename device.")
            except Exception:
                pass
