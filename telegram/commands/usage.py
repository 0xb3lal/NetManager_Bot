import asyncio
from logger import logger
from state import state
import db
import usage_db
import telegram.db as telegram_db
import telegram.client as telegram_client
from services.traffic import get_today_usage_by_mac
from utils.traffic import format_data_size
from telegram.commands.register import command


@command("/usage")
async def handle_usage_command(chat_id: str, first_name: str = ""):
    """Reply to /usage with a clear breakdown of today's usage for the sender's device."""
    mac = telegram_db.get_mac_by_chat_id(chat_id)
    if not mac:
        await telegram_client.send_message(
            chat_id,
            "<b>`❌` This Telegram chat isn't linked to any device yet.</b>\n"
        )
        return

    await telegram_client.send_chat_action(chat_id, "typing")

    usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)
    usage_gb = usage_by_mac.get(mac, 0)

    effective_limit = db.get_effective_daily_limit(mac)
    extra_quota = usage_db.get_extra_quota(mac)
    base_limit = max(effective_limit - extra_quota, 0)  # what the device gets before any bonus

    device_name = state.macs_list.get(mac, mac)
    is_banned = mac in state.banned_macs
    is_whitelisted = mac in state.allowed_macs

    remaining_total = max(effective_limit - usage_gb, 0)
    used_from_extra = max(usage_gb - base_limit, 0)
    remaining_extra = max(extra_quota - used_from_extra, 0)

    if is_banned:
        status = "🚫 Blocked"
    elif usage_gb >= effective_limit:
        status = "⚠️ Over limit"
    else:
        status = "✅ Active"

    lines = [
        f"{'Device:'.ljust(13)} {device_name}",
        f"{'Status:'.ljust(13)} {status}",
        f"{'White listed:'.ljust(13)} {'Yes' if is_whitelisted else 'No'}",
        f"{'Used Today:'.ljust(13)} {format_data_size(usage_gb)}",
        f"{'Base Limit:'.ljust(13)} {format_data_size(base_limit)}",
    ]

    if extra_quota > 0:
        lines.append(f"{'Extra Added:'.ljust(13)} +{format_data_size(extra_quota)}")
        lines.append(f"{'Left of Extra:'.ljust(13)} {format_data_size(remaining_extra)}")

    lines.append(f"{'Remaining:'.ljust(13)} {format_data_size(remaining_total)}")

    text = "📊 <b>Your Usage Today</b>\n<pre>" + "\n".join(lines) + "</pre>"

    logger.info(f"Telegram /usage requested by {first_name or chat_id} for {device_name} ({mac}).")
    await telegram_client.send_message(chat_id, text)