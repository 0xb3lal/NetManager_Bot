"""Focused regression tests for Tomato shell.cgi + static lease MAC fix.

Tests A-F per task spec. Run with: venv/Scripts/python.exe test_static_leases_fix.py
"""
import pathlib
import sys

# Ensure imports work
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from router.client import _decode_js_escapes, parse_shell_cgi_result
from router.static_leases import (_escape_field, _normalize_mac,
                                  _parse_raw_entries, _serialize_entry)


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}")

def test_A_js_wrapper_and_mac_normalization():
    # A) Input wrapper with \x5c escapes
    raw_wrapper = "cmdresult = 'D6\\x5c:E8\\x5c:06\\x5c:F4\\x5c:32\\x5c:8A';"
    decoded = parse_shell_cgi_result(raw_wrapper)
    # After JS decode, \x5c -> \, so should be D6\:E8\:...
    assert_eq(decoded, r"D6\:E8\:06\:F4\:32\:8A", "A: JS decode failed")
    # Then MAC normalization via static lease parser must produce canonical
    # Simulate parsing an entry that contains this decoded MAC
    entry_raw = decoded + "<192.168.1.10<MyDevice<0"
    parsed = _parse_raw_entries(entry_raw)
    assert len(parsed) == 1
    assert_eq(parsed[0]["mac"], "D6:E8:06:F4:32:8A", "A: MAC normalization failed")
    print("PASS A")

def test_B_normal_mac_remains():
    # B) Normal MAC without escaping
    mac = "AA:24:31:7D:B4:93"
    wrapper = f"cmdresult = '{mac}';"
    decoded = parse_shell_cgi_result(wrapper)
    assert_eq(decoded, mac, "B: normal MAC decode changed")
    # Via static lease entry
    entry = f"{mac}<192.168.1.100<HostA<0"
    parsed = _parse_raw_entries(entry)
    assert_eq(parsed[0]["mac"], mac, "B: parse changed normal MAC")
    # Serialize must remain canonical, no backslashes
    ser = _serialize_entry(mac, "192.168.1.100", "HostA", "0")
    assert_eq(ser, f"{mac}<192.168.1.100<HostA<0", "B: serialize added backslash")
    assert "\\:" not in ser and "\\x5c" not in ser.lower(), "B: serialize contains escaped colon"
    print("PASS B")

def test_C_complete_entry():
    # C) Complete dhcpd_static entry
    raw = "AA:BB:CC:DD:EE:FF<192.168.1.5<MyHost<0"
    parsed = _parse_raw_entries(raw)
    assert_eq(len(parsed), 1)
    assert_eq(parsed[0]["mac"], "AA:BB:CC:DD:EE:FF")
    assert_eq(parsed[0]["ip"], "192.168.1.5")
    assert_eq(parsed[0]["hostname"], "MyHost")
    assert_eq(parsed[0]["flag"], "0")
    # Hostname with hyphen
    raw2 = "11:22:33:44:55:66<10.0.0.2<my-device-01<0"
    parsed2 = _parse_raw_entries(raw2)
    assert_eq(parsed2[0]["hostname"], "my-device-01")
    print("PASS C")

def test_D_roundtrip_canonical():
    # D) serialize -> parse -> serialize remains canonical
    mac = "D6:E8:06:F4:32:8A"
    ip = "192.168.1.10"
    host = "TestHost"
    ser1 = _serialize_entry(mac, ip, host, "0")
    assert "\\:" not in ser1, "D: ser1 contains colon escape"
    parsed = _parse_raw_entries(ser1)
    ser2 = _serialize_entry(parsed[0]["mac"], parsed[0]["ip"], parsed[0]["hostname"], parsed[0]["flag"])
    assert_eq(ser1, ser2, "D: roundtrip not canonical")
    # From corrupted input, after normalize then serialize, should be canonical
    corrupted = r"D6\:E8\:06\:F4\:32\:8A<192.168.1.10<Corrupt<0"
    parsed_c = _parse_raw_entries(corrupted)
    assert_eq(parsed_c[0]["mac"], mac, "D: corrupted not normalized")
    ser_c = _serialize_entry(parsed_c[0]["mac"], parsed_c[0]["ip"], parsed_c[0]["hostname"], parsed_c[0]["flag"])
    assert_eq(ser_c, f"{mac}<192.168.1.10<Corrupt<0")
    # Also \x5c corruption
    corrupted2 = r"D6\x5c:E8\x5c:06\x5c:F4\x5c:32\x5c:8A<192.168.1.10<X<0"
    parsed_c2 = _parse_raw_entries(corrupted2)
    assert_eq(parsed_c2[0]["mac"], mac, "D: x5c corrupted not normalized")
    print("PASS D")

