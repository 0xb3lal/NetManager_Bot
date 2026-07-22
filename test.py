import os
import db
import re
import urllib3
import asyncio
import discord
import requests
import demjson3
import hashlib
import hmac as _hmac
from logger import logger
from zoneinfo import ZoneInfo
from discord.ext import tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord import app_commands
from datetime import datetime, time
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
WIFI_IFACE   = os.getenv("WIFI_IFACE", "eth1")

# ========= DB CONFIG =========
db.init_db()
THRESHOLD      = db.get_threshold()
BANNED_MACS    = db.get_banned()
MACS_LIST      = db.get_devices()
ALLOWED_MACS   = db.get_allowed()
LOCKDOWN_STATE = db.get_lockdown_state()

# ========= HELPERS =========

def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()

def hex_hmac_md5(key, data):
    return _hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()

def parse_traffic_to_gb(traffic_str):
    parts = traffic_str.strip().split()
    value = float(parts[0])
    unit  = parts[1].upper() if len(parts) > 1 else "GB"
    if unit.startswith("MB"):
        return value / 1024
    elif unit.startswith("KB"):
        return value / (1024 * 1024)
    return value

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
            return True
        else:
            logger.error(f"Router returned error code {response.status_code} for command: {cmd}")
            return False
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        if _retry:
            logger.warning(f"Retrying command once: {cmd}")
            return run_cmd(router, headers, cmd, _retry=False)
        return False
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd: {e}")
        return False

def run_cmd_output(router, headers, cmd, timeout=15):
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = router.post(f"{ROUTER_URL}/shell.cgi", headers=headers, data=data, timeout=timeout)
        if response.status_code == 200:
            return response.text
        logger.error(f"Router returned error code {response.status_code} for command: {cmd}")
        return None
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd_output: {e}")
        return None

# ========= LOCKDOWN LOGIC =========
def _kick_non_allowed_devices(router, headers):
    output = run_cmd_output(router, headers, f"wl -i {WIFI_IFACE} assoclist")
    if not output:
        return
    connected_macs = re.findall(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", output)
    for mac in connected_macs:
        mac = mac.upper()
        if mac not in ALLOWED_MACS:
            logger.info(f"Kicking non-allowed device during lockdown: {mac}")
            run_cmd(router, headers, f"wl -i {WIFI_IFACE} deauthenticate {mac}")

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

    if force_lock:
        _kick_non_allowed_devices(router, headers)

def ban_mac(router, headers, mac, reason="manual"):
    mac = mac.upper()
    if mac not in BANNED_MACS:
        BANNED_MACS.add(mac)
        db.ban_device(mac, reason=reason)
        logger.info(f"Internal: Added {mac} to banned set (reason={reason}).")
        enable_lockdown(router, headers, force_lock=LOCKDOWN_STATE)
    else:
        logger.warning(f"Internal: {mac} is already in banned set, skipping rewrite.")

def unban_mac(router, headers, mac):
    mac = mac.upper()
    if mac in BANNED_MACS:
        BANNED_MACS.remove(mac)
        db.unban_device(mac)
        logger.info(f"Internal: Removed {mac} from banned set.")
        enable_lockdown(router, headers, force_lock=LOCKDOWN_STATE)
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
    global LOCKDOWN_STATE
    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    if traffic_value < THRESHOLD:
        enable_lockdown(router, headers, force_lock=True)
        LOCKDOWN_STATE = True
        db.set_lockdown_state(True)
        return "`❌` System Lockdown", 0xff4747
    else:
        enable_lockdown(router, headers, force_lock=False)
        LOCKDOWN_STATE = False
        db.set_lockdown_state(False)
        return "`✅` System Normal", 0x47ff7e

async def async_check_and_lock(bot_instance):
    try:
        available_traffic = await asyncio.to_thread(_fetch_radius_traffic)
        if not available_traffic:
            return
        traffic_value = parse_traffic_to_gb(available_traffic)

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

def _decode_date(n):
    year  = ((n >> 16) & 0xFF) + 1900
    month = (n >> 8) & 0xFF
    day   = n & 0xFF
    return year, month, day

def get_speed_history():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Origin": ROUTER_URL,
        "Referer": f"{ROUTER_URL}/bwm-ipt-24.asp",
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

def get_daily_history():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Origin": ROUTER_URL,
        "Referer": f"{ROUTER_URL}/bwm-ipt-daily.asp",
        "X-Requested-With": "XMLHttpRequest"
    }
    try:
        url = f"{ROUTER_URL}/update.cgi"
        r = router.post(url, headers=headers, data="exec=ipt_bandwidth&arg0=daily&_http_id=TIDe5b1505eeac7f67f", timeout=30)
        match = re.search(r"daily_history\s*=\s*(\[.*?\]);", r.text, re.DOTALL)
        if not match:
            logger.warning("daily_history block not found in router response.")
            return []
        return demjson3.decode(match.group(1))
    except Exception as e:
        logger.error(f"Error fetching daily history: {e}")
        return []

