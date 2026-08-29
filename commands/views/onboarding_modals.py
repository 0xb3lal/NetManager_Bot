"""
commands/views/onboarding_modals.py

Extracted from services/onboarding.py:1125-1351 (Phase B).

Discord Modal for renaming during onboarding — RenameModal.
Keeps the full on_submit flow including IP resolution, static lease
update, discovery resync, and ack embeds. All cross-layer helpers are
imported locally or via leaf-safe top-level leaves.
"""

import asyncio

import discord

from commands.views.onboarding_embeds import (
    _allowed_ack_embed,
    _blocked_ack_embed,
    _processing_embed,
)
from logger import logger
from services.onboarding_sessions import (
    ONBOARDING_STEP_TIMEOUT,
    OnboardingSession,
    _device_gone,
    _gone_notice,
    _sessions,
    drop_session,
)
from state import ROUTER_LOCK, state


class RenameModal(discord.ui.Modal):

    name_input = discord.ui.TextInput(
        label="Custom device name",
        placeholder="e.g. Baba's Phone",
        max_length=32,
        required=True,
    )

    def __init__(self, session: OnboardingSession):
        super().__init__(title="Name This Device", timeout=ONBOARDING_STEP_TIMEOUT)
        self.session = session

    async def on_submit(self, interaction: discord.Interaction):
        session = self.session
        if _device_gone(session):
            try:
                await _gone_notice(session)
            except Exception:
                pass
            return
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.errors.HTTPException as e:
            logger.debug(f"RenameModal defer failed for {session.mac}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Unexpected defer error for {session.mac}: {e}")

        # Placeholder after defer
        placeholder = _processing_embed(session, "Processing rename…")
        try:
            await interaction.followup.edit_message(
                message_id=session.message.id,
                embed=placeholder,
                view=None,
            )
        except Exception as e:
            logger.warning(f"Failed to show rename placeholder for {session.mac}: {e}")

        session.busy = True
        try:
            new_name = str(self.name_input.value).strip()
            if not new_name:
                session.step = "done"
                if session.context == "allowed":
                    logger.info(
                        f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, empty name keep unknown)."
                    )
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                else:
                    logger.info(
                        f"Onboarding complete: {session.mac} BLOCKED (empty name)."
                    )
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                drop_session(session.mac)
                return

            from router.static_leases import (
                is_valid_hostname,
                resolve_ip_for_mac,
                set_static_hostname,
            )

            if not is_valid_hostname(new_name):
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        content=f"`❌` Invalid hostname '{new_name}'. Use 1-32 alphanumeric/hyphen characters.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(
                        f"Failed to edit message for invalid hostname {session.mac}"
                    )
                session.step = "done"
                if session.context == "allowed":
                    logger.info(
                        f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, invalid hostname keep)."
                    )
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                else:
                    logger.info(
                        f"Onboarding complete: {session.mac} BLOCKED (invalid hostname)."
                    )
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                drop_session(session.mac)
                return

            ip = None
            try:
                from router.devices import fetch_devlist

                async with ROUTER_LOCK:
                    dhcp_leases, _, _ = await asyncio.to_thread(fetch_devlist)
                for lease in dhcp_leases:
                    if lease[2].upper() == session.mac:
                        ip = lease[1]
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Fresh IP lookup failed for {session.mac}: {e}")
            if not ip:
                ip = resolve_ip_for_mac(session.mac)
                if ip:
                    logger.debug(f"Using cached IP fallback for {session.mac}: {ip}")
            if not ip:
                ip = session.ip
            if not ip:
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        content=f"`❌` Could not resolve IP for `{session.mac}`. Router rename failed — hostname unchanged. Use /rename with explicit IP.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(
                        f"Failed to edit message for unresolved IP {session.mac}"
                    )
                session.step = "done"
                if session.context == "allowed":
                    logger.info(
                        f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, no IP)."
                    )
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                else:
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED (no IP).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                drop_session(session.mac)
                return

            success, err = await set_static_hostname(session.mac, ip, new_name)
            if not success:
                from services.onboarding_retry import _handle_rename_failure

                await _handle_rename_failure(session, interaction, ip, new_name, err)
                return

            try:
                from router.devices import fetch_devlist

                async with ROUTER_LOCK:
                    await asyncio.to_thread(fetch_devlist)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Post-rename discovery resync failed: {e}")

            state.macs_list[session.mac] = new_name
            session.named = new_name
            session.step = "done"

            if session.context == "allowed":
                logger.info(
                    f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, renamed={new_name})."
                )
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_allowed_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send onboarding ack for {session.mac}: {e}"
                    )
            else:
                logger.info(
                    f"Onboarding complete: {session.mac} BLOCKED (renamed={new_name})."
                )
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_blocked_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send onboarding ack for {session.mac}: {e}"
                    )
            drop_session(session.mac)
        except asyncio.CancelledError:
            try:
                drop_session(session.mac)
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in RenameModal for {session.mac}: {e}", exc_info=True
            )
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    content=f"`❌` Unexpected error during rename: {e}",
                    embed=None,
                    view=None,
                )
            except Exception:
                logger.warning(
                    f"Failed to edit unexpected error message for {session.mac}"
                )
            try:
                if session.mac in _sessions:
                    drop_session(session.mac)
            except Exception:
                pass
        finally:
            session.busy = False
            if session.mac in _sessions and session.step != "done":
                logger.warning(
                    f"RenameModal finally cleanup for orphaned session {session.mac}"
                )
                try:
                    drop_session(session.mac)
                except Exception:
                    pass
