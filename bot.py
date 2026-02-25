import hmac
import hashlib
import logging
import asyncio
import discord
import requests
import copy
from discord.ext import tasks
from bs4 import BeautifulSoup
from discord import app_commands
from logging.handlers import RotatingFileHandler
from requests.exceptions import ReadTimeout, ConnectionError

# ========= CONFIG =========
D_USERNAME = "01552802883" # Dashboard Username (radiusmanager/user.php)
D_PASSWORD = "123"         # Dashboard Password (radiusmanager/user.php)
ROUTER_URL = "http://192.168.1.1:7080"
ROUTER_AUTH = ("belal", "107003##$$")
THRESHOLD = 3.0
BANNED_MACS = set()
DISCORD_TOKEN = "MTQ3NTA0NjE4NjEzMjk2MzQ4Mw.GGx6QD.ypWbUDrswE_Wc2EEeyW7CVCwWRZnkuZSb1oMIw"
GUILD_ID = discord.Object(id=1475047474832867338) 
CHANNEL_ID = 1475047475680383081
ALLOWED_MACS = [
    "4C:20:B8:87:12:E2",
    "F8:34:41:DA:93:EB",
    "DC:53:60:73:FD:84"
]
MACS_LIST = {
    "D2:C5:E2:DE:F5:B4": "Baba",
    "5A:CE:CB:B5:D4:C9": "Ziad",
    "F8:34:41:DA:93:EB": "Me/Windows",
    "22:9F:AE:3B:5D:C4": "Mama",
    "1A:8C:37:73:23:8C": "Me/Iphone",
    "F2:72:C9:B8:4B:C7": "Tablet",
    "DC:53:60:73:FD:84": "Kali linux",
    "D6:62:9E:2B:31:3D": "Yousef"
}
# ========= LOGING SYS =========
class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[41m",  # Red background
    }

    RESET = "\033[0m"
    def format(self, record):
        record_copy = copy.copy(record)

        levelname = record_copy.levelname
        if levelname in self.COLORS:
            record_copy.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"

        return super().format(record_copy)
handler = RotatingFileHandler(
    "bot.log",
    maxBytes=5*1024*1024,
    backupCount=1,
    encoding='utf-8'
)
console_handler = logging.StreamHandler()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(message)s"
)
color_formatter = ColorFormatter(
    "%(asctime)s | %(levelname)-8s | %(message)s"
)
handler.setFormatter(formatter)
console_handler.setFormatter(color_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[handler, console_handler]
)
logger = logging.getLogger(__name__)

# ========= HELPERS =========
def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()

def hex_hmac_md5(key, data):
    return hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()

# ========= ROUTER EXEC =========
def run_cmd(router, headers, cmd):
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        logger.debug(f"Sending Command to Router: {cmd}")
        
        response = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data=data,
            timeout=10
        )
        if response.status_code == 200:
            logger.debug(f"Router executed: {cmd} successfully.")
        else:
            logger.error(f"Router returned error code {response.status_code} for command: {cmd}")    
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd: {e}")

# ========= LOCKDOWN LOGIC =========
def clear_lockdown(router, headers):
    logger.info("Clearing all lockdown firewall rules...")
    run_cmd(router, headers, "iptables -D FORWARD -i br0 -j LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -F LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -X LOCKDOWN 2>/dev/null")

def enable_lockdown(router, headers, force_lock=False):
    mode = "FORCE (Whitelist only)" if force_lock else "NORMAL (Banning list)"
    logger.info(f"Applying Firewall Lockdown: Mode={mode}")
    
    run_cmd(router, headers, "iptables -F LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -X LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -N LOCKDOWN 2>/dev/null")

    if force_lock:
        for mac in ALLOWED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd(router, headers, "iptables -A LOCKDOWN -j DROP")
        logger.debug(f"Whitelist applied: {len(ALLOWED_MACS)} devices allowed, others dropped.")
    else:
        for mac in BANNED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j DROP")
        for mac in ALLOWED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd(router, headers, "iptables -A LOCKDOWN -j ACCEPT")
        logger.debug(f"Banned list applied: {len(BANNED_MACS)} devices dropped.")

    run_cmd(router, headers, "iptables -D FORWARD -i br0 -j LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -I FORWARD 1 -i br0 -j LOCKDOWN")
    logger.info("Firewall rules synchronized successfully.")

def ban_mac(router, headers, mac):
    mac = mac.upper()
    if mac not in BANNED_MACS:
        BANNED_MACS.add(mac)
        logger.info(f"Internal: Added {mac} to memory banned set.")
        enable_lockdown(router, headers, force_lock=False)
    else:
        logger.warning(f"Internal: {mac} is already in banned set, skipping rewrite.")

def unban_mac(router, headers, mac):
    mac = mac.upper()
    if mac in BANNED_MACS:
        BANNED_MACS.remove(mac)
        logger.info(f"Internal: Removed {mac} from memory banned set.")
        enable_lockdown(router, headers, force_lock=False)
    else:
        logger.warning(f"Internal: Attempted to unban {mac} but it wasn't in the list.")

