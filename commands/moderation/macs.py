import asyncio

import discord
from discord import app_commands

import db
from logger import logger
from state import ROUTER_LOCK, state
from utils.autocomplete import all_macs_autocomplete
from utils.validators import is_valid_mac


def setup(bot):

    @bot.tree.command(
        name="macs", description="List or manage known devices (list/edit/remove)"
    )
    @app_commands.describe(
        action="Action to perform",
        mac="Target MAC (for edit old / remove / list filter)",
        new_mac="New MAC (for edit only)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Edit", value="edit"),
            app_commands.Choice(name="Remove", value="remove"),
        ]
    )
    @app_commands.autocomplete(mac=all_macs_autocomplete)
    @app_commands.autocomplete(new_mac=all_macs_autocomplete)
    async def macs(
        interaction: discord.Interaction,
        action: app_commands.Choice[str] = None,
        mac: str = None,
        new_mac: str = None,
    ):
        action_value = action.value if action else "list"

        if action_value in ("edit", "remove"):
            user = interaction.user
            if (
                isinstance(user, discord.Member)
                and not user.guild_permissions.administrator
            ):
                await interaction.response.send_message(
                    "`❌` Only administrators can manage devices.", ephemeral=True
                )
                return
            try:
                await interaction.response.defer(thinking=True)
            except Exception as e:
                logger.error(f"Failed to defer /macs {action_value}: {e}")
                return
            if action_value == "edit":
                await _handle_edit(interaction, mac, new_mac)
                return
            elif action_value == "remove":
                await _handle_remove(interaction, mac)
                return

        logger.info(f"ACTION: /macs list | User: {interaction.user}")
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer /macs: {e}")
            return
        try:
            if state.macs_list:
                msg = "\n".join(
                    f"`{mac}` : **{hostname}**"
                    for mac, hostname in state.macs_list.items()
                )
                embed_color = discord.Color.blue()
            else:
                msg = "*No MAC addresses found.*"
                embed_color = discord.Color.light_grey()
            embed = discord.Embed(
                title="`📋` Known Devices", description=msg, color=embed_color
            )
            await interaction.followup.send(embed=embed)
            logger.info(
                f"SUCCESS: /macs list | Returned {len(state.macs_list)} devices"
            )
        except Exception as e:
            logger.error(f"FAILURE in /macs list: {e}")
            await interaction.followup.send("`❌` Failed to retrieve the MACs list.")


