import discord

from services.onboarding_sessions import _device_name

_COLOR_NEW = 0xF39C12
_COLOR_OK = 0x2ECC71
_COLOR_BLOCK = 0xFF4747


def _info_box(lines: list[tuple[str, str]]) -> str:
    body = "\n".join(f"{label.ljust(10)} {value}" for label, value in lines)
    return f"```\n{body}\n```"


def _kickoff_embed(session) -> discord.Embed:
    from config import DISTANCE_ESTIMATION_ENABLED, RSSI_DISPLAY_ENABLED

    lines = [
        ("Device:", session.hostname),
        ("MAC:", session.mac),
        ("Status:", "Blocked until reviewed"),
    ]
    if RSSI_DISPLAY_ENABLED:
        if session.rssi_dbm is not None and session.quality_pct is not None:
            lines.append(("Signal:", f"📶 {session.quality_pct}%"))
        else:
            lines.append(("Signal:", "— (wired)"))
        if DISTANCE_ESTIMATION_ENABLED and session.distance_m is not None:
            lines.append(("Distance:", f"📏 ~{session.distance_m:.1f} m"))
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


def _whitelist_question_embed(session) -> discord.Embed:
    embed = discord.Embed(
        title="`⭐` Add to Whitelist?",
        description=(
            _info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + "\nAdd this device to the whitelist?\nWhitelisted devices bypass daily caps and survive system-wide lockdowns."
        ),
        color=_COLOR_NEW,
    )
    embed.set_footer(text="You can change this later with /wl add or /wl remove.")
    return embed


def _name_question_embed(session) -> discord.Embed:
    embed = discord.Embed(
        title="`✏️` Name This Device",
        description=(
            _info_box(
                [
                    ("Device:", _device_name(session.mac)),
                    ("MAC:", session.mac),
                ]
            )
            + "\nAssign a custom name now?"
        ),
        color=_COLOR_NEW,
    )
    embed.set_footer(text="Naming helps identify the device in lists later.")
    return embed


def _allowed_ack_embed(session) -> discord.Embed:
    whitelist_value = (
        "Yes — bypasses daily caps, survives lockdowns" if session.whitelisted else "No"
    )
    embed = discord.Embed(
        title="`✅` Device Allowed",
        description=_info_box(
            [
                ("Device:", session.named or _device_name(session.mac)),
                ("MAC:", session.mac),
                ("Whitelisted:", whitelist_value),
            ]
        ),
        color=_COLOR_OK,
    )
    embed.set_footer(
        text="Whitelisted devices bypass daily caps and survive lockdowns. Applies immediately."
    )
    return embed


def _blocked_ack_embed(session) -> discord.Embed:
    embed = discord.Embed(
        title="`🚫` Device Blocked",
        description=_info_box(
            [
                ("Device:", session.named or _device_name(session.mac)),
                ("MAC:", session.mac),
                ("Status:", "Blocked"),
            ]
        ),
        color=_COLOR_BLOCK,
    )
    embed.set_footer(text="Use /rm to unblock it later.")
    return embed


def _permanent_failure_embed(session) -> discord.Embed:
    return discord.Embed(
        title="`❌` Setup Failed — Contact Admin",
        description=_info_box(
            [
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]
        )
        + "\nSetup failed after 3 attempts. Please contact an administrator.",
        color=_COLOR_BLOCK,
    )


def _processing_embed(session, title: str = "Processing…") -> discord.Embed:
    return discord.Embed(
        title=f"`⏳` {title}",
        description=_info_box(
            [
                ("Device:", _device_name(session.mac)),
                ("MAC:", session.mac),
            ]
        )
        + f"\n{title}",
        color=_COLOR_NEW,
    )
