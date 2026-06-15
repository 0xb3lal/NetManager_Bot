import os
import db
import re
import copy
import urllib3
import logging
import asyncio
import discord
import requests
import demjson3
import hashlib
import hmac as _hmac
from zoneinfo import ZoneInfo
from discord.ext import tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord import app_commands
from datetime import datetime, time
from logging.handlers import RotatingFileHandler
from requests.exceptions import ReadTimeout, ConnectionError

# ========= CONFIG =========
load_dotenv()
D_USERNAME   = os.getenv("D_USERNAME")
D_PASSWORD   = os.getenv("D_PASSWORD")
ROUTER_URL   = os.getenv("ROUTER_URL")
RADIUS_URL   = os.getenv("RADIUS_URL")
ROUTER_AUTH  = (os.getenv("ROUTER_USER"), os.getenv("ROUTER_PASS"))
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID     = discord.Object(id=1475047474832867338)
CHANNEL_ID   = int(os.getenv("CHANNEL_ID")) if os.getenv("CHANNEL_ID") else 0

db.init_db()

THRESHOLD   = db.get_threshold()
BANNED_MACS = db.get_banned()
MACS_LIST   = db.get_devices()
ALLOWED_MACS = db.get_allowed()

# ========= LOGGING SYS =========
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_path = os.path.join(log_dir, "bot.log")

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",
        "INFO":     "\033[34m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record):
        record_copy = copy.copy(record)
        levelname = record_copy.levelname
        if levelname in self.COLORS:
            record_copy.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"
        return super().format(record_copy)

handler = RotatingFileHandler(
    log_path, maxBytes=5*1024*1024, backupCount=1, encoding="utf-8", mode="w"
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
logging.basicConfig(level=logging.INFO, handlers=[handler, console_handler])
logger = logging.getLogger(__name__)

# ========= HELPERS =========

def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()

def hex_hmac_md5(key, data):
    return _hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()

async def safe_defer(interaction: discord.Interaction, thinking: bool = False) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(thinking=thinking)
        return True
    except discord.errors.HTTPException as e:
        cmd = interaction.command.name if interaction.command else "?"
        if e.code == 40060:
            logger.warning(f"Interaction already acknowledged for /{cmd} (40060), continuing.")
            return True
        elif e.code == 10062:
            logger.warning(f"Unknown/expired interaction for /{cmd} (10062), aborting.")
            return False
        else:
            logger.error(f"Failed to defer /{cmd}: {e}")
            return False
    except Exception as e:
        logger.error(f"Failed to defer: {e}")
        return False

# ========= ROUTER EXEC =========
def run_cmd(router, headers, cmd, _retry=True):
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        logger.debug(f"Sending Command to Router: {cmd}")
        response = router.post(f"{ROUTER_URL}/shell.cgi", headers=headers, data=data, timeout=30)
        if response.status_code == 200:
            logger.debug(f"Router executed: {cmd} successfully.")
        else:
            logger.error(f"Router returned error code {response.status_code} for command: {cmd}")
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        if _retry:
            logger.warning(f"Retrying command once: {cmd}")
            run_cmd(router, headers, cmd, _retry=False)
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd: {e}")

# ========= LOCKDOWN LOGIC =========
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
        db.ban_device(mac)
        logger.info(f"Internal: Added {mac} to banned set.")
        enable_lockdown(router, headers, force_lock=False)
    else:
        logger.warning(f"Internal: {mac} is already in banned set, skipping rewrite.")

def unban_mac(router, headers, mac):
    mac = mac.upper()
    if mac in BANNED_MACS:
        BANNED_MACS.remove(mac)
        db.unban_device(mac)
        logger.info(f"Internal: Removed {mac} from banned set.")
        enable_lockdown(router, headers, force_lock=False)
    else:
        logger.warning(f"Internal: Attempted to unban {mac} but it wasn't in the list.")

# ========= RADIUS =========
def _fetch_radius_traffic():
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final    = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}
    session.post(f"{RADIUS_URL}/radiusmanager/user.php?cont=login", data=payload, timeout=30)
    session.get(f"{RADIUS_URL}/radiusmanager/user.php?cont=change_lang&lang=English", timeout=30)
    dash = session.get(f"{RADIUS_URL}/radiusmanager/user.php", timeout=30)
    soup = BeautifulSoup(dash.text, "html.parser")
    for td in soup.find_all("td"):
        if "Available total traffic" in td.get_text(strip=True):
            return td.find_next_sibling("td").get_text(strip=True)
    return None

