import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DB_FILE = "netmanager.db"

_CAIRO_TZ = ZoneInfo("Africa/Cairo")

def _today_cairo_str() -> str:
    return datetime.now(_CAIRO_TZ).date().isoformat()

def _is_today_only_expired(mode: str | None, expires_on: str | None) -> bool:
    if mode != "today_only":
        return False
    if not expires_on:
        return False
    return expires_on != _today_cairo_str()

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

        # ---- Migration: per-device daily limit persistence (persistent vs today_only) ----
        if not _column_exists(conn, "daily_limits", "mode"):
            conn.execute(
                "ALTER TABLE daily_limits ADD COLUMN mode TEXT NOT NULL DEFAULT 'persistent' CHECK(mode IN ('persistent','today_only'))"
            )
            logger.info("Migrated 'daily_limits' table: added 'mode' column (persistent/today_only).")
        if not _column_exists(conn, "daily_limits", "expires_on"):
            conn.execute("ALTER TABLE daily_limits ADD COLUMN expires_on TEXT")
            logger.info("Migrated 'daily_limits' table: added 'expires_on' column.")

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

def update_hostname(mac: str, hostname: str) -> bool:
    """Update only the hostname for an existing device.

    Does NOT touch onboard_status, allowed, exempt_daily_limit,
    anomaly_handled, last_seen, banned, limits, etc.
    Returns True if row was updated.
    """
    mac = mac.upper()
    hostname = hostname.strip() or "Unknown"
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT hostname FROM devices WHERE mac = ?", (mac,)
            ).fetchone()
            if row is None:
                return False
            if row["hostname"] == hostname:
                return False
            conn.execute(
                "UPDATE devices SET hostname = ? WHERE mac = ?", (hostname, mac)
            )
            updated = conn.execute("SELECT changes() as c").fetchone()["c"]
        if updated:
            logger.info(f"Hostname updated for {mac}: '{row['hostname']}' -> '{hostname}' (via update_hostname)")
        return bool(updated)
    except Exception as e:
        logger.error(f"Error updating hostname for {mac}: {e}")
        return False

def migrate_device_mac(old_mac: str, new_mac: str) -> bool:
    """Atomically migrate a device's MAC across all MAC-keyed tables.

    Preserves hostname, allowed, onboard_status, exempt_daily_limit,
    anomaly_handled, last_seen, daily_limits (limit/mode/expires_on),
    extra_quota, banned reason, daily_notified, device_telegram,
    threshold_notified. Returns True on success, False on conflict/error
    with no partial state (transaction rollback).
    """
    old_mac = old_mac.strip().upper()
    new_mac = new_mac.strip().upper()
    if old_mac == new_mac:
        return False
    try:
        with get_db() as conn:
            # Conflict checks inside transaction
            exists_old = conn.execute("SELECT 1 FROM devices WHERE mac=?", (old_mac,)).fetchone()
            if not exists_old:
                return False
            exists_new = conn.execute("SELECT 1 FROM devices WHERE mac=?", (new_mac,)).fetchone()
            if exists_new:
                return False
            # Also check satellites where new already exists would cause PK conflict on UPDATE
            # For daily_limits etc., check new exists
            for tbl in ("daily_limits", "extra_quota", "banned", "daily_notified", "device_telegram", "threshold_notified"):
                try:
                    row_new = conn.execute(f"SELECT 1 FROM {tbl} WHERE mac=?", (new_mac,)).fetchone()
                    if row_new:
                        return False
                except Exception:
                    # Table may not exist yet (e.g., device_telegram)
                    pass
            # Migrate devices PK
            conn.execute("UPDATE devices SET mac=? WHERE mac=?", (new_mac, old_mac))
            # Migrate satellites
            for tbl in ("daily_limits", "extra_quota", "banned", "daily_notified", "device_telegram", "threshold_notified"):
                try:
                    conn.execute(f"UPDATE {tbl} SET mac=? WHERE mac=?", (new_mac, old_mac))
                except Exception:
                    pass
        logger.info(f"Migrated device MAC {old_mac} -> {new_mac}")
        return True
    except sqlite3.IntegrityError as e:
        logger.error(f"MAC migration conflict {old_mac}->{new_mac}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error migrating MAC {old_mac}->{new_mac}: {e}")
        return False

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
    """Return active custom limit or None. today_only expired limits are treated as absent."""
    mac = mac.upper()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT limit_gb, mode, expires_on FROM daily_limits WHERE mac = ?", (mac,)
            ).fetchone()
        if not row:
            return None
        mode = row["mode"] if "mode" in row.keys() and row["mode"] else "persistent"
        expires_on = row["expires_on"] if "expires_on" in row.keys() else None
        if _is_today_only_expired(mode, expires_on):
            return None
        return float(row["limit_gb"])
    except Exception as e:
        logger.error(f"Error loading daily limit for {mac}: {e}")
        return None

