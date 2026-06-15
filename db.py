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

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

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
        """)

        for mac, hostname, allowed in SEED_DEVICES:
            conn.execute(
                "INSERT OR IGNORE INTO devices (mac, hostname, allowed) VALUES (?, ?, ?)",
                (mac, hostname, allowed)
            )

        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('threshold', '3.0')"
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

def ban_device(mac: str) -> bool:
    mac = mac.upper()
    hostname = get_hostname(mac)
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO banned (mac, hostname) VALUES (?, ?)",
                (mac, hostname)
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