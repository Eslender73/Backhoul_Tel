#!/bin/bash

# --- تنظیمات ---
BOT_BINARY_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/monitor_bot.bin"
REQUIREMENTS_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/requirements.txt"

INSTALL_DIR="/opt/monitor_bot"
BOT_BINARY="monitor_bot.bin"
CONFIG_FILE="$INSTALL_DIR/config.json"
SERVICE_NAME="monitor_bot.service"

# تابع برای نصب نیازمندی‌ها
install_dependencies() {
    echo "--- مرحله ۱: نصب نیازمندی‌های سیستم ---"
    # jq برای خواندن فایل جیسون اضافه شد
    apt update && apt install -y curl python3-pip jq build-essential
    if [ $? -ne 0 ]; then echo "❌ خطا در نصب نیازمندی‌های سیستم."; exit 1; fi

    echo "در حال دانلود requirements.txt..."
    curl -L -o "requirements.txt" "$REQUIREMENTS_URL"
    if [ $? -ne 0 ]; then echo "❌ خطا در دانلود فایل نیازمندی‌ها."; exit 1; fi

    echo "در حال نصب کتابخانه‌های پایتون..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then echo "❌ خطا در نصب کتابخانه‌های پایتون."; exit 1; fi

    rm requirements.txt
    echo "✅ نیازمندی‌ها با موفقیت نصب شدند."
}

# تابع برای پرسیدن سوالات و ساخت فایل کانفیگ
create_config() {
    echo "--- مرحله ۳: پیکربندی ربات ---"

    # مقادیر پیش‌فرض
    DEFAULT_TOKEN=""
    DEFAULT_CHAT_ID=""
    DEFAULT_INTERVAL=5

    # اگر فایل کانفیگ از قبل وجود داشت، مقادیر آن را به عنوان پیش‌فرض می‌خوانیم
    if [ -f "$CONFIG_FILE" ]; then
        echo "⚠️ فایل کانفیگ قبلی یافت شد. مقادیر فعلی به عنوان پیش‌فرض نمایش داده می‌شوند."
        DEFAULT_TOKEN=$(jq -r '.telegram_token' "$CONFIG_FILE")
        DEFAULT_CHAT_ID=$(jq -r '.chat_id' "$CONFIG_FILE")
        DEFAULT_INTERVAL=$(jq -r '.update_interval_seconds' "$CONFIG_FILE")
    fi

    # پرسیدن اطلاعات از کاربر با نمایش مقدار پیش‌فرض
    read -p "توکن تلگرام را وارد کنید [$DEFAULT_TOKEN]: " input_token < /dev/tty
    # اگر کاربر چیزی وارد نکرد (فقط اینتر زد)، از مقدار پیش‌فرض استفاده کن
    TELEGRAM_TOKEN=${input_token:-$DEFAULT_TOKEN}

    read -p "آیدی عددی چت را وارد کنید [$DEFAULT_CHAT_ID]: " input_chat_id < /dev/tty
    CHAT_ID=${input_chat_id:-$DEFAULT_CHAT_ID}
    
    read -p "فاصله زمانی آپدیت وضعیت (ثانیه) [$DEFAULT_INTERVAL]: " input_interval < /dev/tty
    UPDATE_INTERVAL=${input_interval:-$DEFAULT_INTERVAL}

    echo "در حال ساخت فایل $CONFIG_FILE..."
    # استفاده از Heredoc برای ساخت امن فایل JSON
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

    # ساخت فایل‌های داده خالی در صورت عدم وجود
    touch "$INSTALL_DIR/servers.tolm"
    touch "$INSTALL_DIR/iran_servers.json"
    touch "$INSTALL_DIR/cron_links.json"

    echo "✅ فایل کانفیگ و داده‌ها با موفقیت ساخته شدند."
}

# تابع برای ساخت و نصب سرویس systemd
create_service() {
    echo "--- مرحله ۴: ساخت و نصب سرویس systemd ---"
    cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=Telegram Monitor Bot
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/$BOT_BINARY
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

    echo "در حال فعال‌سازی و اجرای سرویس..."
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    systemctl restart $SERVICE_NAME
    
    echo "✅ سرویس با موفقیت نصب و اجرا شد."
    echo "برای بررسی وضعیت، از دستور زیر استفاده کنید:"
    echo "systemctl status $SERVICE_NAME"
}

# تابع برای عملیات نصب کامل
install_flow() {
    echo "🚀 شروع فرآیند نصب/بروزرسانی..."
    
    echo "--- مرحله ۲: دانلود فایل اجرایی ربات ---"
    # ابتدا سرویس قبلی را (اگر وجود دارد) متوقف می‌کنیم تا فایل قابل جایگزینی باشد
    systemctl stop $SERVICE_NAME &>/dev/null
    
    curl -L -o "$BOT_BINARY" "$BOT_BINARY_URL"
    if [ $? -ne 0 ]; then echo "❌ خطا در دانلود فایل ربات."; exit 1; fi
    
    mkdir -p "$INSTALL_DIR"
    mv "./$BOT_BINARY" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/$BOT_BINARY"
    echo "✅ فایل ربات با موفقیت دانلود و منتقل شد."
    
    create_config
    create_service
    echo "🎉 نصب/بروزرسانی کامل شد!"
}


# تابع برای حذف کامل ربات
uninstall_bot() {
    read -p "آیا از حذف کامل ربات و تمام فایل‌های آن مطمئن هستید؟ (y/n) " -n 1 -r < /dev/tty
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "در حال توقف و حذف سرویس..."
        systemctl stop $SERVICE_NAME
        systemctl disable $SERVICE_NAME
        rm -f "/etc/systemd/system/$SERVICE_NAME"
        systemctl daemon-reload
        
        echo "در حال حذف فایل‌های نصب شده..."
        rm -rf "$INSTALL_DIR"
        
        echo "🗑️ حذف کامل شد."
    else
        echo "عملیات لغو شد."
    fi
}

# --- ✅ منوی اصلی تعاملی ---
clear
echo "--- منوی مدیریت ربات مانیتورینگ ---"
PS3="لطفاً گزینه مورد نظر را انتخاب کنید: "
options=("نصب یا بروزرسانی ربات" "حذف ربات" "خروج")
select opt in "${options[@]}"
do
    case $opt in
        "نصب یا بروزرسانی ربات")
            install_dependencies
            install_flow
            break
            ;;
        "حذف ربات")
            uninstall_bot
            break
            ;;
        "خروج")
            break
            ;;
        *) echo "گزینه نامعتبر $REPLY";;
    esac
done