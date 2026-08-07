import re
from datetime import datetime
from zoneinfo import ZoneInfo
import demjson3
from logger import logger
from config import (
    ROUTER_URL,
    ROUTER_SESSION,
)

def _decode_date(n):
    """Decode Tomato firmware date encoding to (year, month, day)."""
    year  = ((n >> 16) & 0xFF) + 1900
    month = (n >> 8) & 0xFF
    day   = n & 0xFF

    return year, month, day


def get_speed_history():
    """Fetch current speed/traffic history from router."""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    }

    try:
        url = f"{ROUTER_URL}/update.cgi"

        ROUTER_SESSION.post(
            url,
            headers=headers,
            data="exec=ipt_bandwidth&arg0=start&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )

        r = ROUTER_SESSION.post(
            url,
            headers=headers,
            data="exec=ipt_bandwidth&arg0=speed&_http_id=TIDe5b1505eeac7f67f",
            timeout=30
        )

        match = re.search(
            r"speed_history\s*=\s*(\{.*?\});",
            r.text,
            re.DOTALL
        )

        if not match:
            logger.warning(
                "speed_history block not found in router response."
            )
            return {}

        return demjson3.decode(match.group(1))

    except Exception as e:
        logger.error(
            f"Error fetching speed history: {e}"
        )
        return {}


def get_daily_history():
    """Fetch daily traffic history from router (JFFS2)."""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest"
    }

    try:
        url = f"{ROUTER_URL}/update.cgi"

        r = ROUTER_SESSION.post(
            url,
            headers=headers,
            data="exec=ipt_bandwidth&arg0=daily&_http_id=TIDe5b1505eeac7f67f",
            timeout=30
        )

        match = re.search(
            r"daily_history\s*=\s*(\[.*?\]);",
            r.text,
            re.DOTALL
        )

        if not match:
            logger.warning(
                "daily_history block not found in router response."
            )
            return []

        return demjson3.decode(match.group(1))

    except Exception as e:
        logger.error(
            f"Error fetching daily history: {e}"
        )
        return []

def get_today_usage(daily_history):
    """Extract today's usage data from daily history."""
    now   = datetime.now(ZoneInfo("Africa/Cairo"))
    tomato_month = now.month - 1
    today = (now.year, tomato_month, now.day)
    result = {}
    for entry in daily_history:
        if len(entry) < 4:
            continue
        y, m, d = _decode_date(entry[0])
        if (y, m, d) != today:
            continue
        ip       = entry[1]
        rx_bytes = entry[2]
        tx_bytes = entry[3]
        if ip not in result:
            result[ip] = {"rx": 0, "tx": 0}
        result[ip]["rx"] += rx_bytes
        result[ip]["tx"] += tx_bytes
    return result


def get_today_combined(speed_history, daily_history):
    """Combine speed history and daily history for comprehensive traffic view."""
    jffs_today = get_today_usage(daily_history)
    result = {}
    for ip, data in jffs_today.items():
        result[ip] = data["rx"] + data["tx"]
    for ip, data in speed_history.items():
        if not ip or ip.startswith("_") or ip.endswith(".0"):
            continue
        rx = data.get("rx_total", 0) if isinstance(data, dict) else data
        tx = data.get("tx_total", 0) if isinstance(data, dict) else 0
        speed_total = rx + tx
        result[ip] = max(result.get(ip, 0), speed_total)

    return result