#!/bin/bash
# اسکریپت آپدیت امن و پیشنهادی با دانلود سورس کد

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] - $1"
}

# آدرس فایل سورس .py
BOT_SOURCE_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/monitor_bot.py"
UPDATE_FILE_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/update.sh"
REQUIREMENTS_URL="https://raw.githubusercontent.com/Eslender73/Backhoul_Tel/main/requirements.txt"

log "=== شروع فرآیند به‌روزرسانی ربات ==="
cd "$(dirname "$0")" || exit

log "۱. پاک‌سازی فایل‌های کامپایل‌شده قدیمی..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -exec rm -r {} +

log "۲. در حال دانلود فایل‌های جدید..."
curl -sSL -o requirements.txt "$REQUIREMENTS_URL"
curl -sSL -o update.sh "$UPDATE_FILE_URL" && chmod +x update.sh
if ! curl -sSL -o monitor_bot.py "$BOT_SOURCE_URL"; then
    log "❌ دانلود فایل سورس ربات ناموفق بود! فرآیند متوقف شد."
    exit 1
fi
log "✅ فایل‌ها با موفقیت دانلود شدند."

log "۳. در حال نصب کتابخانه‌ها..."
pip install -r requirements.txt

log "۴. در حال ری‌استارت سرویس ربات..."
systemctl restart monitor_bot.service
log "✅ فرآیند به‌روزرسانی با موفقیت به پایان رسید."