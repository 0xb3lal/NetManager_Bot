import importlib

from logger import logger
from services.onboarding_sessions import (
    ONBOARDING_STEP_TIMEOUT,
    OnboardingSession,
    _device_gone,
    _device_name,
    _gone_notice,
    _sessions,
    drop_session,
)

__all__ = [
    "OnboardingSession",
    "_sessions",
    "drop_session",
    "ONBOARDING_STEP_TIMEOUT",
    "RenameModal",
    "start_onboarding",
]


async def start_onboarding(
    bot_instance,
    mac: str,
    hostname: str,
    ip: str = None,
    rssi_dbm: int | None = None,
    distance_m: float | None = None,
    quality_pct: int | None = None,
):
    """Open the question chain for a freshly discovered (already PENDING) MAC.

    Defined at module level with local View/Embed imports to break the
    shim ↔ views cycle (shim → views would be import-time cycle via
    router/devices.py:9 → services/onboarding). Deferring to call-time
    keeps boot clean; mirrors tasks/anomaly_check.py lazy view instantiation.
    """
    # Local imports — break cycle services → commands/views (see §3)
    from commands.views.onboarding_embeds import _kickoff_embed
    from commands.views.onboarding_views import OnboardingQ1View
    from config import CHANNEL_ID
    from router.static_leases import resolve_ip_for_mac

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


def __getattr__(name: str):
    """Lazy re-exports for names that moved.

    Only fires for names NOT found normally — so start_onboarding (defined
    above) never hits this, avoiding the dead-branch bug. Keep only genuinely
    lazy names here.
    """
    # Views / Modals / Retry Views (presentation layer)
    if name == "RenameModal":
        mod = importlib.import_module("commands.views.onboarding_modals")
        return getattr(mod, name)
    if name in (
        "OnboardingQ1View",
        "OnboardingQ2View",
        "OnboardingQ3View",
        "_OnboardingBaseView",
    ):
        mod = importlib.import_module("commands.views.onboarding_views")
        return getattr(mod, name)
    if name in ("FirewallRetryView", "RenameRetryView"):
        mod = importlib.import_module("commands.views.onboarding_retry_views")
        return getattr(mod, name)
    # Retry helpers (services layer, but lazy to keep shim leaf)
    if name in (
        "_handle_router_failure",
        "_handle_rename_failure",
        "_run_router_with_retry",
    ):
        mod = importlib.import_module("services.onboarding_retry")
        return getattr(mod, name)
    # Embeds / helpers
    if name in (
        "_info_box",
        "_kickoff_embed",
        "_whitelist_question_embed",
        "_name_question_embed",
        "_allowed_ack_embed",
        "_blocked_ack_embed",
        "_permanent_failure_embed",
        "_processing_embed",
        "_COLOR_NEW",
        "_COLOR_OK",
        "_COLOR_BLOCK",
    ):
        mod = importlib.import_module("commands.views.onboarding_embeds")
        return getattr(mod, name)
    # Session helpers (fallback — though top-level already covers main ones)
    if name in (
        "ONBOARDING_STEP_TIMEOUT",
        "OnboardingSession",
        "_sessions",
        "drop_session",
        "_device_gone",
        "_gone_notice",
        "_device_name",
    ):
        mod = importlib.import_module("services.onboarding_sessions")
        return getattr(mod, name)
    # Old _admin_gate → new utils/discord.ensure_admin (alias)
    if name == "_admin_gate":
        mod = importlib.import_module("utils.discord")
        return getattr(mod, "ensure_admin")
    if name == "ensure_admin":
        mod = importlib.import_module("utils.discord")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
