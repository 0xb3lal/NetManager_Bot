from router.devices import fetch_devlist
from router.traffic import get_daily_history, get_speed_history, get_today_combined
from state import state
from utils.traffic import bytes_to_mb


def get_today_usage_by_mac():
    """Return today's usage per MAC from router stats."""
    speed_history = get_speed_history()
    daily_history = get_daily_history()
    dhcp_leases, _, _ = fetch_devlist()
    ip_to_mac = dict(state.ip_to_mac_cache)
    combined_usage = get_today_combined(speed_history, daily_history)

    usage_by_mac = {}
    for ip, total_bytes in combined_usage.items():
        mac = ip_to_mac.get(ip)
        if not mac:
            continue
        usage_by_mac[mac] = usage_by_mac.get(mac, 0) + bytes_to_mb(total_bytes) / 1024
    return usage_by_mac