def _apply_lockdown_for_traffic(traffic_value):
    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    if traffic_value < THRESHOLD:
        enable_lockdown(router, headers, force_lock=True)
        return "`❌` System Lockdown", 0xff4747
    else:
        enable_lockdown(router, headers, force_lock=False)
        return "`✅` System Normal", 0x47ff7e

async def async_check_and_lock(bot_instance):
    try:
        available_traffic = await asyncio.to_thread(_fetch_radius_traffic)
        if not available_traffic:
            return
        traffic_value = float(available_traffic.split()[0])

        async with ROUTER_LOCK:
            e_title, e_color = await asyncio.to_thread(_apply_lockdown_for_traffic, traffic_value)

        balance_label = "Balance:".ljust(9)
        limit_label   = "Limit:".ljust(9)
        status_box = (
            f"```\n"
            f"{balance_label} {available_traffic}\n"
            f"{limit_label} {THRESHOLD} GB\n"
            f"```"
        )
        embed = discord.Embed(title=e_title, description=status_box, color=e_color)
        try:
            channel = bot_instance.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
        except Exception:
            logger.error("Failed to push status update to Discord")
    except Exception as e:
        logger.error(f"Main Check Error: {e}")

# ========= GET BALANCE =========
def get_balance():
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final    = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}
    try:
        session.post(f"{RADIUS_URL}/radiusmanager/user.php?cont=login", data=payload, timeout=30).raise_for_status()
        session.get(f"{RADIUS_URL}/radiusmanager/user.php?cont=change_lang&lang=English", timeout=30)
        dash = session.get(f"{RADIUS_URL}/radiusmanager/user.php", timeout=30)
        dash.raise_for_status()
        soup = BeautifulSoup(dash.text, "html.parser")
        for td in soup.find_all("td"):
            if "Available total traffic" in td.get_text(strip=True):
                balance = td.find_next_sibling("td").get_text(strip=True)
                logger.info(f"Successfully fetched balance: {balance}")
                return balance
        logger.warning("Balance field not found in dashboard HTML.")
        return None
    except requests.exceptions.Timeout:
        logger.error("Timeout: Radius Dashboard is not responding.")
    except requests.exceptions.ConnectionError:
        logger.error("Connection Error: Could not connect to Radius.")
    except Exception as e:
        logger.error(f"Unexpected error in get_balance: {e}")
    return None

# ========= NETWORK USAGE HELPERS =========
urllib3.disable_warnings()

def bytes_to_mb(value):
    return value / (1024 * 1024)

def get_speed_history():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Origin": ROUTER_URL,
        "Referer": f"{ROUTER_URL}/bwm-realtime.asp",
        "X-Requested-With": "XMLHttpRequest"
    }
    try:
        url = f"{ROUTER_URL}/update.cgi"
        router.post(url, headers=headers, data="exec=ipt_bandwidth&arg0=start&_http_id=TIDe5b1505eeac7f67f", timeout=10)
        r = router.post(url, headers=headers, data="exec=ipt_bandwidth&arg0=speed&_http_id=TIDe5b1505eeac7f67f", timeout=30)
        match = re.search(r"speed_history\s*=\s*(\{.*?\});", r.text, re.DOTALL)
        if not match:
            logger.warning("speed_history block not found in router response.")
            return {}
        return demjson3.decode(match.group(1))
    except Exception as e:
        logger.error(f"Error fetching speed history: {e}")
        return {}

