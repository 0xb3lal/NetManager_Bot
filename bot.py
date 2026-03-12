import os
import re
import hmac
import copy
import urllib3
import hashlib
import logging
import asyncio
import discord
import requests
import demjson3
from datetime import datetime
from discord.ext import tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord import app_commands
from logging.handlers import RotatingFileHandler
from requests.exceptions import ReadTimeout, ConnectionError
urllib3.disable_warnings()
# ========= CONFIG =========
load_dotenv()
D_USERNAME = os.getenv("D_USERNAME")         # Dashboard Username (radiusmanager/user.php)
D_PASSWORD = os.getenv("D_PASSWORD")         # Dashboard Password (radiusmanager/user.php)
ROUTER_URL = "http://192.168.1.1:7080"
ROUTER_AUTH = (os.getenv("ROUTER_USER"),
               os.getenv("ROUTER_PASS"))
THRESHOLD = 3.0
BANNED_MACS = set()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord.Object(id=1475047474832867338) 
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) if os.getenv("CHANNEL_ID") else 0
CONFIG_FILE = "settings.conf"
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

# ========= LOAD THRESHOLD HELPER =========
def load_threshold():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                for line in f:
                    if line.startswith("THRESHOLD="):
                        return float(line.split("=")[1].strip())
    except Exception as e:
        logger.error(f"Error loading config: {e}")
    return 3.0  

def save_threshold(value):
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(f"THRESHOLD={value}\n")
            f.write(f"# Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    except Exception as e:
        logger.error(f"Error saving config: {e}")

THRESHOLD = load_threshold()

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

def check_and_lock(bot_instance):
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}

    try:
        session.post("http://10.0.0.254/radiusmanager/user.php?cont=login", data=payload, timeout=10)
        session.get("http://10.0.0.254/radiusmanager/user.php?cont=change_lang&lang=English", timeout=10)
        dash = session.get("http://10.0.0.254/radiusmanager/user.php", timeout=10)
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
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}

        if traffic_value < THRESHOLD:
            enable_lockdown(router, headers, force_lock=True)
            e_title, e_color = "`❌` System Lockdown", 0xff4747
        else:
            enable_lockdown(router, headers, force_lock=False)
            e_title, e_color = "`✅` System Normal", 0x47ff7e

        balance_label = "Balance:".ljust(9)
        limit_label   = "Limit:".ljust(9)
        
        status_box = (
            f"```\n"
            f"{balance_label} {available_traffic}\n"
            f"{limit_label} {THRESHOLD} GB\n"
            f"```"
        )
        embed = discord.Embed(
            title=e_title,
            description=status_box,
            color=e_color
        )   
        async def safe_send():
            try:
                channel = bot_instance.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)
            except Exception:
                logger.error("Failed to push status update to Discord")

        if bot_instance.loop.is_running():
            bot_instance.loop.create_task(safe_send())
                
    except Exception as e:
        logger.error(f"Main Check Error: {e}")

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

# ========= NETWORK USAGE HELPERS =========
def bytes_to_mb(value):
    return value / (1024 * 1024)

def get_speed_history():
    router = requests.Session()
    router.auth = ROUTER_AUTH
    router.verify = False

    url = f"{ROUTER_URL}/update.cgi"
    data = "exec=ipt_bandwidth&arg0=speed&_http_id=TIDe5b1505eeac7f67f"
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": ROUTER_URL + "/",
        "Origin": ROUTER_URL
    }
    cookies = {
        "tomato_ipt_tab": "192.168.1.0",
        "tomato_ipt_refresh": "1"
    }
    try:
        r = router.post(url, headers=headers, cookies=cookies, data=data, timeout=10)
        match = re.search(r"speed_history\s*=\s*(\{.*?\});", r.text, re.DOTALL)
        if not match:
            logger.warning("speed_history block not found in router response.")
            return {}
        return demjson3.decode(match.group(1))
    except Exception as e:
        logger.error(f"Error fetching speed history: {e}")
        return {}