# ========= MAIN CHECK =========
def check_and_lock(bot_instance):
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}

    try:
        session.post("http://10.0.0.254/radiusmanager/user.php?cont=login", data=payload)
        session.get("http://10.0.0.254/radiusmanager/user.php?cont=change_lang&lang=English")
        dash = session.get("http://10.0.0.254/radiusmanager/user.php")
        soup = BeautifulSoup(dash.text, "html.parser")

        available_traffic = None
        for td in soup.find_all("td"):
            if "Available total traffic" in td.get_text(strip=True):
                available_traffic = td.find_next_sibling("td").get_text(strip=True)
                break

        if not available_traffic: return
        traffic_value = float(available_traffic.split()[0])
        router = requests.Session()
        router.auth = ROUTER_AUTH
        router.get(ROUTER_URL + "/", timeout=10)
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}

        msg = ""
        if traffic_value < THRESHOLD:
            enable_lockdown(router, headers, force_lock=True)
            msg = f"⚠️ **LOCKDOWN enabled.**\n```\n📊 Balance: {available_traffic}\n⏳ Limit: {THRESHOLD}```"
        else:
            enable_lockdown(router, headers, force_lock=False)
            msg = f"✅ **Normal Mode.**\n```\n📊 Balance: {available_traffic}\n⏳ Limit: {THRESHOLD}```"

        async def safe_send():
            try:
                channel = bot_instance.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(msg)
            except Exception as e:
                logger.exception("Discord send error (Network/Timeout)")

        if bot_instance.loop.is_running():
            bot_instance.loop.create_task(safe_send())
                
    except Exception as e:
        logger.exception("Dashboard/Router Access Error")

# ========= Get Balance Only =========
def get_balance():
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}
    
    try:
        login_url = "http://10.0.0.254/radiusmanager/user.php?cont=login"
        response = session.post(login_url, data=payload, timeout=10)
        response.raise_for_status()
        session.get("http://10.0.0.254/radiusmanager/user.php?cont=change_lang&lang=English", timeout=10)
        dash = session.get("http://10.0.0.254/radiusmanager/user.php", timeout=10)
        dash.raise_for_status()
        soup = BeautifulSoup(dash.text, "html.parser")

        for td in soup.find_all("td"):
            if "Available total traffic" in td.get_text(strip=True):
                balance = td.find_next_sibling("td").get_text(strip=True)
                logger.info(f"Successfully fetched balance: {balance}")
                return balance
        
        logger.warning("Balance field 'Available total traffic' not found in dashboard HTML.")
        return None

    except requests.exceptions.Timeout:
        logger.error("Timeout: Radius Dashboard (10.0.0.254) is not responding.")
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Could not connect to 10.0.0.254. Is the server down?")
    except Exception as e:
        logger.error(f"Unexpected error in get_balance: {e}")
    
    return None

# ========= DISCORD BOT SETUP =========
class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)
        if not traffic_check_task.is_running():
            traffic_check_task.start()

bot = MyBot()

# ========= Helpers Functions =========
async def mac_autocomplete(interaction: discord.Interaction, current: str):
    choices = [
        app_commands.Choice(name=name, value=mac)
        for mac, name in MACS_LIST.items()
        if current.lower() in name.lower() or current.lower() in mac.lower()
    ]
    return choices[:25]

async def banned_macs_autocomplete(interaction: discord.Interaction, current: str):
    choices = [
        app_commands.Choice(name=f"{MACS_LIST.get(mac, 'Unknown')} ({mac})", value=mac)
        for mac in BANNED_MACS
        if current.lower() in mac.lower() or current.lower() in MACS_LIST.get(mac, '').lower()
    ]
    return choices[:25]

def get_banned_list_text():
    if BANNED_MACS:
        return "\n".join(f"{i+1}- {m} ({MACS_LIST.get(m, 'Unknown')})" for i, m in enumerate(BANNED_MACS))
    return "No MACs banned"

# ========= Main Commands =========

# --------- /blk ---------
@bot.tree.command(name="blk", description="Ban a MAC address from the list")
@app_commands.autocomplete(mac=mac_autocomplete)
async def ban(interaction: discord.Interaction, mac: str):
    logger.info(f"ACTION: /blk | User: {interaction.user} (ID: {interaction.user.id}) | Target: {mac}")
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        mac_upper = mac.upper()
        ban_mac(router, headers, mac_upper)
        current_list = get_banned_list_text()
        device_name = MACS_LIST.get(mac_upper, "Unknown Device")
        response_msg = (
            f"🚫 **Blocked:** {device_name} ({mac_upper})\n\n"
            f"**Updated Banned List:**\n"
            f"```\n{current_list}```"
        )   
        await interaction.response.send_message(response_msg)
        logger.info(f"SUCCESS: {mac_upper} ({device_name}) blocked by {interaction.user}. Total banned: {len(BANNED_MACS)}")
    except Exception as e:
        logger.error(f"FAILURE: Could not block {mac} for {interaction.user}. Error: {e}")
        await interaction.response.send_message(
            f"❌ **Router Error:** Could not apply block. Check `bot.log`.", 
            ephemeral=True
        )

