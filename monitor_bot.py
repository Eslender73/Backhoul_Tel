import os
import time
import requests
import paramiko
from datetime import datetime
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
import re
import json
CONFIG_FILE = "config.json"
# --- ثابت‌ها و توکن ---
IS_LICENSED = False
g_license_info = "⚠️ لایسنس یافت نشد یا نامعتبر است."
def load_config():
    """تنظیمات را از فایل config.json بارگذاری می‌کند."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ خطای حیاتی: فایل کانفیگ '{CONFIG_FILE}' یافت نشد.")
        sys.exit(1) # در صورت نبود فایل، برنامه متوقف می‌شود
    except json.JSONDecodeError:
        print(f"❌ خطای حیاتی: فایل '{CONFIG_FILE}' دارای خطای ساختاری است.")
        sys.exit(1)

# بارگذاری تنظیمات در هنگام شروع
config = load_config()

# تخصیص متغیرها از کانفیگ خوانده شده
TELEGRAM_TOKEN = config.get("telegram_token")
CHAT_ID = config.get("chat_id")
SERVERS_FILE = config.get("servers_file", "servers.tolm")
IRAN_SERVERS_FILE = config.get("iran_servers_file", "iran_servers.json")
CRON_LINKS_FILE = config.get("cron_links_file", "cron_links.json")
UPDATE_INTERVAL_SECONDS = config.get("update_interval_seconds", 5)

# بررسی وجود توکن که برای اجرای ربات ضروری است
if not TELEGRAM_TOKEN:
    print("❌ خطای حیاتی: 'telegram_token' در فایل config.json یافت نشد.")
    sys.exit(1)

# --- حالت‌های گفتگو ---
(
    ADD_SERVER, UPDATE_SERVER_SELECT, UPDATE_SERVER_INFO, 
    ADD_IRAN_SERVER_NAME, ADD_IRAN_SERVER_CREDS, 
    SMART_RESET_CHOOSE_IRAN,
    ADD_CRON_CHOOSE_PORT, ADD_CRON_CHOOSE_IRAN, ADD_CRON_FINALIZE,
    ADD_TUNNEL_GET_PORT, ADD_TUNNEL_GET_IRAN_SERVER,
    ADD_TUNNEL_ADVANCED_SETTINGS, 
    ADD_TUNNEL_GET_FORWARD_PORTS,
    ADD_TUNNEL_GET_CHANNEL_SIZE, ADD_TUNNEL_GET_CONN_POOL, ADD_TUNNEL_GET_MUX_CON,
    ADD_TUNNEL_GET_HEARTBEAT, ADD_TUNNEL_GET_MUX_FRAMESIZE,
    ADD_TUNNEL_GET_MUX_RECIEVEBUFFER, ADD_TUNNEL_GET_MUX_STREAMBUFFER,
    ADD_TUNNEL_GET_NODELAY, ADD_TUNNEL_GET_SNIFFER, ADD_TUNNEL_GET_WEB_PORT,
    ADD_TUNNEL_GET_PROXY_PROTOCOL,ADD_TUNNEL_GET_TRANSPORT, ADD_TUNNEL_GET_TOKEN
) = range(26)



# --- قالب اسکریپت‌ها ---
GUARDIAN_SCRIPT_CONTENT = """
#!/usr/bin/env python3
import subprocess
import time
import sys
import configparser
from datetime import datetime

if len(sys.argv) < 2:
    print("Usage: python3 guardian.py <port>")
    sys.exit(1)

PORT = sys.argv[1]
SERVICE_NAME_KHAREJ = f"backhaul-kharej{PORT}.service"
SERVICE_NAME_IRAN = f"backhaul-iran{PORT}.service"
LOG_FILE = f"/var/log/guardian_{PORT}.log"
CONFIG_FILE = f"/root/iran_creds_{PORT}.conf"

def write_log(message):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{now}] {message}\\n")

def get_last_log_line():
    cmd = f"journalctl -u {SERVICE_NAME_KHAREJ} -n 1 --no-pager"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

def restart_service(service_name, ssh_target=None):
    if ssh_target:
        cmd = f"sshpass -p '{ssh_target['password']}' ssh -p {ssh_target['port']} -o StrictHostKeyChecking=no {ssh_target['user']}@{ssh_target['ip']} 'systemctl restart {service_name}'"
        write_log(f"Attempting to restart {service_name} on Iran server {ssh_target['ip']}...")
    else:
        cmd = f"systemctl restart {service_name}"
        write_log(f"Attempting to restart local service {service_name}...")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        write_log(f"Restart command for {service_name} sent successfully.")
    else:
        write_log(f"FAILED to send restart for {service_name}. Error: {result.stderr}")

if __name__ == "__main__":
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if 'iran' not in config:
        write_log(f"Config file {CONFIG_FILE} not found or invalid.")
        sys.exit(1)
        
    iran_server = config['iran']
    write_log("Guardian script started.")
    
    while True:
        try:
            last_line = get_last_log_line()
            if "[ERROR]" in last_line:
                write_log(f"ERROR detected in {SERVICE_NAME_KHAREJ}. Log line: '{last_line}'")
                restart_service(SERVICE_NAME_KHAREJ)
                restart_service(SERVICE_NAME_IRAN, ssh_target=iran_server)
                write_log("Both services restarted. Waiting for 60 seconds to re-check...")
                time.sleep(60)
                final_status_line = get_last_log_line()
                write_log(f"Post-restart check. Last log line: '{final_status_line}'")
        except Exception as e:
            write_log(f"An error occurred in the guardian script: {e}")
        time.sleep(10)
"""

SYSTEMD_SERVICE_TEMPLATE = """
[Unit]
Description=Guardian Script for Backhaul Port %i
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /root/guardian.py %i
Restart=always
User=root
Group=root

[Install]
WantedBy=multi-user.target
"""

# --- کلاس SSH ---
class PersistentSSHClient:
    def __init__(self, server):
        self.server = server
        self.client = None
        self.cpu_cores = "?"
        self.ram_total_gb = "?"
        self.last_cpu = 0
        self.last_ram = 0
        self.last_success = 0
        self.last_error = None
        self.connect()

    def connect(self):
        try:
            if self.client: self.client.close()
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                self.server["ip"], port=self.server["port"], username=self.server["user"],
                password=self.server["password"], timeout=5
            )
            self.client.get_transport().set_keepalive(30)
            self.cpu_cores = self.get_cpu_cores()
            self.ram_total_gb = self.get_total_ram()
        except Exception as e:
            self.client = None
            print(f"❌ اتصال به {self.server['name']} قطع شد: {e}")

    def get_cpu_cores(self):
        try:
            stdin, stdout, stderr = self.client.exec_command("nproc")
            return stdout.read().decode().strip()
        except: return "?"

    def get_total_ram(self):
        try:
            stdin, stdout, stderr = self.client.exec_command("free -g | grep Mem")
            return stdout.read().decode().split()[1]
        except: return "?"

    def get_stats(self):
        now = time.time()
        if not self.client or not self.client.get_transport().is_active():
            if now - self.last_success >= 10:
                return self.last_cpu, self.last_ram, "Failed to connect"
            else: self.connect()
        try:
            stdin, stdout, stderr = self.client.exec_command("top -bn1 | grep '%Cpu' | awk '{print 100 - $8}'")
            cpu_usage = float(stdout.read().decode().strip())
            stdin, stdout, stderr = self.client.exec_command("free -m")
            mem_line = stdout.read().decode().splitlines()[1].split()
            ram_usage = (float(mem_line[2]) / float(mem_line[1])) * 100
            self.last_cpu, self.last_ram, self.last_success, self.last_error = cpu_usage, ram_usage, now, None
            return cpu_usage, ram_usage, None
        except Exception as e:
            self.client = None
            if now - self.last_success >= 10:
                return self.last_cpu, self.last_ram, "Failed to connect"
            else: return self.last_cpu, self.last_ram, None


servers_cache = []
def parse_servers():
    global servers_cache
    servers = []
    if not os.path.exists(SERVERS_FILE): return []
    with open(SERVERS_FILE, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    conn, password, name = line.split(";")
                    user, ip_port = conn.split("@")
                    ip, port_str = ip_port.split(":")
                    servers.append({"ip": ip, "port": int(port_str), "user": user, "password": password, "name": name})
                except Exception as e: print(f"❌ خطا در خواندن خط: {line} -> {e}")
    servers_cache = servers
    return servers

def load_iran_servers():
    if not os.path.exists(IRAN_SERVERS_FILE): return {}
    try:
        with open(IRAN_SERVERS_FILE, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return {}

def save_iran_servers(servers):
    with open(IRAN_SERVERS_FILE, 'w') as f: json.dump(servers, f, indent=4)

def load_tunnel_links():
    """ارتباطات ذخیره شده بین پورت سرور خارج و نام سرور ایران را می‌خواند."""
    if not os.path.exists(CRON_LINKS_FILE): # فرض می‌کنیم از همان فایل کرون جاب استفاده کنیم
        return {}
    try:
        with open(CRON_LINKS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_tunnel_links(links):

    with open(CRON_LINKS_FILE, 'w') as f: # فرض می‌کنیم از همان فایل کرون جاب استفاده کنیم
        json.dump(links, f, indent=4)

def load_cron_links():
    """ارتباطات ذخیره شده بین سرور خارج و ایران را از فایل می‌خواند."""
    if not os.path.exists(CRON_LINKS_FILE):
        return {}
    try:
        with open(CRON_LINKS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def generate_toml_configs(info: dict) -> tuple:
    port = info['port']
    iran_ip = info['iran_ip']
    token = info['token']
    forward_ports = info.get('forward_ports', [])
    subnet = f"10.10.{int(port) % 255}.0/24"
    kharej_config = f"""[client]
remote_addr = "{iran_ip}:{port}"
transport = "{info.get('transport')}"
token = "{token}"
connection_pool = {info.get('connection_pool')}
aggressive_pool = {'true' if info.get('aggressive_pool') else 'false'}
keepalive_period = {info.get('keepalive_period')}
nodelay = {'true' if info.get('nodelay') else 'false'}
retry_interval = {info.get('retry_interval')}
dial_timeout = {info.get('dial_timeout')}
mux_version = {info.get('mux_version')}
mux_framesize = {info.get('mux_framesize')}
mux_recievebuffer = {info.get('mux_recievebuffer')}
mux_streambuffer = {info.get('mux_streambuffer')}
sniffer = {'true' if info.get('sniffer') else 'false'}
web_port = {info.get('web_port')}
sniffer_log = "{info.get('sniffer_log')}"
log_level = "{info.get('log_level')}"
tun_name = "{info.get('tun_name')}"
tun_subnet = "{subnet}"
mtu = {info.get('mtu')}
"""
    formatted_ports = ", ".join([f'"{p.strip()}"' for p in forward_ports])
    iran_config = f"""[server]
bind_addr = ":{port}"
transport = "{info.get('transport')}"
accept_udp = {'true' if info.get('accept_udp') else 'false'}
token = "{token}"
keepalive_period = {info.get('keepalive_period')}
nodelay = {'true' if info.get('nodelay') else 'false'}
channel_size = {info.get('channel_size')}
heartbeat = {info.get('heartbeat')}
mux_con = {info.get('mux_con')}
mux_version = {info.get('mux_version')}
mux_framesize = {info.get('mux_framesize')}
mux_recievebuffer = {info.get('mux_recievebuffer')}
mux_streambuffer = {info.get('mux_streambuffer')}
sniffer = {'true' if info.get('sniffer') else 'false'}
web_port = {info.get('web_port')}
sniffer_log = "{info.get('sniffer_log')}"
log_level = "{info.get('log_level')}"
ip_limit = {'true' if info.get('ip_limit') else 'false'}
proxy_protocol = {'true' if info.get('proxy_protocol') else 'false'}
tun_name = "{info.get('tun_name')}"
tun_subnet = "{subnet}"
mtu = {info.get('mtu')}
ports = [
    {formatted_ports}
]
"""
    return kharej_config, iran_config

def generate_service_files(port):
    """محتوای فایل‌های systemd را برای ایران و خارج تولید می‌کند."""
    kharej_service = f"""[Unit]
Description=Backhaul Kharej Port {port}
After=network.target
[Service]
Type=simple
ExecStart=/root/backhaul-core/backhaul_premium -c /root/backhaul-core/kharej{port}.toml
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
"""
    iran_service = f"""[Unit]
