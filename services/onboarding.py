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

    def __init__(self, mac: str, hostname: str, ip: str = None):
        self.mac = mac
        self.hostname = hostname
        self.ip = ip
        self.step = "q1"          # q1 -> q2/q3 -> done
        self.context = None       # "allowed" | "blocked" once Q1 answered
        self.whitelisted = False
        self.named = None         # custom name if one was assigned
        self.message = None       # the Discord message carrying the current question


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
    embed = discord.Embed(
        title="`🆕` New Device Detected — Review Required",
        description=_info_box([
            ("Device:", session.hostname),
            ("MAC:", session.mac),
            ("Status:", "Blocked until reviewed"),
        ]),
        color=_COLOR_NEW,
    )
    embed.set_footer(text="This device is blocked. Answer below to finish setup.")
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
        if _sessions.get(session.mac) is not session or session.step != self.step_tag:
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


class OnboardingQ1View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q1")

    @discord.ui.button(label="✅ Allow", style=discord.ButtonStyle.success)
    async def allow(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if _device_gone(session):
            await _gone_notice(session)
            return
        await interaction.response.defer()

        # Q1 resolves through the ORDINARY BAN SYSTEM only — it must never
        # touch the whitelist (state.allowed_macs / devices.allowed). The
        # whitelist means "exempt from both auto-block systems" and is
        # exclusively Q2's decision.
        async with ROUTER_LOCK:
            state.pending_macs.discard(session.mac)
            db.set_onboarding_confirmed(session.mac)
            if session.mac in state.banned_macs:
                await asyncio.to_thread(unban_mac, session.mac)   # rebuilds firewall itself
            else:
                await asyncio.to_thread(
                    enable_lockdown, force_lock=state.lockdown_state
                )

        session.step = "q2"
        session.context = "allowed"

        # Q2 (whitelist) is always asked when allowed; only Q3 depends on
        # the reported hostname being unknown.
        await session.message.edit(embed=_whitelist_question_embed(session), view=OnboardingQ2View(session))

    @discord.ui.button(label="🚫 Block", style=discord.ButtonStyle.danger)
    async def block(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if _device_gone(session):
            await _gone_notice(session)
            return
        await interaction.response.defer()

        async with ROUTER_LOCK:
            state.pending_macs.discard(session.mac)
            db.set_onboarding_confirmed(session.mac)
            await asyncio.to_thread(ban_mac, session.mac, "onboarding")   # rebuilds when newly banned

        session.context = "blocked"

        hostname = _device_name(session.mac)
        if hostname.lower() != "unknown":
            await _finish_blocked(session)
        else:
            session.step = "q3"
            await session.message.edit(embed=_name_question_embed(session), view=OnboardingQ3View(session))


class OnboardingQ2View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q2")

    @discord.ui.button(label="✅ Yes / Whitelist", style=discord.ButtonStyle.success)
    async def whitelist_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if _device_gone(session):
            await _gone_notice(session)
            return
        await interaction.response.defer()

        async with ROUTER_LOCK:
            db.set_device_allowed(session.mac, True)
            if session.mac not in state.allowed_macs:
                state.allowed_macs.append(session.mac)
            await asyncio.to_thread(
                enable_lockdown, force_lock=state.lockdown_state
            )
        session.whitelisted = True
        session.step = "q3"

        if _device_name(session.mac).lower() != "unknown":
            await _finish_allowed(session)
        else:
            await session.message.edit(embed=_name_question_embed(session), view=OnboardingQ3View(session))

    @discord.ui.button(label="❌ No", style=discord.ButtonStyle.secondary)
    async def whitelist_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if _device_gone(session):
            await _gone_notice(session)
            return
        await interaction.response.defer()

        session.whitelisted = False
        session.step = "q3"

        if _device_name(session.mac).lower() != "unknown":
            await _finish_allowed(session)
        else:
            await session.message.edit(embed=_name_question_embed(session), view=OnboardingQ3View(session))


class OnboardingQ3View(_OnboardingBaseView):

    def __init__(self, session: OnboardingSession):
        super().__init__(session, step_tag="q3")

    @discord.ui.button(label="✏️ Enter Name", style=discord.ButtonStyle.primary)
    async def enter_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RenameModal(self.session))

    @discord.ui.button(label="❓ Keep Unknown", style=discord.ButtonStyle.secondary)
    async def keep_unknown(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.session
        if _device_gone(session):
            await _gone_notice(session)
            return
        await interaction.response.defer()
        if session.context == "allowed":
            await _finish_allowed(session)
        else:
            await _finish_blocked(session)


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
            await _gone_notice(session)
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

        try:
            new_name = str(self.name_input.value).strip()
            if not new_name:
                if session.context == "allowed":
                    await _finish_allowed(session)
                else:
                    await _finish_blocked(session)
                return

            from router.static_leases import is_valid_hostname, set_static_hostname, resolve_ip_for_mac
            if not is_valid_hostname(new_name):
                try:
                    await session.message.edit(
                        content=f"`❌` Invalid hostname '{new_name}'. Use 1-32 alphanumeric/hyphen characters.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(f"Failed to edit message for invalid hostname {session.mac}")
                if session.context == "allowed":
                    await _finish_allowed(session)
                else:
                    await _finish_blocked(session)
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
                    await session.message.edit(
                        content=f"`❌` Could not resolve IP for `{session.mac}`. Router rename failed — hostname unchanged. Use /rename with explicit IP.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(f"Failed to edit message for unresolved IP {session.mac}")
                if session.context == "allowed":
                    await _finish_allowed(session)
                else:
                    await _finish_blocked(session)
                return

            success, err = await set_static_hostname(session.mac, ip, new_name)
            if not success:
                logger.error(f"Onboarding rename failed for {session.mac} -> {new_name} ({ip}): {err}")
                try:
                    await session.message.edit(
                        content=f"`❌` Router rename failed for `{session.mac}`: {err}. Hostname unchanged.",
                        embed=None,
                        view=None,
                    )
                except Exception:
                    logger.warning(f"Failed to edit router failure message for {session.mac}")
                if session.context == "allowed":
                    await _finish_allowed(session)
                else:
                    await _finish_blocked(session)
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

            if session.context == "allowed":
                await _finish_allowed(session)
            else:
                await _finish_blocked(session)
        except asyncio.CancelledError:
            try:
                drop_session(session.mac)
            except Exception:
                pass
            raise
        except Exception as e:
            logger.error(f"Unexpected error in RenameModal for {session.mac}: {e}", exc_info=True)
            try:
                await session.message.edit(
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
            if session.mac in _sessions and session.step != "done":
                logger.warning(f"RenameModal finally cleanup for orphaned session {session.mac}")
                try:
                    drop_session(session.mac)
                except Exception:
                    pass


async def _finish_allowed(session: OnboardingSession):
    session.step = "done"
    drop_session(session.mac)
    logger.info(f"Onboarding complete: {session.mac} ALLOWED (whitelisted={session.whitelisted}).")
    try:
        await session.message.edit(embed=_allowed_ack_embed(session), view=None)
    except Exception as e:
        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")


async def _finish_blocked(session: OnboardingSession):
    session.step = "done"
    drop_session(session.mac)
    logger.info(f"Onboarding complete: {session.mac} BLOCKED.")
    try:
        await session.message.edit(embed=_blocked_ack_embed(session), view=None)
    except Exception as e:
        logger.error(f"Failed to send onboarding ack for {session.mac}: {e}")


async def start_onboarding(bot_instance, mac: str, hostname: str, ip: str = None):
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
    session = OnboardingSession(mac, hostname, ip)
    view = OnboardingQ1View(session)
    try:
        message = await channel.send(embed=_kickoff_embed(session), view=view)
    except Exception as e:
        logger.error(f"Could not post onboarding prompt for {mac}: {e}")
        return

    session.message = message
    _sessions[mac] = session
    logger.info(f"Onboarding started for {mac} ({hostname}).")