def _fetch_devlist():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    url     = f"{ROUTER_URL}/update.cgi"
    data    = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    r = router.post(url, headers=headers, data=data, timeout=30)
    dhcp_leases  = demjson3.decode(re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text).group(1))
    wireless_devs = demjson3.decode(re.search(r"wldev\s*=\s*(\[.*?\]);", r.text).group(1))
    return dhcp_leases, wireless_devs

def _fetch_devlist_and_discover(bot_instance):
    dhcp_leases, wireless_devs = _fetch_devlist()
    new_devices = []
    for lease in dhcp_leases:
        mac      = lease[2].upper()
        hostname = lease[0].strip() or "Unknown"
        if db.add_device(mac, hostname):
            MACS_LIST[mac] = hostname
            new_devices.append((mac, hostname))
    if new_devices and bot_instance.loop.is_running():
        async def _notify():
            channel = bot_instance.get_channel(CHANNEL_ID)
            if not channel:
                return
            for mac, hostname in new_devices:
                embed = discord.Embed(
                    title="`🆕` New Device Discovered",
                    description=f"**Hostname:** `{hostname}`\n**MAC:** `{mac}`",
                    color=0xf39c12
                )
                await channel.send(embed=embed)
        bot_instance.loop.create_task(_notify())
    return dhcp_leases, wireless_devs

# ========= STATUS OF SERVICES =========
def check_bot_services():
    status = {}
    try:
        router  = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Referer": f"{ROUTER_URL}/", "User-Agent": "Mozilla/5.0"}
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=df -h\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )
        status["jffs2"] = "ONLINE" if "/jffs" in r.text else "OFFLINE"
    except:
        status["jffs2"] = "TIMEOUT"
    try:
        r = requests.get(f"{RADIUS_URL}/radiusmanager/user.php", timeout=10)
        status["radius"] = "READY" if r.status_code == 200 else "DOWN"
    except:
        status["radius"] = "DOWN"
    try:
        r = requests.get(ROUTER_URL, auth=ROUTER_AUTH, timeout=10)
        status["link"] = "OK" if r.status_code == 200 else "AUTH_ERR"
    except:
        status["link"] = "UNREACHABLE"
    return status

# ========= ROUTER HEARTBEAT =========
def _router_heartbeat():
    try:
        router = requests.Session()
        router.auth   = ROUTER_AUTH
        router.verify = False
        headers = {"Referer": f"{ROUTER_URL}/", "User-Agent": "Mozilla/5.0"}
        router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=true\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )
        logger.debug("Router heartbeat sent successfully.")
    except (ReadTimeout, ConnectionError):
        logger.debug("Router heartbeat timed out (router busy), skipping.")
    except Exception as e:
        logger.debug(f"Router heartbeat failed: {e}")

# ========= DAILY REPORT =========
REPORT_TIME = time(hour=23, minute=59, tzinfo=ZoneInfo("Africa/Cairo"))

