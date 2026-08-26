import asyncio

import discord
from discord.ext import tasks

import db
from config import (
    CHANNEL_ID,
    UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB,
    ANOMALY_CHECK_INTERVAL_MINUTES,
)
from logger import logger
from state import state, ROUTER_LOCK, acquire_router_lock_bounded
from router.firewall import ban_mac, unban_mac
from services.traffic import get_today_usage_by_mac
from utils.traffic import format_data_size

ANOMALY_PROMPT_TIMEOUT = 1800  # seconds the Recognized/Suspicious question waits


def setup_anomaly_check_task(bot):
    """Create and configure the unknown-hostname traffic anomaly task."""

    @tasks.loop(minutes=ANOMALY_CHECK_INTERVAL_MINUTES)
    async def anomaly_check_task():
        """Flag unknown-hostname devices whose traffic crosses the threshold.

        Default action while awaiting an admin reply: BLOCK immediately;
        unblock only if the device is confirmed as recognized/allowed.
        """
        logger.info("Unknown-hostname anomaly check TRIGGERED — starting scan...")

        threshold_gb = UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB / 1024

        # --- Phase 1: router I/O only, under the lock ---
        if not await acquire_router_lock_bounded("Unknown-hostname anomaly check"):
            return
        try:
            usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)
        except Exception as e:
            logger.error(f"Error fetching usage snapshot in anomaly check: {e}")
            return
        finally:
            ROUTER_LOCK.release()

        try:
            # --- Phase 2 (no lock): eligibility ---
            # Pending-onboarding devices are skipped entirely: they are still
            # mid-questionnaire (and already firewall-blocked by default).
            # A banned device whose question was never successfully delivered
            # stays eligible, so the prompt is retried on the next cycle.
            candidates = []
            for mac, usage_gb in usage_by_mac.items():
                if usage_gb <= threshold_gb:
                    continue
                if state.macs_list.get(mac, "unknown").lower() != "unknown":
                    continue
                if not db.device_exists(mac):
                    continue  # ghost mapping, no live device row
                if mac in state.pending_macs or db.is_onboarding_pending(mac):
                    continue
                if mac in state.allowed_macs:
                    continue
                if mac in state.banned_macs and db.is_anomaly_handled(mac):
                    continue
                candidates.append((mac, usage_gb))

            channel = bot.get_channel(CHANNEL_ID)

            for mac, usage_gb in candidates:
                # --- Phase 3 (short lock): block first, ask second ---
                # Re-verify under the lock: admins may have resolved the
                # device while this cycle was scanning.
                async with ROUTER_LOCK:
                    if (
                        mac in state.allowed_macs
                        or mac in state.pending_macs
                    ):
                        logger.debug(f"Anomaly skipped {mac} (state changed since scan).")
                        continue
                    if mac not in state.banned_macs:
                        # ban_mac() itself rebuilds the firewall rules.
                        await asyncio.to_thread(ban_mac, mac, "unknown_anomaly")

                if not channel:
                    logger.warning(
                        f"Anomaly: {mac} blocked but no admin channel found — "
                        f"prompt NOT delivered and will be retried next cycle."
                    )
                    continue

                # --- Phase 4 (no lock): deliver the review question ---
                view = AnomalyView(mac)
                embed = _anomaly_embed(mac, usage_gb, threshold_gb)
                try:
                    message = await channel.send(embed=embed, view=view)
                except Exception as e:
                    # Not marked as handled: the prompt retries next cycle.
                    logger.error(f"Failed to post anomaly prompt for {mac}: {e}")
                    continue

                view.message = message
                db.mark_anomaly_handled(mac)
                logger.info(
                    f"Anomaly block: {mac} exceeded {format_data_size(threshold_gb)} "
                    f"with an unknown hostname ({format_data_size(usage_gb)} used). "
                    f"Blocked pending review."
                )

            logger.info("Unknown-hostname anomaly check completed.")
        except Exception as e:
            logger.error(f"Error in anomaly_check_task: {e}")


    @anomaly_check_task.before_loop
    async def before_anomaly_check():
        await bot.wait_until_ready()
        logger.info(
            "Unknown-hostname anomaly check started "
            f"(threshold {UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB} MB, "
            "blocks first and asks after)."
        )

    return anomaly_check_task


