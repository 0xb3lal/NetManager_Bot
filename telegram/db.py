import logging

from db import get_db

logger = logging.getLogger(__name__)


def init_telegram_tables():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS device_telegram (
                mac     TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS threshold_notified (
                mac       TEXT NOT NULL,
                threshold INTEGER NOT NULL,
                PRIMARY KEY (mac, threshold)
            );
        """)
    logger.info("Telegram tables initialized.")


# ========= CHAT MAPPING =========
def get_device_chat_id(mac: str) -> str | None:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT chat_id FROM device_telegram WHERE mac = ?", (mac,)
        ).fetchone()
    return row["chat_id"] if row else None


def set_device_chat_id(mac: str, chat_id: str):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO device_telegram (mac, chat_id) VALUES (?, ?)",
                (mac, chat_id)
            )
        logger.info(f"Telegram chat_id for {mac} set to {chat_id}")
    except Exception as e:
        logger.error(f"Error saving telegram chat_id for {mac}: {e}")


def remove_device_chat_id(mac: str) -> bool:
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM device_telegram WHERE mac = ?", (mac,))
            deleted = conn.execute("SELECT changes() as c").fetchone()["c"]
        return bool(deleted)
    except Exception as e:
        logger.error(f"Error removing telegram chat_id for {mac}: {e}")
        return False


# ========= THRESHOLD NOTIFICATION DEDUP (50% / 75% / 100%) =========
def get_notified_thresholds(mac: str) -> set:
    mac = mac.upper()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT threshold FROM threshold_notified WHERE mac = ?", (mac,)
        ).fetchall()
    return {row["threshold"] for row in rows}


def mark_threshold_notified(mac: str, threshold: int):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO threshold_notified (mac, threshold) VALUES (?, ?)",
                (mac, threshold)
            )
    except Exception as e:
        logger.error(f"Error marking {threshold}% threshold for {mac}: {e}")


def clear_all_threshold_notifications():
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM threshold_notified")
        logger.info("Usage threshold notification flags cleared.")
    except Exception as e:
        logger.error(f"Error clearing threshold notifications: {e}")

def reset_notified_thresholds(mac: str):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM threshold_notified WHERE mac = ?", (mac,))
        logger.info(f"Reset usage threshold notification flags for {mac}.")
    except Exception as e:
        logger.error(f"Error resetting threshold notifications for {mac}: {e}")

def get_mac_by_chat_id(chat_id: str) -> str | None:
    """Reverse lookup: given a Telegram chat_id, find which device it's linked to."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT mac FROM device_telegram WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
    return row["mac"] if row else None