@tasks.loop(time=REPORT_TIME)
async def daily_network_report():
    try:
        async with ROUTER_LOCK:
            speed_history = await asyncio.to_thread(get_speed_history)

        async with ROUTER_LOCK:
            dhcp_leases, _ = await asyncio.to_thread(_fetch_devlist)

        devices_info     = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_data    = []
        total_day_usage_mb = 0.0

        for ip, data in speed_history.items():
            if not ip or ip.startswith("_") or ip.endswith(".0"):
                continue
            rx = data.get("rx_total", 0) if isinstance(data, dict) else data
            tx = data.get("tx_total", 0) if isinstance(data, dict) else 0
            usage_mb = bytes_to_mb(rx + tx)
            if usage_mb < 0.1:
                continue
            total_day_usage_mb += usage_mb
            target_mac = next((mac for mac, info in devices_info.items() if info["ip"] == ip), None)
            raw_name   = devices_info.get(target_mac, {}).get("name", "Unknown") if target_mac else "Unknown"
            final_name = MACS_LIST.get(target_mac, raw_name)
            combined_data.append({"name": final_name, "usage": usage_mb})

        combined_data.sort(key=lambda x: x["usage"], reverse=True)
        if not combined_data:
            return

        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            return

        now   = datetime.now(ZoneInfo("Africa/Cairo"))
        lines = []
        for dev in combined_data[:15]:
            u_str = f"{dev['usage']/1024:.1f}GB" if dev["usage"] >= 1024 else f"{int(dev['usage'])}MB"
            lines.append(f"`{dev['name'][:15].ljust(15)} | 📊{u_str.rjust(8)}`")

        embed = discord.Embed(
            title=f"📅 Daily Usage Report ({now.strftime('%Y-%m-%d')})",
            description="\n".join(lines),
            color=0x3498db,
            timestamp=now
        )
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
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": f"{ROUTER_URL}/", "Origin": ROUTER_URL}
        selected_macs = [m.upper() for m in self.values]

        def _bulk_ban():
            added = []
            for mac in selected_macs:
                if mac not in BANNED_MACS:
                    BANNED_MACS.add(mac)
                    db.ban_device(mac)
                    added.append(mac)
                    logger.info(f"Internal: Added {mac} to banned set.")
            if added:
                enable_lockdown(router, headers, force_lock=False)
            return [MACS_LIST.get(m, "Unknown") for m in selected_macs]

        async with ROUTER_LOCK:
            success_list = await asyncio.to_thread(_bulk_ban)

        lines = [f"{i:02d}. {MACS_LIST.get(m, 'Unknown Device')}" for i, m in enumerate(BANNED_MACS, 1)]
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
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": f"{ROUTER_URL}/", "Origin": ROUTER_URL}
        selected_macs = self.values

        async with ROUTER_LOCK:
            success_list = await asyncio.to_thread(
                lambda: [
                    (unban_mac(router, headers, mac.upper()), MACS_LIST.get(mac.upper(), "Unknown"))[1]
                    for mac in selected_macs
                ]
            )

        lines = [f"{i:02d}. {MACS_LIST.get(m, 'Unknown Device')}" for i, m in enumerate(BANNED_MACS, 1)]
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
        super().__init__(intents=discord.Intents.default(), heartbeat_timeout=60.0)
        self.tree = app_commands.CommandTree(self)

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.CommandInvokeError):
            cause = error.original
        else:
            cause = error
        if isinstance(cause, discord.errors.NotFound) and cause.code == 10062:
            return
        logger.error(f"Tree error in /{interaction.command.name if interaction.command else '?'}: {error}")

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)
        if not traffic_check_task.is_running():
            traffic_check_task.start()
        if not daily_network_report.is_running():
            daily_network_report.start()
        if not heartbeat_task.is_running():
            heartbeat_task.start()
        if not discord_keepalive_task.is_running():
            discord_keepalive_task.start()

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord Gateway. Waiting for automatic reconnect...")

    async def on_resumed(self):
        logger.info("Discord Gateway session resumed successfully. Bot is fully operational.")

    async def on_ready(self):
        global BANNED_MACS, MACS_LIST, ALLOWED_MACS, THRESHOLD
        logger.info(f"Bot ready: {self.user}")
        BANNED_MACS  = db.get_banned()
        MACS_LIST    = db.get_devices()
        ALLOWED_MACS = db.get_allowed()
        THRESHOLD    = db.get_threshold()
        if BANNED_MACS:
            logger.info(f"Loaded {len(BANNED_MACS)} banned MACs from DB, reapplying firewall rules...")
            async with ROUTER_LOCK:
                await asyncio.to_thread(_reapply_banned_macs)

# ========= ROUTER LOCK =========
ROUTER_LOCK = asyncio.Lock()

bot = MyBot()

# ========= HELPERS FOR COMMANDS =========
def _reapply_banned_macs():
    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    enable_lockdown(router, headers, force_lock=False)

async def mac_autocomplete(interaction: discord.Interaction, current: str):
    choices = [
        app_commands.Choice(name=hostname, value=mac)
        for mac, hostname in MACS_LIST.items()
        if (current.lower() in hostname.lower() or current.lower() in mac.lower())
        and mac not in BANNED_MACS
    ]
    return choices[:25]

