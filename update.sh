#!/bin/bash

# اسکریپت به‌روزرسانی ربات

# به مسیر دایرکتوری ربات بروید
cd "$(dirname "$0")" || exit

echo "در حال دریافت آخرین تغییرات از گیت..."
# دریافت آخرین تغییرات از شاخه اصلی (main یا master)
git pull origin main

echo "در حال نصب یا به‌روزرسانی کتابخانه‌های مورد نیاز..."
# نصب وابستگی‌ها از فایل requirements.txt
pip install -r requirements.txt

echo "ریبوت کردن سرویس ربات..."
# سرویس ربات را ری‌استارت کنید (نام سرویس خود را جایگزین کنید)
systemctl restart monitor_bot.service

echo "ربات با موفقیت به‌روزرسانی و ری‌استارت شد."