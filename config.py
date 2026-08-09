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

RADIUS_SESSION = requests.Session()