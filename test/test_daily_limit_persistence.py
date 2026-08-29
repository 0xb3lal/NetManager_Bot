import pathlib
import tempfile
from unittest.mock import patch

import db


def setup_temp_db():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    orig = db.DB_FILE
    db.DB_FILE = tmp.name
    db.init_db()
    return orig, tmp.name

def teardown_temp_db(orig, path):
    db.DB_FILE = orig
    try:
        pathlib.Path(path).unlink()
    except Exception:
        pass

def assert_eq(a,b,msg=""):
    if a != b:
        raise AssertionError(f"{msg} expected {b!r} got {a!r}")

def test_today_only_same_day():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_daily_default_limit(2.0)
        db.set_device_daily_limit(mac, 0.5, mode="today_only")
        # Same day: should be active
        assert_eq(db.get_device_daily_limit(mac), 0.5, "today_only same day")
        # Effective should be 0.5 (no extra quota)
        assert_eq(db.get_effective_daily_limit(mac), 0.5)
        # List should contain it
        assert mac in db.get_all_device_daily_limits()
        # With mode info
        lim, mode, exp = db.get_device_daily_limit_with_mode(mac)
        assert_eq(mode, "today_only")
        assert exp is not None
        print("PASS today_only same day")
    finally:
        teardown_temp_db(orig, path)

def test_today_only_next_day_fallback():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_daily_default_limit(2.0)
        # Create today_only for today
        db.set_device_daily_limit(mac, 0.5, mode="today_only")
        # Simulate next day by patching _today_cairo_str to return tomorrow
        import db as dbmod
        tomorrow = "2099-12-31"
        with patch.object(dbmod, "_today_cairo_str", return_value=tomorrow):
            # Lazy check: should be considered expired, fallback to default
            assert_eq(dbmod.get_device_daily_limit(mac), None, "today_only next day should be None")
            assert_eq(dbmod.get_effective_daily_limit(mac), 2.0, "effective should fallback to default")
            # List should not contain expired
            assert mac not in dbmod.get_all_device_daily_limits()
            assert mac not in dbmod.get_all_device_daily_limits_with_mode()
            # Even without cleanup, lazy check prevents use
            # Now test cleanup deletes it
            # Need to set expires_on to yesterday to simulate, but our mock makes today=tomorrow, so expires_on != today
            deleted = dbmod.clear_expired_today_only_limits()
            # Since today is mocked to tomorrow, it should delete (expires_on is today original date != tomorrow)
            assert deleted >= 1, "cleanup should delete expired today_only"
            # After cleanup, still None
            assert_eq(dbmod.get_device_daily_limit(mac), None)
        print("PASS today_only next day fallback + cleanup")
    finally:
        teardown_temp_db(orig, path)

def test_persistent_remains():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_daily_default_limit(2.0)
        db.set_device_daily_limit(mac, 0.5, mode="persistent")
        import db as dbmod
        tomorrow = "2099-12-31"
        with patch.object(dbmod, "_today_cairo_str", return_value=tomorrow):
            assert_eq(dbmod.get_device_daily_limit(mac), 0.5, "persistent should remain")
            assert_eq(dbmod.get_effective_daily_limit(mac), 0.5)
            assert mac in dbmod.get_all_device_daily_limits()
            # Midnight cleanup should NOT delete persistent
            deleted = dbmod.clear_expired_today_only_limits()
            assert_eq(deleted, 0, "persistent should not be deleted")
            assert_eq(dbmod.get_device_daily_limit(mac), 0.5)
        print("PASS persistent remains")
    finally:
        teardown_temp_db(orig, path)

def test_backward_compat_existing_rows():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        # Simulate old row without mode/expires_on: directly insert old schema style
        import sqlite3
        with db.get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO daily_limits (mac, limit_gb) VALUES (?, ?)", (mac, 0.7))
        # Now get should treat as persistent
        assert_eq(db.get_device_daily_limit(mac), 0.7, "old row should be persistent")
        lim, mode, exp = db.get_device_daily_limit_with_mode(mac)
        assert_eq(mode, "persistent", "old row mode fallback")
        # Next day still active
        import db as dbmod
        with patch.object(dbmod, "_today_cairo_str", return_value="2099-12-31"):
            assert_eq(dbmod.get_device_daily_limit(mac), 0.7)
        print("PASS backward compat")
    finally:
        teardown_temp_db(orig, path)

def test_switch_persistent_to_today_only():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_daily_default_limit(2.0)
        db.set_device_daily_limit(mac, 0.5, mode="persistent")
        assert_eq(db.get_device_daily_limit(mac), 0.5)
        # Switch to today_only
        db.set_device_daily_limit(mac, 0.6, mode="today_only")
        lim, mode, exp = db.get_device_daily_limit_with_mode(mac)
        assert_eq(mode, "today_only")
        assert_eq(lim, 0.6)
        # Next day should expire
        import db as dbmod
        with patch.object(dbmod, "_today_cairo_str", return_value="2099-12-31"):
            assert_eq(dbmod.get_device_daily_limit(mac), None)
        print("PASS switch persistent->today_only")
    finally:
        teardown_temp_db(orig, path)

def test_switch_today_only_to_persistent():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_device_daily_limit(mac, 0.5, mode="today_only")
        # Switch to persistent
        db.set_device_daily_limit(mac, 0.8, mode="persistent")
        lim, mode, exp = db.get_device_daily_limit_with_mode(mac)
        assert_eq(mode, "persistent")
        assert_eq(exp, None)
        import db as dbmod
        with patch.object(dbmod, "_today_cairo_str", return_value="2099-12-31"):
            assert_eq(dbmod.get_device_daily_limit(mac), 0.8, "switched to persistent should survive")
        print("PASS switch today_only->persistent")
    finally:
        teardown_temp_db(orig, path)

def test_reset_still_works():
    orig, path = setup_temp_db()
    try:
        mac1 = "AA:BB:CC:DD:EE:FF"
        mac2 = "11:22:33:44:55:66"
        db.set_device_daily_limit(mac1, 0.5, mode="persistent")
        db.set_device_daily_limit(mac2, 0.6, mode="today_only")
        assert len(db.get_all_device_daily_limits()) == 2
        cleared = db.clear_all_device_daily_limits()
        assert_eq(cleared, 2)
        assert len(db.get_all_device_daily_limits()) == 0
        print("PASS reset still works")
    finally:
        teardown_temp_db(orig, path)

def test_multiple_today_only_same_day_overwrite():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.set_device_daily_limit(mac, 0.5, mode="today_only")
        first_exp = db.get_device_daily_limit_with_mode(mac)[2]
        db.set_device_daily_limit(mac, 0.9, mode="today_only")
        second_exp = db.get_device_daily_limit_with_mode(mac)[2]
        assert_eq(first_exp, second_exp, "overwrite same day should keep same expires_on")
        assert_eq(db.get_device_daily_limit(mac), 0.9)
        print("PASS multiple today_only same day overwrite")
    finally:
        teardown_temp_db(orig, path)

if __name__ == "__main__":
    test_today_only_same_day()
    test_today_only_next_day_fallback()
    test_persistent_remains()
    test_backward_compat_existing_rows()
    test_switch_persistent_to_today_only()
    test_switch_today_only_to_persistent()
    test_reset_still_works()
    test_multiple_today_only_same_day_overwrite()
    print("\nAll daily limit persistence tests PASSED")

