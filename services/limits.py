import asyncio
import discord
import db
import usage_db
import telegram.db as telegram_db
import telegram.client as telegram_client
from config import CHANNEL_ID
from state import state, ROUTER_LOCK
from services.traffic import get_today_usage_by_mac
from router.firewall import unban_mac
from utils.traffic import format_data_size


async def recheck_device_after_limit_change(bot_instance, mac):
    """Recheck and auto-unban device if usage is now below new limit."""

    if mac not in state.banned_macs or db.get_ban_reason(mac) != "daily_limit":
        return

    async with ROUTER_LOCK:
        usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)

    usage_gb = usage_by_mac.get(mac, 0)
    effective_limit = db.get_effective_daily_limit(mac)

    if usage_gb >= effective_limit:
        return

    async with ROUTER_LOCK:
        await asyncio.to_thread(unban_mac, mac)

    device_name = state.macs_list.get(mac, mac)

    channel = bot_instance.get_channel(CHANNEL_ID)

    if channel:
        status_box = (
            f"```\n"
            f"{'Device:'.ljust(10)} {device_name}\n"
            f"{'Usage:'.ljust(10)} {format_data_size(usage_gb)}\n"
            f"{'New Limit:'.ljust(10)} {format_data_size(effective_limit)}\n"
            f"```"
        )

        embed = discord.Embed(
            title="`✅` Device Auto-Unblocked (Limit Increased)",
            description=status_box,
            color=0x2ecc71
        )

        await channel.send(embed=embed)


async def recheck_default_limit_devices(bot_instance):
    """Auto-unban devices that were limited by daily_limit but now under default."""

    daily_banned = db.get_banned_by_reason("daily_limit")
    overrides = db.get_all_device_daily_limits()

    candidates = [
        mac
        for mac in daily_banned
        if mac not in overrides
    ]

    if not candidates:
        return

    async with ROUTER_LOCK:
        usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)

    default_limit = db.get_daily_default_limit()

    channel = bot_instance.get_channel(CHANNEL_ID)

    for mac in candidates:

        usage_gb = usage_by_mac.get(mac, 0)

        if usage_gb >= default_limit:
            continue

        async with ROUTER_LOCK:
            await asyncio.to_thread(unban_mac, mac)

        device_name = state.macs_list.get(mac, mac)

        if channel:

            status_box = (
                f"```\n"
                f"{'Device:'.ljust(10)} {device_name}\n"
                f"{'Usage:'.ljust(10)} {format_data_size(usage_gb)}\n"
                f"{'New Limit:'.ljust(10)} {format_data_size(default_limit)}\n"
                f"```"
            )

            embed = discord.Embed(
                title="`✅` Device Auto-Unblocked (Limit Increased)",
                description=status_box,
                color=0x2ecc71
            )

            await channel.send(embed=embed)

async def add_extra_quota_covering_overage(bot_instance, mac, amount_gb):
    """
    Add extra quota to a device, guaranteeing the requested amount becomes real,
    additional usable data — even if the device is currently over its limit
    (e.g. already blocked). Any existing overage is absorbed first, so the
    device always ends up with exactly `amount_gb` of new headroom.
    Returns (new_extra_total, new_effective_limit).
    """
    async with ROUTER_LOCK:
        usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)

    usage_gb = usage_by_mac.get(mac, 0)
    current_effective_limit = db.get_effective_daily_limit(mac)
    current_extra = usage_db.get_extra_quota(mac)

    deficit = max(usage_gb - current_effective_limit, 0)
    new_extra_total = current_extra + amount_gb + deficit

    usage_db.set_extra_quota(mac, new_extra_total)
    telegram_db.reset_notified_thresholds(mac)
    new_effective_limit = db.get_effective_daily_limit(mac)

    await recheck_device_after_limit_change(bot_instance, mac)

    chat_id = telegram_db.get_device_chat_id(mac)
    if chat_id:
        device_name = state.macs_list.get(mac, mac)
        text = (
            "`➕` <b>Extra Quota Added</b>\n"
            f"<pre>\n"
            f"{'Device:'.ljust(12)} {device_name}\n"
            f"{'Added:'.ljust(12)} {format_data_size(amount_gb)}\n"
            f"{'Extra Today:'.ljust(12)} {format_data_size(new_extra_total)}\n"
            f"{'New Limit:'.ljust(12)} {format_data_size(new_effective_limit)}\n"
            f"</pre>"
        )
        await telegram_client.send_message(chat_id, text)

    return new_extra_total, new_effective_limit