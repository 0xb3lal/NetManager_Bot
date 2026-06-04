import os
import re
import json
import hmac
import copy
import urllib3
import hashlib
import logging
import asyncio
import discord
import requests
import demjson3
from datetime import datetime, time
from zoneinfo import ZoneInfo
from discord.ext import tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord import app_commands
from logging.handlers import RotatingFileHandler
from requests.exceptions import ReadTimeout, ConnectionError

# ========= CONFIG =========
load_dotenv()
D_USERNAME = os.getenv("D_USERNAME")         # Dashboard Username (radiusmanager/user.php)
D_PASSWORD = os.getenv("D_PASSWORD")         # Dashboard Password (radiusmanager/user.php)
ROUTER_URL = os.getenv("ROUTER_URL")
ROUTER_AUTH = (os.getenv("ROUTER_USER"), os.getenv("ROUTER_PASS"))
THRESHOLD = 3.0
BANNED_MACS = set()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = discord.Object(id=1475047474832867338) 
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) if os.getenv("CHANNEL_ID") else 0
CONFIG_FILE = "settings.conf"
BANNED_MACS_FILE = "bannedDevices.json"
ALLOWED_MACS = [
    "4C:20:B8:87:12:E2",
    "F8:34:41:DA:93:EB",
    "32:AC:87:47:17:5D"
]
MACS_LIST = {
    "D2:C5:E2:DE:F5:B4": "Baba",
    "5A:CE:CB:B5:D4:C9": "Ziad",
    "F8:34:41:DA:93:EB": "Windows",
    "22:9F:AE:3B:5D:C4": "Mama",
    "4C:20:B8:87:12:E2": "Iphone",
    "F2:72:C9:B8:4B:C7": "Tablet",
    "32:AC:87:47:17:5D": "Fedora",
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

# ========= BANNED MACS PERSISTENCE =========
def save_banned_macs():
    try:
        with open(BANNED_MACS_FILE, "w") as f:
            json.dump(list(BANNED_MACS), f)
    except Exception as e:
        logger.error(f"Error saving banned MACs: {e}")

def load_banned_macs():
    try:
        if os.path.exists(BANNED_MACS_FILE):
            with open(BANNED_MACS_FILE, "r") as f:
                return set(json.load(f))
    except Exception as e:
        logger.error(f"Error loading banned MACs: {e}")
    return set()

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
        save_banned_macs()
        logger.info(f"Internal: Added {mac} to memory banned set.")
        enable_lockdown(router, headers, force_lock=False)
    else:
        logger.warning(f"Internal: {mac} is already in banned set, skipping rewrite.")

def unban_mac(router, headers, mac):
    mac = mac.upper()
    if mac in BANNED_MACS:
        BANNED_MACS.remove(mac)
        save_banned_macs()
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
urllib3.disable_warnings() # Disable SSL/Insecure connection warnings in console
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

# ========= STATUS OF SERVICES HELPER ========= 
def check_bot_services():
    status = {}

    try:
    # 1. CIFS2 Check
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {
            "Referer": f"{ROUTER_URL}/",
            "User-Agent": "Mozilla/5.0",
        }
        data = "action=execute&command=df -h\n&_http_id=TIDe5b1505eeac7f67f"
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data=data,
            timeout=5
        )
        status['cifs'] = "ONLINE" if "/cifs2" in r.text else "OFFLINE"
    except:
        status['cifs'] = "TIMEOUT"

    # 2. Radius Dashboard
    try:
        r = requests.get("http://10.0.0.254/radiusmanager/user.php", timeout=3)
        status['radius'] = "READY" if r.status_code == 200 else "DOWN"
    except:
        status['radius'] = "DOWN"

    # 3. Router Connectivity
    try:
        r = requests.get(ROUTER_URL, auth=ROUTER_AUTH, timeout=3)
        status['link'] = "OK" if r.status_code == 200 else "AUTH_ERR"
    except:
        status['link'] = "UNREACHABLE"

    return status

# ========= Helper: fetch devlist from router in a thread =========
def _fetch_devlist():
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
    r = router.post(url, headers=headers, data=data, timeout=10)
    dhcp_leases = demjson3.decode(re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text).group(1))
    wireless_devs = demjson3.decode(re.search(r"wldev\s*=\s*(\[.*?\]);", r.text).group(1))
    return dhcp_leases, wireless_devs