# --------- /rm ---------
@bot.tree.command(name="rm", description="Unban a device from the current banned list")
@app_commands.autocomplete(mac=banned_macs_autocomplete)
async def rm(interaction: discord.Interaction, mac: str):
    logger.info(f"ACTION: /rm | User: {interaction.user} (ID: {interaction.user.id}) | Target MAC: {mac}")
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL} 
        mac = mac.upper()
        unban_mac(router, headers, mac)
        current_list = get_banned_list_text()
        device_name = MACS_LIST.get(mac, "Unknown Device")
        response_msg = (
            f"✅ **Unblocked:** {device_name} ({mac})\n\n"
            f"**Updated Banned List:**\n"
            f"```\n{current_list}```"
        )
        await interaction.response.send_message(response_msg)
        logger.info(f"SUCCESS: {mac} has been unblocked by {interaction.user}. New list size: {len(BANNED_MACS)}")
    except Exception as e:
        logger.error(f"FAILURE: Could not unblock {mac} for {interaction.user}. Error: {e}")
        await interaction.response.send_message(f"❌ **Error:** Failed to communicate with the router. Check `bot.log` for details.", ephemeral=True)

# --------- /macs ---------
@bot.tree.command(name="macs", description="List known MAC names")
async def macs(interaction: discord.Interaction):
    msg = "\n".join(f"{mac} : {name}" for mac, name in MACS_LIST.items())
    await interaction.response.send_message(f"**MAC Names List:**\n```\n{msg}```")

# --------- /list---------
@bot.tree.command(name="list", description="List currently banned MACs")
async def list_banned(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} (ID: {interaction.user.id}) requested the banned MACs list.")
    try:
        if BANNED_MACS:
            banned_list = "\n".join(
                f"{i+1}- {m} ({MACS_LIST.get(m, 'Unknown')})" 
                for i, m in enumerate(BANNED_MACS)
            )
            count = len(BANNED_MACS)
        else:
            banned_list = "No MACs banned"
            count = 0     
        await interaction.response.send_message(f"**Banned MACs ({count}):**\n```\n{banned_list}```")
        logger.info(f"Sent banned list ({count} devices) to {interaction.user}.")
    except Exception as e:
        logger.error(f"Error while listing banned MACs for {interaction.user}: {e}")
        await interaction.response.send_message("❌ An unexpected error occurred while fetching the list.", ephemeral=True)

# --------- /balance---------
@bot.tree.command(name="balance", description="Check current available traffic")
async def balance(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} requested balance check.")
    await interaction.response.defer() 
    traffic = get_balance()
    if traffic:
        await interaction.followup.send(f"\n📊 **Current Balance:** __{traffic}__")
        logger.info(f"Balance sent to {interaction.user}: {traffic}")
    else:
        await interaction.followup.send("\n❌ **Error:** Could not fetch balance. The Radius server might be down.")
        logger.error(f"Failed to provide balance to {interaction.user} due to server error.")

# ========= Manage commands =========
purge_group = app_commands.Group(name="purge", description="Commands to delete messages")

@purge_group.command(name="user", description="Delete messages from a specific user")
@app_commands.describe(user="The user to delete messages for", amount="Number of messages to check")
async def purge_user(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    def is_user(m):
        return m.author == user
    deleted = await interaction.channel.purge(limit=amount, check=is_user)
    await interaction.followup.send(f"Deleted {len(deleted)} messages for {user.display_name}", ephemeral=True)

@purge_group.command(name="any", description="Delete any messages in the channel")
@app_commands.describe(amount="Number of messages to delete")
async def purge_any(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {len(deleted)} messages from the channel", ephemeral=True)

bot.tree.add_command(purge_group)

# ========= THREADS =========
@tasks.loop(hours=1.0)
async def traffic_check_task():
    logger.info("Starting scheduled traffic check...")
    try:
        check_and_lock(bot)
        logger.info("Scheduled traffic check completed successfully.")
    except Exception as e:
        logger.exception(f"Unexpected error during traffic check task: {e}")

@traffic_check_task.before_loop
async def before_traffic_check():
    logger.info("Waiting for bot to be ready before starting traffic task...")
    await bot.wait_until_ready()
    logger.info("Bot is ready. Traffic task started.")

async def main():
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    except asyncio.CancelledError:
        logger.warning("Main coroutine cancelled.")
    except Exception as e:
        logger.error(f"Fatal error in main loop: {e}")
        
if __name__ == "__main__":
    logger.info("--- Starting NetManager Bot ---")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received (Ctrl+C).")
    finally:

        if not bot.is_closed():
            asyncio.run(bot.close())
        logger.info("--- Bot has been stopped safely ---")