Description=Backhaul Iran Port {port}
After=network.target
[Service]
Type=simple
ExecStart=/root/backhaul-core/backhaul_premium -c /root/backhaul-core/iran{port}.toml
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
"""
    return kharej_service, iran_service

def save_cron_links(links):
    """ارتباطات کرون‌جاب را در فایل ذخیره می‌کند."""
    with open(CRON_LINKS_FILE, 'w') as f:
        json.dump(links, f, indent=4)

def build_chart(percent):
    if percent is None: percent = 0
    filled = int(percent / 10)
    return "▮" * filled + "▯" * (10 - filled)

def translate_cron_schedule(schedule_str):
    """عبارت کرون جاب را به متن فارسی روان ترجمه می‌کند."""
    parts = schedule_str.split()
    if len(parts) != 5:
        return schedule_str

    minute, hour, _, _, _ = parts

    if minute == "0" and hour == "*":
        return "هر 1 ساعت"
        
    if minute.startswith("*/"):
        return f"هر {minute.split('/')[1]} دقیقه"
    
    if minute == "0" and hour.startswith("*/"):
        return f"هر {hour.split('/')[1]} ساعت"
        
    if minute == "0" and hour == "0":
        return "هر روز نیمه‌شب"
    
    return schedule_str # اگر هیچکدام نبود، خود عبارت را برگردان
    
def build_message(clients):
    message = "گزارش زنده وضعیت سرورها:\n\n"
    for cli in clients:
        cpu, ram, error = cli.get_stats()
        message += f"🖥️ <b>{cli.server['name']} ({cli.server['ip']})</b>\n"
        message += f"🧩 <i>{cli.cpu_cores} Core - {cli.ram_total_gb}GB RAM</i>\n"
        if error: message += f"❌ <i>{error}</i>\n\n"
        else:
            message += f"⚙️ CPU: [{build_chart(cpu)}] {cpu:.0f}%\n"
            message += f"🧠 RAM: [{build_chart(ram)}] {ram:.0f}%\n\n"
    return message
# --- توابع جدید برای دریافت اطلاعات کشور ---
def get_country_info(ip_address: str, context: ContextTypes.DEFAULT_TYPE) -> tuple:
    """با استفاده از IP، کد کشور و اموجی پرچم را برمی‌گرداند."""
    # از یک دیکشنری برای کش کردن نتایج استفاده می‌کنیم تا درخواست‌های تکراری ارسال نشود
    if 'country_cache' not in context.bot_data:
        context.bot_data['country_cache'] = {}
    
    if ip_address in context.bot_data['country_cache']:
        return context.bot_data['country_cache'][ip_address]

    try:
        # استفاده از سرویس وب رایگان برای یافتن کشور
        response = requests.get(f"http://ip-api.com/json/{ip_address}?fields=country,countryCode", timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get('countryCode'):
            country_code = data['countryCode']
            country_name = data.get('country', country_code)
            # تبدیل کد کشور دو حرفی به اموجی پرچم
            flag_emoji = "".join(chr(ord(c) + 127397) for c in country_code.upper())
            
            result = (country_name, flag_emoji)
            context.bot_data['country_cache'][ip_address] = result # ذخیره در کش
            return result
            
    except Exception as e:
        print(f"Could not get country for IP {ip_address}: {e}")
        
    return ("نامشخص", "❓") 

# --- توابع مربوط به بررسی لایسنس ---

def get_remote_license(url: str) -> dict | None:
    """اطلاعات لایسنس را از یک URL راه دور دریافت می‌کند."""
    try:
        print(f"[License Check] Fetching license from {url}...")
        # استفاده از requests که در بالای فایل import شده است
        response = requests.get(url, timeout=10)
        response.raise_for_status() # برای خطاهای HTTP
        return response.json()
    except Exception as e:
        print(f"[License Check] FAILED to fetch or parse remote license: {e}")
        return None

def check_ip_license(allowed_ips: list) -> bool:
    """IP عمومی فعلی را با لیست IP های مجاز مقایسه می‌کند."""
    try:
        response = requests.get("https://api.ipify.org", timeout=5)
        response.raise_for_status()
        current_ip = response.text.strip()
        print(f"[License Check] Current IP: {current_ip}, Allowed IPs: {allowed_ips}")
        # چک می‌کند آیا IP فعلی در لیست مجاز وجود دارد
        return current_ip in allowed_ips
    except Exception as e:
        print(f"[License Check] Failed to get current IP: {e}")
        return False

def check_expiry_license(expiry_date_str: str) -> bool:
    """بررسی می‌کند که آیا تاریخ فعلی از تاریخ انقضا گذشته است یا نه."""
    try:
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        current_date = datetime.now()
        print(f"[License Check] Current Date: {current_date}, Expiry Date: {expiry_date}")
        return current_date < expiry_date
    except Exception as e:
        print(f"[License Check] Failed to parse expiry date: {e}")
        return False
    """بررسی می‌کند که آیا تاریخ فعلی از تاریخ انقضا گذشته است یا نه."""
    try:
        # تاریخ انقضا را از فرمت رشته‌ای به شیء تاریخ تبدیل می‌کنیم
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
        current_date = datetime.now()
        print(f"[License Check] Current Date: {current_date}, Expiry Date: {expiry_date}")
        return current_date < expiry_date
    except Exception as e:
        print(f"[License Check] Failed to parse expiry date: {e}")
        return False

COUNTRY_NAMES_FA = {
    # کشورهای اروپایی
    "Germany": "آلمان",
    "France": "فرانسه",
    "Netherlands": "هلند",
    "The Netherlands": "هلند",
    "United Kingdom": "انگلستان",
    "Finland": "فنلاند",
    "Sweden": "سوئد",
    "Italy": "ایتالیا",
    "Spain": "اسپانیا",
    "Russia": "روسیه",
    "Poland": "لهستان",
    "Switzerland": "سوئیس",
    "Ireland": "ایرلند",
    "Belgium": "بلژیک",
    "Austria": "اتریش",
    "Norway": "نروژ",
    "Denmark": "دانمارک",
    "Czechia": "جمهوری چک",
    "Romania": "رومانی",
    "Lithuania": "لیتوانی",
    
    # کشورهای آمریکای شمالی
    "United States": "ایالات متحده",
    "Canada": "کانادا",
    
    # کشورهای آسیایی
    "Turkey": "ترکیه",
    "United Arab Emirates": "امارات",
    "Singapore": "سنگاپور",
    "Japan": "ژاپن",
    "South Korea": "کره جنوبی",
    "India": "هند",
    "Hong Kong": "هنگ کنگ",
    "Malaysia": "مالزی",
    "Armenia": "ارمنستان",
    
    # اقیانوسیه
    "Australia": "استرالیا",
    
    # سایر
    "South Africa": "آفریقای جنوبی",
}

def generate_service_files(port):
    """محتوای فایل‌های systemd را برای ایران و خارج تولید می‌کند."""
    kharej_service = f"""[Unit]
Description=Backhaul Kharej Port {port}
After=network.target
[Service]
Type=simple
ExecStart=/root/backhaul-core/backhaul_premium -c /root/backhaul-core/kharej{port}.toml
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
"""
    iran_service = f"""[Unit]
Description=Backhaul Iran Port {port}
After=network.target
[Service]
Type=simple
ExecStart=/root/backhaul-core/backhaul_premium -c /root/backhaul-core/iran{port}.toml
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
"""
    return kharej_service, iran_service

#1
async def add_tunnel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱ افزودن تونل: درخواست انتخاب سرور ایران."""
    query = update.callback_query
    await query.answer()

    _, server_idx_str = query.data.split("|")
    # اطلاعات اولیه را در حافظه ربات ذخیره می‌کنیم
    context.user_data['tunnel_info'] = {
        **DEFAULT_TUNNEL_PARAMS, 
        'server_idx': server_idx_str
    }
    
    iran_servers = load_iran_servers()
    if not iran_servers:
        await reply_or_edit(update, context, "هیچ سرور ایرانی برای انتخاب وجود ندارد!", [])
        return ConversationHandler.END
        
    text = "لطفاً سرور ایران متناظر با تونل جدید را انتخاب کنید:"
    keyboard = [[InlineKeyboardButton(name, callback_data=f"add_tunnel_iran|{name}")] for name in iran_servers.keys()]
    keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")])
    
    await reply_or_edit(update, context, text, keyboard)
    
    # ربات را به حالت "منتظر دریافت سرور ایران" می‌بریم
    return ADD_TUNNEL_GET_IRAN_SERVER

#2
async def add_tunnel_get_iran_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۲: دریافت سرور ایران و درخواست نوع Transport."""
    query = update.callback_query
    await query.answer()
    
    iran_server_name = query.data.split("|")[1]
    # اطلاعات انتخاب شده را در حافظه ربات ذخیره می‌کنیم
    context.user_data['tunnel_info']['iran_server'] = iran_server_name
    context.user_data['tunnel_info']['iran_ip'] = load_iran_servers()[iran_server_name]['ip']
    
    text = "نوع اتصال (Transport) را انتخاب کنید:"
    transport_options = ["tcp", "tcpmux", "utcpmux", "ws", "wsmux", "uwsmux", "udp"]
    keyboard = [
        [InlineKeyboardButton(opt, callback_data=f"add_tunnel_transport|{opt}") for opt in transport_options[:3]],
        [InlineKeyboardButton(opt, callback_data=f"add_tunnel_transport|{opt}") for opt in transport_options[3:]],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]

    await reply_or_edit(update, context, text, keyboard)
    
    # ربات را به حالت "منتظر دریافت نوع Transport" می‌بریم
    return ADD_TUNNEL_GET_TRANSPORT

#3
async def add_tunnel_get_transport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۳: دریافت نوع Transport و درخواست شماره پورت تونل."""
    query = update.callback_query
    await query.answer()
    
    transport_type = query.data.split("|")[1]
    context.user_data['tunnel_info']['transport'] = transport_type
    
    text = "لطفاً شماره پورت تونل را وارد کنید (این پورت در هر دو سرور استفاده خواهد شد):\n\n(برای لغو /cancel را ارسال کنید)"
    
    # پیام قبلی را ویرایش کرده و درخواست پورت را نمایش می‌دهیم
    await query.edit_message_text(text, reply_markup=None)
    
    # ربات را به حالت "منتظر دریافت پورت" می‌بریم
    return ADD_TUNNEL_GET_PORT

