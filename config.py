import os
import urllib3
import requests
import discord
from dotenv import load_dotenv
load_dotenv()

REQUIRED_ENV_VARS = (
    "D_USERNAME", "D_PASSWORD", "ROUTER_URL", "RADIUS_URL",
    "DISCORD_TOKEN", "CHANNEL_ID", "ROUTER_USER", "ROUTER_PASS"
)

missing_env = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_env:
    raise RuntimeError(
        f"Missing required environment variable(s): {', '.join(missing_env)}"
    )

D_USERNAME = os.getenv("D_USERNAME")
D_PASSWORD = os.getenv("D_PASSWORD")
ROUTER_URL = os.getenv("ROUTER_URL")
RADIUS_URL = os.getenv("RADIUS_URL")
ROUTER_AUTH = (
    os.getenv("ROUTER_USER"),
    os.getenv("ROUTER_PASS"),
)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord.Object(id=1475047474832867338)
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
WIFI_IFACE = os.getenv("WIFI_IFACE", "eth1")

# Unknown-hostname traffic anomaly check
UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB = float(os.getenv("UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB", "100"))
ANOMALY_CHECK_INTERVAL_MINUTES = int(os.getenv("ANOMALY_CHECK_INTERVAL_MINUTES", "10"))

# Stale device cleanup (devices unseen by the router for this long are removed)
STALE_DEVICE_CHECK_INTERVAL_DAYS = int(os.getenv("STALE_DEVICE_CHECK_INTERVAL_DAYS", "3"))
STALE_DEVICE_THRESHOLD_DAYS = int(os.getenv("STALE_DEVICE_THRESHOLD_DAYS", "3"))

_invalid_limits = []
if min(STALE_DEVICE_CHECK_INTERVAL_DAYS, STALE_DEVICE_THRESHOLD_DAYS, ANOMALY_CHECK_INTERVAL_MINUTES) < 1:
    _invalid_limits.append(
        "STALE_DEVICE_CHECK_INTERVAL_DAYS, STALE_DEVICE_THRESHOLD_DAYS and "
        "ANOMALY_CHECK_INTERVAL_MINUTES must be >= 1"
    )
if UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB <= 0:
    _invalid_limits.append("UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB must be > 0")
if _invalid_limits:
    # Fail fast: a zero stale threshold would purge the whole device table,
    # and a zero interval crashes task registration at startup.
    raise RuntimeError(f"Invalid configuration: {'; '.join(_invalid_limits)}")

# URL validation for router/radius endpoints
from urllib.parse import urlparse as _urlparse
for _name, _val in (("ROUTER_URL", ROUTER_URL), ("RADIUS_URL", RADIUS_URL)):
    _parsed = _urlparse(_val)
    if _parsed.scheme not in ("http", "https") or not _parsed.hostname:
        raise RuntimeError(f"Invalid {_name}={_val!r}: must be http(s)://host[:port][/path]")
# Normalize trailing slash to avoid double-slash in URL construction (e.g. http://192.168.1.1/ + /tomato.cgi)
ROUTER_URL = ROUTER_URL.rstrip("/")
RADIUS_URL = RADIUS_URL.rstrip("/")

# Telegram bot token and chat ID for notifications
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# GLOBAL SESSIONS
urllib3.disable_warnings()

ROUTER_SESSION = requests.Session()
ROUTER_SESSION.auth = ROUTER_AUTH
ROUTER_SESSION.verify = False
ROUTER_SESSION.headers.update({
    "Content-Type": "text/plain;charset=UTF-8",
    "Referer": f"{ROUTER_URL}/",
    "Origin": ROUTER_URL,
    "User-Agent": "Mozilla/5.0",
})