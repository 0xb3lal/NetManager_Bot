import asyncio

import discord

import db
from commands.views.onboarding_embeds import (
    _COLOR_BLOCK,
    _COLOR_NEW,
    _allowed_ack_embed,
    _blocked_ack_embed,
    _info_box,
    _name_question_embed,
    _processing_embed,
    _whitelist_question_embed,
)
from logger import logger
from router.firewall import ban_mac, enable_lockdown, unban_mac
from services.onboarding_sessions import (
    ONBOARDING_STEP_TIMEOUT,
    OnboardingSession,
    _device_gone,
    _device_name,
    _gone_notice,
    _sessions,
    drop_session,
)
from state import ROUTER_LOCK, state
from utils.discord import ensure_admin


class _OnboardingBaseView(discord.ui.View):

    def __init__(self, session: OnboardingSession, step_tag: str):
        super().__init__(timeout=ONBOARDING_STEP_TIMEOUT)
        self.session = session
        self.step_tag = step_tag

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await ensure_admin(interaction)

    async def on_timeout(self):
        # Fail-safe: unanswered simply leaves the device PENDING => blocked.
        # Only the view whose step is still current may time the flow out.
        session = self.session
        if (
            _sessions.get(session.mac) is not session
            or session.step != self.step_tag
            or session.busy
        ):
            if session.busy:
                logger.debug(
                    f"Onboarding on_timeout suppressed for {session.mac}: busy"
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
                f"Could not edit timed-out onboarding message for {session.mac}: {e}"
            )
        drop_session(session.mac)


class OnboardingQ1View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q1")

    @discord.ui.button(label="✅ Allow", style=discord.ButtonStyle.success)
    async def allow(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        session = self.session
        if _device_gone(session):
            try:
                await interaction.response.edit_message(
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=self,
                )
            except Exception as e:
                logger.warning(f"Could not ack gone for {session.mac}: {e}")
                try:
                    await _gone_notice(session)
                except Exception:
                    pass
            else:
                drop_session(session.mac)
            self.stop()
            return
        placeholder = discord.Embed(
            title="`⏳` Allowing Device…",
            description=_info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + "\nPlease wait…",
            color=_COLOR_NEW,
        )
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Q1 allow for {session.mac}: {e}")
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
            # Local import — breaks cycle with retry helpers (services ↔ views)
            from services.onboarding_retry import _run_router_with_retry

            async def _router_work():
                async with ROUTER_LOCK:
                    if session.mac in state.banned_macs:
                        await asyncio.to_thread(unban_mac, session.mac)
                    else:
                        await asyncio.to_thread(
                            enable_lockdown, force_lock=state.lockdown_state
                        )

            async def _on_success():
                # Commit only after the router rebuild succeeded: stop
                # treating the device as pending. If the DB confirm fails,
                # fail closed by restoring the pending flag (the pending
                # DROP keeps blocking). Mirrors the Q1 block path below.
                had_pending = session.mac in state.pending_macs
                try:
                    state.pending_macs.discard(session.mac)
                    db.set_onboarding_confirmed(session.mac)
                except Exception as e:
                    if had_pending:
                        state.pending_macs.add(session.mac)
                    logger.error(f"DB confirm failed for {session.mac} (Q1 allow): {e}")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=discord.Embed(
                                title="`⚠️` Update Failed",
                                description=_info_box(
                                    [
                                        ("Device:", _device_name(session.mac)),
                                        ("MAC:", session.mac),
                                    ]
                                )
                                + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                                color=_COLOR_BLOCK,
                            ),
                            view=None,
                        )
                    except Exception as e2:
                        logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                    drop_session(session.mac)
                    return
                session.step = "q2"
                session.context = "allowed"
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_whitelist_question_embed(session),
                    view=OnboardingQ2View(session),
                )

            ok = await _run_router_with_retry(
                session, interaction, "q1_allow", _router_work, _on_success
            )
            if not ok:
                return
        finally:
            session.busy = False
            self.stop()

    @discord.ui.button(label="🚫 Block", style=discord.ButtonStyle.danger)
    async def block(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        session = self.session
        if _device_gone(session):
            try:
                await interaction.response.edit_message(
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=self,
                )
            except Exception as e:
                logger.warning(f"Could not ack gone for {session.mac}: {e}")
                try:
                    await _gone_notice(session)
                except Exception:
                    pass
            else:
                drop_session(session.mac)
            self.stop()
            return
        placeholder = discord.Embed(
            title="`⏳` Blocking Device…",
            description=_info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + "\nPlease wait…",
            color=_COLOR_NEW,
        )
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Q1 block for {session.mac}: {e}")
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
            # NOTE: no DB confirm / pending discard here. The device stays in
            # state.pending_macs until the router ban actually succeeds, so
            # every rebuild keeps emitting a DROP rule even if all retries are
            # exhausted or the session times out (fail-closed).
            from services.onboarding_retry import _run_router_with_retry

            async def _router_work():
                async with ROUTER_LOCK:
                    await asyncio.to_thread(ban_mac, session.mac, "onboarding")

            async def _on_success():
                # Commit only after ban_mac succeeded: stop treating the
                # device as pending. If the DB confirm fails, fail closed by
                # restoring the pending flag (the pending DROP keeps blocking).
                had_pending = session.mac in state.pending_macs
                try:
                    state.pending_macs.discard(session.mac)
                    db.set_onboarding_confirmed(session.mac)
                except Exception as e:
                    if had_pending:
                        state.pending_macs.add(session.mac)
                    logger.error(f"DB confirm failed for {session.mac} (Q1 block): {e}")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=discord.Embed(
                                title="`⚠️` Update Failed",
                                description=_info_box(
                                    [
                                        ("Device:", _device_name(session.mac)),
                                        ("MAC:", session.mac),
                                    ]
                                )
                                + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                                color=_COLOR_BLOCK,
                            ),
                            view=None,
                        )
                    except Exception as e2:
                        logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                    drop_session(session.mac)
                    return
                session.context = "blocked"
                hostname = _device_name(session.mac)
                if hostname.lower() != "unknown":
                    session.step = "done"
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED.")
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_blocked_ack_embed(session),
                        view=None,
                    )
                    drop_session(session.mac)
                else:
                    session.step = "q3"
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_name_question_embed(session),
                        view=OnboardingQ3View(session),
                    )

            ok = await _run_router_with_retry(
                session, interaction, "q1_block", _router_work, _on_success
            )
            if not ok:
                return
        finally:
            session.busy = False
            self.stop()


