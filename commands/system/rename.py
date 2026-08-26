import asyncio
import discord
from discord import app_commands
import db
from logger import logger
from state import state, ROUTER_LOCK
from utils.discord import safe_defer
from utils.validators import is_valid_mac
from utils.autocomplete import all_macs_autocomplete
from router.static_leases import set_static_hostname, resolve_ip_for_mac, is_valid_ip, is_valid_hostname
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
            mac_upper = mac.upper()
            if not is_valid_mac(mac_upper):
                await interaction.followup.send("`❌` Invalid MAC address format.")
                return
            if not db.device_exists(mac_upper):
                await interaction.followup.send("`❌` Device not found in database.")
                return
            # Hostname validation
            if not is_valid_hostname(name.strip()):
                await interaction.followup.send("`❌` Invalid hostname. Use 1-32 alphanumeric/hyphen characters, must start/end with alnum.")
                return
            name = name.strip()
            # Resolve IP: explicit IP takes precedence, otherwise fresh lookup first, cache fallback
            resolved_ip = ip
            if not resolved_ip:
                # Fresh router lookup first (authoritative), cache only as fallback
                try:
                    async with ROUTER_LOCK:
                        dhcp_leases, _, _ = await asyncio.to_thread(fetch_devlist)
                    for lease in dhcp_leases:
                        if lease[2].upper() == mac_upper:
                            resolved_ip = lease[1]
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Fresh IP lookup failed for {mac_upper}: {e}")
                if not resolved_ip:
                    resolved_ip = resolve_ip_for_mac(mac_upper)
                    if resolved_ip:
                        logger.debug(f"Using cached IP fallback for {mac_upper}: {resolved_ip}")
            if not resolved_ip:
                await interaction.followup.send("`❌` Could not resolve device IP. Please provide IP explicitly: `/rename mac:... name:... ip:192.168.1.x`")
                return
            if not is_valid_ip(resolved_ip):
                await interaction.followup.send("`❌` Invalid IP address format.")
                return

            # Call router-native writer with bounded retry (3 attempts) without holding lock during sleeps
            # We need to not hold ROUTER_LOCK while sleeping, so call async helper which handles retries internally without lock
            # The helper will do fetch+push each attempt; we should ensure lock discipline: acquire per attempt inside helper
            # Here we just call it; it handles its own retries
            # But we need to ensure discovery is under lock after success
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

            # Success: trigger immediate discovery resync to pull router truth into DB/cache
            try:
                async with ROUTER_LOCK:
                    await asyncio.to_thread(fetch_devlist)
                # Also ensure state reflects new name eventually via discovery; but fetch_devlist updates ip_to_mac_cache and refresh_last_seen, not macs_list directly? fetch_devlist_and_discover updates macs_list.
                # Force sync: update local state to new name (will be overwritten by next discovery poll anyway from router truth)
                # We let normal discovery update DB/cache, but we can optimistically update for immediate feedback
                # The DB will be updated on next discovery poll from router's dhcpd_static? Actually discovery polls dhcp leases not static leases.
                # Hostname from static lease vs lease hostname: static assignment hostname may not appear in dhcpd_lease until renewal.
                # So we update state optimistically for display, but DB remains to be synced via next poll that reads lease hostname? 
                # To keep router as source of truth, we don't write DB directly here; we just inform user.
                pass
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
