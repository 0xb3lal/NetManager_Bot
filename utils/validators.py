import re


def is_valid_mac(mac: str) -> bool:
    """Validate MAC address format."""
    return bool(re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac, re.IGNORECASE))