# ========= DAILY REPORT HELPER ========= 
REPORT_TIME = time(hour=23, minute=59, tzinfo=ZoneInfo("Africa/Cairo"))

@tasks.loop(time=REPORT_TIME)
async def daily_network_report():
    try:
        speed_history = await asyncio.to_thread(get_speed_history)
        router = requests.Session()
        router.auth = ROUTER_AUTH
        router.verify = False
        url = f"{ROUTER_URL}/update.cgi"
        data = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
        r = await asyncio.to_thread(router.post, url, data=data, timeout=10)
        dhcp_leases = demjson3.decode(re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text).group(1))
        devices_info = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        
        combined_data = []
        total_day_usage_mb = 0.0

        for ip, data in speed_history.items():
            if not ip or ip.startswith("_") or ip.endswith(".0"): continue
            rx = data.get("rx_total", 0) if isinstance(data, dict) else data
            tx = data.get("tx_total", 0) if isinstance(data, dict) else 0
            usage_mb = bytes_to_mb(rx + tx)
            if usage_mb < 0.1: continue
            total_day_usage_mb += usage_mb

            target_mac = next((mac for mac, info in devices_info.items() if info['ip'] == ip), None)
            raw_name = devices_info.get(target_mac, {}).get('name', 'Unknown') if target_mac else "Unknown"
            final_name = MACS_LIST.get(target_mac, raw_name)
            combined_data.append({"name": final_name, "usage": usage_mb})

        combined_data.sort(key=lambda x: x['usage'], reverse=True)
        if combined_data:
            channel = bot.get_channel(CHANNEL_ID)
            if not channel: return
            now = datetime.now(ZoneInfo("Africa/Cairo"))
            lines = [f"`{dev['name'][:15].ljust(15)} | 📊{(f'{dev['usage']/1024:.1f}GB' if dev['usage']>=1024 else f'{int(dev['usage'])}MB').rjust(8)}`" for dev in combined_data[:15]]
            embed = discord.Embed(title=f"📅 Daily Usage Report ({now.strftime('%Y-%m-%d')})", description="\n".join(lines), color=0x3498db, timestamp=now)
            embed.set_footer(text=f"Total Network Load: {total_day_usage_mb/1024:.2f} GB")
            await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in daily_network_report: {e}")

@daily_network_report.before_loop
async def before_daily_report():
    await bot.wait_until_ready()

# ========= BlockAll SETUP =========
class BulkBlockSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Select devices to block...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer BulkBlockSelect: {e}")
            return

        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {
            "Content-Type": "text/plain;charset=UTF-8", 
            "Referer": f"{ROUTER_URL}/", 
            "Origin": ROUTER_URL
        }
        
        selected_macs = self.values
        success_list = await asyncio.to_thread(
            lambda: [
                (ban_mac(router, headers, mac.upper()), MACS_LIST.get(mac.upper(), "Unknown"))[1]
                for mac in selected_macs
            ]
        )

        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name = MACS_LIST.get(m, 'Unknown Device')
            lines.append(f"{i:02d}. {name}")
        
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "No devices currently banned"

        embed = discord.Embed(
            title="`🚫` Bulk Block Completed",
            description=f"**Blocked:** {', '.join(success_list)}",
            color=0xff4747
        )
        embed.add_field(name="`📝` Updated Banned List", value=current_list, inline=False)
        
        await interaction.followup.send(embed=embed)

class BulkBlockView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=60)
        self.add_item(BulkBlockSelect(options))

# ========= RemoveAll SETUP =========
class BulkUnblockSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Select devices to unblock...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer BulkUnblockSelect: {e}")
            return

        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {
            "Content-Type": "text/plain;charset=UTF-8", 
            "Referer": f"{ROUTER_URL}/", 
            "Origin": ROUTER_URL
        }
        
        selected_macs = self.values
        success_list = await asyncio.to_thread(
            lambda: [
                (unban_mac(router, headers, mac.upper()), MACS_LIST.get(mac.upper(), "Unknown"))[1]
                for mac in selected_macs
            ]
        )

        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name = MACS_LIST.get(m, 'Unknown Device')
            lines.append(f"{i:02d}. {name}")
        
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "No devices currently banned"

        embed = discord.Embed(
            title="`✅` Bulk Unblock Completed",
            description=f"**Unblocked:** {', '.join(success_list)}",
            color=0x47ff47
        )
        embed.add_field(name="`📝` Remaining Banned List", value=current_list, inline=False)
        
        await interaction.followup.send(embed=embed)

