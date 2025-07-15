#!/usr/bin/env python3
import subprocess
import time
import sys
import configparser
import os # os import شد
from datetime import datetime

if len(sys.argv) < 2:
    print("Usage: python3 guardian.py <port>")
    sys.exit(1)

PORT = sys.argv[1]
SERVICE_NAME_KHAREJ = f"backhaul-kharej{PORT}.service"
SERVICE_NAME_IRAN = f"backhaul-iran{PORT}.service"
LOG_FILE = f"/var/log/guardian_{PORT}.log"

# --- ✨ تغییر اصلی اینجاست ---
# مسیر اسکریپت را پیدا کرده و مسیر فایل کانفیگ را بر اساس آن می‌سازد
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, f"iran_creds_{PORT}.conf")
# ---

def write_log(message):
    now = datetime.now().strftime('%d-%m %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{now}] {message}\n")

# ... (بقیه توابع اسکریپت guardian.py بدون تغییر باقی می‌مانند) ...

def get_last_log_line(service_name, ssh_target=None):
    cmd = f"journalctl -u {service_name} -n 1 --no-pager"
    if ssh_target:
        cmd = f"sshpass -p '{ssh_target['password']}' ssh -p {ssh_target['port']} -o StrictHostKeyChecking=no {ssh_target['user']}@{ssh_target['ip']} '{cmd}'"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return f"Timeout getting log for {service_name}."

def restart_service(service_name, ssh_target=None):
    log_prefix = "سرویس خارج" if not ssh_target else "سرویس ایران"
    write_log(f"ارسال درخواست ریست برای {log_prefix}...")
    if ssh_target:
        cmd = f"sshpass -p '{ssh_target['password']}' ssh -p {ssh_target['port']} -o StrictHostKeyChecking=no {ssh_target['user']}@{ssh_target['ip']} 'systemctl restart {service_name}'"
    else:
        cmd = f"systemctl restart {service_name}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        write_log(f"✅ تایید ریست برای {log_prefix} دریافت شد.")
    else:
        write_log(f"❌ خطا در ارسال درخواست ریست برای {log_prefix}. خطا: {result.stderr}")


if __name__ == "__main__":
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'iran' not in config:
        write_log(f"Config file {CONFIG_FILE} not found or invalid.")
        sys.exit(1)
        
    iran_server_creds = config['iran']
    write_log("Guardian script started.")
    
    while True:
        try:
            last_kharej_log = get_last_log_line(SERVICE_NAME_KHAREJ)
            if "[ERROR]" in last_kharej_log:
                write_log("تشخیص ارور در سرویس خارج.")
                try:
                    error_details = last_kharej_log.split('[ERROR]', 1)[1].strip()
                    write_log(f"نوع ارور: {error_details}")
                except IndexError:
                    write_log(f"لاگ کامل ارور: '{last_kharej_log}'")
                
                restart_service(SERVICE_NAME_KHAREJ)
                restart_service(SERVICE_NAME_IRAN, ssh_target=iran_server_creds)
                
                write_log("انتظار 30 ثانیه برای برقراری ارتباط...")
                time.sleep(30)
                
                post_restart_kharej_log = get_last_log_line(SERVICE_NAME_KHAREJ)
                write_log(f"آخرین لاگ سرویس خارج: '{post_restart_kharej_log}'")

                post_restart_iran_log = get_last_log_line(SERVICE_NAME_IRAN, ssh_target=iran_server_creds)
                write_log(f"آخرین لاگ سرویس ایران: '{post_restart_iran_log}'")
                
                write_log("عملیات ری‌استارت تمام شد. ادامه مانیتورینگ...")
        except Exception as e:
            write_log(f"An error occurred in the guardian script: {e}")
        time.sleep(10)