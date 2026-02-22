import requests
import hashlib
import hmac
from bs4 import BeautifulSoup
from requests.exceptions import ReadTimeout, ConnectionError
import time
import threading
import discord 
from discord import app_commands

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
# ========= HELPERS =========
def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()

def hex_hmac_md5(key, data):
    return hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()
# ========= ROUTER EXEC =========
def run_cmd(router, headers, cmd):
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        router.post(
            f"{ROUTER_URL}/shell.cgi",
            headers=headers,
            data=data,
            timeout=10
        )
    except (ReadTimeout, ConnectionError):
        pass

# ========= LOCKDOWN LOGIC =========
def clear_lockdown(router, headers):
    run_cmd(router, headers, "iptables -D FORWARD -i br0 -j LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -F LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -X LOCKDOWN 2>/dev/null")

def enable_lockdown(router, headers, force_lock=False):
    run_cmd(router, headers, "iptables -F LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -X LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -N LOCKDOWN 2>/dev/null")

    if force_lock:
        for mac in ALLOWED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd(router, headers, "iptables -A LOCKDOWN -j DROP")
    else:
        for mac in BANNED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j DROP")
        for mac in ALLOWED_MACS:
            run_cmd(router, headers, f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd(router, headers, "iptables -A LOCKDOWN -j ACCEPT")

    run_cmd(router, headers, "iptables -D FORWARD -i br0 -j LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -I FORWARD 1 -i br0 -j LOCKDOWN")

def ban_mac(router, headers, mac):
    mac = mac.upper()
    BANNED_MACS.add(mac)
    enable_lockdown(router, headers, force_lock=False)

def unban_mac(router, headers, mac):
    mac = mac.upper()
    if mac in BANNED_MACS:
        BANNED_MACS.remove(mac)
    enable_lockdown(router, headers, force_lock=False)

# ========= MAIN CHECK =========
def check_and_lock(bot_instance):
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}
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
    router.get(ROUTER_URL + "/")
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}

    msg = ""
    if traffic_value < THRESHOLD:
        enable_lockdown(router, headers, force_lock=True)
        msg = f"⚠️ **LOCKDOWN enabled.**\n```\n📊 Balance: {available_traffic}\n⏳ Limit: {THRESHOLD}```"
    else:
        enable_lockdown(router, headers, force_lock=False)
        msg = f"✅ **Normal Mode.**\n```\n📊 Balance: {available_traffic}\n⏳ Limit: {THRESHOLD}```"

    channel = bot_instance.get_channel(CHANNEL_ID)
    if channel:
        bot_instance.loop.create_task(channel.send(msg))

# ========= Get Balance Only =========
def get_balance():
    session = requests.Session()
    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)
    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}
    session.post("http://10.0.0.254/radiusmanager/user.php?cont=login", data=payload)
    session.get("http://10.0.0.254/radiusmanager/user.php?cont=change_lang&lang=English")
    dash = session.get("http://10.0.0.254/radiusmanager/user.php")
    soup = BeautifulSoup(dash.text, "html.parser")

    for td in soup.find_all("td"):
        if "Available total traffic" in td.get_text(strip=True):
            return td.find_next_sibling("td").get_text(strip=True)
    return None

# ========= DISCORD BOT SETUP =========
class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)

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
    router = requests.Session()
    router.auth = ROUTER_AUTH
    headers = {"Content-Type": "text/plain;charset=UTF-8", "Referer": ROUTER_URL + "/", "Origin": ROUTER_URL}
    
    ban_mac(router, headers, mac)
    
    current_list = get_banned_list_text()
    device_name = MACS_LIST.get(mac.upper(), "Unknown Device")
    
    response_msg = (
        f"🚫 **Blocked:** {device_name} ({mac.upper()})\n\n"
        f"**Updated Banned List:**\n"
        f"```\n{current_list}```"
    )
    await interaction.response.send_message(response_msg)

# --------- /rm ---------
@bot.tree.command(name="rm", description="Unban a device from the current banned list")
@app_commands.autocomplete(mac=banned_macs_autocomplete)
async def rm(interaction: discord.Interaction, mac: str):
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

# --------- /macs ---------
@bot.tree.command(name="macs", description="List known MAC names")
async def macs(interaction: discord.Interaction):
    msg = "\n".join(f"{mac} : {name}" for mac, name in MACS_LIST.items())
    await interaction.response.send_message(f"**MAC Names List:**\n```\n{msg}```")

# --------- /list---------
@bot.tree.command(name="list", description="List currently banned MACs")
async def list_banned(interaction: discord.Interaction):
    if BANNED_MACS:
        banned_list = "\n".join(f"{i+1}- {m} ({MACS_LIST.get(m, 'Unknown')})" for i, m in enumerate(BANNED_MACS))
    else:
        banned_list = "No MACs banned"
    await interaction.response.send_message(f"**Banned MACs:**\n```\n{banned_list}```")

# --------- /balance---------
@bot.tree.command(name="balance", description="Check current available traffic")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer() 
    traffic = get_balance()
    if traffic:
        await interaction.followup.send(f"📊 **Current Balance:** __{traffic}__")
    else:
        await interaction.followup.send("❌ Could not fetch balance. Check logs.")

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
def run_check_loop():
    while True:
        try:
            check_and_lock(bot)
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(15)

threading.Thread(target=run_check_loop, daemon=True).start()

bot.run(DISCORD_TOKEN)