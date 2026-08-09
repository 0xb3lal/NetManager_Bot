import asyncio
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
    """Reply to /usage with the sender's device usage details for today."""
    mac = telegram_db.get_mac_by_chat_id(chat_id)
    if not mac:
        await telegram_client.send_message(
            chat_id,
            "`❌` This Telegram chat isn't linked to any device yet."
        )
        return

    usage_by_mac = await asyncio.to_thread(get_today_usage_by_mac)
    usage_gb = usage_by_mac.get(mac, 0)

    effective_limit = db.get_effective_daily_limit(mac)
    default_limit = db.get_daily_default_limit()
    extra_quota = usage_db.get_extra_quota(mac)
    device_name = state.macs_list.get(mac, mac)
    is_banned = mac in state.banned_macs
    is_whitelisted = mac in state.allowed_macs

    remaining_gb = max(effective_limit - usage_gb, 0)
    percent = (usage_gb / effective_limit * 100) if effective_limit > 0 else 0

    if is_banned:
        status = "🚫 Blocked (limit exceeded)"
    elif is_whitelisted:
        status = "✅ Active (whitelisted — never auto-blocked)"
    elif usage_gb >= effective_limit:
        status = "⚠️ Over limit"
    else:
        status = "✅ Active"

    lines = [
        f"{'Device:'.ljust(13)} {device_name}",
        f"{'Status:'.ljust(13)} {status}",
        f"{'Used Today:'.ljust(13)} {format_data_size(usage_gb)}",
        f"{'Base Limit:'.ljust(13)} {format_data_size(default_limit)}",
        f"{'Extra Quota:'.ljust(13)} {format_data_size(extra_quota)}",
        f"{'Total Limit:'.ljust(13)} {format_data_size(effective_limit)}",
        f"{'Remaining:'.ljust(13)} {format_data_size(remaining_gb)}",
        f"{'Used:'.ljust(13)} {percent:.0f}%",
    ]

    text = "📊 <b>Your Usage Today</b>\n<pre>" + "\n".join(lines) + "</pre>"
    await telegram_client.send_message(chat_id, text)