def test_E_multiple_devices_rename():
    # E) Multiple devices must remain intact when renaming one
    raw = "AA:BB:CC:DD:EE:FF<192.168.1.10<Host1<0>11:22:33:44:55:66<192.168.1.11<Host2<0>77:88:99:AA:BB:CC<192.168.1.12<Host3<0"
    entries, _ = _parse_raw_entries(raw), raw
    # Simulate set_static_hostname_sync logic: re-serialize all, update middle
    from router.static_leases import _serialize_entry
    mac_target = "11:22:33:44:55:66"
    new_host = "Renamed2"
    new_parts = []
    for ent in entries:
        if ent["mac"] == mac_target:
            new_parts.append(_serialize_entry(mac_target, ent["ip"], new_host, ent["flag"]))
        else:
            new_parts.append(_serialize_entry(ent["mac"], ent["ip"], ent["hostname"], ent["flag"]))
    new_raw = ">".join(new_parts)
    parsed_new = _parse_raw_entries(new_raw)
    assert_eq(len(parsed_new), 3, "E: device count changed")
    assert_eq(parsed_new[0]["mac"], "AA:BB:CC:DD:EE:FF")
    assert_eq(parsed_new[0]["hostname"], "Host1")
    assert_eq(parsed_new[1]["mac"], mac_target)
    assert_eq(parsed_new[1]["hostname"], new_host)
    assert_eq(parsed_new[2]["mac"], "77:88:99:AA:BB:CC")
    assert_eq(parsed_new[2]["hostname"], "Host3")
    # Ensure no corruption introduced
    for p in parsed_new:
        assert "\\" not in p["mac"] and "\\x" not in p["mac"].lower()
    print("PASS E")

def test_F_cmdresult_not_stored():
    # F) literal cmdresult wrapper must NOT be stored as MAC
    # Simulate corrupted fetch where wrapper was stored verbatim via old bug
    # Inner content is "2A:B5:94:90:99:86<192.168.1.20<Host<0" but outer wrapper was saved
    outer = "cmdresult = '2A:B5:94:90:99:86<192.168.1.20<Host<0';"
    # If someone mistakenly stored outer as dhcpd_static value, then on next fetch
    # the router would JS-encode it and return outer wrapper again with \x27
    # Our parse + normalize should not produce MAC "cmdresult"
    # Test the observed nested case: cmdresult = 'cmdresult = \x272A:B5:94:90:99:86...'
    nested_wrapper = "cmdresult = 'cmdresult = \\x272A:B5:94:90:99:86<192.168.1.20<Host<0';"
    decoded = parse_shell_cgi_result(nested_wrapper)
    # Decoded inner should be "cmdresult = '2A:B5:..."
    assert "2A:B5:94:90:99:86" in decoded, "F: nested decode missing MAC"
    parsed = _parse_raw_entries(decoded)
    # The first entry's mac should be extracted as 2A:B5... not "cmdresult"
    # Even if parsing includes prefix, _normalize_mac extracts canonical MAC via regex
    found_macs = [p["mac"] for p in parsed]
    assert "2A:B5:94:90:99:86" in found_macs, f"F: MAC not extracted {found_macs}"
    for mac in found_macs:
        assert mac.upper() != "CMDRESULT", "F: cmdresult stored as MAC"
        assert mac.count(":") == 5, f"F: malformed MAC {mac}"
    # Also test direct outer not treated as MAC string
    simple = "cmdresult = 'hello';"
    assert_eq(parse_shell_cgi_result(simple), "hello", "F: simple hello decode failed")
    # Ensure escape handling for other JS escapes
    assert_eq(_decode_js_escapes(r"\x22hello\x22"), '"hello"', "F: x22 decode")
    assert_eq(_decode_js_escapes(r"a\nb"), "a\nb", "F: \\n decode")
    assert_eq(_decode_js_escapes(r"a\tb"), "a\tb", "F: \\t decode")
    assert_eq(_decode_js_escapes(r"a\rb"), "a\rb", "F: \\r decode")
    assert_eq(_decode_js_escapes(r"a\\b"), "a\\b", "F: \\\\ decode")
    print("PASS F")

def test_escape_no_colon():
    # Extra: ensure _escape_field does not escape colon
    ser = _escape_field("AA:BB:CC:DD:EE:FF")
    assert_eq(ser, "AA:BB:CC:DD:EE:FF", "escape should not touch colon")
    # But must escape < > \
    assert_eq(_escape_field("a<b>c\\d"), r"a\<b\>c\\d")
    print("PASS escape")

def test_normalize_variants():
    assert_eq(_normalize_mac(r"D6\x5c:E8\x5c:06\x5c:F4\x5c:32\x5c:8A"), "D6:E8:06:F4:32:8A")
    assert_eq(_normalize_mac(r"D6\:E8\:06\:F4\:32\:8A"), "D6:E8:06:F4:32:8A")
    assert_eq(_normalize_mac("d6:e8:06:f4:32:8a"), "D6:E8:06:F4:32:8A")
    print("PASS normalize")

if __name__ == "__main__":
    test_A_js_wrapper_and_mac_normalization()
    test_B_normal_mac_remains()
    test_C_complete_entry()
    test_D_roundtrip_canonical()
    test_E_multiple_devices_rename()
    test_F_cmdresult_not_stored()
    test_escape_no_colon()
    test_normalize_variants()
    print("\nAll tests PASSED")
