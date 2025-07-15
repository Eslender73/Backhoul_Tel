#!/bin/bash
# اسکریپت آپدیت که تمام خروجی را در یک فایل لاگ ذخیره می‌کند

# مسیر فایل لاگ در کنار خود اسکریپت
LOG_FILE="$(dirname "$0")/update.log"

# پاک کردن لاگ قدیمی
> "$LOG_FILE"
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] - $1" | tee -a "$LOG_FILE"
}

# آدرس فایل سورس .py
BOT_SOURCE_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/monitor_bot.pyc"
UPDATE_FILE_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/update.sh"
REQUIREMENTS_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/requirements.txt"

log "🚀 شروع فرآیند به‌روزرسانی ربات..."
cd "$(dirname "$0")" || exit

log "📁 بررسی یا ساخت مسیر نصب ربات"
sudo mkdir -p /opt/monitor_bot
sudo chown "$USER":"$USER" /opt/monitor_bot

log "🧹 ۱. پاک‌سازی فایل‌های کامپایل‌شده قدیمی..."
find . -type f -name "*.pyc" -delete >> "$LOG_FILE" 2>&1

log "⬇️ ۲. در حال دانلود فایل‌های جدید..."
curl -sSL -o requirements.txt "$REQUIREMENTS_URL"


if ! curl -sSL -o /opt/monitor_bot/monitor_bot.pyc "$BOT_SOURCE_URL"; then
    log "❌ دانلود فایل سورس ربات ناموفق بود! فرآیند متوقف شد."
    exit 1
fi
log "✅ فایل‌ها با موفقیت دانلود شدند."

log "📦 ۳. در حال نصب کتابخانه‌های موردنیاز..."
pip install -r requirements.txt 

log "🔁 ۴. در حال ری‌استارت سرویس ربات..."
sudo systemctl restart monitor_bot.service

log "🎉 به‌روزرسانی ربات با موفقیت انجام شد."
