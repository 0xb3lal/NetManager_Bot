"""
services/onboarding.py

Interactive review flow for newly discovered devices. A new MAC is inserted
as PENDING (firewall-dropped by default) by discovery; this module asks the
admins a short chained questionnaire on Discord and persists every answer:

    Q1  Allow internet access?            -> allow | block
    Q2  (if allowed) Add to whitelist?    -> whitelist | normal
    Q3  (if hostname is unknown) assign
        a custom name now?                -> rename | keep unknown (router-native)

Sessions are keyed strictly by MAC so concurrent flows cannot cross-talk.
Fail-safe invariant: until an admin answers, the device stays PENDING and
the firewall drops it — including across bot restarts (state lives in the DB).
Whitelisted devices bypass daily-cap auto-block and survive lockdowns.
"""

import asyncio

import discord

import db
from config import CHANNEL_ID
from logger import logger
from state import state, ROUTER_LOCK
from router.firewall import ban_mac, unban_mac, enable_lockdown
from utils.traffic import format_data_size

ONBOARDING_STEP_TIMEOUT = 600  # seconds each question waits for an answer

_COLOR_NEW = 0xF39C12
_COLOR_OK = 0x2ECC71
_COLOR_BLOCK = 0xFF4747


def _device_name(mac: str) -> str:
    return state.macs_list.get(mac, "unknown")


class OnboardingSession:
    def __init__(self, mac: str, hostname: str, ip: str = None, rssi_dbm: int | None = None, distance_m: float | None = None, quality_pct: int | None = None):
        self.mac = mac
        self.hostname = hostname
        self.ip = ip
        self.rssi_dbm = rssi_dbm
        self.distance_m = distance_m
        self.quality_pct = quality_pct
        self.step = "q1"          # q1 -> q2/q3 -> done
        self.context = None       # "allowed" | "blocked" once Q1 answered
        self.whitelisted = False
        self.named = None         # custom name if one was assigned
        self.message = None       # the Discord message carrying the current question
        self.busy: bool = False
        self.retry_count: int = 0
        self.max_retries: int = 3


_sessions: dict[str, OnboardingSession] = {}


def drop_session(mac: str):
    """Forget any active session for this MAC (used on completion, timeout,
    and stale-device cleanup so no dangling reference survives deletion)."""
    _sessions.pop(mac.upper(), None)


def _device_gone(session: OnboardingSession) -> bool:
    """True when the underlying devices row disappeared (e.g. purged by the
    stale-device cleanup while this session was waiting)."""
    return not db.device_exists(session.mac)


async def _gone_notice(session: OnboardingSession):
    try:
        await session.message.edit(
            content=(
                f"`⚠️` Device record for `{session.mac}` no longer exists — "
                f"onboarding cancelled."
            ),
            embed=None,
            view=None,
        )
    except Exception as e:
        logger.warning(f"Could not update deleted-device message for {session.mac}: {e}")
    drop_session(session.mac)


async def _admin_gate(interaction: discord.Interaction) -> bool:
    """Any administrator may answer onboarding questions (the bot itself
    starts these messages, so there is no single invoker to restrict to)."""
    user = interaction.user
    if isinstance(user, discord.Member) and user.guild_permissions.administrator:
        return True
    await interaction.response.send_message(
        "`❌` Only administrators can answer onboarding questions.", ephemeral=True
    )
    return False


def _info_box(lines: list[tuple[str, str]]) -> str:
    body = "\n".join(f"{label.ljust(10)} {value}" for label, value in lines)
    return f"```\n{body}\n```"


def _kickoff_embed(session: OnboardingSession) -> discord.Embed:
    from config import RSSI_DISPLAY_ENABLED, DISTANCE_ESTIMATION_ENABLED

    lines = [
        ("Device:", session.hostname),
        ("MAC:", session.mac),
        ("Status:", "Blocked until reviewed"),
    ]
    if RSSI_DISPLAY_ENABLED:
        if session.rssi_dbm is not None and session.quality_pct is not None:
            lines.append(("Signal:", f"`📶` {session.quality_pct}%"))
        else:
            lines.append(("Signal:", "— (wired)"))
        if DISTANCE_ESTIMATION_ENABLED and session.distance_m is not None:
            lines.append(("Distance:", f"`📏` ~{session.distance_m:.1f} m"))
    footer = "This device is blocked. Answer below to finish setup."
    if DISTANCE_ESTIMATION_ENABLED and session.distance_m is not None:
        footer = "Est. distance is approximate (±50%+ indoors) • " + footer
    embed = discord.Embed(
        title="`🆕` New Device Detected — Review Required",
        description=_info_box(lines),
        color=_COLOR_NEW,
    )
    embed.set_footer(text=footer)
    return embed