#4
async def add_tunnel_get_port(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۴: دریافت پورت متنی و رفتن به مرحله بعد (دریافت توکن)."""
    port = update.message.text.strip()
    await update.message.delete()
    if not port.isdigit():
        await context.bot.send_message(update.effective_chat.id, "ورودی نامعتبر است. لطفاً فقط عدد وارد کنید.")
        # در همین مرحله باقی می‌مانیم تا کاربر عدد صحیح وارد کند
        return ADD_TUNNEL_GET_PORT
        
    context.user_data['tunnel_info']['port'] = port

    # پس از دریافت پورت، به مرحله بعدی (دریافت توکن) می‌رویم
    text = "لطفاً یک توکن مشترک برای این تونل وارد کنید:\n\n(برای لغو /cancel را ارسال کنید)"
    await reply_or_edit(update, context, text, [], new_message=True)

    return ADD_TUNNEL_GET_TOKEN
    
#5    
async def add_tunnel_get_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۴: دریافت توکن و درخواست Channel Size."""
    token = update.message.text.strip()
    await update.message.delete()
    context.user_data['tunnel_info']['token'] = token
    
    text = "ظرفیت کانال ارتباطی (Channel Size) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما به صورت دکمه‌های ۲*۳
    options = [2048, 4096, 8192, 12288, 16384, 24576]
    keyboard = [
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_chsize|{opt}") for opt in options[:3]],
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_chsize|{opt}") for opt in options[3:]],
        [InlineKeyboardButton("پیشفرض (2048)", callback_data="add_tunnel_chsize|2048")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await reply_or_edit(update, context, text, keyboard, new_message=True)

    return ADD_TUNNEL_GET_CHANNEL_SIZE # رفتن به حالت جدید

#6 
async def add_tunnel_get_channel_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۵: دریافت Channel Size و درخواست Connection Pool."""
    query = update.callback_query
    await query.answer()

    channel_size = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['channel_size'] = channel_size

    text = "تعداد اتصالات رزرو شده (Connection Pool) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما به صورت دکمه‌های ۲*۳
    options = [8, 12, 16, 20, 24, 32]
    keyboard = [
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_pool|{opt}") for opt in options[:3]],
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_pool|{opt}") for opt in options[3:]],
        [InlineKeyboardButton("پیشفرض (24)", callback_data="add_tunnel_pool|24")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_CONN_POOL # رفتن به حالت جدید

#7
async def add_tunnel_get_connection_pool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۶: دریافت Connection Pool و درخواست تعداد اتصالات همزمان (mux_con)."""
    query = update.callback_query
    await query.answer()

    connection_pool = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['connection_pool'] = connection_pool

    text = "تعداد اتصالات همزمان (Mux Connections) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما به صورت دکمه‌های ۲*۳
    options = [8, 16, 24, 32, 40, 48]
    keyboard = [
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_muxcon|{opt}") for opt in options[:3]],
        [InlineKeyboardButton(str(opt), callback_data=f"add_tunnel_muxcon|{opt}") for opt in options[3:]],
        [InlineKeyboardButton("پیشفرض (8)", callback_data="add_tunnel_muxcon|8")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_MUX_CON # رفتن به حالت جدید

#8
async def add_tunnel_get_mux_con(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۷: دریافت Mux Connections و درخواست Heartbeat."""
    query = update.callback_query
    await query.answer()

    mux_con = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['mux_con'] = mux_con

    text = "فاصله بررسی اتصال (Heartbeat) به ثانیه را انتخاب کنید:"

    options = [10, 15, 20, 25, 30, 40]
    keyboard = [
        [InlineKeyboardButton(f"{opt}s", callback_data=f"add_tunnel_heartbeat|{opt}") for opt in options[:3]],
        [InlineKeyboardButton(f"{opt}s", callback_data=f"add_tunnel_heartbeat|{opt}") for opt in options[3:]],
        [InlineKeyboardButton("پیشفرض (20)", callback_data="add_tunnel_heartbeat|20")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    return ADD_TUNNEL_GET_HEARTBEAT

#9
async def add_tunnel_get_heartbeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۸: دریافت Heartbeat و درخواست اندازه فریم (Mux Frame Size)."""
    query = update.callback_query
    await query.answer()

    heartbeat = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['heartbeat'] = heartbeat

    text = "اندازه هر فریم داده (Mux Frame Size) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما (بدون تکرار)
    options = [16384, 32768, 65536]
    keyboard = [
        [InlineKeyboardButton(f"{opt // 1024}KB", callback_data=f"add_tunnel_framesize|{opt}") for opt in options],
        [InlineKeyboardButton("پیشفرض (32KB)", callback_data="add_tunnel_framesize|32768")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_MUX_FRAMESIZE # رفتن به حالت جدید

#10
async def add_tunnel_get_mux_framesize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۹: دریافت Mux Frame Size و درخواست بافر دریافت (Mux Recieve Buffer)."""
    query = update.callback_query
    await query.answer()

    mux_framesize = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['mux_framesize'] = mux_framesize

    text = "بافر دریافت کلی (Mux Recieve Buffer) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما
    options = [4194304, 8388608, 16777216, 25165824, 33554432, 50331648]
    keyboard = [
        [InlineKeyboardButton(f"{opt // 1048576}MB", callback_data=f"add_tunnel_recievebuffer|{opt}") for opt in options[:3]],
        [InlineKeyboardButton(f"{opt // 1048576}MB", callback_data=f"add_tunnel_recievebuffer|{opt}") for opt in options[3:]],
        [InlineKeyboardButton("پیشفرض (4MB)", callback_data="add_tunnel_recievebuffer|4194304")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_MUX_RECIEVEBUFFER # رفتن به حالت جدید

#11
async def add_tunnel_get_mux_recievebuffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۰: دریافت Mux Recieve Buffer و درخواست بافر هر جریان (Mux Stream Buffer)."""
    query = update.callback_query
    await query.answer()

    mux_recievebuffer = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['mux_recievebuffer'] = mux_recievebuffer

    text = "بافر هر جریان (Mux Stream Buffer) را انتخاب کنید:"
    
    # مقادیر پیشنهادی شما (بدون تکرار)
    options = [32768, 65536, 131072, 262144]
    keyboard = [
        [InlineKeyboardButton(f"{opt // 1024}KB", callback_data=f"add_tunnel_streambuffer|{opt}") for opt in options],
           [InlineKeyboardButton("پیشفرض (2MB)", callback_data="add_tunnel_streambuffer|2000000")],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]   
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_MUX_STREAMBUFFER # رفتن به حالت جدید

#12
async def add_tunnel_get_mux_streambuffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۱: دریافت Mux Stream Buffer و درخواست وضعیت TCP_NODELAY."""
    query = update.callback_query
    await query.answer()

    mux_streambuffer = int(query.data.split("|")[1])
    context.user_data['tunnel_info']['mux_streambuffer'] = mux_streambuffer

    text = "آیا TCP_NODELAY فعال باشد؟"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ فعال", callback_data="add_tunnel_nodelay|true"),
            InlineKeyboardButton("❌ غیرفعال", callback_data="add_tunnel_nodelay|false")
        ],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_NODELAY # رفتن به حالت جدید

#13
async def add_tunnel_get_nodelay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۲: دریافت وضعیت TCP_NODELAY و درخواست وضعیت Sniffer."""
    query = update.callback_query
    await query.answer()

    nodelay = query.data.split("|")[1] == 'true'
    context.user_data['tunnel_info']['nodelay'] = nodelay

    text = "آیا Sniffer فعال باشد؟"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ فعال", callback_data="add_tunnel_sniffer|true"),
            InlineKeyboardButton("❌ غیرفعال", callback_data="add_tunnel_sniffer|false")
        ],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return ADD_TUNNEL_GET_SNIFFER # رفتن به حالت جدید

#14
async def add_tunnel_get_sniffer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۳: دریافت وضعیت Sniffer و درخواست شماره Web Port."""
    query = update.callback_query
    await query.answer()

    sniffer = query.data.split("|")[1] == 'true'
    context.user_data['tunnel_info']['sniffer'] = sniffer

    text = "شماره Web Port را وارد کنید (عدد 0 برای غیرفعال کردن):\n\n(برای لغو /cancel را ارسال کنید)"
    
    await query.edit_message_text(text, reply_markup=None)

    return ADD_TUNNEL_GET_WEB_PORT # رفتن به حالت جدید

#15
async def add_tunnel_get_web_port(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۴: دریافت شماره Web Port و درخواست وضعیت Proxy Protocol."""
    web_port = update.message.text.strip()
    await update.message.delete()
    if not web_port.isdigit():
        await context.bot.send_message(update.effective_chat.id, "ورودی نامعتبر است. لطفاً فقط عدد وارد کنید.")
        return ADD_TUNNEL_GET_WEB_PORT

    context.user_data['tunnel_info']['web_port'] = int(web_port)

    text = "آیا Proxy Protocol فعال باشد؟"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ فعال", callback_data="add_tunnel_proxy|true"),
            InlineKeyboardButton("❌ غیرفعال", callback_data="add_tunnel_proxy|false")
        ],
        [InlineKeyboardButton("🔙 لغو", callback_data="cancel_conv")]
    ]
    
    await reply_or_edit(update, context, text, keyboard, new_message=True)

    return ADD_TUNNEL_GET_PROXY_PROTOCOL # رفتن به حالت جدید

#16
async def add_tunnel_get_proxy_protocol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱۵: دریافت وضعیت Proxy Protocol و درخواست پورت‌های داخلی ایران."""
    query = update.callback_query
    await query.answer()

    proxy_protocol = query.data.split("|")[1] == 'true'
    context.user_data['tunnel_info']['proxy_protocol'] = proxy_protocol

    # ✅ تغییر اصلی: استفاده از فرمت HTML به جای MarkdownV2
    text = (
        "پورت‌های داخلی سرور ایران که باید باز شوند را وارد کنید.\n"
        "(مثال: <code>9092</code> یا <code>9092,9093</code>)\n\n"
        "(برای لغو /cancel را ارسال کنید)"
    )
    
    # پیام قبلی را ویرایش کرده و درخواست نهایی را نمایش می‌دهیم
    await query.edit_message_text(text, reply_markup=None, parse_mode=ParseMode.HTML)

    return ADD_TUNNEL_GET_FORWARD_PORTS
    
#17
async def add_tunnel_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله نهایی: دریافت پورت‌های داخلی و ایجاد تراکنشی تونل کامل."""
    
    forward_ports_str = update.message.text.strip()
    await update.message.delete()
    context.user_data['tunnel_info']['forward_ports'] = [p.strip() for p in forward_ports_str.split(',')]
    
    tunnel_info = context.user_data['tunnel_info']
    server_idx = int(tunnel_info['server_idx'])
    port = tunnel_info['port']
    iran_server_name = tunnel_info['iran_server']
    
    kharej_server = servers_cache[server_idx]
    iran_server_creds = load_iran_servers()[iran_server_name]
    
    # تولید محتوای فایل‌های کانفیگ و سرویس
    kharej_config, iran_config = generate_toml_configs(tunnel_info)
    kharej_service, iran_service = generate_service_files(port)
    
    await reply_or_edit(update, context, "⏳ تمام اطلاعات دریافت شد. شروع فرآیند نصب تراکنشی...", [], new_message=True)
    
    kharej_ssh = PersistentSSHClient(kharej_server)
    if not kharej_ssh.client:
        await reply_or_edit(update, context, f"❌ اتصال به سرور خارج {kharej_server['name']} برقرار نشد.", [], new_message=True)
        return ConversationHandler.END

    # مرحله ۱: نصب روی سرور ایران
    await reply_or_edit(update, context, f"⏳ مرحله ۱/۲: نصب روی سرور ایران ({iran_server_name})...", [], new_message=True)
    iran_commands = [
        f"mkdir -p /root/backhaul-core",
        f"cat << 'EOF' > /root/backhaul-core/iran{port}.toml\n{iran_config}\nEOF",
        f"cat << 'EOF' > /etc/systemd/system/backhaul-iran{port}.service\n{iran_service}\nEOF",
        "systemctl daemon-reload",
        f"systemctl enable --now backhaul-iran{port}.service"
    ]
    
    iran_failed = False
    for cmd_template in iran_commands:
        jump_cmd = f"sshpass -p '{iran_server_creds['password']}' ssh -p {iran_server_creds['port']} -o StrictHostKeyChecking=no {iran_server_creds['user']}@{iran_server_creds['ip']} '{cmd_template}'"
        stdin, stdout, stderr = kharej_ssh.client.exec_command(jump_cmd, timeout=60)
        if stdout.channel.recv_exit_status() != 0:
            error_output = stderr.read().decode().strip()
            await reply_or_edit(update, context, f"❌ خطا در نصب سرور ایران:\n<pre>{error_output}</pre>", [], new_message=True)
            iran_failed = True
            break
            
    if iran_failed:
        if 'tunnel_info' in context.user_data: del context.user_data['tunnel_info']
        return ConversationHandler.END

    # تأخیر کوتاه قبل از نصب روی سرور خارج
    await asyncio.sleep(3)

    # مرحله ۲: نصب روی سرور خارج
    await reply_or_edit(update, context, f"⏳ مرحله ۲/۲: نصب روی سرور خارج ({kharej_server['name']})...", [], new_message=True)
    kharej_commands = [
        f"mkdir -p /root/backhaul-core",
        f"cat << 'EOF' > /root/backhaul-core/kharej{port}.toml\n{kharej_config}\nEOF",
        f"cat << 'EOF' > /etc/systemd/system/backhaul-kharej{port}.service\n{kharej_service}\nEOF",
        "systemctl daemon-reload",
        f"systemctl enable --now backhaul-kharej{port}.service"
    ]

    for cmd in kharej_commands:
        stdin, stdout, stderr = kharej_ssh.client.exec_command(cmd, timeout=30)
        if stdout.channel.recv_exit_status() != 0:
            await reply_or_edit(update, context, f"❌ خطا در نصب سرور خارج:\n<pre>{stderr.read().decode()}</pre>", [], new_message=True)
            if 'tunnel_info' in context.user_data: del context.user_data['tunnel_info']
            return ConversationHandler.END

    # مرحله نهایی: ذخیره لینک و نمایش پیام موفقیت
    links = load_cron_links() # از همان فایل برای لینک تونل هم استفاده می‌کنیم
    if kharej_server['name'] not in links:
        links[kharej_server['name']] = {}
    links[kharej_server['name']][port] = iran_server_name
    save_cron_links(links)
    
    final_text = f"✅ تونل پورت {port} بین {kharej_server['name']} و {iran_server_name} با موفقیت کامل ایجاد و فعال شد."
    keyboard = [[InlineKeyboardButton("🔙 بازگشت به مدیریت تانل‌ها", callback_data=f"tunnel_menu|{server_idx}")]]
    await reply_or_edit(update, context, final_text, keyboard, new_message=True)
    
    if 'tunnel_info' in context.user_data:
        del context.user_data['tunnel_info']
    return ConversationHandler.END
 
async def add_tunnel_get_forward_ports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    await update.message.delete()
    context.user_data['tunnel_info']['token'] = token
    text = "پورت‌های داخلی سرور ایران را وارد کنید (مثال: 9092,9093):\n\n(برای لغو /cancel را ارسال کنید)"
    await reply_or_edit(update, context, text, [], new_message=True)
    return await show_advanced_settings_menu(update, context)

async def show_advanced_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    info = context.user_data.get('tunnel_info')
    if not info: return ConversationHandler.END
    
    query = update.callback_query
    if query: await query.answer()
    
    text = "مقادیر زیر استفاده خواهند شد. برای تغییر روی هر مورد کلیک کنید."
    def bool_to_fa(status): return "✅ فعال" if status else "❌ غیرفعال"
    keyboard = [
        [InlineKeyboardButton(f"Transport: {info['transport']}", callback_data="set_param|transport")],
        [InlineKeyboardButton(f"Connection Pool: {info['connection_pool']}", callback_data="set_param|connection_pool")],
        [InlineKeyboardButton(f"TCP NoDelay: {bool_to_fa(info['nodelay'])}", callback_data="toggle_param|nodelay")],
        [InlineKeyboardButton(f"Heartbeat: {info['heartbeat']}s", callback_data="set_param|heartbeat")],
        [InlineKeyboardButton("✅ ذخیره و ساخت تونل", callback_data="finalize_tunnel_creation")],
        [InlineKeyboardButton("🔙 لغو کامل", callback_data="cancel_conv")]
    ]
    await reply_or_edit(update, context, text, keyboard, new_message=True)
    return ADD_TUNNEL_ADVANCED_SETTINGS




async def recheck_license_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لایسنس را به صورت دستی مجدداً بررسی کرده و نتیجه را به کاربر اعلام می‌کند."""
    query = update.callback_query
    await query.answer(text="در حال بررسی مجدد لایسنس از سرور...")

    # تابع بررسی دوره‌ای را به صورت دستی فراخوانی می‌کنیم تا وضعیت آپدیت شود
    await check_license_periodically(context)

    # حالا وضعیت جدید لایسنس را چک می‌کنیم
    if IS_LICENSED:
        await query.edit_message_text("✅ لایسنس با موفقیت تایید شد! ربات برای شما فعال است.")
        # کاربر را به منوی اصلی هدایت می‌کنیم
        await start(update, context)
    else:
        # اگر لایسنس هنوز نامعتبر بود، فقط یک هشدار نمایش می‌دهیم
        await query.answer(text="❌ لایسنس همچنان نامعتبر است.", show_alert=True)

async def check_license_periodically(context: ContextTypes.DEFAULT_TYPE):
    """
    این تابع به صورت دوره‌ای توسط ربات اجرا شده، فایل لایسنس را از راه دور
    چک کرده و وضعیت لایسنس (متغیر IS_LICENSED) را آپدیت می‌کند.
    """
    global IS_LICENSED
    LICENSE_URL = "http://license.salamatpaya.com:8080/license" # << URL فایل لایسنس خود را اینجا وارد کنید
    
    print("\n[Periodic License Check] Running scheduled license check...")
    
    license_data = get_remote_license(LICENSE_URL)
    new_license_status = False # وضعیت پیش‌فرض لایسنس در هر بار چک

    if license_data and "licenses" in license_data:
        try:
            # IP عمومی سرور ربات را دریافت می‌کنیم
            current_ip_response = requests.get("https://api.ipify.org", timeout=5)
            current_ip_response.raise_for_status()
            current_ip = current_ip_response.text.strip()

            # در لیست لایسنس‌ها به دنبال IP فعلی می‌گردیم
            for license_item in license_data["licenses"]:
                if license_item.get("ip") == current_ip:
                    # اگر IP پیدا شد، تاریخ انقضای آن را چک می‌کنیم
                    if check_expiry_license(license_item.get("expiry_date", "2000-01-01")):
                        new_license_status = True # اگر تاریخ معتبر بود، لایسنس تایید می‌شود
                    break # پس از پیدا کردن IP، از حلقه خارج می‌شویم
        
        except Exception as e:
            print(f"[Periodic License Check] Error during check: {e}")
    
    # فقط در صورتی که وضعیت لایسنس تغییر کرده باشد، آن را لاگ می‌کنیم
    if IS_LICENSED != new_license_status:
        IS_LICENSED = new_license_status
        status_text = "✅ معتبر" if IS_LICENSED else "❌ نامعتبر"
        print(f"✅ وضعیت لایسنس به صورت خودکار به {status_text} تغییر یافت.")
    else:
        status_text = "✅ معتبر" if IS_LICENSED else "❌ نامعتبر"
        print(f"✅ بررسی انجام شد. وضعیت لایسنس بدون تغییر باقی ماند: {status_text}")
        
async def license_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """یک پیام خطای استاندارد برای لایسنس نامعتبر به همراه دکمه بررسی مجدد ارسال می‌کند."""
    error_text = (
        "❌ **خطای لایسنس** ❌\n\n"
        "اشتراک شما برای استفاده از این ربات معتبر نیست یا به پایان رسیده است.\n\n"
        "لطفاً جهت تمدید یا فعال‌سازی با پشتیبانی تماس بگیرید."
    )
    
    # ✅ کیبورد جدید با دکمه بررسی مجدد
    keyboard = [
        [InlineKeyboardButton("🔄 بررسی مجدد لایسنس", callback_data="recheck_license")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    query = update.callback_query
    if query:
        await query.answer(text="خطای لایسنس!", show_alert=True)
        try:
            # تلاش برای ویرایش پیام قبلی و افزودن دکمه
            await query.edit_message_text(error_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except BadRequest:
            # اگر ویرایش ممکن نبود، پیام جدید می‌فرستیم
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_html(error_text, reply_markup=reply_markup)
        
async def receive_param_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مقدار جدید پارامتر را از کاربر دریافت، ذخیره و به منوی تنظیمات بازمی‌گردد."""
    
    # نام پارامتری که در حال تنظیم آن هستیم را از حافظه ربات می‌خوانیم
    param = context.user_data.get('param_to_set')
    if not param:
        # در صورت بروز خطا به منوی اصلی بازمی‌گردیم
        await start(update, context)
        return ConversationHandler.END

    # مقدار جدید را از پیام متنی کاربر می‌خوانیم
    value = update.message.text.strip()
    # پیام کاربر را برای خلوتی صفحه حذف می‌کنیم
    await update.message.delete()
    
    # مقدار را به نوع مناسب (عددی یا بولی) تبدیل می‌کنیم
    final_value = value
    if value.lower() in ['true', 'false']:
        final_value = (value.lower() == 'true')
    elif value.isdigit():
        final_value = int(value)
    
    # مقدار جدید را در دیکشنری اطلاعات تونل ذخیره می‌کنیم
    context.user_data['tunnel_info'][param] = final_value
    
    # نام پارامتر موقت را از حافظه پاک می‌کنیم
    del context.user_data['param_to_set']
    
    # کاربر را به منوی تنظیمات پیشرفته با مقادیر آپدیت شده بازمی‌گردانیم
    return await show_advanced_settings_menu(update, context)

async def tunnel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی مدیریت تونل‌ها را با خواندن اطلاعات از فایل‌های .toml نمایش می‌دهد."""
    query = update.callback_query
    await query.answer()

    _, server_idx_str = query.data.split("|")
    server_idx = int(server_idx_str)
    server = servers_cache[server_idx]

    await query.edit_message_text(f"⏳ دریافت لیست تونل‌ها در سرور {server['name']}...")

    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return

    # بارگذاری سرورهای ایران برای جستجوی نام
    iran_servers = load_iran_servers()
    ip_to_name_map = {details['ip']: name for name, details in iran_servers.items()}

    # گرفتن لیست فایل‌های toml
    cmd_ls = "ls /root/backhaul-core/kharej*.toml 2>/dev/null"
    stdin, stdout, _ = ssh.client.exec_command(cmd_ls)
    toml_files = stdout.read().decode().strip().splitlines()
    
    header = f"<b>🔗 مدیریت تانل | {server['name']}</b>\n\n"
    text_body = ""
    keyboard = []
    
    if not toml_files:
        text_body = "بروی این سرور هیچ کانفیگ تونلی یافت نشد"
    else:
        # خواندن محتوای تمام فایل‌ها با یک دستور
        cmd_cat = "for f in /root/backhaul-core/kharej*.toml; do echo \"$f\"; cat \"$f\"; echo '---EOF---'; done"
        stdin, stdout, _ = ssh.client.exec_command(cmd_cat)
        full_output = stdout.read().decode()
        
        file_contents = full_output.strip().split('---EOF---')
        
        for content_block in file_contents:
            if not content_block.strip():
                continue
            
            lines = content_block.strip().split('\n')
            file_path = lines[0]
            file_content = "\n".join(lines[1:])
            
            port_match = re.search(r"kharej(\d+)\.toml", file_path)
            iran_ip_match = re.search(r'remote_addr\s*=\s*"([^:]+):\d+"', file_content)
            
            if port_match:
                port = port_match.group(1)
                iran_ip = iran_ip_match.group(1) if iran_ip_match else "نامشخص"
                
                # ✅ اینجا تورفتگی‌ها اصلاح شده و همه چیز داخل حلقه است
                display_name = ip_to_name_map.get(iran_ip, iran_ip)
                service_text = f"پورت: {port} | متصل به ایران: {display_name}"
                keyboard.append([InlineKeyboardButton(service_text, callback_data=f"manage_tunnel|{server_idx_str}|{port}")])

    keyboard.append([InlineKeyboardButton("➕ افزودن تانل جدید", callback_data=f"add_tunnel|{server_idx_str}")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"svc|{server_idx_str}")])

    await reply_or_edit(update, context, header + text_body, keyboard)
    
async def manage_tunnel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی مدیریت برای یک تونل خاص (حذف/بروزرسانی) را نمایش می‌دهد."""
    query = update.callback_query
    await query.answer()

    _, server_idx_str, port = query.data.split("|")
    server = servers_cache[int(server_idx_str)]
    
    text = f"اقدام مورد نظر برای تونل پورت <b>{port}</b> در سرور <b>{server['name']}</b> را انتخاب کنید:"
    keyboard = [
        [InlineKeyboardButton(f"🗑️ حذف کامل تانل {port}", callback_data=f"delete_tunnel_confirm|{server_idx_str}|{port}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"tunnel_menu|{server_idx_str}")]
    ]
    
    await reply_or_edit(update, context, text, keyboard)    
    
async def delete_tunnel_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """برای حذف کامل تونل از کاربر تاییدیه می‌گیرد."""
    query = update.callback_query
    await query.answer()
    
    _, server_idx_str, port = query.data.split("|")
    
    text = f"⚠️ **اخطار:** آیا از حذف کامل تونل پورت <b>{port}</b> و تمام فایل‌ها و سرویس‌های مرتبط با آن در هر دو سرور ایران و خارج مطمئن هستید؟ این عمل غیرقابل بازگشت است."
    keyboard = [
        [InlineKeyboardButton(f"✅ بله، حذف کن", callback_data=f"delete_tunnel_action|{server_idx_str}|{port}")],
        [InlineKeyboardButton(f"❌ خیر، لغو", callback_data=f"manage_tunnel|{server_idx_str}|{port}")],
    ]
    await reply_or_edit(update, context, text, keyboard)

async def delete_tunnel_action(update: Update, context: ContextTypes.DEFAULT_TYPE, is_update: bool = False):
    """عملیات حذف تراکنشی تونل را (ابتدا ایران، سپس خارج) انجام می‌دهد."""
    query = update.callback_query
    if not is_update:
        await query.answer()

    _, server_idx_str, port = query.data.split("|")
    kharej_server = servers_cache[int(server_idx_str)]
    
    if not is_update:
        await query.edit_message_text(f"⏳ شروع فرآیند حذف کامل تونل پورت {port}...")

    # ۱. اتصال به سرور خارج
    ssh = PersistentSSHClient(kharej_server)
    if not ssh.client:
        await query.edit_message_text(f"❌ اتصال به سرور {kharej_server['name']} برقرار نشد.")
        return

    # ۲. خواندن فایل toml برای پیدا کردن IP سرور ایران
    kharej_toml_path = f"/root/backhaul-core/kharej{port}.toml"
    stdin, stdout, _ = ssh.client.exec_command(f"cat {kharej_toml_path}")
    if stdout.channel.recv_exit_status() != 0:
        print(f"Config file {kharej_toml_path} not found. Deleting service only.")
    else:
        file_content = stdout.read().decode()
        iran_ip_match = re.search(r'remote_addr\s*=\s*"([^:]+):\d+"', file_content)
        iran_ip = iran_ip_match.group(1) if iran_ip_match else None
        
        iran_server_creds = None
        if iran_ip:
            iran_servers = load_iran_servers()
            for name, details in iran_servers.items():
                if details['ip'] == iran_ip:
                    iran_server_creds = details
                    iran_server_creds['name'] = name
                    break
        
        if iran_server_creds:
            if not is_update:
                await query.edit_message_text(f"⏳ حذف از سرور ایران ({iran_server_creds['name']})...")
            iran_service = f"backhaul-iran{port}.service"
            iran_toml = f"/root/backhaul-core/iran{port}.toml"
            iran_delete_cmds = f"'systemctl stop {iran_service}; systemctl disable {iran_service}; rm -f {iran_toml}; systemctl daemon-reload'"
            jump_cmd = f"sshpass -p '{iran_server_creds['password']}' ssh -p {iran_server_creds['port']} -o StrictHostKeyChecking=no {iran_server_creds['user']}@{iran_server_creds['ip']} {iran_delete_cmds}"
            
            stdin, stdout, stderr = ssh.client.exec_command(jump_cmd)
            if stdout.channel.recv_exit_status() != 0:
                if not is_update:
                    await query.edit_message_text(f"⚠️ خطا در پاکسازی سرور ایران. حذف سرور خارج ادامه می‌یابد...")
                    await asyncio.sleep(2)
                
    # ۵. حذف از سرور خارج
    if not is_update:
        await query.edit_message_text(f"⏳ حذف از سرور خارج ({kharej_server['name']})...")
    kharej_service = f"backhaul-kharej{port}.service"
    kharej_delete_cmd = f"systemctl stop {kharej_service}; systemctl disable {kharej_service}; rm -f {kharej_toml_path}; systemctl daemon-reload"
    stdin, stdout, stderr = ssh.client.exec_command(kharej_delete_cmd)
    
    # حذف لینک از فایل JSON
    links = load_cron_links()
    if kharej_server['name'] in links and port in links[kharej_server['name']]:
        del links[kharej_server['name']][port]
        save_cron_links(links)
    
    if not is_update:
        final_text = f"✅ عملیات حذف برای تونل پورت {port} به اتمام رسید."
        keyboard = [[InlineKeyboardButton("🔙 بازگشت به مدیریت تانل‌ها", callback_data=f"tunnel_menu|{server_idx_str}")]]
        await reply_or_edit(update, context, final_text, keyboard)
    


async def reply_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard: list, new_message: bool = False):
    reply_markup = InlineKeyboardMarkup(keyboard)
    query = update.callback_query
    
    if query and not new_message:
        try:
            await query.answer()
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            return
        except BadRequest: print("Could not edit message, will send a new one.")

    chat_id = update.effective_chat.id
    last_message_id = context.user_data.get('last_message_id')
    if last_message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_message_id)
        except BadRequest: pass

    new_msg = await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
    )
    context.user_data['last_message_id'] = new_msg.message_id

async def periodic_status_updater(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    message_id = job.data['message_id']
    
    servers = parse_servers()
    if not servers:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="هیچ سروری برای نمایش وضعیت وجود ندارد.")
        except BadRequest: pass
        context.chat_data['stop_status_update'] = True
        return

    clients = [PersistentSSHClient(s) for s in servers]
    last_message = ""
    
    while not context.chat_data.get('stop_status_update', False):
        current_message_text = build_message(clients)
        if current_message_text != last_message:
            try:
                keyboard = [[InlineKeyboardButton("⏹️ توقف بروزرسانی", callback_data="stop_update")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=current_message_text,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup
                )
                last_message = current_message_text
            except BadRequest as e:
                if "Message is not modified" in str(e): pass
                else: break
            except Exception: break
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)
    
    # --- تغییر اصلی اینجاست ---
    # کار پس‌زمینه دیگر هیچ پیامی ارسال نمی‌کند و فقط بی‌صدا متوقف می‌شود
    print(f"Live status update loop stopped for chat {chat_id}.")
    if 'stop_status_update' in context.chat_data:
        del context.chat_data['stop_status_update']

async def cron_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منویی با دکمه فقط برای سرورهای 'خارج' نمایش می‌دهد."""
    query = update.callback_query
    await query.answer()

    # ✅ فقط سرورهای اصلی (خارج) را می‌خوانیم
    servers = parse_servers()
    
    text = "لطفاً یکی از سرورهای خارج را برای مدیریت کرون‌جاب انتخاب کنید:"
    keyboard = []
    
    for i, server in enumerate(servers):
        country_name_en, flag = get_country_info(server['ip'], context)
        country_name_fa = COUNTRY_NAMES_FA.get(country_name_en, country_name_en)
        button_text = f"{flag} {server['name']} ({country_name_fa})"
        
        # ✅ callback_data اکنون فقط شامل ایندکس سرور اصلی است
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"show_cron|{i}")])

    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")])
    await reply_or_edit(update, context, text, keyboard)
    
async def show_cron_for_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """وضعیت کرون‌جاب‌های یک سرور خاص را نمایش داده و گفتگوهای قبلی را خاتمه می‌دهد."""
    query = update.callback_query
    # ورودی این تابع ۲ بخش است: show_cron|{server_idx}
    _, server_idx_str = query.data.split("|")
    server_idx = int(server_idx_str)
    
    # چون این منو فقط برای سرورهای خارج است، نوع آن همیشه 'main' است
    server_type = 'main'
    server = servers_cache[server_idx]
    
    await query.answer()
    await query.edit_message_text(f"⏳ دریافت اطلاعات کرون جاب از سرور {server['name']}...")

    country_name_en, flag = get_country_info(server['ip'], context)
    country_name_fa = COUNTRY_NAMES_FA.get(country_name_en, country_name_en)
    
    header = f"<b>{flag} مدیریت کرون‌جاب | {server['name']} ({country_name_fa})</b>\n\n"
    message_text = ""
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        message_text = "❌ اتصال برقرار نشد"
    else:
        stdin, stdout, _ = ssh.client.exec_command("crontab -l")
        crontab_output = stdout.read().decode()
        
        cron_links = load_cron_links().get(server['name'], {})
        
        active_cron_ports = {}
        for line in crontab_output.splitlines():
            if line.strip().startswith("#") or not line.strip():
                continue
            match = re.search(r"^((?:[^\s]+\s+){4}[^\s]+)\s+.*backhaul-kharej(\d+)\.service", line)
            if match:
                schedule = match.group(1).strip()
                port = match.group(2)
                active_cron_ports[port] = schedule

        if not active_cron_ports:
            message_text = "هیچ کرون جاب فعالی یافت نشد."
        else:
            for port, schedule in active_cron_ports.items():
                human_schedule = translate_cron_schedule(schedule)
                iran_server_link = cron_links.get(port, "نامشخص")
                message_text += f"✅ پورت <code>{port}</code> | <b>{human_schedule}</b> | متصل به: <b>{iran_server_link}</b>\n"

    # --- ✅ تغییر اصلی اینجاست ---
    # callback_data دکمه افزودن، اکنون شامل server_type هم می‌شود (۳ بخش کامل)
    keyboard = [
        [InlineKeyboardButton("➕ افزودن کرون جاب", callback_data=f"add_cron|{server_type}|{server_idx_str}")],
        [InlineKeyboardButton("➖ حذف کرون جاب", callback_data=f"remove_cron_menu|{server_idx_str}")],
        [InlineKeyboardButton("🔙 بازگشت به لیست سرورها", callback_data="cron_menu")]
    ]
    
    await reply_or_edit(update, context, header + message_text, keyboard)
    return ConversationHandler.END
    
async def remove_cron_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی حذف یک کرون جاب خاص را نمایش می‌دهد."""
    query = update.callback_query
    await query.answer()
    
    _, server_idx_str = query.data.split("|")
    server_idx = int(server_idx_str)
    server = servers_cache[server_idx]

    await query.edit_message_text(f"⏳ دریافت لیست کرون‌جاب‌های فعال از سرور {server['name']}...")

    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return

    stdin, stdout, _ = ssh.client.exec_command("crontab -l")
    crontab_output = stdout.read().decode()

    active_cron_jobs = []
    for line in crontab_output.splitlines():
        match = re.search(r"backhaul-kharej(\d+)\.service", line)
        if match:
            active_cron_jobs.append(match.group(1))

    if not active_cron_jobs:
        text = "هیچ کرون جاب فعالی برای حذف روی این سرور وجود ندارد."
        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"show_cron|{server_idx_str}")]]
    else:
        text = "لطفاً کرون‌جابی که می‌خواهید حذف شود را انتخاب کنید:"
        keyboard = []
        for port in active_cron_jobs:
            button_text = f"🗑️ حذف کرون جاب پورت {port}"
            # --- ✅ تغییر اصلی اینجاست ---
            # callback_data را با ۳ بخش صحیح و کامل می‌سازیم
            callback_data = f"remove_cron_action|{server_idx_str}|{port}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"show_cron|{server_idx_str}")])

    await reply_or_edit(update, context, text, keyboard)
    
async def remove_cron_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """یک کرون جاب مشخص را به صورت تراکنشی یا یک‌طرفه حذف می‌کند."""
    query = update.callback_query
    await query.answer()

    _, server_idx_str, port_to_delete = query.data.split("|")
    server_idx = int(server_idx_str)
    kharej_server = servers_cache[server_idx]
    
    # متغیر متن نهایی را در ابتدا تعریف می‌کنیم تا از خطا جلوگیری شود
    final_text = "❌ یک خطای ناشناخته رخ داد."

    await query.edit_message_text(f"⏳ شروع فرآیند حذف برای پورت {port_to_delete}...")

    # یافتن سرور ایران متناظر
    cron_links = load_cron_links()
    iran_server_name = cron_links.get(kharej_server['name'], {}).get(port_to_delete)
    iran_server_creds = load_iran_servers().get(iran_server_name) if iran_server_name else None

    # ۱. اتصال به سرور خارج
    kharej_ssh = PersistentSSHClient(kharej_server)
    if not kharej_ssh.client:
        await query.edit_message_text("❌ اتصال به سرور خارج برقرار نشد.")
        return

    # ۲. اگر سرور ایران مشخص بود، ابتدا از آنجا حذف کن
    if iran_server_name and iran_server_creds:
        await query.edit_message_text(f"⏳ مرحله ۱/۲: در حال حذف کرون‌جاب از سرور ایران: {iran_server_name}...")
        iran_service_name = f"backhaul-iran{port_to_delete}.service"
        iran_delete_cmd = f"'(crontab -l 2>/dev/null | grep -v -F \"{iran_service_name}\" || true) | crontab -'"
        jump_cmd = f"sshpass -p '{iran_server_creds['password']}' ssh -p {iran_server_creds['port']} -o StrictHostKeyChecking=no {iran_server_creds['user']}@{iran_server_creds['ip']} {iran_delete_cmd}"
        
        stdin, stdout, stderr = kharej_ssh.client.exec_command(jump_cmd)
        if stdout.channel.recv_exit_status() != 0:
            error_output = stderr.read().decode().strip()
            await query.edit_message_text(f"❌ خطا در حذف کرون‌جاب از سرور ایران. عملیات متوقف شد.\n<pre>{error_output}</pre>", parse_mode=ParseMode.HTML)
            return

    # ۳. حذف کرون جاب از سرور خارج
    await query.edit_message_text(f"⏳ در حال حذف کرون‌جاب از سرور خارج: {kharej_server['name']}...")
    kharej_service_name = f"backhaul-kharej{port_to_delete}.service"
    kharej_delete_cmd = f'(crontab -l 2>/dev/null | grep -v -F "{kharej_service_name}" || true) | crontab -'
    
    stdin, stdout, stderr = kharej_ssh.client.exec_command(kharej_delete_cmd)
    
    if stdout.channel.recv_exit_status() == 0:
        # ۴. حذف لینک از فایل JSON در صورت وجود
        if kharej_server['name'] in cron_links and port_to_delete in cron_links[kharej_server['name']]:
            del cron_links[kharej_server['name']][port_to_delete]
            save_cron_links(cron_links)
        
        if iran_server_name and iran_server_creds:
            final_text = f"✅ کرون جاب پورت {port_to_delete} از هر دو سرور با موفقیت حذف شد."
        else:
            final_text = f"✅ کرون جاب پورت {port_to_delete} فقط از سرور خارج حذف شد (ارتباط با ایران نامشخص بود)."
    else:
        error = stderr.read().decode().strip()
        final_text = f"❌ خطا در حذف کرون‌جاب از سرور خارج:\n<pre>{error}</pre>"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به منوی کرون‌جاب", callback_data=f"show_cron|{server_idx_str}")]]
    await reply_or_edit(update, context, final_text, keyboard)

async def add_cron_get_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله نهایی: دریافت زمان‌بندی و ساخت کرون جاب."""
    query = update.callback_query
    await query.answer()

    schedule = query.data.split("|")[1]
    cron_info = context.user_data['cron_info']
    server_type, server_id, port = cron_info['type'], cron_info['id'], cron_info['port']

    if server_type == 'iran':
        server = {**load_iran_servers()[server_id], "name": server_id}
        service_name = f"backhaul-iran{port}.service"
    else:
        server = servers_cache[int(server_id)]
        service_name = f"backhaul-kharej{port}.service"

    await query.edit_message_text(f"⏳ در حال افزودن کرون جاب به سرور {server['name']}...")

    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return ConversationHandler.END

    # دستور جدید کرون جاب
    new_cron_line = f'{schedule} systemctl restart {service_name}'
    # این دستور، دستور جدید را به لیست کرون‌تب اضافه می‌کند و از تکرار آن جلوگیری می‌کند
    command = f'(crontab -l | grep -v -F "{service_name}" ; echo "{new_cron_line}") | crontab -'

    stdin, stdout, stderr = ssh.client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()

    if exit_status == 0:
        await query.edit_message_text(f"✅ کرون جاب برای پورت {port} با موفقیت افزوده/بروزرسانی شد.")
    else:
        error = stderr.read().decode()
        await query.edit_message_text(f"❌ خطا در افزودن کرون جاب:\n<pre>{error}</pre>")

    # پاک کردن اطلاعات موقت و بازگشت به منو
    del context.user_data['cron_info']
    await asyncio.sleep(2)
    fake_query_data = f"show_cron_for_server|{server_type}|{server_id}"
    fake_update = Update(update.update_id, callback_query=type('obj', (object,), {'data': fake_query_data, 'answer': (lambda: True), 'message': query.message})())
    await show_cron_for_server(fake_update, context)
    
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    """تابع اصلی برای ارسال یا ویرایش پیام‌های اصلی ربات. این تابع پیام قبلی را حذف می‌کند."""

    if update.message:
        try:
            await update.message.delete()
        except BadRequest:
            pass

    context.chat_data['stop_status_update'] = True
    await asyncio.sleep(0.1)

    base_text = "ربات مدیریت سرور | یکی از گزینه‌ها را انتخاب کنید:"
    display_text = f"{g_license_info}\n\n---\n\n{base_text}"
    display_text = f"<i>{message_text}</i>\n\n{base_text}" if message_text else base_text
    
    keyboard = [
        [InlineKeyboardButton("📊 نمایش زنده وضعیت", callback_data="status_live")],
        [InlineKeyboardButton("🔧 مدیریت سرویس‌ها", callback_data="services")],
        [InlineKeyboardButton("⚙️ مدیریت سرورها", callback_data="manage_servers_menu")],
        [InlineKeyboardButton("⏰ مدیریت کرون جاب‌ها", callback_data="cron_menu")],
        [InlineKeyboardButton("📜 نمایش لایسنس", callback_data="show_license")]        # دکمه جدید
    
    ]
    
    await reply_or_edit(update, context, display_text, keyboard, new_message=True)
    return ConversationHandler.END

async def show_license_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اطلاعات لایسنس ذخیره شده در متغیر سراسری را به صورت یک هشدار نمایش می‌دهد."""
    query = update.callback_query
    
    # برای نمایش بهتر در هشدار، تگ‌های HTML را حذف کرده و خطوط جدید را با | جایگزین می‌کنیم
    # این کار باعث می‌شود متن طولانی در پاپ‌آپ به درستی نمایش داده شود
    clean_text = re.sub('<[^<]+?>', '', g_license_info).replace('\n', ' | ')
    
    # نمایش اطلاعات به صورت یک پاپ‌آپ بزرگ (show_alert=True)
    await query.answer(text=clean_text, show_alert=True, cache_time=5)

async def add_cron_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی تک مرحله‌ای برای افزودن کرون جاب را نمایش می‌دهد."""
    query = update.callback_query
    await query.answer()

    _, server_type, server_id = query.data.split("|")
    server = servers_cache[int(server_id)] if server_type == 'main' else {**load_iran_servers()[server_id], "name": server_id}
    
    await query.edit_message_text(f"⏳ دریافت لیست سرویس‌های بدون کرون‌جاب از {server['name']}...")
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return

    stdin, stdout, _ = ssh.client.exec_command("crontab -l")
    crontab_output = stdout.read().decode()
    active_cron_ports = {m.group(1) for m in re.finditer(r"backhaul-(?:iran|kharej)(\d+)\.service", crontab_output)}

    service_pattern = "iran" if server_type == 'iran' else 'kharej'
    stdin, stdout, _ = ssh.client.exec_command(f"ls /root/backhaul-core/*{service_pattern}*.toml 2>/dev/null")
    toml_files = [os.path.basename(f) for f in stdout.read().decode().strip().splitlines()]

    keyboard = []
    available_ports_found = False
    text = "برای کدام پورت و با چه زمان‌بندی کرون‌جاب ساخته شود؟\n\n"

    for f in toml_files:
        port_match = re.search(r'(\d+)', f)
        if port_match:
            port = port_match.group(1)
            if port not in active_cron_ports:
                available_ports_found = True
                keyboard.append([InlineKeyboardButton(f"پورت {port} - هر ساعت", callback_data=f"add_cron_action|{server_type}|{server_id}|{port}|0 * * * *")])
                keyboard.append([InlineKeyboardButton(f"پورت {port} - هر ۶ ساعت", callback_data=f"add_cron_action|{server_type}|{server_id}|{port}|0 */6 * * *")])
                keyboard.append([InlineKeyboardButton(f"پورت {port} - هر ۱۲ ساعت", callback_data=f"add_cron_action|{server_type}|{server_id}|{port}|0 */12 * * *")])
                keyboard.append([InlineKeyboardButton(" ", callback_data="noop")]) 

    if not available_ports_found:
        text = "تمام سرویس‌های این سرور در حال حاضر کرون‌جاب فعال دارند."
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"show_cron_for_server|{server_type}|{server_id}")])
    await reply_or_edit(update, context, text, keyboard)

async def add_cron_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کرون جاب را با اطلاعات دریافتی از دکمه، روی سرور ایجاد می‌کند."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("|")
    _, server_type, server_id, port = parts[0], parts[1], parts[2], parts[3]
    schedule = " ".join(parts[4:])

    server = servers_cache[int(server_id)] if server_type == 'main' else {**load_iran_servers()[server_id], "name": server_id}
    service_name_part = "iran" if server_type == 'iran' else 'kharej'
    service_name = f"backhaul-{service_name_part}{port}.service"

    await query.edit_message_text(f"⏳ در حال افزودن کرون جاب به سرور {server['name']}...")
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return

    new_cron_line = f'{schedule} systemctl restart {service_name}'
    command = f'(crontab -l 2>/dev/null | grep -v -F "{service_name}" ; echo "{new_cron_line}") | crontab -'
    
    stdin, stdout, stderr = ssh.client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()

    final_text = ""
    if exit_status == 0:
        final_text = f"✅ کرون جاب برای پورت {port} با موفقیت افزوده/بروزرسانی شد."
    else:
        error = stderr.read().decode()
        final_text = f"❌ خطا در افزودن کرون جاب:\n<pre>{error}</pre>"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به منوی کرون‌جاب", callback_data=f"show_cron_for_server|{server_type}|{server_id}")]]
    await reply_or_edit(update, context, final_text, keyboard)

async def add_cron_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۱: نمایش پورت‌های موجود (بدون کرون‌جاب) برای انتخاب."""
    query = update.callback_query
    await query.answer()

    # --- ✅ تغییر اصلی اینجاست: اکنون ۳ بخش اطلاعات را به درستی می‌خواند ---
    _, server_type, server_id = query.data.split("|")
    context.user_data['cron_info'] = {'type': server_type, 'id': server_id}
    
    server = servers_cache[int(server_id)] if server_type == 'main' else {**load_iran_servers()[server_id], "name": server_id}
    
    await query.edit_message_text(f"⏳ دریافت لیست سرویس‌های بدون کرون‌جاب از {server['name']}...")
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await query.edit_message_text("❌ اتصال برقرار نشد.")
        return ConversationHandler.END

    stdin, stdout, _ = ssh.client.exec_command("crontab -l")
    crontab_output = stdout.read().decode()
    active_cron_ports = {m.group(1) for m in re.finditer(r"backhaul-(?:iran|kharej)(\d+)\.service", crontab_output)}

    service_pattern = "iran" if server_type == 'iran' else 'kharej'
    stdin, stdout, _ = ssh.client.exec_command(f"ls /root/backhaul-core/*{service_pattern}*.toml 2>/dev/null")
    toml_files = [os.path.basename(f) for f in stdout.read().decode().strip().splitlines()]
    all_possible_ports = {re.search(r'(\d+)', f).group(1) for f in toml_files if re.search(r'(\d+)', f)}
    
    available_ports = sorted(list(all_possible_ports - active_cron_ports))

    if not available_ports:
        text = "تمام سرویس‌های این سرور در حال حاضر کرون‌جاب فعال دارند."
        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"show_cron|{server_id}")]]
        await reply_or_edit(update, context, text, keyboard)
        return ConversationHandler.END
    else:
        text = "لطفاً پورت سرویسی که می‌خواهید برای آن کرون‌جاب بسازید را انتخاب کنید:"
        keyboard = []
        for port in available_ports:
            keyboard.append([InlineKeyboardButton(f"سرویس پورت {port}", callback_data=f"add_cron_port|{port}")])
        
        keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data=f"show_cron|{server_id}")])
        await reply_or_edit(update, context, text, keyboard)
        return ADD_CRON_CHOOSE_PORT
        