class OnboardingQ2View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q2")

    @discord.ui.button(label="✅ Yes / Whitelist", style=discord.ButtonStyle.success)
    async def whitelist_yes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        if _device_gone(session):
            try:
                await interaction.response.edit_message(
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=self,
                )
            except Exception as e:
                logger.warning(f"Could not ack gone for {session.mac}: {e}")
                try:
                    await _gone_notice(session)
                except Exception:
                    pass
            else:
                drop_session(session.mac)
            self.stop()
            return
        placeholder = discord.Embed(
            title="`⏳` Whitelisting…",
            description=_info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + "\nPlease wait…",
            color=_COLOR_NEW,
        )
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Q2 whitelist_yes for {session.mac}: {e}")
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
            # DB write — fail-forward; router failure handled via retry helper
            try:
                db.set_device_allowed(session.mac, True)
                if session.mac not in state.allowed_macs:
                    state.allowed_macs.append(session.mac)
            except Exception as e:
                logger.error(f"DB whitelist failed for {session.mac}: {e}")
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=discord.Embed(
                            title="`⚠️` Update Failed",
                            description=_info_box(
                                [
                                    ("Device:", _device_name(session.mac)),
                                    ("MAC:", session.mac),
                                ]
                            )
                            + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                            color=_COLOR_BLOCK,
                        ),
                        view=None,
                    )
                except Exception as e2:
                    logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                drop_session(session.mac)
                return

            from services.onboarding_retry import _run_router_with_retry

            async def _router_work():
                async with ROUTER_LOCK:
                    if session.mac in state.banned_macs:
                        await asyncio.to_thread(unban_mac, session.mac)
                    else:
                        await asyncio.to_thread(
                            enable_lockdown, force_lock=state.lockdown_state
                        )

            async def _on_success():
                session.whitelisted = True
                session.step = "q3"
                if _device_name(session.mac).lower() != "unknown":
                    session.step = "done"
                    logger.info(
                        f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted})."
                    )
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_allowed_ack_embed(session),
                        view=None,
                    )
                    drop_session(session.mac)
                else:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_name_question_embed(session),
                        view=OnboardingQ3View(session),
                    )

            ok = await _run_router_with_retry(
                session, interaction, "q2_whitelist", _router_work, _on_success
            )
            if not ok:
                return
        finally:
            session.busy = False
            self.stop()

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.secondary)
    async def whitelist_no(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        if _device_gone(session):
            try:
                await interaction.response.edit_message(
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=self,
                )
            except Exception as e:
                logger.warning(f"Could not ack gone for {session.mac}: {e}")
                try:
                    await _gone_notice(session)
                except Exception:
                    pass
            else:
                drop_session(session.mac)
            self.stop()
            return
        placeholder = _processing_embed(session, "Processing…")
        try:
            await interaction.response.edit_message(view=self, embed=placeholder)
        except Exception as e:
            logger.warning(f"Failed to ack Q2 whitelist_no for {session.mac}: {e}")
        # No busy — no background I/O
        session.whitelisted = False
        session.step = "q3"
        if _device_name(session.mac).lower() != "unknown":
            session.step = "done"
            logger.info(
                f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted})."
            )
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_allowed_ack_embed(session),
                    view=None,
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        else:
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_name_question_embed(session),
                    view=OnboardingQ3View(session),
                )
            except Exception as e:
                logger.error(f"Failed to send Q3 embed for {session.mac}: {e}")
        self.stop()


class OnboardingQ3View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q3")

    @discord.ui.button(label="✏️ Enter Name", style=discord.ButtonStyle.primary)
    async def enter_name(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        # Local import — modal lives in commands/views/onboarding_modals.py (views→views, safe)
        from commands.views.onboarding_modals import RenameModal

        await interaction.response.send_modal(RenameModal(self.session))

    @discord.ui.button(label="❓ Keep Unknown", style=discord.ButtonStyle.secondary)
    async def keep_unknown(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        for c in self.children:
            c.disabled = True
        session = self.session
        if _device_gone(session):
            try:
                await interaction.response.edit_message(
                    content=(
                        f"`⚠️` Device record for `{session.mac}` no longer exists — "
                        f"onboarding cancelled."
                    ),
                    embed=None,
                    view=self,
                )
            except Exception as e:
                logger.warning(f"Could not ack gone for {session.mac}: {e}")
                try:
                    await _gone_notice(session)
                except Exception:
                    pass
            else:
                drop_session(session.mac)
            self.stop()
            return
        # Single edit, no background I/O
        if session.context == "allowed":
            session.step = "done"
            logger.info(
                f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, keep unknown)."
            )
            try:
                await interaction.response.edit_message(
                    embed=_allowed_ack_embed(session), view=None
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        else:
            session.step = "done"
            logger.info(f"Onboarding complete: {session.mac} BLOCKED (keep unknown).")
            try:
                await interaction.response.edit_message(
                    embed=_blocked_ack_embed(session), view=None
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        self.stop()