def get_dhcp_mapping():
    """Return dict mapping IP -> (device name, MAC) from DHCP leases"""
    router = requests.Session()
    router.auth = ROUTER_AUTH
    router.verify = False

    url = f"{ROUTER_URL}/update.cgi"
    data = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": ROUTER_URL + "/",
        "Origin": ROUTER_URL
    }
    try:
        r = router.post(url, headers=headers, data=data, timeout=10)
        match = re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text, re.DOTALL)
        if not match:
            logger.warning("dhcpd_lease block not found in router response.")
            return {}
        leases = demjson3.decode(match.group(1))
        mapping = {}
        for lease in leases:
            # lease = [name, ip, mac, lease_time]
            name, ip, mac = lease[0], lease[1], lease[2]
            mapping[ip] = (name, mac.upper())
        return mapping
    except Exception as e:
        logger.error(f"Error fetching DHCP mapping: {e}")
        return {}

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
        if (current.lower() in name.lower() or current.lower() in mac.lower())
        and mac not in BANNED_MACS
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
    logger.info(f"ACTION: /blk | User: {interaction.user} | Target: {mac}")
    
    await interaction.response.defer() 
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        mac_upper = mac.upper()
        ban_mac(router, headers, mac_upper)
        
        device_name = MACS_LIST.get(mac_upper, "Unknown Device")
        
        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name_fixed = MACS_LIST.get(m, 'Unknown')[:12].ljust(12)
            lines.append(f"{i:02d}. {name_fixed} | {m}")
        
        current_list = "\n".join(lines) if lines else "No devices banned"

        embed = discord.Embed(
            title="`🚫` Device Blocked Successfully",
            description=f"**Target:** `{device_name}`\n**MAC:** `{mac_upper}`",
            color=0xff4747
        )
        
        embed.add_field(
            name="`📝` Updated Banned List",
            value=f"```\n{current_list}```",
            inline=False
        )

        await interaction.followup.send(embed=embed)
        logger.info(f"SUCCESS: {mac_upper} blocked. Total banned: {len(BANNED_MACS)}")
        
    except Exception as e:
        logger.error(f"FAILURE: {e}")
        try:
            await interaction.followup.send("`❌` Router Error: Connection timed out or failed.")
        except:
            pass

# --------- /rm ---------
@bot.tree.command(name="rm", description="Unban a device from the current banned list")
@app_commands.autocomplete(mac=banned_macs_autocomplete)
async def rm(interaction: discord.Interaction, mac: str):
    logger.info(f"ACTION: /rm | User: {interaction.user} | Target MAC: {mac}")
    
    await interaction.response.defer()
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL} 
        mac_upper = mac.upper()
        
        unban_mac(router, headers, mac_upper)
        
        device_name = MACS_LIST.get(mac_upper, "Unknown Device")
        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name_fixed = MACS_LIST.get(m, 'Unknown')[:12].ljust(12)
            lines.append(f"{i:02d}. {name_fixed} | {m}")
        
        current_list = "\n".join(lines) if lines else "No devices currently banned"

        embed = discord.Embed(
            title="`✅` Device Unblocked Successfully",
            description=f"**Target:** `{device_name}`\n**MAC:** `{mac_upper}`",
            color=0x2ecc71 
        )
        
        embed.add_field(
            name="`📝` Updated Banned List",
            value=f"```\n{current_list}```",
            inline=False
        )

        await interaction.followup.send(embed=embed)
        logger.info(f"SUCCESS: {mac_upper} unblocked. New list size: {len(BANNED_MACS)}")
        
    except Exception as e:
        logger.error(f"FAILURE: {e}")
        try:
            await interaction.followup.send("`❌` Router Error: Failed to remove block.")
        except:
            pass

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
        title="`📋` Known MAC Names List",
        description=msg,
        color=embed_color
    )

    await interaction.response.send_message(embed=embed)

# --------- /list---------
@bot.tree.command(name="list", description="List currently banned MACs")
async def list_banned(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} requested the banned MACs list.")
    
    try:
        if BANNED_MACS:
            lines = []
            for i, m in enumerate(BANNED_MACS, 1):
                name_fixed = MACS_LIST.get(m, 'Unknown')[:12].ljust(12)
                lines.append(f"{i:02d}. {name_fixed} | {m}")
            
            banned_output = f"```\n" + "\n".join(lines) + "```"
            count = len(BANNED_MACS)
            embed_color = 0xe67e22
        else:
            banned_output = "✨ *No devices are currently under lockdown.*"
            count = 0 
            embed_color = 0x95a5a6 

        embed = discord.Embed(
            title=f"`🚫` Banned Devices ({count})",
            description=banned_output,
            color=embed_color
        )

        embed.set_footer(text="Use /rm to unblock a specific device")
        await interaction.response.send_message(embed=embed)
        logger.info(f"Sent banned list ({count} devices) to {interaction.user}.")
        
    except Exception as e:
        logger.error(f"Error while listing banned MACs: {e}")
        await interaction.response.send_message("`❌` Failed to retrieve the list.", ephemeral=True)