async def add_cron_get_port(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۲: دریافت پورت و درخواست انتخاب سرور ایران."""
    query = update.callback_query
    await query.answer()

    port = query.data.split("|")[1]
    context.user_data['cron_info']['port'] = port
    
    iran_servers = load_iran_servers()
    if not iran_servers:
        await reply_or_edit(update, context, "هیچ سرور ایرانی برای انتخاب وجود ندارد! ابتدا از منوی مدیریت سرور، آن را اضافه کنید.", [])
        return ConversationHandler.END
        
    text = f"پورت خارج: <b>{port}</b>\n\nلطفاً سرور ایران متناظر با این پورت را انتخاب کنید:"
    keyboard = [[InlineKeyboardButton(name, callback_data=f"add_cron_iran|{name}")] for name in iran_servers.keys()]
    
    server_info = context.user_data['cron_info']
    
    # --- ✅ تغییر اصلی اینجاست ---
    # کلید 'server_idx' به کلید صحیح 'id' تغییر کرد
    keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data=f"show_cron|{server_info['id']}")])
    
    await reply_or_edit(update, context, text, keyboard)
    return ADD_CRON_CHOOSE_IRAN
    
async def add_cron_get_iran_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله ۳: دریافت سرور ایران و نمایش جدول انتخاب ساعت برای زمان‌بندی."""
    query = update.callback_query
    await query.answer()
    
    iran_server_name = query.data.split("|")[1]
    context.user_data['cron_info']['iran_server'] = iran_server_name

    text = "لطفاً ساعت مورد نظر برای ریستارت خودکار (هر چند ساعت یکبار) را انتخاب کنید:"
    
    # --- ساخت کیبورد جدولی ۶ در ۴ ---
    keyboard = []
    row = []
    for hour in range(1, 25):  # اعداد ۱ تا ۲۴
        # فرمت کرون جاب برای "هر X ساعت" به شکل "0 */X * * *" است
        schedule_str = f"0 */{hour} * * *"
        button = InlineKeyboardButton(str(hour), callback_data=f"add_cron_schedule|{schedule_str}")
        row.append(button)
        
        # بعد از هر ۶ دکمه، یک ردیف جدید ایجاد کن
        if len(row) == 6:
            keyboard.append(row)
            row = []
    
    # افزودن دکمه لغو در انتها
    server_idx = context.user_data['cron_info']['id']
    keyboard.append([InlineKeyboardButton("🔙 لغو و بازگشت", callback_data=f"show_cron|{server_idx}")])
    
    await reply_or_edit(update, context, text, keyboard)
    return ADD_CRON_FINALIZE

async def add_cron_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحله نهایی: دریافت زمان‌بندی و ساخت تراکنشی کرون جاب با تنظیم UTC."""
    query = update.callback_query
    await query.answer()
    
    schedule = query.data.split("|")[1]
    
    cron_info = context.user_data['cron_info']
    server_idx = int(cron_info['id'])
    port = cron_info['port']
    iran_server_name = cron_info['iran_server']
    
    kharej_server = servers_cache[server_idx]
    iran_server_creds = load_iran_servers()[iran_server_name]
    
    await query.edit_message_text("⏳ شروع فرآیند افزودن تراکنشی کرون‌جاب...")

    kharej_ssh = PersistentSSHClient(kharej_server)
    if not kharej_ssh.client:
        await query.edit_message_text("❌ اتصال به سرور خارج برقرار نشد.")
        return ConversationHandler.END

    # --- ✅ تغییر اصلی اینجاست: افزودن CRON_TZ=UTC به دستورات ---
    
    # مرحله ۱: افزودن به سرور ایران
    await query.edit_message_text(f"⏳ مرحله ۱/۲: افزودن کرون‌جاب (UTC) به سرور ایران: {iran_server_name}...")
    iran_service_name = f"backhaul-iran{port}.service"
    iran_cron_line = f"{schedule} systemctl restart {iran_service_name}"
    # دستوری که CRON_TZ را اضافه و از تکرار آن جلوگیری می‌کند
    iran_add_cmd = f"'(crontab -l 2>/dev/null | grep -v -F \"{iran_service_name}\" | grep -v -F \"CRON_TZ=UTC\" ; echo \"CRON_TZ=UTC\"; echo \"{iran_cron_line}\") | crontab -'"
    jump_cmd = f"sshpass -p '{iran_server_creds['password']}' ssh -p {iran_server_creds['port']} -o StrictHostKeyChecking=no {iran_server_creds['user']}@{iran_server_creds['ip']} {iran_add_cmd}"
    
    stdin, stdout, stderr = kharej_ssh.client.exec_command(jump_cmd)
    if stdout.channel.recv_exit_status() != 0:
        error_msg = stderr.read().decode().strip()
        await query.edit_message_text(f"❌ خطا در افزودن کرون‌جاب به سرور ایران. عملیات متوقف شد.\n<pre>{error_msg}</pre>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    # مرحله ۲: افزودن به سرور خارج
    await query.edit_message_text(f"⏳ مرحله ۲/۲: افزودن کرون‌جاب (UTC) به سرور خارج: {kharej_server['name']}...")
    kharej_service_name = f"backhaul-kharej{port}.service"
    kharej_cron_line = f"{schedule} systemctl restart {kharej_service_name}"
    # دستوری که CRON_TZ را اضافه و از تکرار آن جلوگیری می‌کند
    kharej_add_cmd = f'(crontab -l 2>/dev/null | grep -v -F "{kharej_service_name}" | grep -v -F "CRON_TZ=UTC" ; echo "CRON_TZ=UTC" ; echo "{kharej_cron_line}") | crontab -'
    
    stdin, stdout, stderr = kharej_ssh.client.exec_command(kharej_add_cmd)
    if stdout.channel.recv_exit_status() == 0:
        links = load_cron_links()
        if kharej_server['name'] not in links:
            links[kharej_server['name']] = {}
        links[kharej_server['name']][port] = iran_server_name
        save_cron_links(links)
        final_text = "✅ کرون جاب (UTC) با موفقیت برای هر دو سرور ایجاد شد."
    else:
        error_msg = stderr.read().decode().strip()
        final_text = f"❌ خطا در افزودن کرون‌جاب به سرور خارج.\n<pre>{error_msg}</pre>"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت به منوی کرون‌جاب", callback_data=f"show_cron|{server_idx}")]]
    await reply_or_edit(update, context, final_text, keyboard)
    
    if 'cron_info' in context.user_data:
        del context.user_data['cron_info']
    return ConversationHandler.END
    
async def start_live_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
        context.chat_data['stop_status_update'] = False
        await query.edit_message_text(text="⏳ در حال آماده‌سازی نمایش زنده... لطفاً منتظر بمانید.")
        context.job_queue.run_once(
            periodic_status_updater, when=0, data={'message_id': query.message.id, 'message': query.message},
            chat_id=query.message.chat_id, name=f"status-updater-{query.message.chat_id}"
        )
    except BadRequest as e: print(f"Query expired: {e}")

async def stop_live_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['stop_status_update'] = True
    query = update.callback_query
    await query.answer("درخواست توقف ارسال شد...")
    
    # بلافاصله منوی اصلی را با پیام تایید نمایش می‌دهد
    await start(update, context, message_text="نمایش زنده متوقف شد.")

async def manage_servers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.chat_data['stop_status_update'] = True
    
    # ✅ چیدمان جدید دکمه‌ها طبق درخواست شما
    keyboard = [
        [
            InlineKeyboardButton("➕ افزودن سرور خارج", callback_data="add_server_start"),
            InlineKeyboardButton("➖  حذف سرور خارج", callback_data="delete_server_list")
        ],
        [
            InlineKeyboardButton("🔄 بروزرسانی سرور خارج", callback_data="update_server_list")
        ],
        [
            InlineKeyboardButton("🇮🇷 مدیریت سرورهای ایران", callback_data="manage_iran_servers")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "مدیریت سرورها | چه کاری می‌خواهید انجام دهید؟"
    
    # استفاده از تابع reply_or_edit برای نمایش تمیز منو
    await reply_or_edit(update, context, text, keyboard)

async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "لطفا اطلاعات سرور جدید را در یک خط و با فرمت زیر ارسال کنید:\n\n"
        "<pre>user@ip:port;password;name</pre>\n\n"
        "برای لغو، دستور /cancel را بفرستید."
    )
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="manage_servers_menu")]]
    await reply_or_edit(update, context, text, keyboard)
    return ADD_SERVER

async def add_server_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    await update.message.delete()
    parts = user_input.split(';')
    if len(parts) != 3 or '@' not in parts[0] or ':' not in parts[0]:
        await update.message.reply_text("فرمت وارد شده اشتباه است. لطفا دوباره تلاش کنید یا /cancel را بزنید.")
        return ADD_SERVER
    try:
        with open(SERVERS_FILE, 'a+') as f:
            f.seek(0)
            content = f.read()
            if len(content) > 0 and not content.endswith('\n'): f.write('\n')
            f.write(user_input)
        await start(update, context, message_text=f"✅ سرور <b>{parts[2]}</b> با موفقیت اضافه شد.")
    except Exception as e:
        await start(update, context, message_text=f"❌ خطا در ذخیره سرور: {e}")
    return ConversationHandler.END

async def delete_server_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = parse_servers()
    text = "کدام سرور را می‌خواهید حذف کنید؟"
    keyboard = []
    if not servers:
        text = "هیچ سروری برای حذف وجود ندارد."
    else:
        keyboard = [[InlineKeyboardButton(f"🗑️ {s['name']}", callback_data=f"delete_server_confirm|{s['name']}")] for s in servers]
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_servers_menu")])
    await reply_or_edit(update, context, text, keyboard)

async def delete_server_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, server_name_to_delete = query.data.split("|")
    servers = parse_servers()
    updated_servers = [s for s in servers if s['name'] != server_name_to_delete]
    with open(SERVERS_FILE, 'w') as f:
        for s in updated_servers:
            line = f"{s['user']}@{s['ip']}:{s['port']};{s['password']};{s['name']}\n"
            f.write(line)
    await query.answer("سرور حذف شد")
    await delete_server_list(update, context)

async def update_server_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = parse_servers()
    text = "کدام سرور را می‌خواهید بروزرسانی کنید؟"
    keyboard = []
    if not servers:
        text = "هیچ سروری برای بروزرسانی وجود ندارد."
    else:
        keyboard = [[InlineKeyboardButton(f"🔄 {s['name']}", callback_data=f"update_select|{s['name']}")] for s in servers]
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_servers_menu")])
    await reply_or_edit(update, context, text, keyboard)
    return UPDATE_SERVER_SELECT

async def update_server_ask_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    server_name = query.data.split("|")[1]
    context.user_data['server_to_update'] = server_name
    servers = parse_servers()
    server_details = next((s for s in servers if s['name'] == server_name), None)
    current_info = f"{server_details['user']}@{server_details['ip']}:{server_details['port']};{server_details['password']};{server_details['name']}"
    text = (
        f"اطلاعات جدید را برای سرور <b>{server_name}</b> وارد کنید.\n"
        f"<i>اطلاعات فعلی:</i>\n<pre>{current_info}</pre>\n"
        "برای لغو، دستور /cancel را بفرستید."
    )
    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="update_server_list")]]
    await reply_or_edit(update, context, text, keyboard)
    return UPDATE_SERVER_INFO

async def update_server_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_info_str = update.message.text.strip()
    await update.message.delete()
    server_to_update = context.user_data.get('server_to_update')
    parts = new_info_str.split(';')
    if len(parts) != 3 or '@' not in parts[0] or ':' not in parts[0]:
        await update.message.reply_text("فرمت وارد شده اشتباه است. لطفا دوباره تلاش کنید یا /cancel را بزنید.")
        return UPDATE_SERVER_INFO
    servers = parse_servers()
    updated_lines = []
    for server in servers:
        if server['name'] == server_to_update:
            updated_lines.append(new_info_str + "\n")
        else:
            line = f"{server['user']}@{server['ip']}:{server['port']};{server['password']};{server['name']}\n"
            updated_lines.append(line)
    with open(SERVERS_FILE, 'w') as f: f.writelines(updated_lines)
    del context.user_data['server_to_update']
    await start(update, context, message_text=f"✅ سرور <b>{server_to_update}</b> با موفقیت بروزرسانی شد.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """گفتگوی فعلی را لغو کرده، پیام کاربر را حذف و به منوی اصلی بازمی‌گردد."""
    
    # ✅ حذف پیام /cancel که کاربر ارسال کرده
    if update.message:
        try:
            await update.message.delete()
        except BadRequest:
            pass # اگر پیام از قبل حذف شده بود، مشکلی نیست

    # پاک کردن هرگونه اطلاعات ذخیره شده موقت در حافظه ربات
    for key in ['server_to_update', 'smart_reset_info', 'iran_server_name']:
        if key in context.user_data:
            del context.user_data[key]
    
    # بازگشت به منوی اصلی با نمایش پیام لغو عملیات
    await start(update, context, message_text="عملیات لغو شد.")
    
    return ConversationHandler.END

async def manage_iran_servers(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    """منوی مدیریت سرورهای ایران را نمایش داده و هر گفتگوی قبلی را خاتمه می‌دهد."""
    query = update.callback_query
    if query:
        await query.answer()

    iran_servers = load_iran_servers()
    base_text = "مدیریت سرورهای ایران (برای ریست هوشمند)"
    display_text = f"<i>{message_text}</i>\n\n{base_text}" if message_text else base_text
    
    keyboard = []
    if not iran_servers:
        display_text += "\n\nهیچ سرور ایرانی ذخیره نشده است."
    else:
        display_text += "\n\nلیست سرورهای ذخیره شده:"
        for name in iran_servers:
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف {name}", callback_data=f"delete_iran_server|{name}")])

    keyboard.append([InlineKeyboardButton("➕ افزودن سرور ایران", callback_data="add_iran_server_start")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="manage_servers_menu")])
    
    await reply_or_edit(update, context, display_text, keyboard)

    # --- ✅ تغییر اصلی اینجاست ---
    # این خط تضمین می‌کند که هر بار این منو نمایش داده می‌شود،
    # هر گفتگوی نیمه‌کاره‌ای بسته شده و مشکل هنگ کردن حل می‌شود.
    return ConversationHandler.END

async def add_iran_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "لطفا یک نام برای این سرور ایران وارد کنید (مثلا: Shatel-Tehran):"
    keyboard = [[InlineKeyboardButton("🔙 لغو", callback_data="manage_iran_servers")]]
    await reply_or_edit(update, context, text, keyboard)
    return ADD_IRAN_SERVER_NAME

async def add_iran_server_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    await update.message.delete()
    context.user_data['iran_server_name'] = name
    text = (f"نام سرور: <b>{name}</b>\n\nحالا اطلاعات اتصال را با فرمت زیر ارسال کنید:\n<pre>USER@IP:PORT:PASSWORD</pre>")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    return ADD_IRAN_SERVER_CREDS

async def add_iran_server_get_creds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creds_str = update.message.text.strip()
    await update.message.delete()
    name = context.user_data.get('iran_server_name')
    try:
        connection_part, password = creds_str.rsplit(':', 1)
        user_part, ip_port_part = connection_part.split('@', 1)
        ip, iran_port_str = ip_port_part.rsplit(':', 1)
        if not all([user_part, ip, iran_port_str, password]): raise ValueError("A field is empty.")
    except (ValueError, IndexError):
        await update.message.reply_text("فرمت اشتباه است. لطفا دوباره تلاش کنید.")
        return ADD_IRAN_SERVER_CREDS
    
    iran_servers = load_iran_servers()
    iran_servers[name] = {"user": user_part, "ip": ip, "port": iran_port_str, "password": password}
    save_iran_servers(iran_servers)
    
    if 'iran_server_name' in context.user_data:
        del context.user_data['iran_server_name']
    
    # ✅ تغییر اصلی: به جای ارسال پیام، منوی مدیریت را با پیام موفقیت نمایش می‌دهیم
    await manage_iran_servers(update, context, message_text=f"✅ سرور ایران با نام <b>{name}</b> با موفقیت ذخیره شد.")
    
    return ConversationHandler.END

async def delete_iran_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, name_to_delete = update.callback_query.data.split("|")
    iran_servers = load_iran_servers()
    if name_to_delete in iran_servers:
        del iran_servers[name_to_delete]
        save_iran_servers(iran_servers)
    await update.callback_query.answer(f"سرور {name_to_delete} حذف شد.")
    await manage_iran_servers(update, context)

async def smart_reset_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, index_str, port = query.data.split("|")
    server = servers_cache[int(index_str)]
    text = f"⏳ بررسی وضعیت سرویس ریست هوشمند برای پورت {port}..."
    await reply_or_edit(update, context, text, [])
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await reply_or_edit(update, context, f"❌ عدم امکان اتصال به سرور {server['name']}.", [])
        return
        
    is_active_cmd = f"systemctl is-active guardian@{port}.service"
    stdin, stdout, stderr = ssh.client.exec_command(is_active_cmd)
    is_active_exit_code = stdout.channel.recv_exit_status()
    keyboard, text = [], ""
    callback_prefix = f"smart_reset|{index_str}|{port}"
    if is_active_exit_code == 0:
        text = f"✅ سرویس ریست هوشمند برای پورت {port} **فعال** است."
        keyboard.append([InlineKeyboardButton("🔴 غیرفعال سازی", callback_data=f"{callback_prefix}|deactivate")])
        keyboard.append([InlineKeyboardButton("🔄 بروزرسانی کانفیگ", callback_data=f"{callback_prefix}|update")])
        keyboard.append([InlineKeyboardButton("📄 مشاهده لاگ", callback_data=f"{callback_prefix}|log")])
    else:
        file_exists_cmd = f"ls /etc/systemd/system/guardian@{port}.service"
        stdin, stdout, stderr = ssh.client.exec_command(file_exists_cmd)
        file_exists_exit_code = stdout.channel.recv_exit_status()
        if file_exists_exit_code == 0:
            text = f"⚠️ سرویس ریست هوشمند برای پورت {port} **خراب (Failed)** شده است."
            keyboard.append([InlineKeyboardButton("🔴 غیرفعال سازی (پاک کردن سرویس خراب)", callback_data=f"{callback_prefix}|deactivate")])
            keyboard.append([InlineKeyboardButton("📄 مشاهده لاگ خطا", callback_data=f"{callback_prefix}|log")])
        else:
            text = f"❌ سرویس ریست هوشمند برای پورت {port} **غیرفعال** است."
            keyboard.append([InlineKeyboardButton("🟢 فعالسازی", callback_data=f"{callback_prefix}|activate")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"svc|{index_str}")])
    await reply_or_edit(update, context, text, keyboard)
    return ConversationHandler.END

async def smart_reset_activate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, index_str, port, action = query.data.split("|")
    
    # اگر بروزرسانی بود، ابتدا سرویس قدیمی را پاک می‌کنیم
    if action == 'update':
        await query.edit_message_text("⏳ در حال حذف کانفیگ قدیمی برای بروزرسانی...")
        server = servers_cache[int(index_str)]
        ssh = PersistentSSHClient(server)
        if ssh.client:
            commands = [
                f"systemctl stop guardian@{port}.service",
                f"systemctl disable guardian@{port}.service",
                f"rm -f /etc/systemd/system/guardian@{port}.service",
                "systemctl daemon-reload"
            ]
            for cmd in commands:
                ssh.client.exec_command(cmd)
                await asyncio.sleep(0.5)
        await query.edit_message_text("✅ کانفیگ قدیمی حذف شد. لطفاً سرور ایران را انتخاب کنید...")
        await asyncio.sleep(1)

    iran_servers = load_iran_servers()
    if not iran_servers:
        text = "هیچ سرور ایرانی ذخیره نشده است. لطفا ابتدا از منوی 'مدیریت سرورها' یک سرور ایران اضافه کنید."
        keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"smart_reset_menu|{index_str}|{port}")]]
        await reply_or_edit(update, context, text, keyboard)
        return ConversationHandler.END

    context.user_data['smart_reset_info'] = {'index': index_str, 'port': port}
    
    keyboard = []
    for name in iran_servers:
        keyboard.append([InlineKeyboardButton(name, callback_data=f"select_iran|{name}")])
    
    cancel_callback_data = f"smart_reset_menu|{index_str}|{port}"
    keyboard.append([InlineKeyboardButton("🔙 لغو", callback_data=cancel_callback_data)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "لطفا سرور ایران متناظر با این سرویس را انتخاب کنید:"
    await reply_or_edit(update, context, text, keyboard)
    
    return SMART_RESET_CHOOSE_IRAN
    
async def smart_reset_activate_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    info = context.user_data.get('smart_reset_info')
    if not info:
        await start(update, context)
        return ConversationHandler.END
        
    index, port = int(info['index']), info['port']
    server = servers_cache[index]
    _, selected_iran_name = query.data.split("|")
    iran_servers = load_iran_servers()
    iran_server_creds = iran_servers[selected_iran_name]

    await reply_or_edit(update, context, "⏳ شروع فرآیند نصب سرویس ریست هوشمند...", [])
    
    ssh = PersistentSSHClient(server)
    if not ssh.client:
        await reply_or_edit(update, context, f"❌ اتصال به سرور {server['name']} برقرار نشد.", [])
        return ConversationHandler.END

    iran_config_content = (f"[iran]\\nuser = {iran_server_creds['user']}\\nip = {iran_server_creds['ip']}\\n"
                           f"port = {iran_server_creds['port']}\\npassword = {iran_server_creds['password']}\\n")
    systemd_service_content = SYSTEMD_SERVICE_TEMPLATE.replace('%i', port)
    create_guardian_script_cmd = f"""
cat << 'GUARDIAN_EOF' > /root/guardian.py
{GUARDIAN_SCRIPT_CONTENT}
GUARDIAN_EOF
"""
    
    commands_with_desc = [
        ("apt-get update", "مرحله ۱ از ۹: بروزرسانی لیست بسته‌ها..."),
        ("apt-get install -y sshpass", "مرحله ۲ از ۹: نصب ابزار sshpass..."),
        (create_guardian_script_cmd, "مرحله ۳ از ۹: ایجاد اسکریپت ناظر..."),
        ("chmod +x /root/guardian.py", "مرحله ۴ از ۹: تنظیم دسترسی‌های اسکریپت..."),
        (f"echo -e '{iran_config_content}' > /root/iran_creds_{port}.conf", "مرحله ۵ از ۹: ایجاد فایل کانفیگ..."),
        (f"chmod 600 /root/iran_creds_{port}.conf", "مرحله ۶ از ۹: امن‌سازی فایل کانفیگ..."),
        (f"echo '{systemd_service_content}' > /etc/systemd/system/guardian@{port}.service", "مرحله ۷ از ۹: ایجاد سرویس..."),
        ("systemctl daemon-reload", "مرحله ۸ از ۹: بارگذاری مجدد systemd..."),
        (f"systemctl enable --now guardian@{port}.service", "مرحله ۹ از ۹: فعال و اجرا کردن سرویس...")
    ]
    
    for i, (cmd, desc) in enumerate(commands_with_desc):
        print(f"[SmartReset INFO] On server '{server['name']}': Executing step {i+1}")
        await reply_or_edit(update, context, f"⏳ {desc}", [])
        stdin, stdout, stderr = ssh.client.exec_command(cmd, timeout=120)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            error_output = stderr.read().decode().strip()
            await reply_or_edit(update, context, f"❌ خطا در مرحله {i+1}:\n`{cmd}`\n\n**جزئیات:**\n`{error_output}`", [])
            del context.user_data['smart_reset_info']
            return ConversationHandler.END
            
    await reply_or_edit(update, context, "⏳ مرحله آخر: بررسی پایداری سرویس پس از ۳ ثانیه...", [])
    await asyncio.sleep(3)
    
    final_check_cmd = f"systemctl is-active guardian@{port}.service"
    stdin, stdout, stderr = ssh.client.exec_command(final_check_cmd)
    exit_status = stdout.channel.recv_exit_status()

    if exit_status == 0:
        final_status = stdout.read().decode().strip()
        
        # متن موفقیت را با منوی نهایی ادغام می‌کنیم
        text = f"✅ سرویس با موفقیت نصب و فعال شد.\nوضعیت: `{final_status}`\n\n"
        text += f"✅ سرویس ریست هوشمند برای پورت {port} **فعال** است."
        
        # تمام دکمه‌های مدیریت را همینجا می‌سازیم
        callback_prefix = f"smart_reset|{index}|{port}"
        keyboard = [
            [InlineKeyboardButton("🔴 غیرفعال سازی", callback_data=f"{callback_prefix}|deactivate")],
            [InlineKeyboardButton("🔄 بروزرسانی کانفیگ", callback_data=f"{callback_prefix}|update")],
            [InlineKeyboardButton("📄 مشاهده لاگ", callback_data=f"{callback_prefix}|log")],
            [InlineKeyboardButton("🔙 بازگشت به لیست سرویس‌ها", callback_data=f"svc|{index}")]
        ]
        
        # و در نهایت، یک پیام واحد با متن کامل و تمام دکمه‌ها ارسال می‌کنیم
        await reply_or_edit(update, context, text, keyboard)
    else:
        debug_cmd = f"journalctl -u guardian@{port}.service -n 15 --no-pager"
        stdin_debug, stdout_debug, _ = ssh.client.exec_command(debug_cmd)
        service_logs = stdout_debug.read().decode().strip()
        error_details = f"سرویس فعال نشد. **لاگ:**\n<pre>{service_logs}</pre>"
        await reply_or_edit(update, context, f"❌ خطا: سرویس پایدار نیست.\n\n{error_details}", [])

    del context.user_data['smart_reset_info']
    return ConversationHandler.END
    
async def smart_reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر برای اقدامات مربوط به سرویس ریست هوشمند (لاگ، غیرفعال‌سازی، بروزرسانی)."""
    query = update.callback_query
    await query.answer()
    
    try:
        callback_parts = query.data.split("|")
        action = callback_parts[-1]
        index_str = callback_parts[1]
        port = callback_parts[2]
        
        # اگر کاربر دکمه "بروزرسانی" را بزند، او را به ابتدای فلو فعالسازی می‌فرستیم
        if action == 'update':
            await smart_reset_activate_start(update, context)
            return ConversationHandler.END # گفتگو را به درستی خاتمه می‌دهیم
            
        server = servers_cache[int(index_str)]
        ssh = PersistentSSHClient(server)
        if not ssh.client:
            await query.edit_message_text(f"❌ عدم امکان اتصال به سرور {server['name']}.")
            return

        if action == "log":
            command = f"tail -n 20 /var/log/guardian_{port}.log"
            stdin, stdout, stderr = ssh.client.exec_command(command)
            log_content = stdout.read().decode().strip() or "فایل لاگ خالی است یا وجود ندارد."
            text = f"📄 لاگ سرویس ریست هوشمند (پورت {port}):\n\n<pre>{log_content}</pre>"
            keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"smart_reset_menu|{index_str}|{port}")]]
            await reply_or_edit(update, context, text, keyboard)

        elif action == "deactivate":
            await query.edit_message_text("⏳ در حال غیرفعال سازی و حذف سرویس...")
            
            commands = [
                f"systemctl stop guardian@{port}.service",
                f"systemctl disable guardian@{port}.service",
                f"rm -f /etc/systemd/system/guardian@{port}.service",
                f"rm -f /root/guardian.py",
                f"rm -f /root/iran_creds_{port}.conf",
                "systemctl daemon-reload"
            ]
            
            for i, cmd in enumerate(commands):
                await query.edit_message_text(f"⏳ در حال اجرای مرحله {i+1} از {len(commands)}...")
                stdin, stdout, stderr = ssh.client.exec_command(cmd, timeout=20)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    print(f"[Deactivate] Warning: Command failed: {cmd} -> {stderr.read().decode()}")

            # --- ✅ تغییر اصلی اینجاست ---
            # پس از اتمام کار، پیام موفقیت با دکمه بازگشت نمایش داده می‌شود
            final_text = "✅ سرویس ریست هوشمند با موفقیت غیرفعال و حذف شد."
            keyboard = [[InlineKeyboardButton("🔙 بازگشت به منوی ریست هوشمند", callback_data=f"smart_reset_menu|{index_str}|{port}")]]
            await reply_or_edit(update, context, final_text, keyboard)

    except Exception as e:
        print(f"Error in smart_reset_handler: {e}")
        # در صورت بروز هر خطایی، به کاربر یک پیام عمومی می‌دهیم
        await query.message.reply_text("یک خطای پیش‌بینی نشده رخ داد. لطفاً دوباره تلاش کنید.")
        
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    context.chat_data['stop_status_update'] = True

    if data == "services":
        servers = parse_servers()
        text = "🌐 یکی از سرورها را برای مدیریت سرویس‌ها انتخاب کنید:"
        keyboard = []
        for i, s in enumerate(servers):
            # ✅ دریافت اطلاعات کشور و ساخت متن جدید برای دکمه
            country_name_en, flag = get_country_info(s['ip'], context)
            country_name_fa = COUNTRY_NAMES_FA.get(country_name_en, country_name_en)
            button_text = f"{flag} {s['name']} ({country_name_fa})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"svc|{i}")])

        keyboard.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")])
        await reply_or_edit(update, context, text, keyboard)

    elif data.startswith("svc|"):
            _, index_str = data.split("|")
            index = int(index_str)
            server = servers_cache[index]
            ssh = PersistentSSHClient(server)
            
            if not ssh.client:
                keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="services")]]
                await reply_or_edit(update, context, f"❌ اتصال به <b>{server['name']}</b> برقرار نشد.", keyboard)
                return

            country_name_en, flag = get_country_info(server['ip'], context)
            country_name_fa = COUNTRY_NAMES_FA.get(country_name_en, country_name_en)
            text = f"مدیریت سرویس های بکهال\n{flag} <b>{server['name']} ({country_name_fa})</b>"
            
            stdin, stdout, _ = ssh.client.exec_command("ls /root/backhaul-core/*.toml 2>/dev/null")
            all_files = [os.path.basename(f) for f in stdout.read().decode().strip().splitlines()]

            if not all_files:
                text += "\n\n❌ هیچ سرویس بکهالی یافت نشد."
                keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="services")]]
            else:
                keyboard = []
                for f in all_files:
                    match = re.search(r'(\d+)\.toml$', f)
                    port = match.group(1) if match else "???"
                    button_text = f"سرویس پورت {port}"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=f"action_menu|{index_str}|{f}")])
                
                # ✅ دکمه جدید مدیریت تانل در اینجا اضافه شد
                keyboard.append([InlineKeyboardButton("🔗 مدیریت تانل بکهال", callback_data=f"tunnel_menu|{index_str}")])
                keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست سرورها", callback_data="services")])
            
            await reply_or_edit(update, context, text, keyboard)

    elif data.startswith("action_menu|"):
        _, index_str, filename = data.split("|")
        server = servers_cache[int(index_str)]
        match = re.search(r'(\d+)\.toml$', filename)
        port = match.group(1) if match else "???"
        text = f"سرویس <b>{server['name']}</b> | پورت: <b>{port}</b>\n\nلطفا اقدام مورد نظر را انتخاب کنید:"
        keyboard = [
            [
                InlineKeyboardButton("🔄 راه اندازی مجدد", callback_data=f"action|restart|{index_str}|{filename}"),
                InlineKeyboardButton("📊 وضعیت", callback_data=f"action|status|{index_str}|{filename}")
            ],
            [InlineKeyboardButton("📝 مشاهده لاگ", callback_data=f"action|log|{index_str}|{filename}")]
        ]
        if 'kharej' in filename:
            keyboard.append([
                InlineKeyboardButton("🤖 سرویس ریست هوشمند", callback_data=f"smart_reset_menu|{index_str}|{port}")
            ])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت به لیست سرویس‌ها", callback_data=f"svc|{index_str}")])
        await reply_or_edit(update, context, text, keyboard)

    elif data.startswith("action|"):
        _, action_type, index_str, filename = data.split("|")
        server = servers_cache[int(index_str)]
        base_name = filename.replace(".toml", "")
        service_name = f"backhaul-{base_name}.service"
        text = f"⏳ در حال اجرای دستور <b>{action_type}</b> روی سرویس <b>{base_name}</b>..."
        await reply_or_edit(update, context, text, [])

        ssh = PersistentSSHClient(server)
        if not ssh.client:
            await reply_or_edit(update, context, f"❌ اتصال به {server['name']} برقرار نشد.", [])
            return
            
        command = ""
        if action_type == 'log': command = f"journalctl -u {service_name} -n 20 --no-pager"
        elif action_type == 'status': command = f"systemctl status {service_name}"
        elif action_type == 'restart': command = f"systemctl restart {service_name} && systemctl status {service_name}"
        
        stdin, stdout, stderr = ssh.client.exec_command(command)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        result_text = output if output else error
        if not result_text: result_text = "دستور اجرا شد اما خروجی نداشت."
        match = re.search(r'(\d+)\.toml$', filename)
        port = match.group(1) if match else "???"
        action_map = {'log': '📝 لاگ', 'status': '📊 وضعیت', 'restart': '🔄 نتیجه ریستارت'}
        action_title = action_map.get(action_type, action_type.capitalize())
        text = f"{action_title} تانل <b>{server['name']}</b> | پورت: <b>{port}</b>:\n\n<pre>{result_text}</pre>"
        keyboard = [[InlineKeyboardButton("🔙 بازگشت به منوی سرویس", callback_data=f"action_menu|{index_str}|{filename}")]]
        await reply_or_edit(update, context, text, keyboard)