def get_today_usage(daily_history):
    now   = datetime.now(ZoneInfo("Africa/Cairo"))
    today = (now.year, now.month - 1, now.day)
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

def _get_today_usage_by_mac():
    speed_history = get_speed_history()
    daily_history = get_daily_history()
    dhcp_leases, _, _ = _fetch_devlist()
    ip_to_mac = {lease[1]: lease[2].upper() for lease in dhcp_leases}
    combined_usage = get_today_combined(speed_history, daily_history)

    usage_by_mac = {}
    for ip, total_bytes in combined_usage.items():
        mac = ip_to_mac.get(ip)
        if not mac:
            continue
        usage_by_mac[mac] = bytes_to_mb(total_bytes) / 1024
    return usage_by_mac

def _fetch_devlist():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    url     = f"{ROUTER_URL}/update.cgi"
    data    = "exec=devlist&_http_id=TIDe5b1505eeac7f67f"
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    r = router.post(url, headers=headers, data=data, timeout=30)
    dhcp_leases   = demjson3.decode(re.search(r"dhcpd_lease\s*=\s*(\[.*?\]);", r.text).group(1))
    wireless_devs = demjson3.decode(re.search(r"wldev\s*=\s*(\[.*?\]);", r.text).group(1))
    arp_list      = demjson3.decode(re.search(r"arplist\s*=\s*(\[.*?\]);", r.text).group(1))
    return dhcp_leases, wireless_devs, arp_list

def _fetch_devlist_and_discover(bot_instance):
    dhcp_leases, wireless_devs, arp_list = _fetch_devlist()
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
    return dhcp_leases, wireless_devs, arp_list

# ========= DAILY LIMIT ROUTER RESET =========
def _reset_ip_traffic_stats():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    data = (
        "_nextpage=%2F%23admin-iptraffic.asp&_service=cstatsnew-restart&cstats_enable=1"
        "&cstats_path=%2Fjffs%2F&cstats_sshut=1&cstats_bak=0&cstats_all=1&f_cstats_enable=on"
        "&f_loc=%2Fjffs%2F&f_user=%2Fjffs%2F&cstats_stime=1&f_sshut=on&f_new=on&cstats_offset=1"
        "&cstats_exclude=&f_all=on&cstats_labels=0&_http_id=TIDe5b1505eeac7f67f"
    )
    try:
        response = router.post(f"{ROUTER_URL}/tomato.cgi", headers=headers, data=data, timeout=30)
        if response.status_code == 200:
            logger.info("IP Traffic stats reset successfully.")
            return True
        logger.error(f"IP Traffic stats reset failed with status {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error resetting IP Traffic stats: {e}")
        return False

def _reset_bandwidth_stats():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    data = (
        "_nextpage=%2F%23admin-bwm.asp&_service=rstatsnew-restart&rstats_enable=1"
        "&rstats_path=%2Fjffs%2F&rstats_sshut=1&rstats_bak=0&f_rstats_enable=on"
        "&f_loc=%2Fjffs%2F&f_user=%2Fjffs%2F&rstats_stime=1&f_sshut=on&f_new=on"
        "&rstats_offset=1&rstats_exclude=&_http_id=TIDe5b1505eeac7f67f"
    )
    try:
        response = router.post(f"{ROUTER_URL}/tomato.cgi", headers=headers, data=data, timeout=30)
        if response.status_code == 200:
            logger.info("Bandwidth stats reset successfully.")
            return True
        logger.error(f"Bandwidth stats reset failed with status {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error resetting Bandwidth stats: {e}")
        return False

# ========= STATUS OF SERVICES =========
def _check_jffs2():
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=df -h\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )
        if "/jffs" not in r.text:
            return "OFFLINE"
        match = re.search(r"/jffs\s+(\d+)K\s+(\d+)K\s+(\d+)K\s+(\d+)%", r.text)
        if match:
            used_pct = int(match.group(4))
            if used_pct >= 90:
                return "CRITICAL"
            return "ONLINE"
        return "ONLINE"
    except Exception as e:
        logger.error(f"JFFS2 check failed: {e}")
        return "TIMEOUT"

