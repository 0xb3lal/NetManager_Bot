import os
import urllib3
import requests
import discord
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import urlparse as _urlparse

# --- Required .env values ---
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
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- ROUTER_URL / RADIUS_URL validation & cleanup ---
for _name, _val in (("ROUTER_URL", ROUTER_URL), ("RADIUS_URL", RADIUS_URL)):
    _parsed = _urlparse(_val)
    if _parsed.scheme not in ("http", "https") or not _parsed.hostname:
        raise RuntimeError(f"Invalid {_name}={_val!r}: must be http(s)://host[:port][/path]")
# Normalize trailing slash to avoid double-slash in URL construction (e.g. http://192.168.1.1/ + /tomato.cgi)
ROUTER_URL = ROUTER_URL.rstrip("/")
RADIUS_URL = RADIUS_URL.rstrip("/")

# --- Fixed settings (edit here directly, not via .env) ---
GUILD_ID = discord.Object(id=1475047474832867338)
WIFI_IFACE = "eth1"
UNKNOWN_HOSTNAME_TRAFFIC_THRESHOLD_MB = 100
ANOMALY_CHECK_INTERVAL_MINUTES = 10
STALE_DEVICE_CHECK_INTERVAL_DAYS = 3
STALE_DEVICE_THRESHOLD_DAYS = 3
RSSI_DISPLAY_ENABLED = True
DISTANCE_ESTIMATION_ENABLED = True
RSSI_AT_1M_DBM = -47
WIFI_PATH_LOSS_EXPONENT = 2.7

# --- Router HTTP session ---
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