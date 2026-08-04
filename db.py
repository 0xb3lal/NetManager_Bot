import sqlite3
import logging

logger = logging.getLogger(__name__)

DB_FILE = "netmanager.db"

SEED_DEVICES = [
    ("D2:C5:E2:DE:F5:B4", "Baba",     0),
    ("5A:CE:CB:B5:D4:C9", "Ziad",     0),
    ("F8:34:41:DA:93:EB", "Windows",  1),
    ("22:9F:AE:3B:5D:C4", "Mama",     0),
    ("4C:20:B8:87:12:E2", "Iphone",   1),
    ("F2:72:C9:B8:4B:C7", "Tablet",   0),
    ("32:AC:87:47:17:5D", "Fedora",   1),
    ("D6:62:9E:2B:31:3D", "Yousef",   0),
    ("A8:6A:86:FE:B7:80", "Redmi-A3", 0),
]

# Default daily-per-device usage cap (GB), used when a device has no custom override.
DEFAULT_DAILY_LIMIT_GB = 1.5


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
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
        # Distinguishes manual /blk bans from automatic daily-limit bans, so the
        # midnight reset only lifts the ones it caused itself.
        if not _column_exists(conn, "banned", "reason"):
            conn.execute(
                "ALTER TABLE banned ADD COLUMN reason TEXT NOT NULL DEFAULT 'manual'"
            )
            logger.info("Migrated 'banned' table: added 'reason' column.")

        for mac, hostname, allowed in SEED_DEVICES:
            conn.execute(
                "INSERT OR IGNORE INTO devices (mac, hostname, allowed) VALUES (?, ?, ?)",
                (mac, hostname, allowed)
            )

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

def add_device(mac: str, hostname: str) -> bool:
    mac = mac.upper()
    hostname = hostname.strip() or "Unknown"
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO devices (mac, hostname, allowed) VALUES (?, ?, 0)",
                (mac, hostname)
            )
            inserted = conn.execute(
                "SELECT changes() as c"
            ).fetchone()["c"]
        if inserted:
            logger.info(f"New device discovered and saved: {mac} ({hostname})")
        return bool(inserted)
    except Exception as e:
        logger.error(f"Error adding device {mac}: {e}")
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
    # e.g. reason='daily_limit' -> only macs auto-banned for exceeding their daily cap,
    # so the midnight reset never touches a manual /blk ban.
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
            conn.execute(
                "INSERT OR IGNORE INTO banned (mac, hostname, reason) VALUES (?, ?, ?)",
                (mac, hostname, reason)
            )
            inserted = conn.execute("SELECT changes() as c").fetchone()["c"]
        return bool(inserted)
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
    # Returns the custom per-device limit, or None if the device uses the default.
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
    # mac -> custom limit_gb (only devices with an override)
    with get_db() as conn:
        rows = conn.execute("SELECT mac, limit_gb FROM daily_limits").fetchall()
    return {row["mac"]: float(row["limit_gb"]) for row in rows}

def get_effective_daily_limit(mac: str) -> float:
    custom = get_device_daily_limit(mac)
    return custom if custom is not None else get_daily_default_limit()

def clear_all_device_daily_limits() -> int:
    # Wipes every custom per-device override so all devices fall back to the
    # daily default. Used by the midnight reset (runs once per day).
    # Returns how many overrides were removed.
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

# ========= DAILY NOTIFICATION TRACKING (whitelist over-limit alerts) =========
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