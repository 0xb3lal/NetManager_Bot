"""
services/onboarding_retry.py

Extracted from services/onboarding.py:572-673 (Phase B).

Pure orchestration helpers for retry — no discord.Embed / discord.ui.View
at top-level. All View/Embed creation is deferred to local imports inside
helpers to break the two cycles:
  services/onboarding_retry ↔ commands/views/onboarding_retry_views
  services/onboarding_retry ↔ commands/views/onboarding_embeds
as specified in §3 (local imports break cycle).
"""

import asyncio  # kept for symmetry with original, not strictly needed here

from logger import logger
from services.onboarding_sessions import drop_session


async def _handle_router_failure(
    session,
    interaction,
    op: str,
    error: Exception,
) -> bool:
    """Handle router failure for firewall ops. Returns True if session dropped (permanent), False if retry shown."""
    # Local imports — break cycle services ↔ views/embeds
    from commands.views.onboarding_embeds import _permanent_failure_embed, _info_box, _COLOR_BLOCK
    from services.onboarding_sessions import _device_name
    import discord

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
    # Local import — breaks cycle with retry Views
    from commands.views.onboarding_retry_views import FirewallRetryView
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
    session,
    interaction,
    ip: str,
    new_name: str,
    error: str,
) -> bool:
    """Handle rename failure — increments retry_count, shows retry or permanent failure."""
    from commands.views.onboarding_embeds import _permanent_failure_embed, _info_box, _COLOR_BLOCK
    from services.onboarding_sessions import _device_name
    import discord

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
    from commands.views.onboarding_retry_views import RenameRetryView
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
    session,
    interaction,
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