def _check_wan_status():
    try:
        router = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=nvram get wan_ipaddr\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )
        match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", r.text)
        if match and match.group(1) != "0.0.0.0":
            return "CONNECTED"
        return "DISCONNECTED"
    except Exception as e:
        logger.error(f"WAN status check failed: {e}")
        return "TIMEOUT"

def _check_ip_traffic_service():
    try:
        speed_data = get_speed_history()
        if speed_data and isinstance(speed_data, dict) and len(speed_data) > 0:
            return "ACTIVE"
        return "EMPTY"
    except Exception as e:
        logger.error(f"IP Traffic check failed: {e}")
        return "ERROR"

def check_bot_services():
    status = {}
    try:
        router  = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Referer": f"{ROUTER_URL}/", "User-Agent": "Mozilla/5.0"}
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=true\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=10
        )
        status["link"] = "OK" if r.status_code == 200 else "AUTH_ERR"
    except Exception as e:
        logger.error(f"Router link check failed: {e}")
        status["link"] = "UNREACHABLE"

    status["jffs2"]      = _check_jffs2()
    status["wan"]        = _check_wan_status()
    status["ip_traffic"] = _check_ip_traffic_service()

    try:
        r = requests.get(f"{RADIUS_URL}/radiusmanager/user.php", timeout=10)
        status["radius"] = "READY" if r.status_code == 200 else "DOWN"
    except Exception as e:
        logger.error(f"Radius check failed: {e}")
        status["radius"] = "DOWN"

    return status

# ========= ROUTER REBOOT =========
def _reboot_router():
    router = requests.Session()
    router.auth   = ROUTER_AUTH
    router.verify = False
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    data = "action=execute&command=reboot\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = router.post(f"{ROUTER_URL}/shell.cgi", headers=headers, data=data, timeout=10)
        if response.status_code == 200:
            logger.warning("Reboot command acknowledged by router (HTTP 200).")
            return True
        logger.error(f"Router responded with unexpected status {response.status_code} for reboot command.")
        return False
    except (ReadTimeout, ConnectionError) as e:
        logger.warning(f"Connection dropped while router was rebooting (expected): {e}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reboot command: {e}")
        return False

def _is_router_alive():
    try:
        router = requests.Session()
        router.auth   = ROUTER_AUTH
        router.verify = False
        headers = {"Referer": f"{ROUTER_URL}/", "User-Agent": "Mozilla/5.0"}
        r = router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data="action=execute&command=true\n&_http_id=TIDe5b1505eeac7f67f",
            timeout=5
        )
        return r.status_code == 200
    except Exception:
        return False

async def _wait_for_router_and_notify(channel, user_mention):
    await asyncio.sleep(15)
    max_wait = 300
    interval = 10
    waited   = 0
    logger.info("Started watching for router recovery after manual reboot.")
    while waited < max_wait:
        try:
            async with ROUTER_LOCK:
                alive = await asyncio.to_thread(_is_router_alive)
        except Exception as e:
            logger.error(f"Error while checking router recovery: {e}")
            alive = False

        logger.debug(f"Router recovery check #{waited // interval + 1}: alive={alive} (waited {waited}s/{max_wait}s)")

        if alive:
            embed = discord.Embed(
                title="`✅` Router is Back Online",
                description=f"{user_mention} I'm up! The router has rebooted successfully and is responding again.",
                color=0x2ecc71
            )
            try:
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Failed to send router recovery notice: {e}")
            logger.info("Router recovery confirmed and notified.")
            return
        await asyncio.sleep(interval)
        waited += interval

    try:
        embed = discord.Embed(
            title="`⚠️` Router Still Unreachable",
            description=f"{user_mention} The router hasn't come back online {max_wait // 60} minutes after the reboot. Please check it manually.",
            color=0xe67e22
        )
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to send router recovery timeout notice: {e}")
    logger.warning("Router did not come back online within the expected window.")

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
REPORT_TIME = time(hour=23, minute=55, tzinfo=ZoneInfo("Africa/Cairo"))

