"""
commands/views/onboarding_retry_views.py

Extracted from services/onboarding.py:248-569 (Phase B).

Discord retry Views for onboarding — FirewallRetryView + RenameRetryView.
These contain the only router-touching retry buttons.

Top-level imports are leaf-only (discord, logger, state, router.firewall,
services/onboarding_sessions, utils.discord). All embed/view continuations
and helper calls that would close the cycle are deferred to local imports
inside button callbacks (see §3 cycle break).
"""

import asyncio

import discord

from logger import logger
from router.firewall import ban_mac, enable_lockdown, unban_mac
from services.onboarding_sessions import (
    _device_gone,
    _device_name,
    _gone_notice,
    _sessions,
    drop_session,
)
from state import ROUTER_LOCK, state
from utils.discord import ensure_admin


class FirewallRetryView(discord.ui.View):

    def __init__(self, session, op: str):
        super().__init__(timeout=300)
        self.session = session
        self.op = op
        self.step_tag = session.step

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await ensure_admin(interaction)

    async def on_timeout(self):
        session = self.session
        if (
            _sessions.get(session.mac) is not session
            or session.step != self.step_tag
            or session.busy
        ):
            if session.busy:
                logger.debug(
                    f"FirewallRetryView on_timeout suppressed for {session.mac}: busy"
                )
            return
        try:
            await session.message.edit(
                content=(
                    f"`⌛` Onboarding timed out — `{session.mac}` remains BLOCKED. "
                    f"Use /pending to review it later."
                ),
                embed=None,
                view=None,
            )
        except Exception as e:
            logger.warning(
                f"Could not edit timed-out retry message for {session.mac}: {e}"
            )
        drop_session(session.mac)

    @discord.ui.button(label="🔄 Retry", style=discord.ButtonStyle.primary)
    async def retry_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        attempt = session.retry_count + 1
        if attempt < 1:
            attempt = 1
        if attempt > session.max_retries:
            attempt = session.max_retries
        # Local imports — embeds are in commands/views, not services
        from commands.views.onboarding_embeds import _COLOR_NEW, _info_box

        placeholder = discord.Embed(
            title=f"`⏳` Retrying… (attempt {attempt}/{session.max_retries})",
            description=_info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + f"\nRetrying… (attempt {attempt}/{session.max_retries})",
            color=_COLOR_NEW,
        )
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Firewall retry for {session.mac}: {e}")
        session.busy = True
        try:
            if _device_gone(session):
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        content=(
                            f"`⚠️` Device record for `{session.mac}` no longer exists — "
                            f"onboarding cancelled."
                        ),
                        embed=None,
                        view=None,
                    )
                except Exception:
                    await _gone_notice(session)
                else:
                    drop_session(session.mac)
                return
            # Only router call, no DB — local helper import breaks cycle services↔views
            from services.onboarding_retry import _handle_router_failure

            try:
                async with ROUTER_LOCK:
                    if self.op == "q1_allow":
                        if session.mac in state.banned_macs:
                            await asyncio.to_thread(unban_mac, session.mac)
                        else:
                            await asyncio.to_thread(
                                enable_lockdown, force_lock=state.lockdown_state
                            )
                    elif self.op == "q1_block":
                        await asyncio.to_thread(ban_mac, session.mac, "onboarding")
                    elif self.op == "q2_whitelist":
                        await asyncio.to_thread(
                            enable_lockdown, force_lock=state.lockdown_state
                        )
                    else:
                        logger.error(
                            f"Unknown FirewallRetry op {self.op} for {session.mac}"
                        )
                        raise RuntimeError(f"unknown op {self.op}")
            except Exception as e:
                await _handle_router_failure(session, interaction, self.op, e)
                return
            # Success — advance as original flow (local view/embed imports break N4→N5 cycle)
            if self.op == "q1_allow":
                session.step = "q2"
                session.context = "allowed"
                try:
                    from commands.views.onboarding_embeds import (
                        _whitelist_question_embed,
                    )
                    from commands.views.onboarding_views import OnboardingQ2View

                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_whitelist_question_embed(session),
                        view=OnboardingQ2View(session),
                    )
                except Exception as e:
                    logger.error(f"Failed to send Q2 embed for {session.mac}: {e}")
            elif self.op == "q1_block":
                session.context = "blocked"
                hostname = _device_name(session.mac)
                if hostname.lower() != "unknown":
                    session.step = "done"
                    logger.info(
                        f"Onboarding complete: {session.mac} BLOCKED (retry success)."
                    )
                    try:
                        from commands.views.onboarding_embeds import _blocked_ack_embed

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
                else:
                    session.step = "q3"
                    try:
                        from commands.views.onboarding_embeds import (
                            _name_question_embed,
                        )
                        from commands.views.onboarding_views import OnboardingQ3View

                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_name_question_embed(session),
                            view=OnboardingQ3View(session),
                        )
                    except Exception as e:
                        logger.error(f"Failed to send Q3 embed for {session.mac}: {e}")
            elif self.op == "q2_whitelist":
                session.whitelisted = True
                session.step = "q3"
                if _device_name(session.mac).lower() != "unknown":
                    session.step = "done"
                    logger.info(
                        f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}) (retry)."
                    )
                    try:
                        from commands.views.onboarding_embeds import _allowed_ack_embed

                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send onboarding ack for {session.mac}: {e}"
                        )
                    drop_session(session.mac)
                else:
                    try:
                        from commands.views.onboarding_embeds import (
                            _name_question_embed,
                        )
                        from commands.views.onboarding_views import OnboardingQ3View

                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_name_question_embed(session),
                            view=OnboardingQ3View(session),
                        )
                    except Exception as e:
                        logger.error(f"Failed to send Q3 embed for {session.mac}: {e}")
        finally:
            session.busy = False
            self.stop()


