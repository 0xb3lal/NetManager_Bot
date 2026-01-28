import requests
import hashlib
import hmac
from bs4 import BeautifulSoup
from requests.exceptions import ReadTimeout, ConnectionError
import time
import threading

# ========= HELPERS =========
def hex_md5(data):
    return hashlib.md5(data.encode()).hexdigest()

def hex_hmac_md5(key, data):
    return hmac.new(key.encode(), data.encode(), hashlib.md5).hexdigest()

# ========= CONFIG =========
username = "01552802883"
password = "123"

bot_token = "8466579075:AAG9pTDFtFt8C0HYCGxadgt9xbDtU9h9SD4"
chat_id = "8264390523"
telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

allowed_macs = [
    "4C:20:B8:87:12:E2",
    "F8:34:41:DA:93:EB",
    "DC:53:60:73:FD:84"
]
mac_names = {
    "D2:C5:E2:DE:F5:B4": "Baba",
    "5A:CE:CB:B5:D4:C9": "Ziad",
    "F8:34:41:DA:93:EB": "Me/Windows",
    "22:9F:AE:3B:5D:C4": "Mama",
    "1A:8C:37:73:23:8C": "Me/Iphone",
    "F2:72:C9:B8:4B:C7": "Tablet",
    "DC:53:60:73:FD:84": "Kali linux",
    "D6:62:9E:2B:31:3D": "Yousef"
}

def prt_names():
    
    msg = "\n".join(f"{mac} : {name}" for mac, name in mac_names.items())
    
    requests.post(telegram_url, data={
        "chat_id": chat_id,
        "text": f"<b>MAC Names List:</b>\n<pre>{msg}</pre>",
        "parse_mode": "HTML"
    },timeout=10)
BANNED_MACS = set()
LAST_UPDATE_ID = 0

THRESHOLD = 3.0
ROUTER_URL = "http://192.168.1.1:7080"
ROUTER_AUTH = ("belal", "107003##$$")

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
    # Clear old rules and rebuild chain
    run_cmd(router, headers, "iptables -F LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -X LOCKDOWN 2>/dev/null")
    run_cmd(router, headers, "iptables -N LOCKDOWN 2>/dev/null")

    if force_lock:
        # Add allowed MACs first
        for mac in allowed_macs:
            run_cmd(router, headers,
                    f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        # Block all other devices
        run_cmd(router, headers, "iptables -A LOCKDOWN -j DROP")
    else:
        # Apply manual bans
        for mac in BANNED_MACS:
            run_cmd(router, headers,
                    f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j DROP")
        # Add allowed MACs
        for mac in allowed_macs:
            run_cmd(router, headers,
                    f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        # Default ACCEPT for rest
        run_cmd(router, headers, "iptables -A LOCKDOWN -j ACCEPT")

    # Link chain to FORWARD
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
def check_and_lock():
    session = requests.Session()

    md5_password = hex_md5(password)
    md5_final = hex_hmac_md5(username, md5_password)

    payload = {
        "username": username,
        "md5": md5_final,
        "Submit": "Submit"
    }

    session.post(
        "http://10.0.0.254/radiusmanager/user.php?cont=login",
        data=payload
    )

    session.get(
        "http://10.0.0.254/radiusmanager/user.php?cont=change_lang&lang=English"
    )

    dash = session.get("http://10.0.0.254/radiusmanager/user.php")
    soup = BeautifulSoup(dash.text, "html.parser")

    available_traffic = None
    for td in soup.find_all("td"):
        if "Available total traffic" in td.get_text(strip=True):
            available_traffic = td.find_next_sibling("td").get_text(strip=True)
            break

    if not available_traffic:
        return

    traffic_value = float(available_traffic.split()[0])

    router = requests.Session()
    router.auth = ROUTER_AUTH
    router.get(ROUTER_URL + "/")

    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": ROUTER_URL + "/",
        "Origin": ROUTER_URL
    }

    if traffic_value < THRESHOLD:
        # Lock everything except allowed
        enable_lockdown(router, headers, force_lock=True)
        requests.post(telegram_url, data={
            "chat_id": chat_id,
            "text": f"<b>🚫 Internet LOCKDOWN enabled</b>\n"
                    f"<b>Your Balance: <u>{available_traffic}</u></b>\n"
                    f"<b>The Limit is: {THRESHOLD}</b>",
            "parse_mode": "HTML"
        },timeout=10)
    else:
        # Leave current bans as is
        enable_lockdown(router, headers, force_lock=False)
        requests.post(telegram_url, data={
            "chat_id": chat_id,
            "text": f"<b>Your Balance: <u>{available_traffic}</u></b>\n"
                    f"<b>The Limit is: {THRESHOLD}</b>\n"
                    f"<b>Internet restored to normal.</b>",
            "parse_mode": "HTML"
        },timeout=10)

# ========= TELEGRAM MAC CONTROL =========
def check_telegram_commands(router, headers):
    global LAST_UPDATE_ID, BANNED_MACS

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getUpdates",
            params={"offset": LAST_UPDATE_ID + 1, "timeout": 10}
        )
        data = r.json()
        if not data.get("ok"):
            error_msg = f"❌ Telegram API ERROR:\n{data.get('description')}"
            requests.post(telegram_url, data={
                "chat_id": chat_id,
                "text": error_msg,
                "parse_mode": "HTML"
            }, timeout=10)
            return

        for update in data["result"]:
            LAST_UPDATE_ID = update["update_id"]
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat = msg.get("chat", {}).get("id")

            if chat != int(chat_id):
                continue

            if text.lower().startswith("/ban"):
                mac = text.split(maxsplit=1)[1].upper()
                ban_mac(router, headers, mac)
                requests.post(telegram_url, data={
                    "chat_id": chat_id,
                    "text": f"🚫 <b>MAC Blocked:</b> {mac}",
                    "parse_mode": "HTML"
                }, timeout=10)

            elif text.lower().startswith("/rm"):
                mac = text.split(maxsplit=1)[1].upper()
                unban_mac(router, headers, mac)
                requests.post(telegram_url, data={
                    "chat_id": chat_id,
                    "text": f"✅ <b>MAC Unblocked:</b> {mac}",
                    "parse_mode": "HTML"
                }, timeout=10)

            elif text.lower().startswith("/macs"):
                prt_names()


            elif text.lower().startswith("/list"):
                if BANNED_MACS:
                    banned_list = "\n".join(
                        f"{i+1}- {mac} ({mac_names.get(mac, 'Unknown')})"
                        for i, mac in enumerate(BANNED_MACS)
                    )
                else:
                    banned_list = "No MACs banned"

                requests.post(telegram_url, data={
                    "chat_id": chat_id,
                    "text": f"<b>Banned MACs:</b>\n<pre>\n{banned_list}</pre>",
                    "parse_mode": "HTML"
                }, timeout=10)



    except Exception as e:
        error_msg = f"❌ Telegram handler ERROR:\n{str(e)}"
        requests.post(telegram_url, data={
            "chat_id": chat_id,
            "text": error_msg,
            "parse_mode": "HTML"
        }, timeout=10)

# ========= TELEGRAM LISTENER THREAD =========
def telegram_listener():
    router = requests.Session()
    router.auth = ROUTER_AUTH
    router.get(ROUTER_URL + "/")

    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": ROUTER_URL + "/",
        "Origin": ROUTER_URL
    }

    while True:
        check_telegram_commands(router, headers)
        time.sleep(5)

threading.Thread(target=telegram_listener, daemon=True).start()

# ========= MAIN LOOP =========
while True:
    try:
        check_and_lock()
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(1800)
