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

# 設定服務員群組指令
def register_group(chat_id, user_id, text):
    global staff_group_id, business_groups, group_config

    if text == "/set_staff_group" and user_id in ADMIN_IDS:
        staff_group_id = chat_id
        group_config["staff_group_id"] = chat_id
        save_group_config(group_config)
        send_message(chat_id, f"✅ 此群已設定為【服務員群組】\n群組 ID：`{chat_id}`")
        send_main_menu(chat_id, "staff")
        print(f"[INFO] Staff group set to {chat_id}")
        return "staff"

    if staff_group_id is None:
        send_message(chat_id, "⚙️ 請管理員在服務員群輸入 /set_staff_group 完成初始設定。")
        return None

    if chat_id != staff_group_id and chat_id not in business_groups:
        business_groups.add(chat_id)
        group_config["business_groups"] = list(business_groups)
        save_group_config(group_config)
        send_message(chat_id, f"📋 已自動登錄此群為【業務群組】\n群組 ID：`{chat_id}`")
        send_main_menu(chat_id, "business")
        print(f"[INFO] New business group added: {chat_id}")
        return "business"

    return None

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
