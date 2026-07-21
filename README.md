# Network Manager Bot

A Python script that automatically monitors your internet traffic balance and manages network access through router iptables rules and Discord bot commands.

## Features

- **Traffic Monitoring**: Automatically checks available traffic balance from Radius Manager dashboard every hour
- **Auto Lockdown**: Applies firewall rules to restrict/block devices when traffic falls below threshold
- **Discord Integration**: Full slash command interface for managing banned devices, checking status, and viewing network usage
- **MAC Address Management**: Whitelist/blacklist devices by MAC address with persistent storage
- **Daily Reports**: Automated daily usage reports with per-device breakdowns
- **VPS Ready**: Designed to run on a remote VPS with SSH tunneling to local router

## Requirements

- Python 3.8+
- Discord Bot Token
- Tomato/DD-WRT Router with shell.cgi access
- Radius Manager Dashboard credentials
- Linux host for SSH tunnel (Fedora/Ubuntu/etc.)
- VPS with public IP (e.g., Contabo, Hetzner, DigitalOcean)

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd NetManager_Bot
```

### 2. Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# OR
.venv\Scripts\activate  # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root:

```env
DISCORD_TOKEN=your_discord_bot_token_here
D_USERNAME=your_radius_username
D_PASSWORD=your_radius_password
ROUTER_URL=http://192.168.1.1:7080
RADIUS_URL=http://10.0.0.254
ROUTER_USER=admin
ROUTER_PASS=your_router_password
CHANNEL_ID=your_discord_channel_id
```

**Note**: When running on a VPS, `ROUTER_URL` and `RADIUS_URL` should point to the SSH tunnel endpoints (`http://127.0.0.1:7080` and `http://127.0.0.1:8080`). See the VPS Setup section below.

## Running the Bot

### Local Development (Direct Router Access)

```bash
python bot.py
```

### Production (VPS with SSH Tunnel)

```bash
python test2.py
```

## VPS Setup Guide

This setup allows the bot to run on a remote VPS while managing your local home router and Radius server through secure SSH tunnels.

### Architecture Overview

```
                    SSH Reverse Tunnel
    Home PC (Fedora)  <------------------>  VPS (Contabo)
    +----------------+                     +----------------+
    |  Router        |   -R 7080           |  Discord Bot   |
    |  192.168.1.1   |   -R 8080           |  81.17.98.226  |
    |  10.0.0.254    |                     |                |
    +----------------+                     +----------------+
```

### Step 1: Generate SSH Keys

On your local Linux machine (Fedora/Ubuntu):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tunnel_key -N ""
ssh-copy-id -i ~/.ssh/tunnel_key.pub root@YOUR_VPS_IP
```

Test the key works without password:
```bash
ssh -i ~/.ssh/tunnel_key root@YOUR_VPS_IP
```

### Step 2: Install autossh

```bash
# Fedora/RHEL
sudo dnf install autossh -y

# Ubuntu/Debian
sudo apt install autossh -y
```

### Step 3: Create the Tunnel Service

Create `/etc/systemd/system/router-tunnel.service` on your local machine:

```ini
[Unit]
Description=Router + Radius Tunnel to VPS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=belal
ExecStart=/usr/bin/autossh -M 0 \
  -i /home/belal/.ssh/tunnel_key \
  -o "ServerAliveInterval=5" \
  -o "ServerAliveCountMax=1" \
  -o "ExitOnForwardFailure=yes" \
  -o "StrictHostKeyChecking=no" \
  -o "TCPKeepAlive=yes" \
  -o "BatchMode=yes" \
  -N \
  -R 0.0.0.0:7080:192.168.1.1:7080 \
  -R 0.0.0.0:8080:10.0.0.254:80 \
  root@81.17.98.226
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