def _whitelist_question_embed(session: OnboardingSession) -> discord.Embed:
    embed = discord.Embed(
        title="`⭐` Add to Whitelist?",
        description=(
            _info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ])
            + "\nAdd this device to the whitelist?\nWhitelisted devices bypass daily caps and survive system-wide lockdowns."
        ),
        color=_COLOR_NEW,
    )
    embed.set_footer(text="You can change this later with /wl add or /wl remove.")
    return embed


def _name_question_embed(session: OnboardingSession) -> discord.Embed:
    embed = discord.Embed(
        title="`✏️` Name This Device",
        description=(
            _info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ])
            + "\nAssign a custom name now?"
        ),
        color=_COLOR_NEW,
    )
    embed.set_footer(text="Naming helps identify the device in lists later.")
    return embed


def _allowed_ack_embed(session: OnboardingSession) -> discord.Embed:
    whitelist_value = "Yes — bypasses daily caps, survives lockdowns" if session.whitelisted else "No"
    embed = discord.Embed(
        title="`✅` Device Allowed",
        description=_info_box([
            ("Device:", session.named or _device_name(session.mac)),
            ("MAC:", session.mac),
            ("Whitelisted:", whitelist_value),
        ]),
        color=_COLOR_OK,
    )
    embed.set_footer(text="Whitelisted devices bypass daily caps and survive lockdowns. Applies immediately.")
    return embed


def _blocked_ack_embed(session: OnboardingSession) -> discord.Embed:
    embed = discord.Embed(
        title="`🚫` Device Blocked",
        description=_info_box([
            ("Device:", session.named or _device_name(session.mac)),
            ("MAC:", session.mac),
            ("Status:", "Blocked"),
        ]),
        color=_COLOR_BLOCK,
    )
    embed.set_footer(text="Use /rm to unblock it later.")
    return embed


def _permanent_failure_embed(session: OnboardingSession) -> discord.Embed:
    return discord.Embed(
        title="`❌` Setup Failed — Contact Admin",
        description=_info_box([
            ("Device:", _device_name(session.mac)),
            ("MAC:", session.mac),
        ]) + "\nSetup failed after 3 attempts. Please contact an administrator.",
        color=_COLOR_BLOCK,
    )


def _processing_embed(session: OnboardingSession, title: str = "Processing…") -> discord.Embed:
    return discord.Embed(
        title=f"`⏳` {title}",
        description=_info_box([
            ("Device:", _device_name(session.mac)),
            ("MAC:", session.mac),
        ]) + f"\n{title}",
        color=_COLOR_NEW,
    )


class _OnboardingBaseView(discord.ui.View):

    def __init__(self, session: OnboardingSession, step_tag: str):
        super().__init__(timeout=ONBOARDING_STEP_TIMEOUT)
        self.session = session
        self.step_tag = step_tag

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _admin_gate(interaction)

    async def on_timeout(self):
        # Fail-safe: unanswered simply leaves the device PENDING => blocked.
        # Only the view whose step is still current may time the flow out.
        session = self.session
        if _sessions.get(session.mac) is not session or session.step != self.step_tag or session.busy:
            if session.busy:
                logger.debug(f"Onboarding on_timeout suppressed for {session.mac}: busy")
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
            logger.warning(f"Could not edit timed-out onboarding message for {session.mac}: {e}")
        drop_session(session.mac)


