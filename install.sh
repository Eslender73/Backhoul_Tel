#!/bin/bash

# --- Configuration ---
BOT_BINARY_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/monitor_bot.pyc"
UPDATE_FILE_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/update.sh"
REQUIREMENTS_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/requirements.txt"

INSTALL_DIR="/opt/monitor_bot"
BOT_BINARY="monitor_bot.pyc"
UPDATE_FILE="update.sh"
CONFIG_FILE="$INSTALL_DIR/config.json"
SERVICE_NAME="monitor_bot.service"

# Function to install dependencies
install_dependencies() {
    echo "--- Step 1: Installing System Dependencies ---"
    apt update && apt install && apt install sshpass -y && pip install httpx && pip install packaging -y && curl python3-pip jq build-essential 
    if [ $? -ne 0 ]; then echo "❌ Error installing system dependencies."; exit 1; fi

    echo "Downloading requirements.txt..."
    curl -L -o "requirements.txt" "$REQUIREMENTS_URL"
    if [ $? -ne 0 ]; then echo "❌ Error downloading requirements.txt."; exit 1; fi

    echo "Installing Python libraries via pip..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then echo "❌ Error installing Python libraries."; exit 1; fi

    rm requirements.txt
    echo "✅ Dependencies installed successfully."
}

# Function to create config file
create_config() {
    echo "--- Step 3: Configuring Bot Settings ---"

    # Default values
    DEFAULT_TOKEN=""
    DEFAULT_CHAT_ID=""
    DEFAULT_INTERVAL=5

    # If a config file already exists, read its values as defaults
    if [ -f "$CONFIG_FILE" ]; then
        echo "⚠️ Existing config file found. Current values will be shown as defaults."
        DEFAULT_TOKEN=$(jq -r '.telegram_token // empty' "$CONFIG_FILE")
        DEFAULT_CHAT_ID=$(jq -r '.chat_id // empty' "$CONFIG_FILE")
        DEFAULT_INTERVAL=$(jq -r '.update_interval_seconds // 5' "$CONFIG_FILE")
    fi

    # Prompt user for input, showing the default value
    read -p "Enter Telegram Token [$DEFAULT_TOKEN]: " input_token < /dev/tty
    # If the user just presses Enter, use the default value
    TELEGRAM_TOKEN=${input_token:-$DEFAULT_TOKEN}

    read -p "Enter numeric Chat ID [$DEFAULT_CHAT_ID]: " input_chat_id < /dev/tty
    CHAT_ID=${input_chat_id:-$DEFAULT_CHAT_ID}
    
    read -p "Status update interval in seconds [$DEFAULT_INTERVAL]: " input_interval < /dev/tty
    UPDATE_INTERVAL=${input_interval:-$DEFAULT_INTERVAL}

    echo "Creating config file: $CONFIG_FILE..."
    # Use Heredoc to safely create the JSON file
    cat << EOF > "$CONFIG_FILE"
{
  "telegram_token": "$TELEGRAM_TOKEN",
  "chat_id": "$CHAT_ID",
  "servers_file": "servers.tolm",
  "iran_servers_file": "iran_servers.json",
  "cron_links_file": "cron_links.json",
  "update_interval_seconds": $UPDATE_INTERVAL
}
EOF

    # Create empty data files if they don't exist
    touch "$INSTALL_DIR/servers.tolm"
    touch "$INSTALL_DIR/iran_servers.json"
    touch "$INSTALL_DIR/cron_links.json"

    echo "✅ Config and data files created successfully."
}

# Function to create systemd service
create_service() {
    echo "--- Step 4: Creating and Installing systemd Service ---"
    cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=Telegram Monitor Bot
After=network.target

[Service]
Type=simple
ExecStart=python3 $INSTALL_DIR/$BOT_BINARY
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

    echo "Enabling and starting the service..."
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    systemctl restart $SERVICE_NAME
    
    echo "✅ Service installed and started successfully."
    echo "To check the status, use the command:"
    echo "systemctl status $SERVICE_NAME"
}

# Function to perform the full installation
install_flow() {
    echo "🚀 Starting installation/update process..."
    
    echo "--- Step 2: Downloading Bot Executable ---"
    # Stop the service first if it exists, to allow the binary to be replaced
    systemctl stop $SERVICE_NAME &>/dev/null
    
    curl -L -o "$BOT_BINARY" "$BOT_BINARY_URL"
    if [ $? -ne 0 ]; then echo "❌ Error downloading the bot file."; exit 1; fi
    
    mkdir -p "$INSTALL_DIR"
    mv "./$BOT_BINARY" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/$BOT_BINARY"
    echo "✅ Bot executable downloaded successfully."
	curl -L -o "$UPDATE_FILE" "$UPDATE_FILE_URL"
	if [ $? -ne 0 ]; then echo "❌ Error downloading update.sh."; exit 1; fi
	mv "./$UPDATE_FILE" "$INSTALL_DIR/"
	chmod +x "$INSTALL_DIR/$UPDATE_FILE"
	echo "✅ update.sh downloaded and placed successfully."
    
    create_config
    create_service
    echo "🎉 Installation complete!"
}


# Function to uninstall the bot
uninstall_bot() {
    read -p "Are you sure you want to completely uninstall the bot and all its files? (y/n) " -n 1 -r < /dev/tty
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Stopping and disabling the service..."
        systemctl stop $SERVICE_NAME
        systemctl disable $SERVICE_NAME
        rm -f "/etc/systemd/system/$SERVICE_NAME"
        systemctl daemon-reload
        
        echo "Removing installed files..."
        rm -rf "$INSTALL_DIR"
        
        echo "🗑️ Uninstallation complete."
    else
        echo "Operation cancelled."
    fi
}

# --- Interactive Main Menu ---
clear
echo "--- Monitor Bot Management Menu ---"
PS3="Please select an option: "
options=("Install or Update Bot" "Uninstall Bot" "Exit")
select opt in "${options[@]}"
do
    case $opt in
        "Install or Update Bot")
            install_dependencies
            install_flow
            break
            ;;
        "Uninstall Bot")
            uninstall_bot
            break
            ;;
        "Exit")
            break
            ;;
        *) echo "Invalid option $REPLY";;
    esac
done