@tasks.loop(time=REPORT_TIME)
async def daily_network_report():
    try:

        async with ROUTER_LOCK:
            speed_history = await asyncio.to_thread(get_speed_history)

        async with ROUTER_LOCK:
            daily_history = await asyncio.to_thread(get_daily_history)

        async with ROUTER_LOCK:
            dhcp_leases, _, _ = await asyncio.to_thread(_fetch_devlist)

        devices_info       = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_usage     = get_today_combined(speed_history, daily_history)
        combined_data      = []
        total_day_usage_mb = 0.0

        for ip, total_bytes in combined_usage.items():
            usage_mb = bytes_to_mb(total_bytes)
            if usage_mb < 0.1:
                continue
            total_day_usage_mb += usage_mb
            target_mac = next((mac for mac, info in devices_info.items() if info["ip"] == ip), None)
            if not target_mac:
                continue
            raw_name   = devices_info[target_mac]["name"]
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
                enable_lockdown(router, headers, force_lock=LOCKDOWN_STATE)
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

# ========= Reboot Confirmation SETUP =========
class RebootConfirmView(discord.ui.View):
    def __init__(self, requester_id):
        super().__init__(timeout=30)
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("`⚠️` Only the user who issued this command can confirm it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        try:
            await interaction.response.edit_message(content="`🔄` Reboot confirmed. Sending the command to the router now...", view=self)
        except Exception as e:
            logger.error(f"Failed to edit reboot confirmation message: {e}")

        logger.warning(f"Router reboot CONFIRMED by {interaction.user}")

        logger.info("Waiting 5 seconds before sending the actual reboot command...")
        await asyncio.sleep(5)
        logger.info("Done waiting. Sending the reboot command to the router now.")

        try:
            async with ROUTER_LOCK:
                success = await asyncio.to_thread(_reboot_router)
        except Exception as e:
            logger.exception(f"Unexpected error while sending reboot command: {e}")
            success = False

        try:
            if success:
                logger.info("Sending 'reboot command sent successfully' message to channel.")
                await interaction.channel.send(f"{interaction.user.mention} `✅` Reboot command was sent successfully.")
            else:
                logger.info("Sending 'reboot command failed' message to channel.")
                await interaction.channel.send(f"{interaction.user.mention} `❌` Failed to send the reboot command. Check logs.")
        except Exception as e:
            logger.error(f"Failed to send reboot result message: {e}")

        if success:
            try:
                asyncio.create_task(_wait_for_router_and_notify(interaction.channel, interaction.user.mention))
                logger.info("Router recovery watcher scheduled.")
            except Exception as e:
                logger.exception(f"Failed to schedule router recovery watcher: {e}")
        else:
            logger.warning("Skipping recovery watcher because the reboot command was not confirmed as sent.")

        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="`✖️` Reboot cancelled.", view=self)
        self.stop()

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
        if not device_discovery_task.is_running():
            device_discovery_task.start()
        if not daily_usage_monitor_task.is_running():
            daily_usage_monitor_task.start()
        if not midnight_reset_task.is_running():
            midnight_reset_task.start()

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord Gateway. Waiting for automatic reconnect...")

    async def on_resumed(self):
        logger.info("Discord Gateway session resumed successfully. Bot is fully operational.")

    async def on_ready(self):
        global BANNED_MACS, MACS_LIST, ALLOWED_MACS, THRESHOLD, LOCKDOWN_STATE
        logger.info(f"Bot ready: {self.user}")
        BANNED_MACS    = db.get_banned()
        MACS_LIST      = db.get_devices()
        ALLOWED_MACS   = db.get_allowed()
        THRESHOLD      = db.get_threshold()
        LOCKDOWN_STATE = db.get_lockdown_state()
        logger.info(f"Reapplying firewall rules on startup (lockdown_state={LOCKDOWN_STATE})...")
        async with ROUTER_LOCK:
            await asyncio.to_thread(_reapply_firewall_state)

# ========= ROUTER LOCK =========
ROUTER_LOCK = asyncio.Lock()

bot = MyBot()

# ========= HELPERS FOR COMMANDS =========
def _reapply_firewall_state():
    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    enable_lockdown(router, headers, force_lock=LOCKDOWN_STATE)

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

async def all_macs_autocomplete(interaction: discord.Interaction, current: str):
    choices = [
        app_commands.Choice(name=hostname, value=mac)
        for mac, hostname in MACS_LIST.items()
        if current.lower() in hostname.lower() or current.lower() in mac.lower()
    ]
    return choices[:25]