def main():
    """اجرای ربات، بررسی لایسنس از راه دور و تنظیم تمام هندلرها."""
    global IS_LICENSED, g_license_info
    
    # --- ✅ بررسی لایسنس از راه دور با ساختار جدید ---
    LICENSE_URL = "http://license.salamatpaya.com:8080/license"
    license_data = get_remote_license(LICENSE_URL)

    if license_data and "licenses" in license_data:
        try:
            current_ip_response = requests.get("https://api.ipify.org", timeout=5)
            current_ip_response.raise_for_status()
            current_ip = current_ip_response.text.strip()
            print(f"[License Check] Bot's current IP is: {current_ip}")

            # در لیست لایسنس‌ها به دنبال IP فعلی می‌گردیم
            found_license = False
            for license_item in license_data["licenses"]:
                if license_item.get("ip") == current_ip:
                    found_license = True
                    expiry_date = license_item.get("expiry_date", "2000-01-01")
                    if check_expiry_license(expiry_date):
                        IS_LICENSED = True
                        # اطلاعات لایسنس معتبر را برای نمایش آماده می‌کنیم
                        g_license_info = f"<b>IP مجاز:</b> <code>{current_ip}</code>\n<b>تاریخ انقضا:</b> <code>{expiry_date}</code>"
                    else:
                        g_license_info = f"❌ لایسنس برای IP <code>{current_ip}</code> در تاریخ <code>{expiry_date}</code> منقضی شده است."
                    break # پس از پیدا کردن IP، از حلقه خارج می‌شویم
            
            if not found_license:
                g_license_info = f"❌ IP شما (<code>{current_ip}</code>) در لیست مجاز یافت نشد."

        except Exception as e:
            print(f"[License Check] Could not verify license: {e}")
            g_license_info = "⚠️ خطا در اتصال به سرور بررسی لایسنس."
    else:
        g_license_info = "⚠️ فایل لایسنس یافت نشد یا ساختار آن صحیح نیست."
    
    if IS_LICENSED:
        print("✅ لایسنس معتبر است. ربات در حالت عملیاتی کامل قرار دارد.")
    else:
        print(f"❌ خطای لایسنس: {g_license_info}")

    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        job_queue = app.job_queue
        # تابع check_license_periodically را هر 60 ثانیه یک بار اجرا می‌کند
        # اولین اجرا 10 ثانیه پس از بالا آمدن ربات خواهد بود
        job_queue.run_repeating(check_license_periodically, interval=86400, first=10)
                
        # --- تعریف Conversation Handlers ---
        add_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(add_server_start, pattern='^add_server_start$')],
            states={ ADD_SERVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_receive)] },
            fallbacks=[CommandHandler('cancel', cancel)], per_user=True, per_chat=True
        )
        update_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(update_server_list, pattern='^update_server_list$')],
            states={
                UPDATE_SERVER_SELECT: [CallbackQueryHandler(update_server_ask_info, pattern='^update_select\|.*$')],
                UPDATE_SERVER_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_server_receive)],
            },
            fallbacks=[CommandHandler('cancel', cancel)], per_user=True, per_chat=True
        )
        add_iran_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(add_iran_server_start, pattern='^add_iran_server_start$')],
            states={
                ADD_IRAN_SERVER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_iran_server_get_name)],
                ADD_IRAN_SERVER_CREDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_iran_server_get_creds)],
            },
            fallbacks=[CallbackQueryHandler(manage_iran_servers, pattern='^manage_iran_servers$'), CommandHandler('cancel', cancel)],
            per_user=True, per_chat=True
        )
        add_tunnel_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(add_tunnel_start, pattern='^add_tunnel\|.*$')],
            states={
                ADD_TUNNEL_GET_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tunnel_get_port)],
                ADD_TUNNEL_GET_IRAN_SERVER: [CallbackQueryHandler(add_tunnel_get_iran_server, pattern='^add_tunnel_iran\|.*$')],
                ADD_TUNNEL_GET_TRANSPORT: [CallbackQueryHandler(add_tunnel_get_transport, pattern='^add_tunnel_transport\|.*$')],
                ADD_TUNNEL_GET_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tunnel_get_token)],
                ADD_TUNNEL_GET_CHANNEL_SIZE: [CallbackQueryHandler(add_tunnel_get_channel_size, pattern='^add_tunnel_chsize\|.*$')],
                ADD_TUNNEL_GET_CONN_POOL: [CallbackQueryHandler(add_tunnel_get_connection_pool, pattern='^add_tunnel_pool\|.*$')],
                ADD_TUNNEL_GET_MUX_CON: [CallbackQueryHandler(add_tunnel_get_mux_con, pattern='^add_tunnel_muxcon\|.*$')],
                ADD_TUNNEL_GET_HEARTBEAT: [CallbackQueryHandler(add_tunnel_get_heartbeat, pattern='^add_tunnel_heartbeat\|.*$')],
                ADD_TUNNEL_GET_MUX_FRAMESIZE: [CallbackQueryHandler(add_tunnel_get_mux_framesize, pattern='^add_tunnel_framesize\|.*$')],
                ADD_TUNNEL_GET_MUX_RECIEVEBUFFER: [CallbackQueryHandler(add_tunnel_get_mux_recievebuffer, pattern='^add_tunnel_recievebuffer\|.*$')],
                ADD_TUNNEL_GET_MUX_STREAMBUFFER: [CallbackQueryHandler(add_tunnel_get_mux_streambuffer, pattern='^add_tunnel_streambuffer\|.*$')],
                ADD_TUNNEL_GET_NODELAY: [CallbackQueryHandler(add_tunnel_get_nodelay, pattern='^add_tunnel_nodelay\|.*$')],
                ADD_TUNNEL_GET_SNIFFER: [CallbackQueryHandler(add_tunnel_get_sniffer, pattern='^add_tunnel_sniffer\|.*$')],
                ADD_TUNNEL_GET_WEB_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tunnel_get_web_port)],
                ADD_TUNNEL_GET_PROXY_PROTOCOL: [CallbackQueryHandler(add_tunnel_get_proxy_protocol, pattern='^add_tunnel_proxy\|.*$')],
                ADD_TUNNEL_GET_FORWARD_PORTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tunnel_finalize)],
            },
            fallbacks=[CommandHandler('cancel', cancel), CallbackQueryHandler(cancel, pattern='^cancel_conv$')],
            per_user=True, per_chat=True
        )
        add_cron_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(add_cron_start, pattern='^add_cron\|.*$')],
            states={
                ADD_CRON_CHOOSE_PORT: [CallbackQueryHandler(add_cron_get_port, pattern='^add_cron_port\|.*$')],
                ADD_CRON_CHOOSE_IRAN: [CallbackQueryHandler(add_cron_get_iran_server, pattern='^add_cron_iran\|.*$')],
                ADD_CRON_FINALIZE: [CallbackQueryHandler(add_cron_finalize, pattern='^add_cron_schedule\|.*$')],
            },
            fallbacks=[
                CallbackQueryHandler(show_cron_for_server, pattern='^show_cron\|.*$'),
                CommandHandler('cancel', cancel)
            ],
            per_user=True, per_chat=True
        )
        smart_reset_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(smart_reset_activate_start, pattern='^smart_reset\\|.*\\|(activate|update)$')],
            states={
                SMART_RESET_CHOOSE_IRAN: [CallbackQueryHandler(smart_reset_activate_receive, pattern='^select_iran\|.*$')],
            },
            fallbacks=[CallbackQueryHandler(smart_reset_menu, pattern='^smart_reset_menu\|.*$'), CommandHandler('cancel', cancel)],
            per_user=True, per_chat=True
        )
        
        async def license_check_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, next_handler):
            if not IS_LICENSED:
                await license_error_handler(update, context)
                return ConversationHandler.END # در صورت لایسنس نامعتبر، هر گفتگویی را خاتمه بده
            return await next_handler(update, context)

        # --- ساخت هندلرهای میانی ---
        async def start_licensed(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return await license_check_wrapper(update, context, start)

        async def button_handler_licensed(update: Update, context: ContextTypes.DEFAULT_TYPE):
            return await license_check_wrapper(update, context, button_handler)

            
        # --- ثبت تمام هندلرها ---
        app.add_handler(CommandHandler("start", start_licensed))
        app.add_handler(add_conv)
        app.add_handler(update_conv)
        app.add_handler(add_iran_conv)
        app.add_handler(smart_reset_conv)
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, cron_menu), pattern='^cron_menu$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, start_live_status), pattern='^status_live$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, stop_live_status), pattern='^stop_update$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, show_cron_for_server), pattern='^show_cron_for_server\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, manage_servers_menu), pattern='^manage_servers_menu$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_server_list), pattern='^delete_server_list$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_server_confirm), pattern='^delete_server_confirm\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, remove_cron_menu), pattern='^remove_cron_menu\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, remove_cron_action), pattern='^remove_cron_action\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, manage_iran_servers), pattern='^manage_iran_servers$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_iran_server), pattern='^delete_iran_server\|.*$'))
        app.add_handler(CallbackQueryHandler(recheck_license_handler, pattern='^recheck_license$'))
        app.add_handler(add_cron_conv)
        app.add_handler(add_tunnel_conv)
        app.add_handler(CallbackQueryHandler(show_license_handler, pattern='^show_license$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_tunnel_confirmation), pattern='^delete_tunnel_confirm\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_tunnel_action), pattern='^delete_tunnel_action\|.*$'))        
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_tunnel_confirmation), pattern=f"^delete_tunnel_confirm\|.*$"))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, delete_tunnel_action), pattern=f"^delete_tunnel_action\|.*$"))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, manage_tunnel_menu), pattern=f"^manage_tunnel\|.*$"))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, tunnel_menu), pattern='^tunnel_menu\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, show_cron_for_server), pattern='^show_cron\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, smart_reset_menu), pattern='^smart_reset_menu\|.*$'))
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, smart_reset_handler), pattern='^smart_reset\\|.*\\|(deactivate|log)$'))      
        app.add_handler(CallbackQueryHandler(lambda u, c: license_check_wrapper(u, c, start), pattern='^main_menu$'))
        app.add_handler(CallbackQueryHandler(button_handler))

        print("🚀 ربات در حال آماده سازی برای اجرا...")
        app.run_polling()

    except Exception as e:
        print("\n❌❌❌ یک خطای پیش‌بینی نشده باعث توقف ربات شد! ❌❌❌")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()