async def _handle_edit(interaction: discord.Interaction, mac: str, new_mac: str):
    logger.info(f"ACTION: /macs edit | User: {interaction.user} | {mac} -> {new_mac}")
    if not mac or not new_mac:
        await interaction.followup.send(
            "`❌` Both `mac` (old) and `new_mac` are required for edit."
        )
        return
    old = mac.strip().upper()
    new = new_mac.strip().upper()
    if not is_valid_mac(old):
        await interaction.followup.send(f"`❌` Invalid old MAC: `{mac}`")
        return
    if not is_valid_mac(new):
        await interaction.followup.send(f"`❌` Invalid new MAC: `{new_mac}`")
        return
    if old == new:
        await interaction.followup.send(
            "`ℹ️` Old and new MAC are identical — no change."
        )
        return
    if not db.device_exists(old):
        await interaction.followup.send(f"`❌` Device not found: `{old}`")
        return
    if db.device_exists(new):
        await interaction.followup.send(
            f"`❌` New MAC already exists: `{new}` — choose a different MAC or remove the existing device first."
        )
        return
    if (
        new in state.macs_list
        or new in state.banned_macs
        or new in state.allowed_macs
        or new in state.pending_macs
    ):
        await interaction.followup.send(
            f"`❌` New MAC already exists in state: `{new}`"
        )
        return
    try:
        from services.onboarding import _sessions

        if new in _sessions:
            await interaction.followup.send(
                f"`❌` New MAC already has an active onboarding session: `{new}`"
            )
            return
    except Exception:
        pass

    old_static_entry = None
    new_static_exists = False
    try:
        from router.static_leases import fetch_current_entries

        async with ROUTER_LOCK:
            entries, _ = await asyncio.to_thread(fetch_current_entries)
        for ent in entries:
            if ent["mac"].upper() == old:
                old_static_entry = ent
            if ent["mac"].upper() == new:
                new_static_exists = True
        if new_static_exists:
            await interaction.followup.send(
                f"`❌` New MAC already has a static DHCP entry: `{new}`"
            )
            return
    except Exception as e:
        logger.warning(f"Static DHCP read failed for edit {old}->{new}: {e}")
        old_static_entry = None

    old_hostname = state.macs_list.get(old, db.get_hostname(old))
    was_allowed = old in state.allowed_macs
    was_banned = old in state.banned_macs
    was_pending = old in state.pending_macs
    ip_cache_snapshot = dict(state.ip_to_mac_cache)
    sessions_snapshot = None
    try:
        from services.onboarding import _sessions

        sessions_snapshot = dict(_sessions)
    except Exception:
        pass

    db_success = db.migrate_device_mac(old, new)
    if not db_success:
        await interaction.followup.send(
            f"`❌` Database migration failed for `{old}` -> `{new}` (conflict or DB error). No changes made."
        )
        return

    try:
        if old in state.macs_list:
            state.macs_list[new] = state.macs_list.pop(old)
        else:
            state.macs_list[new] = old_hostname
        if was_allowed:
            try:
                idx = state.allowed_macs.index(old)
                state.allowed_macs[idx] = new
            except ValueError:
                if new not in state.allowed_macs:
                    state.allowed_macs.append(new)
        if was_banned:
            state.banned_macs.discard(old)
            state.banned_macs.add(new)
        if was_pending:
            state.pending_macs.discard(old)
            state.pending_macs.add(new)
        for ip, cached in list(state.ip_to_mac_cache.items()):
            if cached.upper() == old:
                state.ip_to_mac_cache[ip] = new
        try:
            from services.onboarding import _sessions

            if old in _sessions:
                sess = _sessions.pop(old)
                sess.mac = new
                _sessions[new] = sess
        except Exception as e:
            logger.warning(f"Failed to migrate onboarding session {old}->{new}: {e}")
    except Exception as e:
        logger.error(f"State sync failed after DB migrate {old}->{new}: {e}")
        try:
            db.migrate_device_mac(new, old)
        except Exception as re:
            logger.error(f"Rollback DB failed {new}->{old}: {re}")
        state.macs_list.pop(new, None)
        if old_hostname:
            state.macs_list[old] = old_hostname
        if was_allowed and new in state.allowed_macs:
            try:
                state.allowed_macs.remove(new)
                if old not in state.allowed_macs:
                    state.allowed_macs.append(old)
            except Exception:
                pass
        if was_banned:
            state.banned_macs.discard(new)
            state.banned_macs.add(old)
        if was_pending:
            state.pending_macs.discard(new)
            state.pending_macs.add(old)
        state.ip_to_mac_cache.clear()
        state.ip_to_mac_cache.update(ip_cache_snapshot)
        if sessions_snapshot is not None:
            try:
                from services.onboarding import _sessions

                _sessions.clear()
                _sessions.update(sessions_snapshot)
            except Exception:
                pass
        await interaction.followup.send(
            f"`❌` In-memory state sync failed, rolled back. Error: {e}"
        )
        return

    router_success = True
    router_err = None
    if old_static_entry:
        try:
            from router.static_leases import (
                _push_dhcpd_static,
                _serialize_entry,
                fetch_current_entries,
            )

            async with ROUTER_LOCK:
                entries, _ = await asyncio.to_thread(fetch_current_entries)
                new_parts = []
                found_old = False
                for ent in entries:
                    if ent["mac"].upper() == old:
                        new_parts.append(
                            _serialize_entry(
                                new, ent["ip"], ent["hostname"], ent["flag"]
                            )
                        )
                        found_old = True
                    elif ent["mac"].upper() == new:
                        continue
                    else:
                        new_parts.append(
                            _serialize_entry(
                                ent["mac"], ent["ip"], ent["hostname"], ent["flag"]
                            )
                        )
                if not found_old:
                    new_parts.append(
                        _serialize_entry(
                            new,
                            old_static_entry["ip"],
                            old_static_entry["hostname"],
                            old_static_entry["flag"],
                        )
                    )
                new_raw = ">".join(new_parts)
                ok, err = await asyncio.to_thread(_push_dhcpd_static, new_raw)
                if not ok:
                    router_success = False
                    router_err = err
        except Exception as e:
            router_success = False
            router_err = str(e)

    if not router_success:
        logger.error(
            f"Router static DHCP migration failed {old}->{new}: {router_err}, rolling back DB/state"
        )
        try:
            db.migrate_device_mac(new, old)
        except Exception as re:
            logger.error(f"Rollback DB failed {new}->{old}: {re}")
        # Rollback state
        state.macs_list.pop(new, None)
        state.macs_list[old] = old_hostname
        if was_allowed:
            try:
                if new in state.allowed_macs:
                    state.allowed_macs.remove(new)
                if old not in state.allowed_macs:
                    state.allowed_macs.append(old)
            except Exception:
                pass
        if was_banned:
            state.banned_macs.discard(new)
            state.banned_macs.add(old)
        if was_pending:
            state.pending_macs.discard(new)
            state.pending_macs.add(old)
        state.ip_to_mac_cache.clear()
        state.ip_to_mac_cache.update(ip_cache_snapshot)
        if sessions_snapshot is not None:
            try:
                from services.onboarding import _sessions

                _sessions.clear()
                _sessions.update(sessions_snapshot)
            except Exception:
                pass
        await interaction.followup.send(
            f"`❌` Router update failed for `{old}` -> `{new}`: {router_err}. Rolled back, no changes made."
        )
        return

    if was_banned or was_pending or was_allowed:
        try:
            from router.firewall import enable_lockdown

            async with ROUTER_LOCK:
                await asyncio.to_thread(
                    enable_lockdown, force_lock=state.lockdown_state
                )
        except Exception as e:
            logger.warning(f"Firewall reapply after edit {old}->{new} failed: {e}")

    try:
        import time

        from router.devices import _recently_migrated, _recently_removed

        _recently_migrated[old] = time.time() + 600  # 10 min TTL
        _recently_removed.pop(old, None)
        _recently_removed.pop(new, None)
    except Exception:
        pass

    await interaction.followup.send(
        embed=discord.Embed(
            title="`✅` Device MAC Updated",
            description=f"**Old:** `{old}`\n**New:** `{new}`\n**Hostname:** `{old_hostname}`\n\nAll DB, state, and router entries migrated. Use new MAC immediately for block/limit/device commands.",
            color=0x2ECC71,
        )
    )
    logger.info(f"SUCCESS: /macs edit {old} -> {new} by {interaction.user}")


