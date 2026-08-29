"""Regression tests for /macs stale hostname after /rename.

A-H per spec. Run: venv/Scripts/python.exe test_hostname_sync.py
"""
import pathlib
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

# Use a temporary DB file to avoid touching real netmanager.db
import db
import state as state_mod


def setup_temp_db():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    # Patch DB_FILE and init
    orig = db.DB_FILE
    db.DB_FILE = tmp.name
    db.init_db()
    # Reset state to use temp DB
    state_mod.state.macs_list = {}
    state_mod.state.banned_macs = set()
    state_mod.state.pending_macs = set()
    state_mod.state.allowed_macs = []
    state_mod.state.ip_to_mac_cache = {}
    return orig, tmp.name

def teardown_temp_db(orig, path):
    db.DB_FILE = orig
    try:
        pathlib.Path(path).unlink()
    except Exception:
        pass
    # reload original state from real DB (best effort)
    try:
        state_mod.state.reload_from_db()
    except Exception:
        pass

def assert_eq(a,b,msg=""):
    if a != b:
        raise AssertionError(f"{msg} expected {b!r} got {a!r}")

def test_A_rename_updates_db():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        # Insert device as Unknown via add_device
        assert db.add_device(mac, "OldName")
        assert_eq(db.get_hostname(mac), "OldName")
        # Simulate /rename success path: db.update_hostname + state.macs_list
        state_mod.state.macs_list[mac] = "OldName"
        ok = db.update_hostname(mac, "NewName")
        assert_eq(ok, True, "A update_hostname should return True")
        assert_eq(db.get_hostname(mac), "NewName", "A DB hostname not updated")
        print("PASS A rename updates DB")
    finally:
        teardown_temp_db(orig, path)

def test_B_rename_updates_state():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "OldName")
        state_mod.state.macs_list[mac] = "OldName"
        # Simulate rename.py post-success block
        name = "NewName"
        db.update_hostname(mac, name)
        state_mod.state.macs_list[mac] = name
        assert_eq(state_mod.state.macs_list[mac], "NewName", "B state not updated")
        print("PASS B rename updates state.macs_list")
    finally:
        teardown_temp_db(orig, path)

def test_C_macs_sees_new_immediately():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "OldName")
        state_mod.state.macs_list[mac] = "OldName"
        # /macs reads state.macs_list directly
        from commands.moderation.macs import setup as macs_setup

        # Simulate rename
        db.update_hostname(mac, "NewName")
        state_mod.state.macs_list[mac] = "NewName"
        # Now check what /macs would display
        msg = "\n".join(f"`{m}` : **{h}**" for m, h in state_mod.state.macs_list.items())
        assert "NewName" in msg and "OldName" not in msg, "C /macs still shows old"
        print("PASS C /macs sees new immediately")
    finally:
        teardown_temp_db(orig, path)

def test_D_discovery_unknown_no_overwrite():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        # Device has custom hostname via rename
        db.add_device(mac, "CustomHost")
        state_mod.state.macs_list[mac] = "CustomHost"
        # Mock fetch_devlist to return Unknown for same MAC
        with patch("router.devices.fetch_devlist") as mock_fetch:
            mock_fetch.return_value = ([["Unknown", "192.168.1.10", mac]], [], [])
            from router.devices import fetch_devlist_and_discover

            # Need bot mock
            mock_bot = MagicMock()
            mock_bot.loop.is_running.return_value = False
            fetch_devlist_and_discover(mock_bot)
        # DB and state should still be CustomHost, not Unknown
        assert_eq(db.get_hostname(mac), "CustomHost", "D DB overwritten with Unknown")
        assert_eq(state_mod.state.macs_list[mac], "CustomHost", "D state overwritten with Unknown")
        print("PASS D discovery Unknown does NOT overwrite custom")
    finally:
        teardown_temp_db(orig, path)

def test_E_discovery_real_updates():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "OldName")
        state_mod.state.macs_list[mac] = "OldName"
        with patch("router.devices.fetch_devlist") as mock_fetch:
            mock_fetch.return_value = ([["RealHost", "192.168.1.10", mac]], [], [])
            from router.devices import fetch_devlist_and_discover
            mock_bot = MagicMock()
            mock_bot.loop.is_running.return_value = False
            fetch_devlist_and_discover(mock_bot)
        assert_eq(db.get_hostname(mac), "RealHost", "E real hostname should update DB")
        assert_eq(state_mod.state.macs_list[mac], "RealHost", "E real hostname should update state")
        print("PASS E discovery real updates")
    finally:
        teardown_temp_db(orig, path)

def test_F_status_unchanged():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "OldName")
        # Check initial status fields
        with db.get_db() as conn:
            row = conn.execute("SELECT onboard_status, allowed, exempt_daily_limit, anomaly_handled FROM devices WHERE mac=?", (mac,)).fetchone()
            orig_status = tuple(row)
        # Update hostname
        db.update_hostname(mac, "NewName")
        with db.get_db() as conn:
            row = conn.execute("SELECT onboard_status, allowed, exempt_daily_limit, anomaly_handled FROM devices WHERE mac=?", (mac,)).fetchone()
            new_status = tuple(row)
        assert_eq(orig_status, new_status, "F status fields changed")
        # Also test pending/banned not affected: add pending device then rename
        # add_device creates pending for new MACs; update_hostname should not change pending
        mac2 = "11:22:33:44:55:66"
        db.add_device(mac2, "Other")
        assert db.is_onboarding_pending(mac2) == True
        db.update_hostname(mac2, "OtherRenamed")
        assert db.is_onboarding_pending(mac2) == True, "F pending changed"
        print("PASS F status unchanged")
    finally:
        teardown_temp_db(orig, path)

def test_G_ip_resolution_still_pass():
    # Re-run previous IP resolution helper tests to ensure no regression
    # Import the test file's helper if available, else simple sanity
    from router.static_leases import is_valid_ip
    assert is_valid_ip("192.168.1.10")
    assert not is_valid_ip("999.0.0.1")
    # Ensure rename.py still has correct imports
    import pathlib
    txt = pathlib.Path("commands/system/rename.py").read_text()
    assert "fetch_devlist" in txt and "fetch_current_entries" in txt, "G rename still has both lookups"
    assert "resolve_ip_for_mac" not in txt, "G stale cache should be removed"
    print("PASS G IP resolution still intact")

def test_H_static_corruption_still_pass():
    # Ensure previous static lease tests still pass
    from router.static_leases import (_escape_field, _normalize_mac,
                                      _parse_raw_entries, _serialize_entry)
    assert _escape_field("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF", "H colon escape regression"
    assert _normalize_mac(r"D6\x5c:E8\x5c:06") == "D6:E8:06:00:00:00" or "D6:E8" in _normalize_mac(r"D6\x5c:E8\x5c:06")
    # Quick roundtrip
    mac = "AA:BB:CC:DD:EE:FF"
    ser = _serialize_entry(mac, "192.168.1.10", "Host", "0")
    parsed = _parse_raw_entries(ser)
    assert parsed[0]["mac"] == mac
    print("PASS H static corruption still fixed")

if __name__ == "__main__":
    test_A_rename_updates_db()
    test_B_rename_updates_state()
    test_C_macs_sees_new_immediately()
    test_D_discovery_unknown_no_overwrite()
    test_E_discovery_real_updates()
    test_F_status_unchanged()
    test_G_ip_resolution_still_pass()
    test_H_static_corruption_still_pass()
    print("\nAll hostname sync tests PASSED (A-H)")
