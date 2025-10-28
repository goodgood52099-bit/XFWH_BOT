import os
import json
import requests
from flask import Flask, request, jsonify
from datetime import datetime, time as dt_time
import threading
import time
import traceback

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # pip install backports.zoneinfo

# -------------------------------
# 設定區
# -------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ 請設定 BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
ADMIN_IDS = [7236880214,7807558825,7502175264]
TZ = ZoneInfo("Asia/Taipei")

asked_shifts = set()
pending_pay = {}  # 暫存服務員輸入金額流程

# -------------------------------
# 群組管理與自動辨識
# -------------------------------
GROUP_CONFIG_PATH = os.path.join(DATA_DIR, "group_config.json")

def load_group_config():
    if os.path.exists(GROUP_CONFIG_PATH):
        with open(GROUP_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"staff_group_id": None, "business_groups": []}

def save_group_config(config):
    with open(GROUP_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

group_config = load_group_config()
staff_group_id = group_config.get("staff_group_id")
business_groups = set(group_config.get("business_groups", []))

# -------------------------------
# JSON 存取
# -------------------------------

def data_path_for(day): return os.path.join(DATA_DIR, f"{day}.json")

def ensure_today_file(workers=3):
    today = datetime.now(TZ).date().isoformat()
    path = data_path_for(today)
    now = datetime.now(TZ)
    if os.path.exists(path):
        data = load_json_file(path)
        if data.get("date") != today:
            os.remove(path)
    if not os.path.exists(path):
        shifts = []
        for h in range(13, 23):
            shift_time = dt_time(h, 0)
            shift_dt = datetime.combine(now.date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
        save_json_file(path, {"date": today, "shifts": shifts, "候補":[]})
    return path

def load_json_file(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def find_shift(shifts, hhmm):
    for s in shifts:
        if s["time"] == hhmm: return s
    return None

# -------------------------------
# Telegram 發送
# -------------------------------

def send_request(method, payload): return requests.post(API_URL + method, json=payload).json()

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    return send_request("sendMessage", payload)

# -------------------------------
# 主選單與按鈕
# -------------------------------

def send_main_menu(chat_id, role):
    if role == "business":
        keyboard = {
            "keyboard": [[{"text": "📋 查看列表"}], [{"text": "🕒 今日時段"}], [{"text": "👥 候補名單"}]],
            "resize_keyboard": True,
            "one_time_keyboard": False
        }
        send_message(chat_id, "🏢 業務群主選單：", reply_markup=keyboard)
    elif role == "staff":
        keyboard = {
            "keyboard": [[{"text": "📋 查看列表"}], [{"text": "💰 結帳紀錄"}], [{"text": "📢 全部公告"}]],
            "resize_keyboard": True,
            "one_time_keyboard": False
        }
        send_message(chat_id, "🧑‍🔧 服務員群主選單：", reply_markup=keyboard)

def generate_inline_buttons(group, hhmm, name):
    buttons = []
    if group == "business":
        buttons.append([{"text":"客到","callback_data":f"business|arrival|{hhmm}|{name}"}])
    elif group == "staff":
        buttons.append([
            {"text":"上","callback_data":f"staff|up|{hhmm}|{name}"},
            {"text":"完成","callback_data":f"staff|pay|{hhmm}|{name}"}
        ])
    return {"inline_keyboard": buttons}