class BulkUnblockView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=60)
        self.add_item(BulkUnblockSelect(options))

# ========= DISCORD BOT SETUP =========
class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default(), heartbeat_timeout=150.0)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)

        # Start existing traffic check
        if not traffic_check_task.is_running():
            traffic_check_task.start()

        # START THE DAILY REPORT TASK HERE
        if not daily_network_report.is_running():
            daily_network_report.start()

    async def on_connect(self):
        logger.info("Bot connected to Discord gateway.")

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord. Waiting to reconnect...")

    async def on_resumed(self):
        logger.info("Bot connection resumed successfully.")

    async def on_ready(self):
        logger.info(f"Bot ready: {self.user}")
        global BANNED_MACS
        BANNED_MACS = load_banned_macs()
        if BANNED_MACS:
            logger.info(f"Loaded {len(BANNED_MACS)} banned MACs from file, reapplying firewall rules...")
            await asyncio.to_thread(_reapply_banned_macs)

bot = MyBot()

# ========= Helpers Functions For Commands =========
def _reapply_banned_macs():
    router = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    enable_lockdown(router, headers, force_lock=False)

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
        app_commands.Choice(name=MACS_LIST.get(mac, 'Unknown Device'), value=mac)
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
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /blk: {e}")
        return

    logger.info(f"ACTION: /blk | User: {interaction.user} | Target: {mac}")
    
    try:
        mac_upper = mac.upper()
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        
        await asyncio.to_thread(ban_mac, router, headers, mac_upper)
        
        device_name = MACS_LIST.get(mac_upper, "Unknown Device")
        
        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name = MACS_LIST.get(m, 'Unknown Device')
            lines.append(f"{i:02d}. {name}")
        
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "No devices currently banned"

        embed = discord.Embed(
            title="`🚫` Device Blocked",
            description=f"**Target:** `{device_name}`",
            color=0xff4747
        )
        
        embed.add_field(
            name="`📝` Updated Banned List",
            value=current_list,
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
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /rm: {e}")
        return

    logger.info(f"ACTION: /rm | User: {interaction.user} | Target MAC: {mac}")
    
    try:
        mac_upper = mac.upper()
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL} 
        
        await asyncio.to_thread(unban_mac, router, headers, mac_upper)
        
        device_name = MACS_LIST.get(mac_upper, "Unknown Device")
        
        lines = []
        for i, m in enumerate(BANNED_MACS, 1):
            name = MACS_LIST.get(m, 'Unknown')
            lines.append(f"{i:02d}. {name}")
        
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "✨ *No devices currently banned*"

        embed = discord.Embed(
            title="`✅` Device Unblocked",
            description=f"**Target:** `{device_name}`",
            color=0x2ecc71 
        )
        
        embed.add_field(
            name="`📝` Updated Banned List",
            value=current_list,
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
# --------- /blkall ---------
@bot.tree.command(name="blkall", description="Select multiple saved devices to block")
async def blkall(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to defer /blkall: {e}")
        return

    logger.info(f"ACTION: /blkall | User: {interaction.user}")
    
    try:
        options = []
        for mac, name in MACS_LIST.items():
            mac_upper = mac.upper()
            
            if mac_upper in BANNED_MACS:
                continue
                
            options.append(discord.SelectOption(
                label=name,
                value=mac_upper,
                description=f"MAC: {mac_upper}"
            ))

        if not options:
            await interaction.followup.send("`⚠️` All saved devices are already blocked or list is empty.")
            return

        view = BulkBlockView(options[:25])
        await interaction.followup.send("Select the saved devices you want to block:", view=view)

    except Exception as e:
        logger.error(f"FAILURE in blkall: {e}")
        await interaction.followup.send(f"`❌` Error: {str(e)}")

# --------- /rmall ---------
@bot.tree.command(name="rmall", description="Select multiple devices to unblock from the banned list")
async def rmall(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        logger.error(f"Failed to defer /rmall: {e}")
        return

    logger.info(f"ACTION: /rmall | User: {interaction.user}")
    
    try:
        options = []
        for mac in BANNED_MACS:
            mac_upper = mac.upper()
            display_name = MACS_LIST.get(mac_upper, f"Unknown ({mac_upper})")
            
            options.append(discord.SelectOption(
                label=display_name,
                value=mac_upper,
                description=f"MAC: {mac_upper}"
            ))

        if not options:
            await interaction.followup.send("`⚠️` No devices are currently banned.", ephemeral=True)
            return

        view = BulkUnblockView(options[:25])
        await interaction.followup.send("Select the devices you want to unblock:", view=view)

    except Exception as e:
        logger.error(f"FAILURE in rmall: {e}")
        await interaction.followup.send(f"`❌` Error: {str(e)}")

# --------- /macs ---------
@bot.tree.command(name="macs", description="List known MAC names")
async def macs(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /macs: {e}")
        return

    try:
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

        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in /macs command: {e}")
        await interaction.followup.send("`❌` Failed to retrieve the MACs list.")

# --------- /list---------
@bot.tree.command(name="list", description="List currently banned MACs")
async def list_banned(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /list: {e}")
        return

    logger.info(f"User {interaction.user} requested the banned MACs list.")
    
    try:
        if BANNED_MACS:
            lines = []
            for i, m in enumerate(BANNED_MACS, 1):
                device_name = MACS_LIST.get(m, "Unknown Device")
                lines.append(f"{i:02d}. {device_name}")
            
            banned_output = "```\n" + "\n".join(lines) + "```"
            count = len(BANNED_MACS)
            embed_color = 0xe67e22
        else:
            banned_output = "✨ *No devices are currently under lockdown.*"
            count = 0 
            embed_color = 0x95a5a6

        embed = discord.Embed(
            title=f"`🚫` Blocked Devices ({count})",
            description=banned_output,
            color=embed_color
        )
        
        embed.set_footer(text="Use /rm to unblock a specific device")
        await interaction.followup.send(embed=embed)
        logger.info(f"Sent banned list ({count} devices) to {interaction.user}.")
        
    except Exception as e:
        logger.error(f"Error while listing banned MACs: {e}")
        try:
            await interaction.followup.send("`❌` Failed to retrieve the list.")
        except:
            pass

# --------- /balance ---------
@bot.tree.command(name="balance", description="Check current available traffic")
async def balance(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /balance: {e}")
        return

    logger.info(f"User {interaction.user} requested balance check.")
    
    try:
        traffic = await asyncio.to_thread(get_balance)
    except Exception as e:
        logger.error(f"Error in get_balance thread: {e}")
        traffic = None
    
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
@bot.tree.command(name="netstat", description="Show all recognized devices and their usage")
async def netstat(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /netstat: {e}")
        return

    logger.info(f"Full network status requested by {interaction.user}")
    
    try:
        speed_history = await asyncio.to_thread(get_speed_history)
        dhcp_leases, wireless_devs = await asyncio.to_thread(_fetch_devlist)

        active_signals = {dev[1].upper(): dev[2] for dev in wireless_devs}
        devices_info = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}

        combined_data = []
        total_traffic_mb = 0.0

        for ip, data in speed_history.items():
            if not ip or ip.startswith("_") or ip.endswith(".0"): continue
            
            rx = data.get("rx_total", 0) if isinstance(data, dict) else data
            tx = data.get("tx_total", 0) if isinstance(data, dict) else 0
            usage_mb = bytes_to_mb(rx + tx)
            total_traffic_mb += usage_mb

            target_mac = None
            raw_name = "Unknown"
            
            for mac, info in devices_info.items():
                if info['ip'] == ip:
                    target_mac = mac
                    raw_name = info['name']
                    break
            
            if not target_mac: continue

            is_online = target_mac in active_signals
            rssi = active_signals.get(target_mac, None)
            
            if is_online and rssi is not None:
                status_icon = "🟢"
                quality = min(max(2 * (rssi + 100), 0), 100)
                sig_str = f"{quality}%"
            else:
                status_icon = "🔴"
                sig_str = "0%"

            combined_data.append({
                "name": MACS_LIST.get(target_mac, raw_name),
                "usage": usage_mb,
                "signal": sig_str,
                "icon": status_icon,
                "online_sort": 1 if is_online else 0
            })

        combined_data.sort(key=lambda x: (x['online_sort'], x['usage']), reverse=True)

        if combined_data:
            lines = []
            for dev in combined_data[:15]:
                u_str = f"{dev['usage'] / 1024:.1f}GB" if dev['usage'] >= 1024 else f"{int(dev['usage'])}MB"
                
                name_f = dev['name'][:12].ljust(12)
                sig_f = dev['signal'].rjust(4)
                usage_f = u_str.rjust(6)

                lines.append(f"{dev['icon']} `{name_f} | 📶{sig_f} | 📊{usage_f}`")

            embed = discord.Embed(
                title=f"`📡` Network Status ({len(combined_data)} Devices)",
                description="\n".join(lines),
                color=0x2ecc71
            )
            embed.set_footer(text=f"Total Network Load: {total_traffic_mb/1024:.2f} GB")
        else:
            embed = discord.Embed(description="✨ No devices found in history.", color=0x95a5a6)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.error(f"Error in netstat: {e}")
        try:
            await interaction.followup.send("`❌` Error compiling network status.")
        except:
            pass

# --------- /limit ---------
@bot.tree.command(name="limit", description="Change the traffic threshold (GB)")
@app_commands.describe(limit="The new threshold value in GB (e.g. 5.0)")
async def set_limit(interaction: discord.Interaction, limit: float):
    global THRESHOLD
    
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /limit: {e}")
        return

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

# --------- /purge user ---------
purge_group = app_commands.Group(name="purge", description="Commands to delete messages")
@purge_group.command(name="user", description="Delete messages from a specific user")
@app_commands.describe(user="The user to delete messages for", amount="Number of messages to check")
async def purge_user(interaction: discord.Interaction, user: discord.Member, amount: int):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        logger.warning(f"Failed to defer /purge user (ignoring): {e}")

    try:
        def is_user(m):
            return m.author == user
        
        deleted = await interaction.channel.purge(limit=amount, check=is_user)
        
        try:
            await interaction.followup.send(f"`✅` Deleted {len(deleted)} messages for {user.display_name}.", ephemeral=True)
        except:
            logger.warning("Could not send followup for purge user (interaction expired), but messages were deleted.")
    except Exception as e:
        logger.error(f"Error in purge user: {e}")
        try:
            await interaction.followup.send("`❌` Failed to purge messages. Check bot permissions.", ephemeral=True)
        except:
            pass

# --------- /purge any ---------
@purge_group.command(name="any", description="Delete any messages in the channel")
@app_commands.describe(amount="Number of messages to delete")
async def purge_any(interaction: discord.Interaction, amount: int):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        logger.warning(f"Failed to defer /purge any (ignoring): {e}")
    
    try:
        deleted = await interaction.channel.purge(limit=amount)
        try:
            await interaction.followup.send(f"`✅` Deleted {len(deleted)} messages from the channel.", ephemeral=True)
        except:
            logger.warning("Could not send followup for purge any (interaction expired), but messages were deleted.")
    except Exception as e:
        logger.error(f"Error in purge any: {e}")
        try:
            await interaction.followup.send("`❌` Failed to purge messages.", ephemeral=True)
        except:
            pass

bot.tree.add_command(purge_group)

# --------- /botstatus ---------
@bot.tree.command(name="botstatus", description="Check core system services status")
async def botstatus(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /botstatus: {e}")
        return

    health = await asyncio.to_thread(check_bot_services)
    def get_status_emoji(status_val):
        status_val = status_val.upper()
        if status_val in ["ONLINE", "READY", "OK"]:
            return "🟢"
        if status_val in ["OFFLINE", "DOWN", "AUTH_ERR"]:
            return "🔴"
        return "⚪" 

    all_ok = all(v in ["ONLINE", "READY", "OK"] for v in health.values())
    embed_color = 0x2ecc71 if all_ok else 0xe74c3c
    title_icon = "✅" if all_ok else "⚠️"

    cifs_line   = f"{'CIFS Storage':<14} | {health['cifs']:<8} {get_status_emoji(health['cifs'])}"
    radius_line = f"{'Radius Dash':<14} | {health['radius']:<8} {get_status_emoji(health['radius'])}"
    link_line   = f"{'Router Link':<14} | {health['link']:<8} {get_status_emoji(health['link'])}"

    status_box = (
        f"```\n"
        f"{cifs_line}\n"
        f"{radius_line}\n"
        f"{link_line}\n"
        f"```"
    )
    logger.info(f"Bot status requested by {interaction.user}")
    embed = discord.Embed(
        title=f"`{title_icon}` System Health Dashboard",
        description=status_box,
        color=embed_color
    )
    
    await interaction.followup.send(embed=embed)

# ========= THREADS =========
@tasks.loop(hours=1.0)
async def traffic_check_task():
    logger.info("Starting scheduled traffic check...")
    try:
        await asyncio.to_thread(check_and_lock, bot)
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
            await bot.start(DISCORD_TOKEN, reconnect=True)
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