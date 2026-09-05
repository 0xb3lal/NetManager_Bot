import telegram.client as telegram_client
import telegram.db as telegram_db
from logger import logger
from state import state
from utils.traffic import format_data_size

USAGE_NOTIFY_THRESHOLDS = (25, 50, 75, 100)

_THRESHOLD_STYLE = {
    25: ("🔵", "Usage Update"),
    50: ("📶", "Usage Alert"),
    75: ("⚠️", "Usage Alert"),
    100: ("🚫", "Limit Reached"),
}


async def check_and_send_threshold_alerts(
    mac: str, device_name: str, usage_gb: float, effective_limit: float
):
    """Alert on Telegram at 25/50/75/100%; mark notified only after confirmed send."""
    if effective_limit <= 0:
        # No meaningful percentage against a zero/unset limit — skip.
        return
    chat_id = telegram_db.get_device_chat_id(mac)
    if not chat_id:
        return

    percent = (usage_gb / effective_limit) * 100
    already_notified = telegram_db.get_notified_thresholds(mac)

    for threshold in USAGE_NOTIFY_THRESHOLDS:
        if percent < threshold or threshold in already_notified:
            continue

        text = _build_alert_text(mac, device_name, usage_gb, effective_limit, threshold)
        sent = await telegram_client.send_message(chat_id, text)

        if sent:
            telegram_db.mark_threshold_notified(mac, threshold)
            logger.info(f"Sent {threshold}% usage alert to {device_name} ({mac}).")
        else:
            logger.warning(
                f"Failed to send {threshold}% usage alert to {device_name} ({mac}) — "
                f"will retry on next check."
            )


def _build_alert_text(
    mac: str, device_name: str, usage_gb: float, effective_limit: float, threshold: int
) -> str:
    """Build usage alert text for Telegram."""
    remaining_gb = max(effective_limit - usage_gb, 0)
    icon, header = _THRESHOLD_STYLE.get(threshold, ("📶", "Usage Alert"))

    if threshold >= 100 and mac in state.allowed_macs:
        icon, header = "⚠️", "Limit Reached (Whitelisted)"

    lines = [
        f"{'Device:'.ljust(11)} {device_name}",
        f"{'Used:'.ljust(11)} {format_data_size(usage_gb)}",
        f"{'Limit:'.ljust(11)} {format_data_size(effective_limit)}",
        f"{'Remaining:'.ljust(11)} {format_data_size(remaining_gb)}",
    ]

    body = (
        f"{icon} <b>{header} — {threshold}%</b>\n"
        f"<pre>" + "\n".join(lines) + "</pre>"
    )

    if threshold >= 100:
        if mac in state.allowed_macs:
            body += "\nThis device is on the whitelist, so it won't be blocked automatically."
        else:
            body += "\n🔒 Your internet access has been automatically blocked."

    return body