```

**Important**: Replace `belal` with your username, `/home/belal/.ssh/tunnel_key` with your key path, and `YOUR_VPS_IP` with your actual VPS IP.

### Step 4: Enable and Start the Tunnel

```bash
sudo systemctl daemon-reload
sudo systemctl enable router-tunnel.service
sudo systemctl start router-tunnel.service
sudo systemctl status router-tunnel.service
```

### Step 5: Configure VPS SSH Server

On the VPS, edit `/etc/ssh/sshd_config` and ensure:

```bash
GatewayPorts yes
ClientAliveInterval 10
ClientAliveCountMax 2
TCPKeepAlive yes
```

Restart SSH:
```bash
sudo systemctl restart sshd
```

### Step 6: Verify Tunnel on VPS

```bash
ss -tlnp | grep -E "7080|8080"
```

You should see:
```
LISTEN 0  128  0.0.0.0:7080  ...
LISTEN 0  128  0.0.0.0:8080  ...
```

Test the endpoints:
```bash
curl -m 30 http://127.0.0.1:7080
curl -m 30 http://127.0.0.1:8080/radiusmanager/user.php?cont=login
```

### Step 7: Create Bot Service on VPS

Create `/etc/systemd/system/botnet.service` on the VPS:

```ini
[Unit]
Description=BotNet Discord Bot Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/MY-Tools/NetManager_Bot
ExecStart=/root/.venv/bin/python /root/MY-Tools/NetManager_Bot/test2.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Note**: Adjust paths based on your actual setup. If your virtual environment is elsewhere, update `ExecStart` accordingly.

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable botnet.service
sudo systemctl start botnet.service
sudo systemctl status botnet.service
```

### Step 8: Update Bot Configuration for VPS

In your `.env` or configuration, update URLs to use the tunnel:

```env
ROUTER_URL=http://127.0.0.1:7080
RADIUS_URL=http://127.0.0.1:8080
```

The bot will now communicate with your local router and Radius server through the SSH tunnel.

### Monitoring Commands

| Command | Description |
|---------|-------------|
| `sudo systemctl status router-tunnel.service` | Check tunnel status (local machine) |
| `sudo systemctl status botnet.service` | Check bot status (VPS) |
| `sudo journalctl -u router-tunnel.service -f` | View tunnel logs live |
| `sudo journalctl -u botnet.service -f` | View bot logs live |
| `sudo systemctl restart botnet.service` | Restart the bot |
| `sudo systemctl restart router-tunnel.service` | Restart the tunnel |

## Discord Commands

| Command | Description | Permission |
|---------|-------------|------------|
| `/blk` | Ban a specific MAC address | Admin |
| `/rm` | Unban a specific MAC address | Admin |
| `/blkall` | Bulk block multiple devices | Admin |
| `/rmall` | Bulk unblock multiple devices | Admin |
| `/list` | Show currently banned devices | Admin |
| `/macs` | List all known MAC addresses | Admin |
| `/balance` | Check current traffic balance | Admin |
| `/netstat` | Show network status and usage | Admin |
| `/limit` | Change traffic threshold | Admin |
| `/botstatus` | Check system health status | Admin |
| `/purge` | Delete messages | Admin |

## ⚠️ Known Issues & Troubleshooting

### Connection Timeout / Discord 404 (Interaction Failed)

**Problem:** The bot experiences abrupt connection timeouts or fails to receive timely responses from the local Tomato router and Radius server (`10.0.0.254`), causing Discord slash commands to fail with `404 Unknown interaction`. This is typically caused by a mismatch in Linux Kernel TCP extension tracking (`tcp_timestamps`) between the host OS (e.g., Fedora) and the router's older TCP stack.

**Solution:** Disable TCP timestamps on the host machine by running the following command:
```bash
sudo sysctl -w net.ipv4.tcp_timestamps=0
```

### Read Timeout on VPS (SSH Tunnel)

**Problem:** The bot shows `Read timed out` even with increased timeouts when running through an SSH tunnel.

**Solution:** This indicates a stalled SSH tunnel. Ensure `autossh` is configured with proper keepalive settings:
- `ServerAliveInterval=10`
- `ServerAliveCountMax=2`
- `TCPKeepAlive=yes`
- `Restart=always` in systemd service

### SSH Tunnel Port Already in Use

**Problem:** `Warning: remote port forwarding failed for listen port 7080`

**Solution:** Kill the existing tunnel process on the VPS:
```bash
sudo ss -tlnp | grep 7080
sudo kill <PID>
```

Then restart the tunnel service on the local machine.

### Discord 10062 (Unknown Interaction)

**Problem:** Commands show `Unknown/expired interaction for /command (10062)`

**Solution:** This is usually caused by the user clicking multiple times before the bot responds. The bot uses `thinking=True` defer to extend the response window. Ensure users wait for the bot to process commands.

## Security Note

This script contains sensitive credentials. Keep it secure and never commit credentials to version control. Use environment variables or a secure secrets manager for production deployments.

## License

[Your License Here]