async def banned_macs_autocomplete(interaction: discord.Interaction, current: str):
    choices = [
        app_commands.Choice(name=MACS_LIST.get(mac, "Unknown Device"), value=mac)
        for mac in BANNED_MACS
        if current.lower() in mac.lower() or current.lower() in MACS_LIST.get(mac, "").lower()
    ]
    return choices[:25]

# ========= COMMANDS =========

# --------- /blk ---------
@bot.tree.command(name="blk", description="Ban a MAC address from the list")
@app_commands.autocomplete(mac=mac_autocomplete)
async def ban(interaction: discord.Interaction, mac: str):
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"ACTION: /blk | User: {interaction.user} | Target: {mac}")
    try:
        mac_upper = mac.upper()
        router    = requests.Session()
        router.auth = ROUTER_AUTH
        headers   = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        async with ROUTER_LOCK:
            await asyncio.to_thread(ban_mac, router, headers, mac_upper)
        device_name  = MACS_LIST.get(mac_upper, "Unknown Device")
        lines        = [f"{i:02d}. {MACS_LIST.get(m, 'Unknown Device')}" for i, m in enumerate(BANNED_MACS, 1)]
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "No devices currently banned"
        embed = discord.Embed(title="`🚫` Device Blocked", description=f"**Target:** `{device_name}`", color=0xff4747)
        embed.add_field(name="`📝` Updated Banned List", value=current_list, inline=False)
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
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"ACTION: /rm | User: {interaction.user} | Target MAC: {mac}")
    try:
        mac_upper = mac.upper()
        router    = requests.Session()
        router.auth = ROUTER_AUTH
        headers   = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        async with ROUTER_LOCK:
            await asyncio.to_thread(unban_mac, router, headers, mac_upper)
        device_name  = MACS_LIST.get(mac_upper, "Unknown Device")
        lines        = [f"{i:02d}. {MACS_LIST.get(m, 'Unknown')}" for i, m in enumerate(BANNED_MACS, 1)]
        current_list = "```\n" + "\n".join(lines) + "```" if lines else "✨ *No devices currently banned*"
        embed = discord.Embed(title="`✅` Device Unblocked", description=f"**Target:** `{device_name}`", color=0x2ecc71)
        embed.add_field(name="`📝` Updated Banned List", value=current_list, inline=False)
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
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"ACTION: /blkall | User: {interaction.user}")
    try:
        options = [
            discord.SelectOption(label=hostname, value=mac, description=f"MAC: {mac}")
            for mac, hostname in MACS_LIST.items()
            if mac.upper() not in BANNED_MACS
        ]
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
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"ACTION: /rmall | User: {interaction.user}")
    try:
        options = [
            discord.SelectOption(
                label=MACS_LIST.get(mac, f"Unknown ({mac})"),
                value=mac,
                description=f"MAC: {mac}"
            )
            for mac in BANNED_MACS
        ]
        if not options:
            await interaction.followup.send("`⚠️` No devices are currently banned.", ephemeral=True)
            return
        view = BulkUnblockView(options[:25])
        await interaction.followup.send("Select the devices you want to unblock:", view=view)
    except Exception as e:
        logger.error(f"FAILURE in rmall: {e}")
        await interaction.followup.send(f"`❌` Error: {str(e)}")

# --------- /macs ---------
@bot.tree.command(name="macs", description="List known MAC addresses and their hostnames")
async def macs(interaction: discord.Interaction):
    try:
        await interaction.response.defer()
    except Exception as e:
        logger.error(f"Failed to defer /macs: {e}")
        return
    try:
        if MACS_LIST:
            msg = "\n".join(f"`{mac}` : **{hostname}**" for mac, hostname in MACS_LIST.items())
            embed_color = discord.Color.blue()
        else:
            msg = "*No MAC addresses found.*"
            embed_color = discord.Color.light_grey()
        embed = discord.Embed(title="`📋` Known Devices", description=msg, color=embed_color)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in /macs command: {e}")
        await interaction.followup.send("`❌` Failed to retrieve the MACs list.")

