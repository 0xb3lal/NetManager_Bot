import logging
from db import get_db

logger = logging.getLogger(__name__)

def init_usage_tables():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS extra_quota (
                mac      TEXT PRIMARY KEY,
                extra_gb REAL NOT NULL DEFAULT 0
            );
        """)
    logger.info("Usage quota table initialized.")


def get_extra_quota(mac: str) -> float:
    mac = mac.upper()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT extra_gb FROM extra_quota WHERE mac = ?", (mac,)
            ).fetchone()
        return float(row["extra_gb"]) if row else 0.0
    except Exception as e:
        logger.error(f"Error loading extra quota for {mac}: {e}")
        return 0.0


def add_extra_quota(mac: str, amount_gb: float) -> float | None:
    """Add (or subtract) to a device's extra quota for today.
    Returns the new total extra, or None when the write failed."""
    mac = mac.upper()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT extra_gb FROM extra_quota WHERE mac = ?", (mac,)
            ).fetchone()
            current = float(row["extra_gb"]) if row else 0.0
            new_total = current + amount_gb
            conn.execute(
                "INSERT OR REPLACE INTO extra_quota (mac, extra_gb) VALUES (?, ?)",
                (mac, new_total)
            )
        logger.info(f"Extra quota for {mac} changed by {amount_gb:+.2f} GB -> total {new_total:.2f} GB")
        return new_total
    except Exception as e:
        logger.error(f"Error updating extra quota for {mac}: {e}")
        return None

def set_extra_quota(mac: str, amount_gb: float) -> float | None:
    """Overwrite a device's extra quota for today with an absolute value.
    Returns the new value, or None when the write failed."""
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO extra_quota (mac, extra_gb) VALUES (?, ?)",
                (mac, amount_gb)
            )
        logger.info(f"Extra quota for {mac} set (edit) to {amount_gb:.2f} GB")
        return amount_gb
    except Exception as e:
        logger.error(f"Error setting extra quota for {mac}: {e}")
        return None
    

def clear_all_extra_quota() -> int:
    try:
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM extra_quota").fetchone()["c"]
            conn.execute("DELETE FROM extra_quota")
        if count:
            logger.info(f"Cleared {count} extra quota override(s) at midnight reset.")
        return count
    except Exception as e:
        logger.error(f"Error clearing extra quota: {e}")
        return 0