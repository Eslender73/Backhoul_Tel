#!/bin/bash

# --- Configuration ---
BOT_PYC_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/monitor_bot.pyc"
REQUIREMENTS_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/requirements.txt"

INSTALL_DIR="/opt/monitor_bot"
BOT_FILE="monitor_bot.pyc"
SERVICE_NAME="monitor_bot.service"
CONFIG_FILE="$INSTALL_DIR/config.json"

# Function to install dependencies
install_dependencies() {
    echo "Installing system dependencies for building some Python packages..."
    apt update && apt install -y \
        jq curl python3-pip \
        build-essential pkg-config python3-dev \
        libcairo2-dev ninja-build

    echo "Upgrading Meson to meet build requirements..."
    pip3 install --upgrade meson

    echo "Downloading requirements.txt..."
    curl -L -o "requirements.txt" "$REQUIREMENTS_URL"
    if [ $? -ne 0 ]; then
        echo "❌ Error downloading requirements.txt."
        exit 1
    fi

    echo "Installing Python libraries with pip..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "❌ Error installing Python libraries."
        exit 1
    fi

    rm requirements.txt
}

# Function to create config file
create_config() {
    echo "Setting up configuration..."

    if [ -f "$CONFIG_FILE" ]; then
        echo "Existing config file found."
        TELEGRAM_TOKEN=$(jq -r '.telegram_token' "$CONFIG_FILE")
        CHAT_ID=$(jq -r '.chat_id' "$CONFIG_FILE")
        SERVERS_FILE=$(jq -r '.servers_file' "$CONFIG_FILE")
        IRAN_SERVERS_FILE=$(jq -r '.iran_servers_file' "$CONFIG_FILE")
        CRON_LINKS_FILE=$(jq -r '.cron_links_file' "$CONFIG_FILE")
        UPDATE_INTERVAL=$(jq -r '.update_interval_seconds' "$CONFIG_FILE")
    else
        TELEGRAM_TOKEN=""
        CHAT_ID=""
        SERVERS_FILE="servers.tolm"
        IRAN_SERVERS_FILE="iran_servers.json"
        CRON_LINKS_FILE="cron_links.json"
        UPDATE_INTERVAL=5
    fi

    read -p "Telegram Token [$TELEGRAM_TOKEN]: " input
    TELEGRAM_TOKEN=${input:-$TELEGRAM_TOKEN}

    read -p "Chat ID [$CHAT_ID]: " input
    CHAT_ID=${input:-$CHAT_ID}

    read -p "Servers file [$SERVERS_FILE]: " input
    SERVERS_FILE=${input:-$SERVERS_FILE}

    read -p "Iran servers file [$IRAN_SERVERS_FILE]: " input
    IRAN_SERVERS_FILE=${input:-$IRAN_SERVERS_FILE}

    read -p "Cron links file [$CRON_LINKS_FILE]: " input
    CRON_LINKS_FILE=${input:-$CRON_LINKS_FILE}

    read -p "Update interval (seconds) [$UPDATE_INTERVAL]: " input
    UPDATE_INTERVAL=${input:-$UPDATE_INTERVAL}

    mkdir -p "$INSTALL_DIR"

    cat <<EOF > "$CONFIG_FILE"
{
  "telegram_token": "$TELEGRAM_TOKEN",
  "chat_id": "$CHAT_ID",
  "servers_file": "$SERVERS_FILE",
  "iran_servers_file": "$IRAN_SERVERS_FILE",
  "cron_links_file": "$CRON_LINKS_FILE",
  "update_interval_seconds": $UPDATE_INTERVAL
}
EOF

    echo "✅ Config saved to $CONFIG_FILE"
}

# Function to create systemd service
create_service() {
    echo "Creating systemd service..."
    cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=Telegram Monitor Bot Service (.pyc)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $INSTALL_DIR/$BOT_FILE
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

    echo "Enabling and starting the service..."
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    systemctl start $SERVICE_NAME
    echo "✅ Service installed and started successfully."
}

# Function to uninstall the bot
uninstall_bot() {
    echo "Stopping and disabling the service..."
    systemctl stop $SERVICE_NAME
    systemctl disable $SERVICE_NAME
    rm -f "/etc/systemd/system/$SERVICE_NAME"

    echo "Removing installed files..."
    rm -rf "$INSTALL_DIR"

    echo "Reloading systemd..."
    systemctl daemon-reload

    echo "🗑️ Uninstallation complete."
}

# Main script logic
if [ "$1" == "install" ]; then
    echo "🚀 Starting installation..."

    echo "Downloading bot .pyc file from GitHub..."
    curl -L -o "$BOT_FILE" "$BOT_PYC_URL"
    if [ $? -ne 0 ]; then
        echo "❌ Error downloading bot file."
        exit 1
    fi

    mkdir -p "$INSTALL_DIR"
    mv "./$BOT_FILE" "$INSTALL_DIR/"

    install_dependencies
    create_config
    create_service
    echo "🎉 Installation complete!"

elif [ "$1" == "uninstall" ]; then
    uninstall_bot

else
    echo "❌ Invalid command."
    echo "Usage:"
    echo "  $0 install     - Install the bot"
    echo "  $0 uninstall   - Uninstall the bot"
fi