async def _handle_remove(interaction: discord.Interaction, mac: str):
    logger.info(f"ACTION: /macs remove | User: {interaction.user} | {mac}")
    if not mac:
        await interaction.followup.send("`❌` MAC is required for remove.")
        return
    target = mac.strip().upper()
    if not __import__("utils.validators", fromlist=["is_valid_mac"]).is_valid_mac(
        target
    ):
        await interaction.followup.send(f"`❌` Invalid MAC: `{mac}`")
        return
    if not db.device_exists(target):
        if (
            target not in state.macs_list
            and target not in state.banned_macs
            and target not in state.allowed_macs
            and target not in state.pending_macs
        ):
            await interaction.followup.send(f"`❌` Device not found: `{target}`")
            return
    was_banned = target in state.banned_macs
    was_allowed = target in state.allowed_macs
    was_pending = target in state.pending_macs
    was_in_macs = target in state.macs_list

    try:
        from services.onboarding import _sessions, drop_session

        if target in _sessions:
            drop_session(target)
    except Exception:
        pass

    db_success = db.delete_device_purge(target)

    state.macs_list.pop(target, None)
    if was_allowed:
        try:
            state.allowed_macs.remove(target)
        except ValueError:
            pass
    state.banned_macs.discard(target)
    state.pending_macs.discard(target)
    for ip, cached in list(state.ip_to_mac_cache.items()):
        if cached.upper() == target:
            del state.ip_to_mac_cache[ip]
    try:
        from services.onboarding import _sessions

        _sessions.pop(target, None)
    except Exception:
        pass

    try:
        from router.static_leases import (
            _push_dhcpd_static,
            _serialize_entry,
            fetch_current_entries,
        )

        async with ROUTER_LOCK:
            entries, _ = await asyncio.to_thread(fetch_current_entries)
            has_static = any(ent["mac"].upper() == target for ent in entries)
            if has_static:
                new_parts = [
                    _serialize_entry(
                        ent["mac"], ent["ip"], ent["hostname"], ent["flag"]
                    )
                    for ent in entries
                    if ent["mac"].upper() != target
                ]
                new_raw = ">".join(new_parts)
                ok, err = await asyncio.to_thread(_push_dhcpd_static, new_raw)
                if not ok:
                    logger.warning(
                        f"Static DHCP cleanup failed for remove {target}: {err}"
                    )
    except Exception as e:
        logger.warning(f"Static DHCP cleanup failed for remove {target}: {e}")

    if was_banned or was_allowed or was_pending:
        try:
            from router.firewall import enable_lockdown

            async with ROUTER_LOCK:
                await asyncio.to_thread(
                    enable_lockdown, force_lock=state.lockdown_state
                )
        except Exception as e:
            logger.warning(f"Firewall reapply after remove {target} failed: {e}")

    try:
        import time

        from router.devices import _recently_migrated, _recently_removed

        _recently_removed[target] = time.time() + 600
        _recently_migrated.pop(target, None)
    except Exception:
        pass

    await interaction.followup.send(
        embed=discord.Embed(
            title="`🗑️` Device Removed",
            description=f"**MAC:** `{target}`\n\nAll DB, state, and router static entries cleaned. It will no longer appear in /macs or autocomplete until rediscovered (if still connected, it will reappear as pending).",
            color=0xE74C3C,
        )
    )
    logger.info(
        f"SUCCESS: /macs remove {target} by {interaction.user} was_in_macs={was_in_macs} db_success={db_success}"
    )