class FirewallRetryView(discord.ui.View):

    def __init__(self, session: OnboardingSession, op: str):
        super().__init__(timeout=300)
        self.session = session
        self.op = op
        self.step_tag = session.step

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _admin_gate(interaction)

    async def on_timeout(self):
        session = self.session
        if _sessions.get(session.mac) is not session or session.step != self.step_tag or session.busy:
            if session.busy:
                logger.debug(f"FirewallRetryView on_timeout suppressed for {session.mac}: busy")
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
            logger.warning(f"Could not edit timed-out retry message for {session.mac}: {e}")
        drop_session(session.mac)

    @discord.ui.button(label="🔄 Retry", style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        session = self.session
        attempt = session.retry_count + 1
        if attempt < 1:
            attempt = 1
        if attempt > session.max_retries:
            attempt = session.max_retries
        placeholder = discord.Embed(
            title=f"`⏳` Retrying… (attempt {attempt}/{session.max_retries})",
            description=_info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]) + f"\nRetrying… (attempt {attempt}/{session.max_retries})",
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
            # Only router call, no DB
            try:
                async with ROUTER_LOCK:
                    if self.op == "q1_allow":
                        if session.mac in state.banned_macs:
                            await asyncio.to_thread(unban_mac, session.mac)
                        else:
                            await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)
                    elif self.op == "q1_block":
                        await asyncio.to_thread(ban_mac, session.mac, "onboarding")
                    elif self.op == "q2_whitelist":
                        await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)
                    else:
                        logger.error(f"Unknown FirewallRetry op {self.op} for {session.mac}")
                        raise RuntimeError(f"unknown op {self.op}")
            except Exception as e:
                await _handle_router_failure(session, interaction, self.op, e)
                return
            # Success — advance as original flow
            if self.op == "q1_allow":
                session.step = "q2"
                session.context = "allowed"
                try:
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
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED (retry success).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                    drop_session(session.mac)
                else:
                    session.step = "q3"
                    try:
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
                    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}) (retry).")
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
        finally:
            session.busy = False
            self.stop()


class RenameRetryView(discord.ui.View):

    def __init__(self, session: OnboardingSession, ip: str, new_name: str):
        super().__init__(timeout=300)
        self.session = session
        self.ip = ip
        self.new_name = new_name
        self.step_tag = session.step

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _admin_gate(interaction)

    async def on_timeout(self):
        session = self.session
        if _sessions.get(session.mac) is not session or session.step != self.step_tag or session.busy:
            if session.busy:
                logger.debug(f"RenameRetryView on_timeout suppressed for {session.mac}: busy")
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
            logger.warning(f"Could not edit timed-out rename retry message for {session.mac}: {e}")
        drop_session(session.mac)

    @discord.ui.button(label="🔄 Retry", style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        session = self.session
        attempt = session.retry_count + 1
        if attempt < 1:
            attempt = 1
        if attempt > session.max_retries:
            attempt = session.max_retries
        placeholder = discord.Embed(
            title=f"`⏳` Retrying… (attempt {attempt}/{session.max_retries})",
            description=_info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]) + f"\nRetrying rename… (attempt {attempt}/{session.max_retries})",
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
                success, err = await set_static_hostname(session.mac, self.ip, self.new_name)
            except Exception as e:
                success, err = False, str(e)
            if not success:
                await _handle_rename_failure(session, interaction, self.ip, self.new_name, err)
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
                logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, renamed={self.new_name}) (retry).")
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_allowed_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            else:
                logger.info(f"Onboarding complete: {session.mac} BLOCKED (renamed={self.new_name}) (retry).")
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_blocked_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        finally:
            session.busy = False
            self.stop()

    @discord.ui.button(label="❓ Keep Unknown", style=discord.ButtonStyle.secondary)
    async def keep_unknown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        session = self.session
        # Keep Unknown has no background I/O — single edit pattern but follow the disable->ack flow
        # For retry view, we already disabled, so ack via response.edit_message placeholder then finish via followup
        # To keep single-interaction semantics, do response.edit_message with processing then followup to final ack
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
            logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, keep unknown via retry).")
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_allowed_ack_embed(session),
                    view=None,
                )
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
        else:
            logger.info(f"Onboarding complete: {session.mac} BLOCKED (keep unknown via retry).")
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


async def _handle_router_failure(
    session: OnboardingSession,
    interaction: discord.Interaction,
    op: str,
    error: Exception,
) -> bool:
    """Handle router failure for firewall ops. Returns True if session dropped (permanent), False if retry shown."""
    logger.error(f"Router {op} failed for {session.mac}: {error}")
    session.retry_count += 1
    if session.retry_count >= session.max_retries:
        try:
            await interaction.followup.edit_message(
                message_id=session.message.id,
                embed=_permanent_failure_embed(session),
                view=None,
            )
        except Exception as e2:
            logger.error(f"Failed to send permanent failure for {session.mac}: {e2}")
        drop_session(session.mac)
        return True
    remaining = session.max_retries - session.retry_count
    error_embed = discord.Embed(
        title="`⚠️` Update Failed",
        description=_info_box([
            ("Device:", _device_name(session.mac)),
            ("MAC:", session.mac),
        ]) + f"\nRouter update failed: {error}\n{remaining} retries remaining.",
        color=_COLOR_BLOCK,
    )
    retry_view = FirewallRetryView(session, op)
    try:
        await interaction.followup.edit_message(
            message_id=session.message.id,
            embed=error_embed,
            view=retry_view,
        )
    except Exception as e2:
        logger.error(f"Failed to show retry for {session.mac}: {e2}")
    return False