class RenameRetryView(discord.ui.View):

    def __init__(self, session, ip: str, new_name: str):
        super().__init__(timeout=300)
        self.session = session
        self.ip = ip
        self.new_name = new_name
        self.step_tag = session.step

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await ensure_admin(interaction)

    async def on_timeout(self):
        session = self.session
        if (
            _sessions.get(session.mac) is not session
            or session.step != self.step_tag
            or session.busy
        ):
            if session.busy:
                logger.debug(
                    f"RenameRetryView on_timeout suppressed for {session.mac}: busy"
                )
            return
        try:
            await session.message.edit(
                content=(
                    f"`⌛` Onboarding timed out — `{session.mac}` remains BLOCKED. "
                    f"Use /pending to review it later."
                ),
                embed=None,
                view=None,
            )
        except Exception as e:
            logger.warning(
                f"Could not edit timed-out rename retry message for {session.mac}: {e}"
            )
        drop_session(session.mac)

    @discord.ui.button(label="🔄 Retry", style=discord.ButtonStyle.primary)
    async def retry_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        attempt = session.retry_count + 1
        if attempt < 1:
            attempt = 1
        if attempt > session.max_retries:
            attempt = session.max_retries
        from commands.views.onboarding_embeds import _COLOR_NEW, _info_box

        placeholder = discord.Embed(
            title=f"`⏳` Retrying… (attempt {attempt}/{session.max_retries})",
            description=_info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + f"\nRetrying rename… (attempt {attempt}/{session.max_retries})",
            color=_COLOR_NEW,
        )
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Rename retry for {session.mac}: {e}")
        session.busy = True
        try:
            if _device_gone(session):
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        content=(
                            f"`⚠️` Device record for `{session.mac}` no longer exists — "
                            f"onboarding cancelled."
                        ),
                        embed=None,
                        view=None,
                    )
                except Exception:
                    await _gone_notice(session)
                else:
                    drop_session(session.mac)
                return
            from router.static_leases import set_static_hostname

            try:
                success, err = await set_static_hostname(
                    session.mac, self.ip, self.new_name
                )
            except Exception as e:
                success, err = False, str(e)
            if not success:
                from services.onboarding_retry import _handle_rename_failure

                await _handle_rename_failure(
                    session, interaction, self.ip, self.new_name, err
                )
                return
            # success
            try:
                from router.devices import fetch_devlist

                async with ROUTER_LOCK:
                    await asyncio.to_thread(fetch_devlist)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Post-rename discovery resync failed: {e}")
            state.macs_list[session.mac] = self.new_name
            session.named = self.new_name
            session.step = "done"
            if session.context == "allowed":
                logger.info(
                    f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, renamed={self.new_name}) (retry)."
                )
                try:
                    from commands.views.onboarding_embeds import _allowed_ack_embed

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
                    f"Onboarding complete: {session.mac} BLOCKED (renamed={self.new_name}) (retry)."
                )
                try:
                    from commands.views.onboarding_embeds import _blocked_ack_embed

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
        finally:
            session.busy = False
            self.stop()

    @discord.ui.button(label="❓ Keep Unknown", style=discord.ButtonStyle.secondary)
    async def keep_unknown_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        from commands.views.onboarding_embeds import (
            _allowed_ack_embed,
            _blocked_ack_embed,
            _processing_embed,
        )

        placeholder = _processing_embed(session, "Keeping Unknown…")
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack keep_unknown retry for {session.mac}: {e}")
        if _device_gone(session):
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=None,
                )
            except Exception:
                await _gone_notice(session)
            else:
                drop_session(session.mac)
            return
        session.step = "done"
        if session.context == "allowed":
            logger.info(
                f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, keep unknown via retry)."
            )
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_allowed_ack_embed(session),
                    view=None,
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
        else:
            logger.info(
                f"Onboarding complete: {session.mac} BLOCKED (keep unknown via retry)."
            )
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_blocked_ack_embed(session),
                    view=None,
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
        drop_session(session.mac)
        self.stop()