# ========= DAILY LIMIT RECHECK HELPERS =========
async def _recheck_device_after_limit_change(mac):
    if mac not in BANNED_MACS or db.get_ban_reason(mac) != "daily_limit":
        return

    async with ROUTER_LOCK:
        usage_by_mac = await asyncio.to_thread(_get_today_usage_by_mac)

    usage_gb        = usage_by_mac.get(mac, 0)
    effective_limit = db.get_effective_daily_limit(mac)
    if usage_gb >= effective_limit:
        return

    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    async with ROUTER_LOCK:
        await asyncio.to_thread(unban_mac, router, headers, mac)

    device_name = MACS_LIST.get(mac, mac)
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        status_box = (
            f"```\n"
            f"{'Device:'.ljust(10)} {device_name}\n"
            f"{'Usage:'.ljust(10)} {usage_gb:.2f} GB\n"
            f"{'New Limit:'.ljust(10)} {effective_limit:.2f} GB\n"
            f"```"
        )
        embed = discord.Embed(
            title="`✅` Device Auto-Unblocked (Limit Increased)",
            description=status_box,
            color=0x2ecc71
        )
        await channel.send(embed=embed)

async def _recheck_default_limit_devices():
    daily_banned = db.get_banned_by_reason("daily_limit")
    overrides    = db.get_all_device_daily_limits()
    candidates   = [mac for mac in daily_banned if mac not in overrides]
    if not candidates:
        return

    async with ROUTER_LOCK:
        usage_by_mac = await asyncio.to_thread(_get_today_usage_by_mac)

    router  = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    default_limit = db.get_daily_default_limit()
    channel = bot.get_channel(CHANNEL_ID)

    for mac in candidates:
        usage_gb = usage_by_mac.get(mac, 0)
        if usage_gb >= default_limit:
            continue

        async with ROUTER_LOCK:
            await asyncio.to_thread(unban_mac, router, headers, mac)

        device_name = MACS_LIST.get(mac, mac)
        if channel:
            status_box = (
                f"```\n"
                f"{'Device:'.ljust(10)} {device_name}\n"
                f"{'Usage:'.ljust(10)} {usage_gb:.2f} GB\n"
                f"{'New Limit:'.ljust(10)} {default_limit:.2f} GB\n"
                f"```"
            )
            embed = discord.Embed(
                title="`✅` Device Auto-Unblocked (Limit Increased)",
                description=status_box,
                color=0x2ecc71
            )
            await channel.send(embed=embed)

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
            manual_lines = []
            daily_lines  = []
            for m in BANNED_MACS:
                device_name = MACS_LIST.get(m, "Unknown Device")
                reason = db.get_ban_reason(m)
                if reason == "daily_limit":
                    daily_lines.append(device_name)
                else:
                    manual_lines.append(device_name)

            sections = []
            if manual_lines:
                sections.append("🔒 Manual:")
                sections += [f"  {i:02d}. {name}" for i, name in enumerate(manual_lines, 1)]
            if daily_lines:
                if sections:
                    sections.append("")
                sections.append("⏱️ Daily Limit:")
                sections += [f"  {i:02d}. {name}" for i, name in enumerate(daily_lines, 1)]

            banned_output = "```\n" + "\n".join(sections) + "```"
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
            dhcp_leases, wireless_devs, arp_list = await asyncio.to_thread(_fetch_devlist)

        active_signals = {dev[1].upper(): dev[2] for dev in wireless_devs}
        arp_active     = {entry[1].upper() for entry in arp_list}
        devices_info   = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_data  = []

        for mac, info in devices_info.items():
            is_online = mac in active_signals and mac in arp_active
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

        online  = [d for d in combined_data if d["icon"] == "🟢"]
        offline = [d for d in combined_data if d["icon"] == "🔴"]
        banned  = [d for d in combined_data if d["icon"] == "⛔"]

        if combined_data:
            def fmt(dev):
                return f"{dev['icon']} `{dev['name'][:12].ljust(12)} | 📶{dev['signal'].rjust(4)}`"

            lines = []
            if online:
                lines += [fmt(d) for d in online]
            if offline:
                lines += ["─────────────────────"]
                lines += [fmt(d) for d in offline]
            if banned:
                lines += ["─────────────────────"]
                lines += [fmt(d) for d in banned]

            embed = discord.Embed(
                title=f"`📡` Active Devices ({len(arp_active)} Devices)",
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
@bot.tree.command(name="netstat", description="Show device network usage or daily limit status")
@app_commands.describe(view="Choose between raw usage (default) or daily limit comparison")
@app_commands.choices(view=[
    app_commands.Choice(name="Usage", value="usage"),
    app_commands.Choice(name="Daily Limit Status", value="daily_limit"),
])
async def netstat(interaction: discord.Interaction, view: app_commands.Choice[str] = None):
    if not await safe_defer(interaction, thinking=True):
        return
    view_value = view.value if view else "usage"
    logger.info(f"Network usage status requested by {interaction.user} (view={view_value})")
    try:

        async with ROUTER_LOCK:
            speed_history = await asyncio.to_thread(get_speed_history)

        async with ROUTER_LOCK:
            daily_history = await asyncio.to_thread(get_daily_history)

        async with ROUTER_LOCK:
            dhcp_leases, _, _ = await asyncio.to_thread(_fetch_devlist)

        devices_info   = {lease[2].upper(): {"name": lease[0], "ip": lease[1]} for lease in dhcp_leases}
        combined_usage = get_today_combined(speed_history, daily_history)

        if view_value == "daily_limit":
            combined_data = []
            for mac, info in devices_info.items():
                total_bytes     = combined_usage.get(info["ip"], 0)
                usage_gb        = bytes_to_mb(total_bytes) / 1024
                effective_limit = db.get_effective_daily_limit(mac)
                combined_data.append({
                    "name":     MACS_LIST.get(mac, info["name"]),
                    "usage_gb": usage_gb,
                    "limit_gb": effective_limit,
                    "over":     usage_gb >= effective_limit
                })

            combined_data.sort(key=lambda x: (x["usage_gb"] / x["limit_gb"]) if x["limit_gb"] else 0, reverse=True)

            if combined_data:
                lines = []
                for dev in combined_data[:15]:
                    icon = "🔴" if dev["over"] else "🟢"
                    lines.append(f"{icon} `{dev['name'][:12].ljust(12)} | 📊{dev['usage_gb']:.2f}/{dev['limit_gb']:.2f}GB`")
                embed = discord.Embed(
                    title=f"`📡` Daily Limit Status ({len(combined_data)} Devices)",
                    description="\n".join(lines),
                    color=0x2ecc71
                )
            else:
                embed = discord.Embed(description="✨ No devices found in history.", color=0x95a5a6)

            await interaction.followup.send(embed=embed)
            return

        combined_data    = []
        total_traffic_mb = 0.0

        for ip, total_bytes in combined_usage.items():
            usage_mb = bytes_to_mb(total_bytes)
            if usage_mb < 0.1:
                continue
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
@bot.tree.command(name="limit", description="View or change the main balance threshold or daily per-device usage limits")
@app_commands.describe(
    scope="Which limit to change (defaults to the main balance threshold)",
    value="The new limit value",
    unit="Unit for the value (defaults to GB)",
    mac="Set a custom daily limit for one specific device"
)
@app_commands.choices(scope=[
    app_commands.Choice(name="Main Balance", value="main"),
    app_commands.Choice(name="Daily Default", value="daily_default"),
    app_commands.Choice(name="List", value="list"),
])
@app_commands.choices(unit=[
    app_commands.Choice(name="GB", value="GB"),
    app_commands.Choice(name="MB", value="MB"),
])
@app_commands.autocomplete(mac=all_macs_autocomplete)
async def set_limit(
    interaction: discord.Interaction,
    scope: app_commands.Choice[str] = None,
    value: float = None,
    unit: app_commands.Choice[str] = None,
    mac: str = None
):
    global THRESHOLD
    if not await safe_defer(interaction, thinking=True):
        return
    try:
        scope_value = scope.value if scope else "main"
        unit_value  = unit.value if unit else "GB"

        if scope_value == "list":
            default_limit = db.get_daily_default_limit()
            overrides     = db.get_all_device_daily_limits()
            lines = [
                f"{'Main Threshold:'.ljust(18)} {THRESHOLD} GB",
                f"{'Daily Default:'.ljust(18)} {default_limit} GB",
            ]
            if overrides:
                lines.append("")
                lines.append("Custom Daily Limits:")
                for m, gb in overrides.items():
                    device_name = MACS_LIST.get(m, m)
                    lines.append(f"  {device_name[:14].ljust(14)} : {gb} GB")
            status_box = "```\n" + "\n".join(lines) + "\n```"
            embed = discord.Embed(title="`⚙️` Current Limit Configuration", description=status_box, color=0xf1c40f)
            await interaction.followup.send(embed=embed)
            return

        if value is None:
            await interaction.followup.send("`⚠️` Please provide a value.")
            return

        value_gb = value / 1024 if unit_value == "MB" else value

        if mac:
            mac_upper = mac.upper()
            db.set_device_daily_limit(mac_upper, value_gb)
            device_name = MACS_LIST.get(mac_upper, mac_upper)
            logger.info(f"User {interaction.user} set custom daily limit for {mac_upper} to {value_gb} GB")

            status_box = (
                f"```\n"
                f"{'Device:'.ljust(10)} {device_name}\n"
                f"{'New Limit:'.ljust(10)} {value_gb:.2f} GB\n"
                f"```\n"
                f"`✅` *Settings updated.*"
            )
            embed = discord.Embed(title="`⚙️` Daily Limit Update", description=status_box, color=0xf1c40f)
            await interaction.followup.send(embed=embed)

            await _recheck_device_after_limit_change(mac_upper)
            return

        if scope_value == "daily_default":
            old_limit = db.get_daily_default_limit()
            db.set_daily_default_limit(value_gb)
            logger.info(f"User {interaction.user} updated daily default limit to {value_gb} GB")

            label_old = "Old Default:".ljust(14)
            label_new = "New Default:".ljust(14)
            status_box = (
                f"```\n"
                f"{label_old} {old_limit} GB\n"
                f"{label_new} {value_gb} GB\n"
                f"```\n"
                f"`✅` *Settings updated.*"
            )
            embed = discord.Embed(title="`⚙️` Daily Default Limit Update", description=status_box, color=0xf1c40f)
            await interaction.followup.send(embed=embed)

            await _recheck_default_limit_devices()
            return

        old_limit = THRESHOLD
        THRESHOLD = value_gb
        db.set_threshold(value_gb)
        logger.info(f"User {interaction.user} updated THRESHOLD to {value_gb}")
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
        if s in ["OK", "ONLINE", "READY", "ACTIVE", "CONNECTED"]:
            return "🟢"
        if s in ["OFFLINE", "DOWN", "AUTH_ERR", "DISCONNECTED", "ERROR", "CRITICAL"]:
            return "🔴"
        return "⚪"

    all_ok = all(v in ["OK", "ONLINE", "READY", "ACTIVE", "CONNECTED"] for v in health.values())
    embed_color = 0x2ecc71 if all_ok else 0xe74c3c
    title_icon  = "✅" if all_ok else "⚠️"
    status_box  = (
        f"```\n"
        f"{'Router Link':<14} | {health['link']:<12} {get_status_emoji(health['link'])}\n"
        f"{'Wan':<14} | {health['wan']:<12} {get_status_emoji(health['wan'])}\n"
        f"{'JFFS2':<14} | {health['jffs2']:<12} {get_status_emoji(health['jffs2'])}\n"
        f"{'IP Traffic':<14} | {health['ip_traffic']:<12} {get_status_emoji(health['ip_traffic'])}\n"
        f"{'Radius Dash':<14} | {health['radius']:<12} {get_status_emoji(health['radius'])}\n"
        f"```"
    )
    logger.info(f"Bot status requested by {interaction.user}")
    embed = discord.Embed(
        title=f"`{title_icon}` System Health Dashboard",
        description=status_box,
        color=embed_color
    )
    await interaction.followup.send(embed=embed)

# --------- /reboot ---------
@bot.tree.command(name="reboot", description="Reboot the router (requires confirmation)")
async def reboot_router(interaction: discord.Interaction):
    try:
        await interaction.response.send_message(
            "`⚠️` **Are you sure you want to reboot the router?**\nIt will be unreachable for a minute or two.",
            view=RebootConfirmView(interaction.user.id)
        )
        logger.info(f"Reboot requested by {interaction.user} (awaiting confirmation)")
    except Exception as e:
        logger.error(f"Error showing reboot confirmation: {e}")

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

@tasks.loop(minutes=5.0)
async def device_discovery_task():
    if ROUTER_LOCK.locked():
        logger.debug("Device discovery skipped (router busy with another task).")
        return
    try:
        async with ROUTER_LOCK:
            await asyncio.to_thread(lambda: _fetch_devlist_and_discover(bot))
        logger.debug("Scheduled device discovery check completed.")
    except Exception as e:
        logger.error(f"Error in device_discovery_task: {e}")

@device_discovery_task.before_loop
async def before_device_discovery():
    await bot.wait_until_ready()
    logger.info("Device discovery task started (checks for new devices every 5 minutes).")

@tasks.loop(minutes=15.0)
async def daily_usage_monitor_task():
    if ROUTER_LOCK.locked():
        logger.debug("Daily usage monitor skipped (router busy with another task).")
        return
    logger.info("Starting scheduled daily usage monitor check...")
    try:
        async with ROUTER_LOCK:
            usage_by_mac = await asyncio.to_thread(_get_today_usage_by_mac)

        router  = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
        channel = bot.get_channel(CHANNEL_ID)

        for mac, usage_gb in usage_by_mac.items():
            effective_limit = db.get_effective_daily_limit(mac)
            if usage_gb < effective_limit:
                continue

            device_name = MACS_LIST.get(mac, mac)

            if mac in ALLOWED_MACS:
                if not db.was_notified_today(mac):
                    db.mark_notified_today(mac)
                    if channel:
                        status_box = (
                            f"```\n"
                            f"{'Device:'.ljust(10)} {device_name}\n"
                            f"{'Usage:'.ljust(10)} {usage_gb:.2f} GB\n"
                            f"{'Limit:'.ljust(10)} {effective_limit:.2f} GB\n"
                            f"```"
                        )
                        embed = discord.Embed(
                            title="`⚠️` Whitelisted Device Over Daily Limit",
                            description=status_box,
                            color=0xe67e22
                        )
                        embed.set_footer(text="Whitelisted devices are never blocked automatically.")
                        await channel.send(embed=embed)
                continue

            if mac in BANNED_MACS:
                continue

            async with ROUTER_LOCK:
                await asyncio.to_thread(ban_mac, router, headers, mac, "daily_limit")

            logger.info(f"Auto-blocked {mac} for exceeding daily usage limit ({usage_gb:.2f}GB / {effective_limit:.2f}GB).")

            if channel:
                status_box = (
                    f"```\n"
                    f"{'Device:'.ljust(10)} {device_name}\n"
                    f"{'Usage:'.ljust(10)} {usage_gb:.2f} GB\n"
                    f"{'Limit:'.ljust(10)} {effective_limit:.2f} GB\n"
                    f"```"
                )
                embed = discord.Embed(
                    title="`🚫` Device Auto-Blocked (Daily Limit)",
                    description=status_box,
                    color=0xff4747
                )
                await channel.send(embed=embed)

        logger.info("Scheduled daily usage monitor check completed.")
    except Exception as e:
        logger.error(f"Error in daily_usage_monitor_task: {e}")

@daily_usage_monitor_task.before_loop
async def before_daily_usage_monitor():
    await bot.wait_until_ready()
    logger.info("Daily usage monitor task started (checks per-device usage every 15 minutes).")

MIDNIGHT_RESET_TIME = time(hour=0, minute=0, tzinfo=ZoneInfo("Africa/Cairo"))

@tasks.loop(time=MIDNIGHT_RESET_TIME)
async def midnight_reset_task():
    logger.info("Starting scheduled midnight reset...")
    try:
        router  = requests.Session()
        router.auth = ROUTER_AUTH
        headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}

        async with ROUTER_LOCK:
            ip_ok = await asyncio.to_thread(_reset_ip_traffic_stats)
        await asyncio.sleep(5)

        async with ROUTER_LOCK:
            bw_ok = await asyncio.to_thread(_reset_bandwidth_stats)
        await asyncio.sleep(5)

        daily_banned   = db.get_banned_by_reason("daily_limit")
        unbanned_count = 0
        if daily_banned:
            async with ROUTER_LOCK:
                for mac in list(daily_banned):
                    await asyncio.to_thread(unban_mac, router, headers, mac)
                    unbanned_count += 1

        db.clear_daily_notifications()

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            status_box = (
                f"```\n"
                f"{'IP Traffic Reset:'.ljust(20)} {'OK' if ip_ok else 'FAILED'}\n"
                f"{'Bandwidth Reset:'.ljust(20)} {'OK' if bw_ok else 'FAILED'}\n"
                f"{'Devices Unblocked:'.ljust(20)} {unbanned_count}\n"
                f"```"
            )
            embed = discord.Embed(
                title="`🌙` Midnight Reset Completed",
                description=status_box,
                color=0x2ecc71 if (ip_ok and bw_ok) else 0xe67e22
            )
            await channel.send(embed=embed)

        logger.info(f"Midnight reset completed. ip_ok={ip_ok} bw_ok={bw_ok} unbanned={unbanned_count}")
    except Exception as e:
        logger.error(f"Error in midnight_reset_task: {e}")

@midnight_reset_task.before_loop
async def before_midnight_reset():
    await bot.wait_until_ready()
    logger.info("Midnight reset task started (resets router traffic stats and daily-limit bans at 00:00 Cairo time).")

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