from logger import logger
from state import state
from utils.traffic import format_data_size

import telegram.db as telegram_db
import telegram.client as telegram_client

USAGE_NOTIFY_THRESHOLDS = (50, 75, 100)


async def check_and_send_threshold_alerts(mac: str, device_name: str, usage_gb: float, effective_limit: float):
    """Send a Telegram alert the first time a device crosses each usage threshold today."""
    chat_id = telegram_db.get_device_chat_id(mac)
    if not chat_id:
        return

    percent = (usage_gb / effective_limit) * 100
    already_notified = telegram_db.get_notified_thresholds(mac)

    for threshold in USAGE_NOTIFY_THRESHOLDS:
        if percent < threshold or threshold in already_notified:
            continue

        telegram_db.mark_threshold_notified(mac, threshold)
        text = _build_alert_text(mac, device_name, usage_gb, effective_limit, threshold)

        sent = await telegram_client.send_message(chat_id, text)
        if sent:
            logger.info(f"Sent {threshold}% usage alert to {device_name} ({mac}).")


def _build_alert_text(mac: str, device_name: str, usage_gb: float, effective_limit: float, threshold: int) -> str:
    remaining_gb = max(effective_limit - usage_gb, 0)

    if threshold >= 100:
        if mac in state.allowed_macs:
            return (
                f"⚠️ <b>{device_name}</b>\n"
                f"You've used 100% of your daily limit ({format_data_size(effective_limit)}).\n"
                f"This device is on the whitelist, so it won't be blocked automatically."
            )
        return (
            f"🚫 <b>{device_name}</b>\n"
            f"You've used 100% of your daily limit ({format_data_size(effective_limit)}).\n"
            f"Your internet access has been automatically blocked."
        )

    return (
        f"📶 <b>{device_name}</b>\n"
        f"You've used {threshold}% of your daily limit.\n"
        f"Usage: {format_data_size(usage_gb)} of {format_data_size(effective_limit)}\n"
        f"Remaining: {format_data_size(remaining_gb)}"
    )