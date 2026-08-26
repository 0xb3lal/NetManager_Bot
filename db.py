import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_FILE = "netmanager.db"

# NOTE: devices are sourced exclusively from the router via discovery.
# The old static SEED_DEVICES bootstrap list was removed on purpose —
# no manual/hardcoded device entries exist anywhere anymore.

# Default daily-per-device usage cap (GB), used when a device has no custom override.
DEFAULT_DAILY_LIMIT_GB = 1.5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_exists(conn, table: str, column: str) -> bool:
    # Safely escape table name using brackets to prevent SQL formatting issues
    rows = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    return any(row["name"] == column for row in rows)


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                mac      TEXT PRIMARY KEY,
                hostname TEXT NOT NULL DEFAULT 'Unknown',
                allowed  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS banned (
                mac      TEXT PRIMARY KEY,
                hostname TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key      TEXT PRIMARY KEY,
                value    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_limits (
                mac      TEXT PRIMARY KEY,
                limit_gb REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_notified (
                mac      TEXT PRIMARY KEY
            );
        """)

        # ---- Migration: add 'reason' column to the existing 'banned' table ----
        if not _column_exists(conn, "banned", "reason"):
            conn.execute(
                "ALTER TABLE banned ADD COLUMN reason TEXT NOT NULL DEFAULT 'manual'"
            )
            logger.info("Migrated 'banned' table: added 'reason' column.")

        # ---- Migrations: device onboarding / exemption / anomaly / last_seen ----
        # Existing rows are grandfathered as 'confirmed' via the column default,
        # so only devices discovered AFTER this migration go through onboarding.
        if not _column_exists(conn, "devices", "onboard_status"):
            conn.execute(
                "ALTER TABLE devices ADD COLUMN onboard_status TEXT NOT NULL DEFAULT 'confirmed'"
            )
            logger.info("Migrated 'devices' table: added 'onboard_status' column.")
        if not _column_exists(conn, "devices", "exempt_daily_limit"):
            conn.execute(
                "ALTER TABLE devices ADD COLUMN exempt_daily_limit INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migrated 'devices' table: added 'exempt_daily_limit' column.")
        if not _column_exists(conn, "devices", "anomaly_handled"):
            conn.execute(
                "ALTER TABLE devices ADD COLUMN anomaly_handled INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migrated 'devices' table: added 'anomaly_handled' column.")
        if not _column_exists(conn, "devices", "last_seen"):
            conn.execute("ALTER TABLE devices ADD COLUMN last_seen TEXT")
            logger.info("Migrated 'devices' table: added 'last_seen' column.")

        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('threshold', '3.0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('lockdown_state', '0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_default_limit', ?)",
            (str(DEFAULT_DAILY_LIMIT_GB),)
        )

    logger.info("Database initialized successfully.")

# ========= DEVICES =========
def get_devices() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT mac, hostname FROM devices").fetchall()
    return {row["mac"]: row["hostname"] for row in rows}

def get_allowed() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT mac FROM devices WHERE allowed = 1").fetchall()
    return [row["mac"] for row in rows]

def set_device_allowed(mac: str, allowed: bool) -> bool:
    mac = mac.upper()
    val = 1 if allowed else 0
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE devices SET allowed = ? WHERE mac = ?", (val, mac)
            )
            updated = conn.execute("SELECT changes() as c").fetchone()["c"]
        return bool(updated)
    except Exception as e:
        logger.error(f"Error updating allowed state for {mac}: {e}")
        return False

def add_device(mac: str, hostname: str) -> bool:
    """Insert a newly discovered device, or refresh its stored hostname if the
    router reports a new one for a known MAC. Other fields (allowed status,
    limits, quotas, onboarding status) are never touched by a hostname refresh.

    New rows start as onboard_status='pending' (blocked until reviewed) and
    get their first last_seen stamp. Presence refreshes afterwards are handled
    by fetch_devlist(), not here.

    Returns True only when a brand-new device row was inserted.
    """
    mac = mac.upper()
    hostname = hostname.strip() or "Unknown"
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT hostname FROM devices WHERE mac = ?", (mac,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO devices "
                    "(mac, hostname, allowed, onboard_status, exempt_daily_limit, anomaly_handled, last_seen) "
                    "VALUES (?, ?, 0, 'pending', 0, 0, ?)",
                    (mac, hostname, _now_iso())
                )
                logger.info(f"New device discovered and saved as PENDING: {mac} ({hostname})")
                return True
            if row["hostname"] != hostname:
                conn.execute(
                    "UPDATE devices SET hostname = ? WHERE mac = ?",
                    (hostname, mac)
                )
                logger.info(
                    f"Hostname updated for {mac}: '{row['hostname']}' -> '{hostname}'"
                )
        return False
    except Exception as e:
        logger.error(f"Error adding device {mac}: {e}")
        return False


def refresh_last_seen(macs) -> None:
    """Stamp last_seen=now for every given MAC that exists in the table.
    Called from router polls — presence comes exclusively from the router."""
    macs = list({m.upper() for m in macs})
    if not macs:
        return
    try:
        with get_db() as conn:
            conn.executemany(
                "UPDATE devices SET last_seen = ? WHERE mac = ?",
                [(_now_iso(), m) for m in macs]
            )
    except Exception as e:
        logger.error(f"Error refreshing last_seen stamps: {e}")


def device_exists(mac: str) -> bool:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM devices WHERE mac = ?", (mac,)).fetchone()
    return row is not None


def is_onboarding_pending(mac: str) -> bool:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT onboard_status FROM devices WHERE mac = ?", (mac,)
        ).fetchone()
    return bool(row) and row["onboard_status"] == "pending"


def set_onboarding_confirmed(mac: str):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE devices SET onboard_status = 'confirmed' WHERE mac = ?",
                (mac,)
            )
        logger.info(f"Onboarding confirmed for {mac}.")
    except Exception as e:
        logger.error(f"Error confirming onboarding for {mac}: {e}")


def get_pending_devices() -> dict:
    """Return {mac: hostname} for every device still awaiting onboarding."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT mac, hostname FROM devices WHERE onboard_status = 'pending'"
        ).fetchall()
    return {row["mac"]: row["hostname"] for row in rows}


def get_devices_with_last_seen() -> dict:
    """Return {mac: row} for devices that have a last_seen stamp.
    Rows carry mac / hostname / last_seen (sqlite3.Row)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT mac, hostname, last_seen FROM devices WHERE last_seen IS NOT NULL"
        ).fetchall()
    return {row["mac"]: row for row in rows}


def get_last_seen(mac: str) -> str | None:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_seen FROM devices WHERE mac = ?", (mac,)
        ).fetchone()
    return row["last_seen"] if row else None


def is_exempt_from_daily_limit(mac: str) -> bool:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT exempt_daily_limit FROM devices WHERE mac = ?", (mac,)
        ).fetchone()
    return bool(row) and bool(row["exempt_daily_limit"])


def set_exempt_from_daily_limit(mac: str, exempt: bool):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE devices SET exempt_daily_limit = ? WHERE mac = ?",
                (1 if exempt else 0, mac)
            )
        logger.info(f"Daily-limit exemption for {mac} set to {exempt}.")
    except Exception as e:
        logger.error(f"Error setting daily-limit exemption for {mac}: {e}")


def is_anomaly_handled(mac: str) -> bool:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT anomaly_handled FROM devices WHERE mac = ?", (mac,)
        ).fetchone()
    return bool(row) and bool(row["anomaly_handled"])


def mark_anomaly_handled(mac: str):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE devices SET anomaly_handled = 1 WHERE mac = ?", (mac,)
            )
    except Exception as e:
        logger.error(f"Error marking anomaly handled for {mac}: {e}")


def delete_device_purge(mac: str) -> bool:
    """Delete a device and every MAC-keyed satellite row so no ghost state
    survives the purge. Returns True when a devices row was removed."""
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM devices WHERE mac = ?", (mac,))
            deleted = conn.execute("SELECT changes() as c").fetchone()["c"]
            # Satellite tables keyed by MAC — cleaned regardless of whether
            # the devices row existed, to avoid resurrecting ghosts.
            for table in (
                "banned", "daily_limits", "extra_quota",
                "daily_notified", "device_telegram", "threshold_notified",
            ):
                conn.execute(f"DELETE FROM {table} WHERE mac = ?", (mac,))
        return bool(deleted)
    except Exception as e:
        logger.error(f"Error purging stale device {mac}: {e}")
        return False

def get_hostname(mac: str) -> str:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT hostname FROM devices WHERE mac = ?", (mac,)
        ).fetchone()
    return row["hostname"] if row else mac

# ========= BANNED =========
def get_banned() -> set:
    with get_db() as conn:
        rows = conn.execute("SELECT mac FROM banned").fetchall()
    return {row["mac"] for row in rows}

def get_banned_by_reason(reason: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT mac FROM banned WHERE reason = ?", (reason,)
        ).fetchall()
    return {row["mac"] for row in rows}

def get_ban_reason(mac: str) -> str | None:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT reason FROM banned WHERE mac = ?", (mac,)
        ).fetchone()
    return row["reason"] if row else None

def ban_device(mac: str, reason: str = "manual") -> bool:
    mac = mac.upper()
    hostname = get_hostname(mac)
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT reason FROM banned WHERE mac = ?", (mac,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO banned (mac, hostname, reason) VALUES (?, ?, ?)",
                    (mac, hostname, reason)
                )
                return True
            # Already banned: a manual block must overwrite an automatic one,
            # otherwise midnight reset would unban a manually blocked device.
            if row["reason"] != reason:
                conn.execute(
                    "UPDATE banned SET reason = ? WHERE mac = ?", (reason, mac)
                )
                return True
            return False
    except Exception as e:
        logger.error(f"Error banning device {mac}: {e}")
        return False

def unban_device(mac: str) -> bool:
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM banned WHERE mac = ?", (mac,))
            deleted = conn.execute("SELECT changes() as c").fetchone()["c"]
        return bool(deleted)
    except Exception as e:
        logger.error(f"Error unbanning device {mac}: {e}")
        return False

# ========= SETTINGS =========
def get_threshold() -> float:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'threshold'"
            ).fetchone()
        return float(row["value"]) if row else 3.0
    except Exception as e:
        logger.error(f"Error loading threshold: {e}")
        return 3.0

def set_threshold(value: float):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('threshold', ?)",
                (str(value),)
            )
        logger.info(f"Threshold updated to {value} GB")
    except Exception as e:
        logger.error(f"Error saving threshold: {e}")

def get_lockdown_state() -> bool:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'lockdown_state'"
            ).fetchone()
        return row["value"] == "1" if row else False
    except Exception as e:
        logger.error(f"Error loading lockdown state: {e}")
        return False

def set_lockdown_state(state: bool):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('lockdown_state', ?)",
                ("1" if state else "0",)
            )
        logger.info(f"Lockdown state updated to {state}")
    except Exception as e:
        logger.error(f"Error saving lockdown state: {e}")

# ========= DAILY USAGE LIMITS =========
def get_daily_default_limit() -> float:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'daily_default_limit'"
            ).fetchone()
        return float(row["value"]) if row else DEFAULT_DAILY_LIMIT_GB
    except Exception as e:
        logger.error(f"Error loading daily default limit: {e}")
        return DEFAULT_DAILY_LIMIT_GB

def set_daily_default_limit(value: float):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_default_limit', ?)",
                (str(value),)
            )
        logger.info(f"Daily default limit updated to {value} GB")
    except Exception as e:
        logger.error(f"Error saving daily default limit: {e}")

def get_device_daily_limit(mac: str) -> float | None:
    mac = mac.upper()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT limit_gb FROM daily_limits WHERE mac = ?", (mac,)
            ).fetchone()
        return float(row["limit_gb"]) if row else None
    except Exception as e:
        logger.error(f"Error loading daily limit for {mac}: {e}")
        return None

def set_device_daily_limit(mac: str, value: float):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_limits (mac, limit_gb) VALUES (?, ?)",
                (mac, value)
            )
        logger.info(f"Daily limit for {mac} set to {value} GB")
    except Exception as e:
        logger.error(f"Error saving daily limit for {mac}: {e}")

def get_all_device_daily_limits() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT mac, limit_gb FROM daily_limits").fetchall()
    return {row["mac"]: float(row["limit_gb"]) for row in rows}

def get_effective_daily_limit(mac: str) -> float:
    import usage_db  # local import to avoid circular import with usage_db.py
    custom = get_device_daily_limit(mac)
    base = custom if custom is not None else get_daily_default_limit()
    return base + usage_db.get_extra_quota(mac)

def clear_all_device_daily_limits() -> int:
    try:
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM daily_limits").fetchone()["c"]
            conn.execute("DELETE FROM daily_limits")
        if count:
            logger.info(f"Cleared {count} custom daily limit override(s) at midnight reset.")
        return count
    except Exception as e:
        logger.error(f"Error clearing daily limit overrides: {e}")
        return 0

# ========= DAILY NOTIFICATION TRACKING =========
def was_notified_today(mac: str) -> bool:
    mac = mac.upper()
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM daily_notified WHERE mac = ?", (mac,)
        ).fetchone()
    return row is not None

def mark_notified_today(mac: str):
    mac = mac.upper()
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO daily_notified (mac) VALUES (?)", (mac,)
            )
    except Exception as e:
        logger.error(f"Error marking {mac} as notified today: {e}")

def clear_daily_notifications():
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM daily_notified")
        logger.info("Daily notification flags cleared.")
    except Exception as e:
        logger.error(f"Error clearing daily notifications: {e}")