async def _handle_rename_failure(
    session: OnboardingSession,
    interaction: discord.Interaction,
    ip: str,
    new_name: str,
    error: str,
) -> bool:
    """Handle rename failure — increments retry_count, shows retry or permanent failure."""
    logger.error(f"Onboarding rename failed for {session.mac} -> {new_name} ({ip}): {error}")
    session.retry_count += 1
    if session.retry_count >= session.max_retries:
        try:
            await interaction.followup.edit_message(
                message_id=session.message.id,
                embed=_permanent_failure_embed(session),
                view=None,
            )
        except Exception as e2:
            logger.error(f"Failed to send permanent failure for {session.mac}: {e2}")
        drop_session(session.mac)
        return True
    remaining = session.max_retries - session.retry_count
    error_embed = discord.Embed(
        title="`⚠️` Update Failed",
        description=_info_box([
            ("Device:", _device_name(session.mac)),
            ("MAC:", session.mac),
        ]) + f"\nRouter rename failed: {error}\n{remaining} retries remaining.",
        color=_COLOR_BLOCK,
    )
    retry_view = RenameRetryView(session, ip, new_name)
    try:
        await interaction.followup.edit_message(
            message_id=session.message.id,
            embed=error_embed,
            view=retry_view,
        )
    except Exception as e2:
        logger.error(f"Failed to show rename retry error for {session.mac}: {e2}")
    return False


async def _run_router_with_retry(
    session: OnboardingSession,
    interaction: discord.Interaction,
    op: str,
    router_coro,
    on_success,
):
    """Shared helper: run router_coro, on failure show retry view, on success run on_success.
    Returns True if success path taken, False if failure/retry/permanent."""
    try:
        await router_coro()
    except Exception as e:
        await _handle_router_failure(session, interaction, op, e)
        return False
    try:
        await on_success()
    except Exception as e:
        logger.error(f"Failed to advance after {op} for {session.mac}: {e}")
    return True


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
            description=_info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]) + "\nPlease wait…",
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
            # DB + speculative pending discard
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
                            description=_info_box([
                                ("Device:", _device_name(session.mac)),
                                ("MAC:", session.mac),
                            ]) + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                            color=_COLOR_BLOCK,
                        ),
                        view=None,
                    )
                except Exception as e2:
                    logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                drop_session(session.mac)
                return
            async def _router_work():
                async with ROUTER_LOCK:
                    if session.mac in state.banned_macs:
                        await asyncio.to_thread(unban_mac, session.mac)
                    else:
                        await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)

            async def _on_success():
                session.step = "q2"
                session.context = "allowed"
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    embed=_whitelist_question_embed(session),
                    view=OnboardingQ2View(session),
                )

            ok = await _run_router_with_retry(session, interaction, "q1_allow", _router_work, _on_success)
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
            description=_info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]) + "\nPlease wait…",
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
                            description=_info_box([
                                ("Device:", _device_name(session.mac)),
                                ("MAC:", session.mac),
                            ]) + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                            color=_COLOR_BLOCK,
                        ),
                        view=None,
                    )
                except Exception as e2:
                    logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                drop_session(session.mac)
                return
            async def _router_work():
                async with ROUTER_LOCK:
                    await asyncio.to_thread(ban_mac, session.mac, "onboarding")

            async def _on_success():
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

            ok = await _run_router_with_retry(session, interaction, "q1_block", _router_work, _on_success)
            if not ok:
                return
        finally:
            session.busy = False
            self.stop()


