import os
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
DISCORD_TOKEN = "MTQ3NTA0NjE4NjEzMjk2MzQ4Mw.G8vYv7.Pw__PzNL3v6lAQTc1rMC7KHk_BJtynKEfQeFwA"
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
    "4C:20:B8:87:12:E2": "Me/Iphone",
    "F2:72:C9:B8:4B:C7": "Tablet",
    "DC:53:60:73:FD:84": "Kali linux",
    "D6:62:9E:2B:31:3D": "Yousef"
}
# ========= LOGING SYS =========
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_path = os.path.join(log_dir, "bot.log")

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[34m",      # Blue
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
    log_path,
    maxBytes=5*1024*1024,
    backupCount=1,
    encoding='utf-8',
    mode='w' 
)
console_handler = logging.StreamHandler()
formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %I:%M:%S %p"
)
color_formatter = ColorFormatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %I:%M:%S %p"
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
        
# ========= ONLINE DEVICES HELPER =========
def get_router_devices_raw():
    router = requests.Session()
    router.auth = ROUTER_AUTH
    try:
        url = f"{ROUTER_URL}/status-devices.asp"
        headers = {
            "Referer": f"{ROUTER_URL}/",
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,xml;q=0.9,*/*;q=0.8"
        }
        
        response = router.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.text
        return ""
    except Exception as e:
        logger.error(f"Error fetching router devices page: {e}")
        return ""

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

        if traffic_value < THRESHOLD:
            enable_lockdown(router, headers, force_lock=True)
            e_title = "⚠️ LOCKDOWN ENABLED"
            e_color = discord.Color.red()
        else:
            enable_lockdown(router, headers, force_lock=False)
            e_title = "✅ NORMAL MODE"
            e_color = discord.Color.green()

        content = f"📊 **Balance:** `{available_traffic}`\n"
        content += f"⏳ **Limit:** `{THRESHOLD}`"

        embed = discord.Embed(
            title=e_title,
            description=content,
            color=e_color
        )
        async def safe_send():
            try:
                channel = bot_instance.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)
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

        embed = discord.Embed(
            title="🚫 Device Blocked",
            description=f"**Device:** `{device_name}`\n**MAC:** `{mac_upper}`",
            color=discord.Color.red()
        )
        
        embed.add_field(
            name="📝 Updated Banned List:",
            value=f"```\n{current_list if current_list else 'No devices banned'}```",
            inline=False
        )

        await interaction.response.send_message(embed=embed)
        logger.info(f"SUCCESS: {mac_upper} ({device_name}) blocked by {interaction.user}. Total banned: {len(BANNED_MACS)}")
        
    except Exception as e:
        logger.error(f"FAILURE: Could not block {mac} for {interaction.user}. Error: {e}")
        error_embed = discord.Embed(
            title="❌ Router Error",
            description="**Status:** `Could not apply block. Check bot.log for details.`",
            color=discord.Color.dark_red()
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)

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

        embed = discord.Embed(
            title="✅ Device Unblocked",
            description=f"**Device:** `{device_name}`\n**MAC:** `{mac}`",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📝 Updated Banned List:",
            value=f"```\n{current_list if current_list else 'No devices banned'}```",
            inline=False
        )

        await interaction.response.send_message(embed=embed)
        logger.info(f"SUCCESS: {mac} has been unblocked by {interaction.user}. New list size: {len(BANNED_MACS)}")
        
    except Exception as e:
        logger.error(f"FAILURE: Could not unblock {mac} for {interaction.user}. Error: {e}")
        error_embed = discord.Embed(
            title="❌ Error",
            description="**Status:** `Failed to communicate with the router. Check bot.log for details.`",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)

# --------- /macs ---------
@bot.tree.command(name="macs", description="List known MAC names")
async def macs(interaction: discord.Interaction):
    if MACS_LIST:
        msg = "\n".join(f"`{mac}` : **{name}**" for mac, name in MACS_LIST.items())
        embed_color = discord.Color.blue()
    else:
        msg = "*No MAC addresses found in the list.*"
        embed_color = discord.Color.light_grey()

    embed = discord.Embed(
        title="📋 Known MAC Names List",
        description=msg,
        color=embed_color
    )

    await interaction.response.send_message(embed=embed)

# --------- /list---------
@bot.tree.command(name="list", description="List currently banned MACs")
async def list_banned(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} (ID: {interaction.user.id}) requested the banned MACs list.")
    try:
        if BANNED_MACS:
            banned_list = "\n".join(
                f"**{i+1}-** `{m}` ({MACS_LIST.get(m, 'Unknown')})" 
                for i, m in enumerate(BANNED_MACS)
            )
            count = len(BANNED_MACS)
            embed_color = discord.Color.orange()
        else:
            banned_list = "*No MACs are currently banned.*"
            count = 0 
            embed_color = discord.Color.light_grey()

        embed = discord.Embed(
            title=f"🚫 Banned MACs List ({count})",
            description=banned_list,
            color=embed_color
        )

        await interaction.response.send_message(embed=embed)
        logger.info(f"Sent banned list ({count} devices) to {interaction.user}.")
        
    except Exception as e:
        logger.error(f"Error while listing banned MACs for {interaction.user}: {e}")
        error_embed = discord.Embed(
            title="❌ Error",
            description="**Status:** `An unexpected error occurred while fetching the list.`",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
# --------- /balance---------
@bot.tree.command(name="balance", description="Check current available traffic")
async def balance(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} requested balance check.")
    await interaction.response.defer() 
    traffic = get_balance()
    
    if traffic:
        embed = discord.Embed(
            title="📊 Network Status",
            description=f"**Current Balance:** `{traffic}`",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)
        logger.info(f"Balance sent to {interaction.user}: {traffic}")
    else:
        embed = discord.Embed(
            title="❌ System Error",
            description="**Status:** `Could not fetch balance. The Radius server might be down.`",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)
        logger.error(f"Failed to provide balance to {interaction.user} due to server error.")

# --------- /online ---------
@bot.tree.command(name="online", description="Show currently active devices")
async def online(interaction: discord.Interaction):
    import re
    logger.info(f"User {interaction.user} requested online devices.")
    await interaction.response.defer()
    
    try:
        raw_content = get_router_devices_raw()
        if not raw_content:
            await interaction.followup.send("⚠️ Router connection failed.")
            return

        active_devices = []
        seen_macs = set()
        
        device_pattern = r"['\"](([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})['\"].*?(-\d+)"
        matches = re.finditer(device_pattern, raw_content, re.DOTALL)

        for match in matches:
            mac = match.group(1).upper()
            rssi = int(match.group(3))
            
            if rssi >= 0 or rssi < -100:
                continue

            if mac not in seen_macs:
                device_name = MACS_LIST.get(mac, "Unknown Device")
                active_devices.append({
                    "name": device_name,
                    "mac": mac,
                    "rssi": rssi
                })
                seen_macs.add(mac)

        active_devices.sort(key=lambda x: x['rssi'], reverse=True)

        if active_devices:
            embed = discord.Embed(color=0x2ecc71)
            
            count = len(active_devices)
            header = f"📡 **{count} Active Client{'s' if count > 1 else ''}**\n\n"
            
            lines = []
            for dev in active_devices:
                quality = min(max(2 * (dev['rssi'] + 100), 0), 100)
                
                if quality >= 80: icon = "🟢"
                elif quality >= 50: icon = "🟡"
                else: icon = "🔴"

                lines.append(f"{icon} **{dev['name']}** — `{quality}%`")
            
            embed.description = header + "\n".join(lines)
        else:
            embed = discord.Embed(
                description="✨ No active devices detected.",
                color=0x95a5a6
            )

        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in Tomato parsing: {e}")
        await interaction.followup.send("⚠️ Parsing error.")

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