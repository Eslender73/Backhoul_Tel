# 📡 Backhoul_Tel


------------------------

# Advanced Server & Cloud Management Telegram Bot

A powerful, asynchronous Telegram bot for managing a fleet of Linux servers, Virtualizor VPSs, and Cloudflare accounts through a comprehensive, menu-driven interface. This bot acts as a centralized control panel for all your infrastructure needs, from live status monitoring to transactional service deployments.

## ✨ Key Features

### 🖥️ Core Server Management

  - **Live Status Monitoring:** Get a real-time, auto-refreshing dashboard of CPU and RAM usage for all your servers.
  - **Server CRUD:** Securely add, update, and delete overseas (Kharej) and Iran-based servers directly from the bot.
  - **Reliable Connectivity:** Features a multi-layered connection system that attempts direct connections and falls back to using other servers as jump hosts to ensure maximum uptime and reachability.

### 🔗 Tunnel Management

  - **Backhaul Tunnels:** Full lifecycle management including listing, creating (`.toml` & `systemd` service), and transactionally deleting tunnels on both Kharej and Iran servers.
  - **6to4 Tunnels:**
      - A complete wizard-style conversation to create new 6to4 tunnels.
      - Automatically generates a unique IPv6 ULA (`2002::/16`) subnet for each tunnel.
      - Dynamically creates and deploys installation scripts (`.sh`) to both servers.
      - Configures persistence via `@reboot` in crontab.
      - Provides a management menu to list, delete, and verify tunnel status with a live `ping` test.

### 🤖 Service & Automation Management

  - **Cron Job Manager:** Add and delete restart cron jobs for services on both Kharej and Iran servers simultaneously. Cron jobs are managed within a safe, marked block in the crontab to avoid interfering with user-defined jobs.
  - **Smart Reset Service:** Deploy, manage, and monitor the `guardian.py` watchdog script to automatically restart services upon failure.
  - **Backhaul Installer/Updater:** A dedicated menu to install or update the `backhaul_premium` binary on any server, with automatic architecture detection (`amd64`/`arm64`) and fallback download URLs.

### ☁️ Cloudflare Management

  - **Multi-Account Support:** Securely add and manage multiple Cloudflare accounts.
  - **Domain Dashboard:** For each domain, view a detailed dashboard including:
      - **Analytics:** 30-day stats for total requests and unique visitors (fetched via GraphQL API).
      - **SSL/TLS Settings:** View and modify SSL Mode and Minimum TLS Version.
      - **Toggle Settings:** Enable or disable key features like `Always Use HTTPS`, `Automatic HTTPS Rewrites`, `TLS 1.3`, `IPv6 Compatibility`, and `WebSockets` via interactive buttons.
  - **DNS Record Management:**
      - View all DNS records in a clean, multi-column button layout.
      - Full CRUD for DNS records (`A`, `AAAA`, `CNAME`): Add, Edit (Name, Type, Content), and Delete records through a guided conversation.
      - Toggle Cloudflare Proxy (CDN) status for each record.
      - Smart content validation ensures you enter a valid IP for an `A` record, etc.

### 🌐 Virtualizor (End-User) Management

  - **Multi-Panel Support:** Add and manage multiple Virtualizor control panels.
  - **VPS Listing:** Connect to each panel and list all available Virtual Private Servers (VPS).
  - **Live VPS Monitoring:** Get a live, auto-refreshing dashboard for any selected VPS, showing:
      - Hostname, IP Address(es), and OS
      - CPU Core Count
      - Total Allocated RAM
      - Bandwidth Usage (Total, Used, Remaining, and a percentage progress bar).
  - **(Coming Soon) VPS Controls:** The framework includes functions to Start, Stop, and Restart VPSs.

### 🚀 Deployment & Licensing

  - **One-Liner Installation:** Comes with a sophisticated `install.sh` script that allows for a complete, interactive installation on any fresh server with a single command (`curl ... | sudo bash`).
  - **Interactive Setup:** The installer interactively prompts for configuration details (Telegram Token, Chat ID) and can update an existing installation.
  - **Service Management:** Automatically creates and manages a `systemd` service for the bot to ensure it runs persistently.
  - **Remote Licensing:** The bot checks its license (allowed IP and expiration date) from a remote URL on startup and periodically, allowing for dynamic license management without restarting the bot.

## 🛠️ Technology Stack

  - **Backend:** Python 3.10+
  - **Telegram Framework:** `python-telegram-bot` (v20+)
  - **SSH & SFTP:** `paramiko`
  - **HTTP Requests:** `requests`
  - **Virtualizor API:** `lowendspirit`
  - **Deployment:** Bash, `systemd`
## 🧰 Prerequisites
  -Operating System: Ubuntu 22.04
  -Software: Python 3.10
  -Permissions: sudo access for installing dependencies and running services
  -Telegram Bot Token: Your Telegram bot API token (e.g., 7292304971:AAFj_v5bLbP_PeIDluBPyIDlk8Ly-qq0IVw)
  -Telegram Chat ID: Your Telegram user or group chat ID (e.g., 5278748577)


## 🚀 Installation

Installation is done via a single command. It will download the installer script, which will then download the bot binary and all dependencies, and guide you through an interactive setup.

```bash
curl -sL -o install.sh https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/install.sh && chmod +x install.sh && sudo ./install.sh
```

## ⚙️ Configuration

The installer will automatically create a `config.json` file in the installation directory (`/opt/monitor_bot/`). This file contains your Telegram token and other essential settings.

#1
```bash
nano /opt/monitor_bot/config.json
```
#2
```json
{
  "telegram_token": "00000000:0000_000000_000000000000000-00000",
  "chat_id": "00000000",
  "servers_file": "servers.tolm",
  "iran_servers_file": "iran_servers.json",
  "cron_links_file": "cron_links.json",
  "update_interval_seconds": 5
}
```
Explanation:
telegram_token
This is the API token for your Telegram bot. It is used to authenticate your bot with the Telegram API and send or receive messages.

chat_id
The unique identifier of the Telegram chat (user, group, or channel) where the bot will send messages or receive commands.

servers_file
The filename (or path) where the general server information is stored. Likely a text or structured file listing your servers.

iran_servers_file
The filename for storing server information specifically related to Iranian servers. This file is in JSON format.

cron_links_file
The filename where scheduled task links or cron job configurations are saved, also in JSON format.

update_interval_seconds
The time interval, in seconds, at which the bot updates its data or performs periodic checks or tasks. Here it is set to 5 seconds.


📬 Contact
If you have any questions or issues, please open an Issue or get in touch with us.

Lovingly developed by Eslender73 — and this one too ❤️