def _anomaly_embed(mac: str, usage_gb: float, threshold_gb: float) -> discord.Embed:
    embed = discord.Embed(
        title="`⚠️` Unknown Device Using Data",
        description=(
            f"```\n"
            f"{'Device:'.ljust(10)} unknown\n"
            f"{'MAC:'.ljust(10)} {mac}\n"
            f"{'Usage:'.ljust(10)} {format_data_size(usage_gb)}\n"
            f"{'Threshold:'.ljust(10)} {format_data_size(threshold_gb)}\n"
            f"```"
        ),
        color=0xE67E22,
    )
    embed.set_footer(text="Device blocked pending your review.")
    return embed


class AnomalyView(discord.ui.View):
    """Recognized -> unblock + whitelist. Suspicious -> stays blocked."""

    def __init__(self, mac: str):
        super().__init__(timeout=ANOMALY_PROMPT_TIMEOUT)
        self.mac = mac
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        if isinstance(user, discord.Member) and user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "`❌` Only administrators can answer this.", ephemeral=True
        )
        return False

    def _device_gone(self) -> bool:
        return not db.device_exists(self.mac)

    async def on_timeout(self):
        # The device is already blocked; nothing to revert. Flag stays set,
        # so this question is never re-fired automatically.
        if self.message:
            try:
                await self.message.edit(
                    content=(
                        f"`⌛` No answer — `{self.mac}` remains BLOCKED "
                        f"(resolve manually via /rm)."
                    ),
                    embed=None,
                    view=None,
                )
            except Exception as e:
                logger.warning(f"Could not edit timed-out anomaly prompt for {self.mac}: {e}")

    @discord.ui.button(label="✅ Recognized — Allow", style=discord.ButtonStyle.success)
    async def recognized(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._device_gone():
            await self._gone_notice(interaction)
            return
        await interaction.response.defer()

        async with ROUTER_LOCK:
            # Recognizing an unnamed device means exempting it from both
            # auto-block systems (whitelist semantics) and lifting the ban.
            db.set_device_allowed(self.mac, True)
            if self.mac not in state.allowed_macs:
                state.allowed_macs.append(self.mac)
            if self.mac in state.banned_macs:
                await asyncio.to_thread(unban_mac, self.mac)   # rebuilds firewall itself
            else:
                await asyncio.to_thread(
                    enable_lockdown, force_lock=state.lockdown_state
                )

        logger.info(f"Anomaly resolved by admin: {self.mac} recognized and allowed.")

        embed = discord.Embed(
            title="`✅` Device Verified — Allowed",
            description=(
                f"```\n"
                f"{'Device:'.ljust(10)} {state.macs_list.get(self.mac, 'unknown')}\n"
                f"{'MAC:'.ljust(10)} {self.mac}\n"
                f"```"
            ),
            color=0x2ECC71,
        )
        embed.set_footer(text="Applies immediately.")
        await self.message.edit(embed=embed, view=None)

    @discord.ui.button(label="🚫 Suspicious — Keep Blocked", style=discord.ButtonStyle.danger)
    async def suspicious(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._device_gone():
            await self._gone_notice(interaction)
            return
        await interaction.response.defer()

        logger.info(f"Anomaly resolved by admin: {self.mac} kept blocked (suspicious).")

        embed = discord.Embed(
            title="`🚫` Kept Blocked (Suspicious)",
            description=(
                f"```\n"
                f"{'Device:'.ljust(10)} {state.macs_list.get(self.mac, 'unknown')}\n"
                f"{'MAC:'.ljust(10)} {self.mac}\n"
                f"{'Status:'.ljust(10)} Blocked\n"
                f"```"
            ),
            color=0xFF4747,
        )
        embed.set_footer(text="Use /rm to unblock it later if this was a mistake.")
        await self.message.edit(embed=embed, view=None)

    async def _gone_notice(self, interaction: discord.Interaction):
        try:
            await self.message.edit(
                content=(
                    f"`⚠️` Device record for `{self.mac}` no longer exists — "
                    f"prompt closed."
                ),
                embed=None,
                view=None,
            )
        except Exception as e:
            logger.warning(f"Could not update deleted-device anomaly prompt: {e}")