class OnboardingQ2View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q2")

    @discord.ui.button(label="✅ Yes / Whitelist", style=discord.ButtonStyle.success)
    async def whitelist_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            description=_info_box([
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]) + "\nPlease wait…",
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
                            description=_info_box([
                                ("Device:", _device_name(session.mac)),
                                ("MAC:", session.mac),
                            ]) + f"\nDatabase error: {e}\nUse /pending to retry manually.",
                            color=_COLOR_BLOCK,
                        ),
                        view=None,
                    )
                except Exception as e2:
                    logger.error(f"Failed to show DB error for {session.mac}: {e2}")
                drop_session(session.mac)
                return

            async def _router_work():
                async with ROUTER_LOCK:
                    await asyncio.to_thread(enable_lockdown, force_lock=state.lockdown_state)

            async def _on_success():
                session.whitelisted = True
                session.step = "q3"
                if _device_name(session.mac).lower() != "unknown":
                    session.step = "done"
                    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}).")
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

            ok = await _run_router_with_retry(session, interaction, "q2_whitelist", _router_work, _on_success)
            if not ok:
                return
        finally:
            session.busy = False
            self.stop()

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.secondary)
    async def whitelist_no(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}).")
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
    async def enter_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        for c in self.children:
            c.disabled = True
        await interaction.response.send_modal(RenameModal(self.session))

    @discord.ui.button(label="❓ Keep Unknown", style=discord.ButtonStyle.secondary)
    async def keep_unknown(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, keep unknown).")
            try:
                await interaction.response.edit_message(embed=_allowed_ack_embed(session), view=None)
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        else:
            session.step = "done"
            logger.info(f"Onboarding complete: {session.mac} BLOCKED (keep unknown).")
            try:
                await interaction.response.edit_message(embed=_blocked_ack_embed(session), view=None)
            except Exception as e:
                logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        self.stop()


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
                    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, empty name keep unknown).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                else:
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED (empty name).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                drop_session(session.mac)
                return

            from router.static_leases import is_valid_hostname, set_static_hostname, resolve_ip_for_mac
            if not is_valid_hostname(new_name):
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        content=f"`❌` Invalid hostname '{new_name}'. Use 1-32 alphanumeric/hyphen characters.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(f"Failed to edit message for invalid hostname {session.mac}")
                session.step = "done"
                if session.context == "allowed":
                    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, invalid hostname keep).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                else:
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED (invalid hostname).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
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
                    logger.warning(f"Failed to edit message for unresolved IP {session.mac}")
                session.step = "done"
                if session.context == "allowed":
                    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, no IP).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_allowed_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                else:
                    logger.info(f"Onboarding complete: {session.mac} BLOCKED (no IP).")
                    try:
                        await interaction.followup.edit_message(
                            message_id=session.message.id,
                            embed=_blocked_ack_embed(session),
                            view=None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
                drop_session(session.mac)
                return

            success, err = await set_static_hostname(session.mac, ip, new_name)
            if not success:
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
                logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}, renamed={new_name}).")
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_allowed_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            else:
                logger.info(f"Onboarding complete: {session.mac} BLOCKED (renamed={new_name}).")
                try:
                    await interaction.followup.edit_message(
                        message_id=session.message.id,
                        embed=_blocked_ack_embed(session),
                        view=None,
                    )
                except Exception as e:
                    logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")
            drop_session(session.mac)
        except asyncio.CancelledError:
            try:
                drop_session(session.mac)
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error(f"Unexpected error in RenameModal for {session.mac}: {e}", exc_info=True)
            try:
                await interaction.followup.edit_message(
                    message_id=session.message.id,
                    content=f"`❌` Unexpected error during rename: {e}",
                    embed=None,
                    view=None,
                )
            except Exception:
                logger.warning(f"Failed to edit unexpected error message for {session.mac}")
            try:
                if session.mac in _sessions:
                    drop_session(session.mac)
            except Exception:
                pass
        finally:
            session.busy = False
            if session.mac in _sessions and session.step != "done":
                logger.warning(f"RenameModal finally cleanup for orphaned session {session.mac}")
                try:
                    drop_session(session.mac)
                except Exception:
                    pass


async def start_onboarding(bot_instance, mac: str, hostname: str, ip: str = None, rssi_dbm: int | None = None, distance_m: float | None = None, quality_pct: int | None = None):
    """Open the question chain for a freshly discovered (already PENDING) MAC."""

    mac = mac.upper()
    if mac in _sessions:
        return
    channel = bot_instance.get_channel(CHANNEL_ID)
    if not channel:
        logger.warning(
            f"No admin channel found — skipping onboarding UI for {mac} "
            f"(stays BLOCKED; resolve via /wl add or /blk)."
        )
        return

    if not ip:
        try:
            from router.static_leases import resolve_ip_for_mac
            ip = resolve_ip_for_mac(mac)
        except Exception:
            pass
    session = OnboardingSession(mac, hostname, ip, rssi_dbm, distance_m, quality_pct)
    view = OnboardingQ1View(session)
    try:
        message = await channel.send(embed=_kickoff_embed(session), view=view)
    except Exception as e:
        logger.error(f"Could not post onboarding prompt for {mac}: {e}")
        return

    session.message = message
    _sessions[mac] = session
    logger.info(f"Onboarding started for {mac} ({hostname}).")
