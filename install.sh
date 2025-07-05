#!/bin/bash

# --- تنظیمات ---
# ✅ آدرس URL خامی که از گیت‌هاب کپی کردید را اینجا قرار دهید
BOT_BINARY_URL="https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/monitor_bot.bin"

# مسیر نصب ربات
INSTALL_DIR="/opt/monitor_bot"
# نام فایل اجرایی کامپایل شده
BOT_BINARY="monitor_bot.bin"
# نام فایل کانفیگ
CONFIG_FILE="config.json"
# نام سرویس systemd
SERVICE_NAME="monitor_bot.service"

# تابع برای پرسیدن سوالات و ساخت فایل کانفیگ
create_config() {
    echo "--- شروع پیکربندی ربات ---"
    read -p "لطفاً توکن تلگرام (TELEGRAM_TOKEN) را وارد کنید: " TELEGRAM_TOKEN
    read -p "لطفاً آیدی عددی چت (CHAT_ID) را وارد کنید: " CHAT_ID
    
    echo "در حال ساخت فایل $CONFIG_FILE..."
    cat << EOF > "$INSTALL_DIR/$CONFIG_FILE"
{
  "telegram_token": "$TELEGRAM_TOKEN",
  "chat_id": "$CHAT_ID",
  "servers_file": "servers.tolm",
  "iran_servers_file": "iran_servers.json",
  "cron_links_file": "cron_links.json",
  "update_interval_seconds": 5
}
EOF
    echo "✅ فایل کانفیگ با موفقیت ساخته شد."
}

# تابع برای ساخت و نصب سرویس systemd
create_service() {
    echo "در حال ساخت فایل سرویس systemd..."
    cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=Telegram Monitor Bot Service
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
    systemctl start $SERVICE_NAME
    
    echo "✅ سرویس با موفقیت نصب و اجرا شد."
    echo "برای بررسی وضعیت، از دستور زیر استفاده کنید:"
    echo "systemctl status $SERVICE_NAME"
}

# تابع برای حذف کامل ربات
uninstall() {
    echo "در حال توقف و حذف سرویس..."
    systemctl stop $SERVICE_NAME
    systemctl disable $SERVICE_NAME
    rm -f "/etc/systemd/system/$SERVICE_NAME"
    systemctl daemon-reload
    
    echo "در حال حذف فایل‌های ربات..."
    rm -rf "$INSTALL_DIR"
    
    echo "✅ ربات و تمام فایل‌های مرتبط با آن با موفقیت حذف شدند."
}

# مدیریت آرگومان‌های ورودی
if [ "$1" == "install" ]; then
    echo "شروع فرآیند نصب..."
    
    # ✅ دانلود فایل اجرایی ربات از گیت‌هاب
    echo "Downloading bot binary from GitHub..."
    curl -L -o "$BOT_BINARY" "$BOT_BINARY_URL"
    if [ $? -ne 0 ]; then
        echo "❌ خطا در دانلود فایل ربات. لطفاً از صحیح بودن URL مطمئن شوید."
        exit 1
    fi
    
    # ایجاد پوشه نصب
    mkdir -p "$INSTALL_DIR"
    # کپی کردن فایل اجرایی به محل نصب
    mv "./$BOT_BINARY" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/$BOT_BINARY"
    
    # اجرای توابع
    create_config
    create_service
    echo "🎉 نصب کامل شد!"

elif [ "$1" == "uninstall" ]; then
    read -p "آیا از حذف کامل ربات و تمام فایل‌های آن مطمئن هستید؟ (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        uninstall
    else
        echo "عملیات لغو شد."
    fi
else
    echo "دستور نامعتبر است."
    echo "نحوه استفاده: sudo bash -s install [یا uninstall]"
fi