# Network Manager Bot

A Python script that automatically monitors your internet traffic balance and manages network access through router iptables rules and Telegram bot commands.

## Features

- **Automatic Traffic Monitoring**: Checks your available internet traffic balance from a Radius Manager portal every 30 minutes
- **Auto Lockdown**: Automatically blocks all devices except whitelisted MAC addresses when traffic balance falls below a threshold (default: 3.0 GB)
- **Telegram Bot Integration**: Control network access remotely via Telegram commands
- **MAC Address Management**: Ban/unban specific devices by MAC address through Telegram

## Telegram Commands

- `/ban <MAC_ADDRESS>` - Block a specific MAC address
- `/rm <MAC_ADDRESS>` - Unblock a MAC address
- `/list` - Show all currently banned MAC addresses
- `/macs` - Display the MAC address name mapping list

## Requirements

- Python 3.6+
- Router with shell access (iptables support)
- Radius Manager account credentials
- Telegram bot token

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the script by editing `netman.py`:
   - Set your Radius Manager username and password
   - Set your Telegram bot token and chat ID
   - Configure router URL and credentials
   - Adjust the traffic threshold as needed
   - Add allowed MAC addresses to the whitelist

3. Run the script:
```bash
python netman.py
```

## ⚠️ Known Issues & Troubleshooting

### Connection Timeout / Discord 404 (Interaction Failed)
* **Problem:** The bot experiences abrupt connection timeouts or fails to receive timely responses from the local Tomato router and Radius server (`10.0.0.254`), causing Discord slash commands to fail with `404 Unknown interaction`. This is typically caused by a mismatch in Linux Kernel TCP extension tracking (`tcp_timestamps`) between the host OS (e.g., Fedora) and the router's older TCP stack.
* **Solution:** Disable TCP timestamps on the host machine by running the following command:
  ```bash
  sudo sysctl -w net.ipv4.tcp_timestamps=0
  ```
## How It Works

1. The script logs into the Radius Manager portal to check available traffic
2. If traffic is below the threshold, it enables a full lockdown (only whitelisted MACs allowed)
3. If traffic is sufficient, it maintains any manual bans while allowing other devices
4. A background thread continuously monitors Telegram for commands to ban/unban devices
5. All actions are reported via Telegram notifications
## Security Note

This script contains sensitive credentials. Keep it secure and never commit credentials to version control.