# --------- /balance ---------
@bot.tree.command(name="balance", description="Check current available traffic")
async def balance(interaction: discord.Interaction):
    logger.info(f"User {interaction.user} requested balance check.")
    await interaction.response.defer()
    traffic = get_balance()
    
    if traffic:
        balance_label = "Current Balance:".ljust(17)
        limit_label   = "System Limit:".ljust(17)
        
        status_box = (
            f"```\n"
            f"{balance_label} {traffic}\n"
            f"{limit_label} {THRESHOLD} GB\n"
            f"```\n"
            f"`💡` *Status is updated automatically every hour.*"
        )
        
        embed = discord.Embed(
            title="`📊` Network Status",
            description=status_box,
            color=0x3498db
        )
    
        await interaction.followup.send(embed=embed)
        logger.info(f"Balance sent to {interaction.user}: {traffic}")
    else:
        embed = discord.Embed(
            title="`❌` System Error",
            description="`Could not fetch balance. Radius server unreachable.`",
            color=0xe74c3c
        )
        await interaction.followup.send(embed=embed)

# --------- /netstat ---------
@bot.tree.command(name="netstat", description="Show online devices with aligned status")
async def netstat(interaction: discord.Interaction):
    logger.info(f"Full network status requested by {interaction.user}")
    await interaction.response.defer()
    
    try:
        raw_content = get_router_devices_raw()
        speed_history = get_speed_history()
        dhcp_mapping = get_dhcp_mapping()

        if not raw_content or not speed_history:
            await interaction.followup.send("`❌` Failed to fetch data from router.")
            return

        active_macs = {}
        device_pattern = r"['\"](([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})['\"].*?(-\d+)"
        matches = re.finditer(device_pattern, raw_content, re.DOTALL)

        for match in matches:
            mac = match.group(1).upper()
            rssi = int(match.group(3))
            if -100 < rssi < 0:
                quality = min(max(2 * (rssi + 100), 0), 100)
                active_macs[mac] = quality

        combined_data = []
        total_traffic_mb = 0.0

        for ip, data in speed_history.items():
            if not ip or ip.startswith("_") or ip.endswith(".0"): continue
            rx_total = data.get("rx_total", 0) if isinstance(data, dict) else data
            tx_total = data.get("tx_total", 0) if isinstance(data, dict) else 0
            usage_mb = bytes_to_mb(rx_total + tx_total)
            total_traffic_mb += usage_mb

            name, mac = dhcp_mapping.get(ip, (ip, "Unknown"))
            if mac in active_macs:
                combined_data.append({
                    "name": MACS_LIST.get(mac, name),
                    "usage": usage_mb,
                    "signal": active_macs[mac]
                })

        combined_data.sort(key=lambda x: x['usage'], reverse=True)

        def fmt_usage(mb: float) -> str:
            return f"{mb / 1024:.1f}GB" if mb >= 1024 else f"{int(mb)}MB"

        def get_status_icon(usage_mb):
            if usage_mb >= 2048: return "🔴"
            if usage_mb >= 500:  return "🟡"
            return "🟢"

        if combined_data:
            embed = discord.Embed(color=0x2ecc71)
            header = f"`📡` **Network Live Status ({len(combined_data)} Devices)**\n"
            
            lines = []
            for dev in combined_data[:15]:
                icon = get_status_icon(dev['usage'])
                u_str = fmt_usage(dev['usage'])
                sig_str = f"{dev['signal']}%"
                
                name_fixed = dev['name'][:12].ljust(12)
                sig_fixed = sig_str.rjust(4)
                usage_fixed = u_str.rjust(6)

                lines.append(f"{icon} `{name_fixed} | 📶{sig_fixed} | 📊{usage_fixed}`")
            
            embed.description = header + "\n" + "\n".join(lines)
            embed.set_footer(text=f"Total Network Load: {fmt_usage(total_traffic_mb)}")
        else:
            embed = discord.Embed(description="✨ No active devices found.", color=0x95a5a6)

        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in netstat: {e}")
        await interaction.followup.send("`❌` Error compiling network status.")

# --------- /limit ---------
@bot.tree.command(name="limit", description="Change the traffic threshold (GB)")
@app_commands.describe(limit="The new threshold value in GB (e.g. 5.0)")
async def set_limit(interaction: discord.Interaction, limit: float):
    global THRESHOLD
    
    await interaction.response.defer()
    try:
        old_limit = THRESHOLD
        THRESHOLD = limit
        
        save_threshold(limit)
        logger.info(f"User {interaction.user} updated THRESHOLD to {limit}")
        
        label_old = "Old Limit:".ljust(14)
        label_new = "New Limit:".ljust(14)        
        status_box = (
            f"```\n"
            f"{label_old} {old_limit} GB\n"
            f"{label_new} {THRESHOLD} GB\n"
            f"```\n"
            f"`✅` *Settings updated.*"
        )
        
        embed = discord.Embed(
            title="`⚙️` System Configuration Update",
            description=status_box,
            color=0xf1c40f
        )
        
        await interaction.followup.send(embed=embed)
        
        await asyncio.to_thread(check_and_lock, bot)

    except Exception as e:
        logger.error(f"Error in limit command: {e}")
        await interaction.followup.send("`❌` Failed to update configuration.")

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