# --------- /list ---------
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
            lines        = [f"{i:02d}. {MACS_LIST.get(m, 'Unknown Device')}" for i, m in enumerate(BANNED_MACS, 1)]
            banned_output = "```\n" + "\n".join(lines) + "```"
            count        = len(BANNED_MACS)
            embed_color  = 0xe67e22
        else:
            banned_output = "✨ *No devices are currently under lockdown.*"
            count         = 0
            embed_color   = 0x95a5a6
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
        embed = discord.Embed(title="`📊` Network Status", description=status_box, color=0x3498db)
        await interaction.followup.send(embed=embed)
        logger.info(f"Balance sent to {interaction.user}: {traffic}")
    else:
        embed = discord.Embed(
            title="`❌` System Error",
            description="`Could not fetch balance. Radius server unreachable.`",
            color=0xe74c3c
        )
        await interaction.followup.send(embed=embed)

# --------- /active ---------
@bot.tree.command(name="active", description="Show currently active devices and their signal strength")
async def active(interaction: discord.Interaction):
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"Active devices status requested by {interaction.user}")
    try:
        async with ROUTER_LOCK:
            dhcp_leases, wireless_devs = await asyncio.to_thread(
                lambda: _fetch_devlist_and_discover(bot)
            )

        active_signals = {dev[1].upper(): dev[2] for dev in wireless_devs}
        devices_info   = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_data  = []

        for mac, info in devices_info.items():
            is_online = mac in active_signals
            rssi      = active_signals.get(mac, None)
            is_banned = mac in BANNED_MACS

            if is_banned:
                status_icon = "⛔"
                sig_str     = "0%"
            elif is_online and rssi is not None:
                status_icon = "🟢"
                quality     = min(max(2 * (rssi + 100), 0), 100)
                sig_str     = f"{quality}%"
            else:
                status_icon = "🔴"
                sig_str     = "0%"

            combined_data.append({
                "name":        MACS_LIST.get(mac, info["name"]),
                "signal":      sig_str,
                "icon":        status_icon,
                "online_sort": 1 if is_online and not is_banned else 0
            })

        combined_data.sort(key=lambda x: (x["online_sort"], x["signal"]), reverse=True)

        if combined_data:
            lines = [
                f"{dev['icon']} `{dev['name'][:12].ljust(12)} | 📶{dev['signal'].rjust(4)}`"
                for dev in combined_data[:15]
            ]
            embed = discord.Embed(
                title=f"`📡` Active Devices ({len(combined_data)} Devices)",
                description="\n".join(lines),
                color=0x2ecc71
            )
        else:
            embed = discord.Embed(description="✨ No devices found.", color=0x95a5a6)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error in active: {e}")
        try:
            await interaction.followup.send("`❌` Error compiling active devices status.")
        except:
            pass