def get_device_daily_limit_with_mode(mac: str) -> tuple[float | None, str | None, str | None]:
    """Return (limit_gb, mode, expires_on) for UI; None if no active custom limit."""
    mac = mac.upper()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT limit_gb, mode, expires_on FROM daily_limits WHERE mac = ?", (mac,)
            ).fetchone()
        if not row:
            return None, None, None
        mode = row["mode"] if "mode" in row.keys() and row["mode"] else "persistent"
        expires_on = row["expires_on"] if "expires_on" in row.keys() else None
        if _is_today_only_expired(mode, expires_on):
            return None, None, None
        return float(row["limit_gb"]), mode, expires_on
    except Exception as e:
        logger.error(f"Error loading daily limit with mode for {mac}: {e}")
        return None, None, None

def set_device_daily_limit(mac: str, value: float, mode: str = "persistent"):
    mac = mac.upper()
    if mode not in ("persistent", "today_only"):
        mode = "persistent"
    expires_on = _today_cairo_str() if mode == "today_only" else None
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_limits (mac, limit_gb, mode, expires_on) VALUES (?, ?, ?, ?)",
                (mac, value, mode, expires_on)
            )
        logger.info(f"Daily limit for {mac} set to {value} GB mode={mode} expires_on={expires_on}")
    except Exception as e:
        logger.error(f"Error saving daily limit for {mac}: {e}")

def get_all_device_daily_limits() -> dict:
    """Return only active custom limits (expired today_only filtered)."""
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT mac, limit_gb, mode, expires_on FROM daily_limits").fetchall()
        out = {}
        for row in rows:
            mode = row["mode"] if "mode" in row.keys() and row["mode"] else "persistent"
            expires_on = row["expires_on"] if "expires_on" in row.keys() else None
            if _is_today_only_expired(mode, expires_on):
                continue
            out[row["mac"]] = float(row["limit_gb"])
        return out
    except Exception as e:
        logger.error(f"Error loading all daily limits: {e}")
        return {}

def get_all_device_daily_limits_with_mode() -> dict:
    """Return active limits with metadata: {mac: (limit_gb, mode, expires_on)}."""
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT mac, limit_gb, mode, expires_on FROM daily_limits").fetchall()
        out = {}
        for row in rows:
            mode = row["mode"] if "mode" in row.keys() and row["mode"] else "persistent"
            expires_on = row["expires_on"] if "expires_on" in row.keys() else None
            if _is_today_only_expired(mode, expires_on):
                continue
            out[row["mac"]] = (float(row["limit_gb"]), mode, expires_on)
        return out
    except Exception as e:
        logger.error(f"Error loading all daily limits with mode: {e}")
        return {}

def get_effective_daily_limit(mac: str) -> float:
    import usage_db  # local import to avoid circular import with usage_db.py
    custom = get_device_daily_limit(mac)
    base = custom if custom is not None else get_daily_default_limit()
    return base + usage_db.get_extra_quota(mac)

def clear_expired_today_only_limits() -> int:
    """Delete only today_only limits whose Cairo day has ended. Returns count."""
    try:
        today = _today_cairo_str()
        with get_db() as conn:
            # Count first
            rows = conn.execute(
                "SELECT mac, expires_on FROM daily_limits WHERE mode='today_only'"
            ).fetchall()
            expired = [r for r in rows if r["expires_on"] and r["expires_on"] != today]
            if not expired:
                return 0
            conn.execute(
                "DELETE FROM daily_limits WHERE mode='today_only' AND expires_on != ?", (today,)
            )
            # Also clean any today_only with NULL expires_on (should not happen) — keep them
            deleted = conn.execute("SELECT changes() as c").fetchone()["c"]
        if deleted:
            logger.info(f"Cleared {deleted} expired today_only daily limit(s) (today={today}).")
        return deleted
    except Exception as e:
        logger.error(f"Error clearing expired today_only limits: {e}")
        return 0

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
