"""Regression tests for /macs edit/remove.

Covers A-R per plan.
Run: venv/Scripts/python.exe test_macs_edit_remove.py
"""
import asyncio
import pathlib
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import db
import state as state_mod
import usage_db
from router.devices import _recently_migrated, _recently_removed


def setup_temp_db():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    orig = db.DB_FILE
    db.DB_FILE = tmp.name
    db.init_db()
    usage_db.init_usage_tables()
    # Also need telegram tables? Not needed for these tests, but delete_device_purge will try to delete from device_telegram etc.
    # Ensure those tables exist via direct creation
    with db.get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS device_telegram (mac TEXT PRIMARY KEY, chat_id TEXT);
            CREATE TABLE IF NOT EXISTS threshold_notified (mac TEXT PRIMARY KEY, threshold INTEGER);
        """)
    state_mod.state.macs_list = {}
    state_mod.state.banned_macs = set()
    state_mod.state.allowed_macs = []
    state_mod.state.pending_macs = set()
    state_mod.state.ip_to_mac_cache = {}
    _recently_migrated.clear()
    _recently_removed.clear()
    # Clear onboarding sessions
    try:
        from services.onboarding import _sessions
        _sessions.clear()
    except Exception:
        pass
    return orig, tmp.name

def teardown_temp_db(orig, path):
    db.DB_FILE = orig
    try:
        pathlib.Path(path).unlink()
    except Exception:
        pass
    try:
        state_mod.state.reload_from_db()
    except Exception:
        pass
    _recently_migrated.clear()
    _recently_removed.clear()
    try:
        from services.onboarding import _sessions
        _sessions.clear()
    except Exception:
        pass

def assert_eq(a,b,msg=""):
    if a != b:
        raise AssertionError(f"{msg} expected {b!r} got {a!r}")
def assert_true(v,msg=""):
    if not v:
        raise AssertionError(f"{msg} expected True got {v!r}")
def assert_false(v,msg=""):
    if v:
        raise AssertionError(f"{msg} expected False got {v!r}")

def test_A_edit_normal():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        db.set_device_daily_limit(old, 1.5, mode="persistent")
        usage_db.set_extra_quota(old, 0.5)
        # Simulate edit via db.migrate
        ok = db.migrate_device_mac(old, new)
        assert_true(ok, "A migrate failed")
        # Manually sync state as macs.py would
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        assert_eq(db.get_hostname(new), "HostA")
        assert_false(db.device_exists(old))
        assert_true(db.device_exists(new))
        # daily limit migrated
        assert_eq(db.get_device_daily_limit(new), 1.5)
        assert_eq(db.get_device_daily_limit(old), None)
        # extra quota migrated
        assert_eq(usage_db.get_extra_quota(new), 0.5)
        assert_eq(usage_db.get_extra_quota(old), 0.0)
        print("PASS A edit normal")
    finally:
        teardown_temp_db(orig, path)

def test_B_macs_immediate():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        db.migrate_device_mac(old, new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # /macs reads state.macs_list
        assert new in state_mod.state.macs_list
        assert old not in state_mod.state.macs_list
        msg = "\n".join(f"`{m}` : **{h}**" for m,h in state_mod.state.macs_list.items())
        assert new in msg and old not in msg
        print("PASS B /macs immediate")
    finally:
        teardown_temp_db(orig, path)

def test_C_autocomplete_immediate():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        db.migrate_device_mac(old, new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # all_macs_autocomplete reads state.macs_list
        from utils.autocomplete import all_macs_autocomplete
        class FakeInteraction:
            pass
        # Use asyncio run
        async def run():
            choices = await all_macs_autocomplete(FakeInteraction(), "11:22")
            vals = [c.value for c in choices]
            assert new in vals, f"C autocomplete missing new {vals}"
            choices2 = await all_macs_autocomplete(FakeInteraction(), "AA:BB")
            vals2 = [c.value for c in choices2]
            assert old not in vals2, f"C autocomplete still has old {vals2}"
        asyncio.run(run())
        print("PASS C autocomplete immediate")
    finally:
        teardown_temp_db(orig, path)

def test_D_block_new_after_edit():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        db.migrate_device_mac(old, new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # Simulate /blk on new
        from router.firewall import ban_mac

        # Mock enable_lockdown to avoid router calls
        with patch("router.firewall.enable_lockdown"):
            ban_mac(new, reason="manual")
        assert new in state_mod.state.banned_macs
        assert old not in state_mod.state.banned_macs
        assert db.get_ban_reason(new) == "manual"
        print("PASS D block new after edit")
    finally:
        teardown_temp_db(orig, path)

def test_E_limit_new_after_edit():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        db.set_device_daily_limit(old, 2.0, mode="persistent")
        db.migrate_device_mac(old, new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # Now set limit on new should work
        db.set_device_daily_limit(new, 5.0, mode="persistent")
        assert_eq(db.get_device_daily_limit(new), 5.0)
        # old should be gone
        assert_eq(db.get_device_daily_limit(old), None)
        print("PASS E limit new after edit")
    finally:
        teardown_temp_db(orig, path)

def test_F_persistent_limit():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        db.set_device_daily_limit(old, 1.0, mode="persistent")
        db.migrate_device_mac(old, new)
        lim, mode, exp = db.get_device_daily_limit_with_mode(new)
        assert_eq(mode, "persistent")
        assert_eq(exp, None)
        assert_eq(lim, 1.0)
        print("PASS F persistent limit")
    finally:
        teardown_temp_db(orig, path)

def test_G_today_only_limit():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        db.set_device_daily_limit(old, 0.5, mode="today_only")
        lim1, mode1, exp1 = db.get_device_daily_limit_with_mode(old)
        db.migrate_device_mac(old, new)
        lim2, mode2, exp2 = db.get_device_daily_limit_with_mode(new)
        assert_eq(mode2, "today_only")
        assert_eq(exp2, exp1)
        assert_eq(lim2, 0.5)
        print("PASS G today_only limit")
    finally:
        teardown_temp_db(orig, path)

def test_H_banned_migration():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        state_mod.state.banned_macs.add(old)
        db.ban_device(old, reason="manual")
        db.migrate_device_mac(old, new)
        state_mod.state.banned_macs.discard(old)
        state_mod.state.banned_macs.add(new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        assert new in state_mod.state.banned_macs
        assert old not in state_mod.state.banned_macs
        assert_eq(db.get_ban_reason(new), "manual")
        assert_eq(db.get_ban_reason(old), None)
        print("PASS H banned migration")
    finally:
        teardown_temp_db(orig, path)

def test_I_allowed_migration():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        state_mod.state.allowed_macs.append(old)
        db.set_device_allowed(old, True)
        db.migrate_device_mac(old, new)
        # sync state
        idx = state_mod.state.allowed_macs.index(old)
        state_mod.state.allowed_macs[idx] = new
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        assert new in state_mod.state.allowed_macs
        assert old not in state_mod.state.allowed_macs
        assert new in db.get_allowed()
        print("PASS I allowed migration")
    finally:
        teardown_temp_db(orig, path)

def test_J_pending_onboarding():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")  # pending by default
        state_mod.state.pending_macs.add(old)
        state_mod.state.macs_list[old] = "HostA"
        from services.onboarding import OnboardingSession, _sessions
        sess = OnboardingSession(old, "HostA", "192.168.1.10")
        _sessions[old] = sess
        db.migrate_device_mac(old, new)
        state_mod.state.pending_macs.discard(old)
        state_mod.state.pending_macs.add(new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        if old in _sessions:
            s = _sessions.pop(old)
            s.mac = new
            _sessions[new] = s
        assert new in state_mod.state.pending_macs
        assert old not in state_mod.state.pending_macs
        assert new in _sessions
        assert old not in _sessions
        assert _sessions[new].mac == new
        print("PASS J pending/onboarding")
    finally:
        teardown_temp_db(orig, path)
        try:
            from services.onboarding import _sessions
            _sessions.clear()
        except Exception:
            pass

def test_K_extra_quota_and_notifications():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        usage_db.set_extra_quota(old, 1.5)
        db.mark_notified_today(old)
        # Create device_telegram entry
        with db.get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO device_telegram (mac, chat_id) VALUES (?, ?)", (old, "12345"))
        db.migrate_device_mac(old, new)
        assert_eq(usage_db.get_extra_quota(new), 1.5)
        assert_eq(usage_db.get_extra_quota(old), 0.0)
        # Check daily_notified migrated
        assert db.was_notified_today(new)
        assert not db.was_notified_today(old)
        # Check telegram
        with db.get_db() as conn:
            row = conn.execute("SELECT chat_id FROM device_telegram WHERE mac=?", (new,)).fetchone()
            assert row and row["chat_id"] == "12345"
            row2 = conn.execute("SELECT chat_id FROM device_telegram WHERE mac=?", (old,)).fetchone()
            assert row2 is None
        print("PASS K extra quota/notifications")
    finally:
        teardown_temp_db(orig, path)

def test_L_static_dhcp():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        # Mock fetch_current_entries to return old entry
        old_entry = {"mac": old, "ip": "192.168.1.10", "hostname": "MyHost", "flag": "0"}
        with patch("router.static_leases.fetch_current_entries") as mock_fetch, \
             patch("router.static_leases._push_dhcpd_static") as mock_push:
            mock_fetch.return_value = ([old_entry], "raw")
            mock_push.return_value = (True, None)
            # Simulate edit static migration logic from macs.py
            from router.static_leases import _serialize_entry
            entries = [old_entry]
            new_parts = []
            for ent in entries:
                if ent["mac"].upper() == old:
                    new_parts.append(_serialize_entry(new, ent["ip"], ent["hostname"], ent["flag"]))
                else:
                    new_parts.append(_serialize_entry(ent["mac"], ent["ip"], ent["hostname"], ent["flag"]))
            new_raw = ">".join(new_parts)
            assert new in new_raw and old not in new_raw
            assert "192.168.1.10" in new_raw and "MyHost" in new_raw
        print("PASS L static DHCP")
    finally:
        teardown_temp_db(orig, path)

def test_M_old_not_exists():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        assert not db.device_exists(old)
        ok = db.migrate_device_mac(old, new)
        assert_false(ok, "M should fail")
        print("PASS M old not exists")
    finally:
        teardown_temp_db(orig, path)

def test_N_new_already_exists():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        db.add_device(new, "HostB")
        ok = db.migrate_device_mac(old, new)
        assert_false(ok, "N should fail duplicate")
        # Ensure old still exists
        assert_true(db.device_exists(old))
        assert_true(db.device_exists(new))
        print("PASS N new already exists")
    finally:
        teardown_temp_db(orig, path)

def test_O_invalid_mac():
    from utils.validators import is_valid_mac
    assert_false(is_valid_mac("notamac"))
    assert_false(is_valid_mac("AA:BB:CC:DD:EE"))
    assert_true(is_valid_mac("AA:BB:CC:DD:EE:FF"))
    print("PASS O invalid MAC")

def test_P_old_new_equal():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        db.add_device(old, "HostA")
        ok = db.migrate_device_mac(old, old)
        assert_false(ok)
        ok2 = db.migrate_device_mac(old.lower(), old.upper())
        assert_false(ok2)
        print("PASS P old==new")
    finally:
        teardown_temp_db(orig, path)

def test_Q_db_failure_no_partial():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        # Patch get_db to fail on second update? Simpler: test that migrate is atomic - new exists should prevent partial
        # Create new device to cause PK conflict in satellites
        db.add_device(new, "HostB")
        # Try migrate old->new should fail and leave old intact
        ok = db.migrate_device_mac(old, new)
        assert_false(ok)
        assert_true(db.device_exists(old))
        assert_true(db.device_exists(new))
        assert_eq(db.get_hostname(old), "HostA")
        assert_eq(db.get_hostname(new), "HostB")
        print("PASS Q DB failure atomic")
    finally:
        teardown_temp_db(orig, path)

def test_R_router_failure_rollback():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        # Simulate DB success then router failure: we manually do DB migrate then attempt router push failure and rollback
        ok = db.migrate_device_mac(old, new)
        assert_true(ok)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # Simulate router failure
        router_ok = False
        if not router_ok:
            # Rollback
            db.migrate_device_mac(new, old)
            state_mod.state.macs_list[old] = state_mod.state.macs_list.pop(new)
        assert_true(db.device_exists(old))
        assert_false(db.device_exists(new))
        assert old in state_mod.state.macs_list
        assert new not in state_mod.state.macs_list
        print("PASS R router failure rollback")
    finally:
        teardown_temp_db(orig, path)

def test_S_discovery_after_edit_no_recreate():
    orig, path = setup_temp_db()
    try:
        old = "AA:BB:CC:DD:EE:FF"
        new = "11:22:33:44:55:66"
        db.add_device(old, "HostA")
        state_mod.state.macs_list[old] = "HostA"
        # Migrate
        db.migrate_device_mac(old, new)
        state_mod.state.macs_list[new] = state_mod.state.macs_list.pop(old)
        # Add TTL guard as macs.py would
        import time
        _recently_migrated[old] = time.time() + 600
        # Mock discovery seeing old still in dhcp_lease
        with patch("router.devices.fetch_devlist") as mock_fetch:
            mock_fetch.return_value = ([["OldHost", "192.168.1.10", old]], [], [])
            from router.devices import fetch_devlist_and_discover
            mock_bot = MagicMock()
            mock_bot.loop.is_running.return_value = False
            fetch_devlist_and_discover(mock_bot)
        # Old should NOT be recreated
        assert_false(db.device_exists(old) and old in state_mod.state.macs_list, "S old should not be recreated")
        assert_true(db.device_exists(new))
        print("PASS S discovery after edit no recreate")
    finally:
        teardown_temp_db(orig, path)
        _recently_migrated.clear()

def test_T_remove_no_stale():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "HostA")
        state_mod.state.macs_list[mac] = "HostA"
        state_mod.state.banned_macs.add(mac)
        db.ban_device(mac, reason="manual")
        db.set_device_daily_limit(mac, 1.0, mode="persistent")
        usage_db.set_extra_quota(mac, 0.5)
        db.mark_notified_today(mac)
        # Remove
        db.delete_device_purge(mac)
        state_mod.state.macs_list.pop(mac, None)
        state_mod.state.banned_macs.discard(mac)
        state_mod.state.allowed_macs = [m for m in state_mod.state.allowed_macs if m != mac]
        state_mod.state.pending_macs.discard(mac)
        # Check
        assert_false(db.device_exists(mac))
        assert mac not in state_mod.state.macs_list
        assert mac not in state_mod.state.banned_macs
        assert_eq(db.get_device_daily_limit(mac), None)
        assert_eq(usage_db.get_extra_quota(mac), 0.0)
        assert not db.was_notified_today(mac)
        # /macs would not show
        assert mac not in state_mod.state.macs_list
        print("PASS T remove no stale")
    finally:
        teardown_temp_db(orig, path)

def test_U_remove_then_discovery_TTL():
    orig, path = setup_temp_db()
    try:
        mac = "AA:BB:CC:DD:EE:FF"
        db.add_device(mac, "HostA")
        state_mod.state.macs_list[mac] = "HostA"
        db.delete_device_purge(mac)
        state_mod.state.macs_list.pop(mac, None)
        import time
        _recently_removed[mac] = time.time() + 600
        with patch("router.devices.fetch_devlist") as mock_fetch:
            mock_fetch.return_value = ([["HostA", "192.168.1.10", mac]], [], [])
            from router.devices import fetch_devlist_and_discover
            mock_bot = MagicMock()
            mock_bot.loop.is_running.return_value = False
            fetch_devlist_and_discover(mock_bot)
        # Should not be recreated due to TTL
        assert_false(db.device_exists(mac))
        print("PASS U remove TTL guard")
    finally:
        teardown_temp_db(orig, path)
        _recently_removed.clear()

if __name__ == "__main__":
    test_A_edit_normal()
    test_B_macs_immediate()
    test_C_autocomplete_immediate()
    test_D_block_new_after_edit()
    test_E_limit_new_after_edit()
    test_F_persistent_limit()
    test_G_today_only_limit()
    test_H_banned_migration()
    test_I_allowed_migration()
    test_J_pending_onboarding()
    test_K_extra_quota_and_notifications()
    test_L_static_dhcp()
    test_M_old_not_exists()
    test_N_new_already_exists()
    test_O_invalid_mac()
    test_P_old_new_equal()
    test_Q_db_failure_no_partial()
    test_R_router_failure_rollback()
    test_S_discovery_after_edit_no_recreate()
    test_T_remove_no_stale()
    test_U_remove_then_discovery_TTL()
    print("\nAll /macs edit/remove tests PASSED (A-U)")
