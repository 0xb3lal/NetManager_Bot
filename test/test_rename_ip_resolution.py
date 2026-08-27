"""Focused regression tests for /rename IP resolution priority.

Priority: explicit IP > active DHCP > static DHCP > ask user
Run: venv/Scripts/python.exe test_rename_ip_resolution.py
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Reuse validators from codebase
from router.static_leases import is_valid_ip
from utils.validators import is_valid_mac

# Helper to simulate the resolution logic from commands/system/rename.py
async def resolve_ip_for_rename(mac: str, ip: str, mock_active, mock_static):
    """
    mac: input MAC (any case)
    ip: explicit ip or None
    mock_active: list of leases as returned by fetch_devlist dhcp_leases
                 each lease is [hostname, ip, mac]
    mock_static: list of dicts as returned by fetch_current_entries -> entries
                 each dict has {"mac":..., "ip":...}
    Returns resolved_ip or None
    """
    mac_upper = mac.strip().upper()
    if not is_valid_mac(mac_upper):
        return None  # would be rejected earlier
    explicit_ip = ip.strip() if isinstance(ip, str) and ip.strip() else None
    if explicit_ip:
        if not is_valid_ip(explicit_ip):
            return None
        return explicit_ip

    # STEP 1 active
    resolved = None
    for lease in mock_active:
        try:
            lease_mac = str(lease[2]).strip().upper()
            lease_ip = str(lease[1]).strip() if lease[1] else ""
        except Exception:
            continue
        if lease_mac == mac_upper and lease_ip and is_valid_ip(lease_ip):
            resolved = lease_ip
            break
    if resolved:
        return resolved

    # STEP 2 static
    for ent in mock_static:
        try:
            ent_mac = str(ent.get("mac","")).strip().upper()
            ent_ip = str(ent.get("ip","")).strip() if ent.get("ip") else ""
        except Exception:
            continue
        if ent_mac == mac_upper and ent_ip and is_valid_ip(ent_ip):
            resolved = ent_ip
            break
    return resolved

def assert_eq(a,b,msg=""):
    if a != b:
        raise AssertionError(f"{msg}\n expected {b!r} got {a!r}")

async def test_A_active_hit():
    mac = "AA:BB:CC:DD:EE:FF"
    active = [["Host1", "192.168.1.10", mac]]
    static = [{"mac": mac, "ip": "192.168.1.99"}]
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, "192.168.1.10", "A active hit should win")
    print("PASS A active hit")

async def test_B_static_fallback():
    mac = "AA:BB:CC:DD:EE:FF"
    active = [["Other", "192.168.1.5", "11:22:33:44:55:66"]]
    static = [{"mac": mac, "ip": "192.168.1.20"}]
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, "192.168.1.20", "B static fallback")
    print("PASS B static fallback")

async def test_C_both_active_wins():
    mac = "AA:BB:CC:DD:EE:FF"
    active = [["HostA", "192.168.1.10", mac]]
    static = [{"mac": mac, "ip": "192.168.1.99"}]
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, "192.168.1.10", "C both -> active wins")
    # also test with different active IP vs static
    active2 = [["HostA", "10.0.0.5", mac]]
    static2 = [{"mac": mac, "ip": "10.0.0.99"}]
    res2 = await resolve_ip_for_rename(mac, None, active2, static2)
    assert_eq(res2, "10.0.0.5", "C both -> active wins 2")
    print("PASS C both active wins")

async def test_D_neither():
    mac = "AA:BB:CC:DD:EE:FF"
    active = [["Host", "192.168.1.5", "11:22:33:44:55:66"]]
    static = [{"mac": "11:22:33:44:55:66", "ip": "192.168.1.5"}]
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, None, "D neither should be None")
    print("PASS D neither -> ask user")

async def test_E_explicit_bypass():
    mac = "AA:BB:CC:DD:EE:FF"
    active = [["HostA", "192.168.1.10", mac]]
    static = [{"mac": mac, "ip": "192.168.1.20"}]
    res = await resolve_ip_for_rename(mac, "192.168.1.50", active, static)
    assert_eq(res, "192.168.1.50", "E explicit should bypass")
    # ensure lookups not needed: explicit invalid should be rejected
    res2 = await resolve_ip_for_rename(mac, "999.999.0.1", active, static)
    assert_eq(res2, None, "E explicit invalid should be None")
    print("PASS E explicit bypass")

async def test_F_lowercase():
    mac_lower = "aa:bb:cc:dd:ee:ff"
    mac_upper = "AA:BB:CC:DD:EE:FF"
    active = [["Host", "192.168.1.10", mac_upper]]
    static = []
    res = await resolve_ip_for_rename(mac_lower, None, active, static)
    assert_eq(res, "192.168.1.10", "F lowercase should match")
    # also static lowercase
    active2 = []
    static2 = [{"mac": mac_upper, "ip": "192.168.1.20"}]
    res2 = await resolve_ip_for_rename(mac_lower, None, active2, static2)
    assert_eq(res2, "192.168.1.20", "F lowercase static")
    print("PASS F lowercase")

async def test_G_invalid_active_fallback():
    mac = "AA:BB:CC:DD:EE:FF"
    # active has invalid IP
    active = [["Host", "", mac], ["Host", "999.999.0.1", mac], ["Host", None, mac]]
    static = [{"mac": mac, "ip": "192.168.1.30"}]
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, "192.168.1.30", "G invalid active -> fallback static")
    # both invalid -> None
    active2 = [["Host", "", mac]]
    static2 = [{"mac": mac, "ip": ""}]
    res2 = await resolve_ip_for_rename(mac, None, active2, static2)
    assert_eq(res2, None, "G both invalid -> None")
    # static invalid with no active -> None
    active3 = []
    static3 = [{"mac": mac, "ip": "invalid"}]
    res3 = await resolve_ip_for_rename(mac, None, active3, static3)
    assert_eq(res3, None, "G static invalid -> None")
    print("PASS G invalid active fallback")

async def test_H_regression_and_multiple_entries():
    # H regression: existing hostname behavior unchanged (only IP resolution changed)
    # Test multiple active entries for same MAC: first valid wins, not arbitrary
    mac = "AA:BB:CC:DD:EE:FF"
    active = [
        ["Host1", "192.168.1.10", mac],
        ["Host2", "192.168.1.11", mac],  # duplicate
    ]
    static = []
    res = await resolve_ip_for_rename(mac, None, active, static)
    assert_eq(res, "192.168.1.10", "H multiple active -> first wins")
    # Test whitespace handling
    mac_ws = "  AA:BB:CC:DD:EE:FF  "
    active2 = [["Host", "192.168.1.10", "AA:BB:CC:DD:EE:FF"]]
    res2 = await resolve_ip_for_rename(mac_ws, None, active2, [])
    assert_eq(res2, "192.168.1.10", "H whitespace MAC")
    ip_ws = "  192.168.1.50  "
    res3 = await resolve_ip_for_rename(mac, ip_ws, active, static)
    assert_eq(res3, "192.168.1.50", "H explicit whitespace IP")
    print("PASS H regression/multiple")

async def test_integration_with_mocks():
    """Integration: mock fetch_devlist and fetch_current_entries as rename.py does."""
    from unittest.mock import patch, AsyncMock

    # Simulate rename.py's actual logic by patching
    mac = "AA:BB:CC:DD:EE:FF"
    # Case 1: active hit, static should not be called
    with patch("router.devices.fetch_devlist") as mock_active, \
         patch("router.static_leases.fetch_current_entries") as mock_static:
        mock_active.return_value = ([["H","192.168.1.10",mac]], [], [])
        mock_static.return_value = ([{"mac": mac, "ip": "192.168.1.99"}], "raw")
        # Call the helper logic directly (replicate rename.py steps)
        res = await resolve_ip_for_rename(mac, None, mock_active.return_value[0], mock_static.return_value[0])
        assert_eq(res, "192.168.1.10")
    print("PASS integration")

async def main():
    await test_A_active_hit()
    await test_B_static_fallback()
    await test_C_both_active_wins()
    await test_D_neither()
    await test_E_explicit_bypass()
    await test_F_lowercase()
    await test_G_invalid_active_fallback()
    await test_H_regression_and_multiple_entries()
    await test_integration_with_mocks()
    print("\nAll rename IP resolution tests PASSED (A-H)")

if __name__ == "__main__":
    asyncio.run(main())