# --------- /netstat ---------
@bot.tree.command(name="netstat", description="Show all recognized devices and their usage")
async def netstat(interaction: discord.Interaction):
    if not await safe_defer(interaction, thinking=True):
        return
    logger.info(f"Network usage status requested by {interaction.user}")
    try:
        async with ROUTER_LOCK:
            speed_history = await asyncio.to_thread(get_speed_history)

        async with ROUTER_LOCK:
            dhcp_leases, _ = await asyncio.to_thread(_fetch_devlist)

        devices_info    = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_data   = []
        total_traffic_mb = 0.0

        for ip, data in speed_history.items():
            if not ip or ip.startswith("_") or ip.endswith(".0"):
                continue
            rx       = data.get("rx_total", 0) if isinstance(data, dict) else data
            tx       = data.get("tx_total", 0) if isinstance(data, dict) else 0
            usage_mb = bytes_to_mb(rx + tx)
            total_traffic_mb += usage_mb

            target_mac = next((mac for mac, info in devices_info.items() if info["ip"] == ip), None)
            if not target_mac:
                continue
            raw_name = devices_info[target_mac]["name"]
            combined_data.append({"name": MACS_LIST.get(target_mac, raw_name), "usage": usage_mb})

        combined_data.sort(key=lambda x: x["usage"], reverse=True)

        if combined_data:
            lines = []
            for dev in combined_data[:15]:
                u_str = f"{dev['usage']/1024:.1f}GB" if dev["usage"] >= 1024 else f"{int(dev['usage'])}MB"
                lines.append(f"`📱` `{dev['name'][:12].ljust(12)} | 📊{u_str.rjust(6)}`")
            embed = discord.Embed(
                title=f"`📡` Network Usage ({len(combined_data)} Devices)",
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
    if not await safe_defer(interaction, thinking=True):
        return
    try:
        old_limit = THRESHOLD
        THRESHOLD = limit
        db.set_threshold(limit)
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
        embed = discord.Embed(title="`⚙️` System Configuration Update", description=status_box, color=0xf1c40f)
        await interaction.followup.send(embed=embed)
        await async_check_and_lock(bot)
    except Exception as e:
        logger.error(f"Error in limit command: {e}")
        await interaction.followup.send("`❌` Failed to update configuration.")

# --------- /purge any ---------
purge_group = app_commands.Group(name="purge", description="Commands to delete messages")

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
    if not await safe_defer(interaction, thinking=True):
        return
    async with ROUTER_LOCK:
        health = await asyncio.to_thread(check_bot_services)

    def get_status_emoji(s):
        s = s.upper()
        if s in ["ONLINE", "READY", "OK"]:
            return "🟢"
        if s in ["OFFLINE", "DOWN", "AUTH_ERR"]:
            return "🔴"
        return "⚪"

    all_ok     = all(v in ["ONLINE", "READY", "OK"] for v in health.values())
    embed_color = 0x2ecc71 if all_ok else 0xe74c3c
    title_icon  = "✅" if all_ok else "⚠️"
    status_box  = (
        f"```\n"
        f"{'JFFS2 Storage':<14} | {health['jffs2']:<8} {get_status_emoji(health['jffs2'])}\n"
        f"{'Radius Dash':<14} | {health['radius']:<8} {get_status_emoji(health['radius'])}\n"
        f"{'Router Link':<14} | {health['link']:<8} {get_status_emoji(health['link'])}\n"
        f"```"
    )
    logger.info(f"Bot status requested by {interaction.user}")
    embed = discord.Embed(
        title=f"`{title_icon}` System Health Dashboard",
        description=status_box,
        color=embed_color
    )
    await interaction.followup.send(embed=embed)

# ========= TASKS =========
@tasks.loop(minutes=1.0)
async def heartbeat_task():
    if ROUTER_LOCK.locked():
        logger.debug("Router heartbeat skipped (router busy with another task).")
        return
    async with ROUTER_LOCK:
        await asyncio.to_thread(_router_heartbeat)

@heartbeat_task.before_loop
async def before_heartbeat():
    await bot.wait_until_ready()
    logger.info("Router heartbeat started (keeps router shell session warm).")

@tasks.loop(hours=1.0)
async def traffic_check_task():
    logger.info("Starting scheduled traffic check...")
    try:
        await async_check_and_lock(bot)
        logger.info("Scheduled traffic check completed successfully.")
    except Exception as e:
        logger.exception(f"Unexpected error during traffic check task: {e}")

@traffic_check_task.before_loop
async def before_traffic_check():
    logger.info("Waiting for bot to be ready before starting traffic task...")
    await bot.wait_until_ready()
    logger.info("Bot is ready. Traffic task started.")

@tasks.loop(minutes=2.0)
async def discord_keepalive_task():
    try:
        await bot.fetch_user(bot.user.id)
        logger.debug("Discord keepalive ping sent successfully.")
    except Exception as e:
        logger.debug(f"Discord keepalive failed (non-critical): {e}")

@discord_keepalive_task.before_loop
async def before_discord_keepalive():
    await bot.wait_until_ready()
    logger.info("Discord keepalive task started (prevents idle Gateway disconnects).")

# ========= MAIN =========
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