# Network Manager Bot

A Python script that automatically monitors your internet traffic balance and manages network access through router iptables rules and Telegram bot commands.

## ⚠️ Known Issues & Troubleshooting

### Connection Timeout / Discord 404 (Interaction Failed)
* **Problem:** The bot experiences abrupt connection timeouts or fails to receive timely responses from the local Tomato router and Radius server (`10.0.0.254`), causing Discord slash commands to fail with `404 Unknown interaction`. This is typically caused by a mismatch in Linux Kernel TCP extension tracking (`tcp_timestamps`) between the host OS (e.g., Fedora) and the router's older TCP stack.
* **Solution:** Disable TCP timestamps on the host machine by running the following command:
  ```bash
  sudo sysctl -w net.ipv4.tcp_timestamps=0
  ```
  
## Security Note

This script contains sensitive credentials. Keep it secure and never commit credentials to version control.

