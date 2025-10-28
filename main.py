import os
import json
import requests
from flask import Flask, request
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
    raise ValueError("❌ 請在 Render 環境變數設定 BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
ADMIN_IDS = [7236880214, 7807558825, 7502175264]
GROUP_FILE = os.path.join(DATA_DIR, "groups.json")
STAFF_FILE = os.path.join(DATA_DIR, "staff_group.json")
TZ = ZoneInfo("Asia/Taipei")

asked_shifts = set()

# -------------------------------
# 群組管理
# -------------------------------
def load_groups():
    if os.path.exists(GROUP_FILE):
        with open(GROUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_groups(groups):
    with open(GROUP_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

GROUP_IDS = load_groups()

def add_group(chat_id, chat_type):
    if chat_type in ["group", "supergroup"] and chat_id not in GROUP_IDS:
        GROUP_IDS.append(chat_id)
        save_groups(GROUP_IDS)

def get_staff_group():
    if os.path.exists(STAFF_FILE):
        with open(STAFF_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("staff_group_id")
    return None

def set_staff_group(chat_id):
    with open(STAFF_FILE, "w", encoding="utf-8") as f:
        json.dump({"staff_group_id": chat_id}, f, ensure_ascii=False, indent=2)

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
            shift_dt = datetime.combine(datetime.now(TZ).date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
        save_json_file(path, {"date": today, "shifts": shifts, "候補": []})
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

def is_future_time(hhmm):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, hhmm.split(":"))
        shift_dt = datetime.combine(datetime.now(TZ).date(), dt_time(hh, mm)).replace(tzinfo=TZ)
        return shift_dt > now
    except:
        return False

# -------------------------------
# Telegram 發送
# -------------------------------
def send_request(method, payload): return requests.post(API_URL + method, json=payload).json()
def send_message(chat_id, text):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    return send_request("sendMessage", payload)

def broadcast_to_sales_groups(message):
    """只發送給業務群（排除服務員群）"""
    staff_group = get_staff_group()
    for gid in GROUP_IDS:
        if gid == staff_group:
            continue
        try:
            send_message(gid, message)
        except Exception:
            traceback.print_exc()

# -------------------------------
# 生成最新時段列表
# -------------------------------
def generate_latest_shift_list():
    path = ensure_today_file()
    data = load_json_file(path)
    msg_lines = []
    checked_in_lines = []
    now = datetime.now(TZ)

    shifts = sorted(data.get("shifts", []), key=lambda s: s.get("time", "00:00"))

    for s in shifts:
        time_label = s["time"]
        limit = s.get("limit", 1)
        bookings = s.get("bookings", [])
        in_progress = s.get("in_progress", [])

        shift_dt = datetime.combine(now.date(), datetime.strptime(time_label, "%H:%M").time()).replace(tzinfo=TZ)
        if shift_dt < now:
            continue

        used_slots = len(bookings) + len(in_progress)
        remaining = max(0, limit - used_slots)
        for b in bookings:
            name = b.get("name") if isinstance(b, dict) else b
            msg_lines.append(f"{time_label} {name}")
        for _ in range(remaining):
            msg_lines.append(f"{time_label} ")

        for name in in_progress:
            checked_in_lines.append(f"{time_label} {name} ✅")

    if not msg_lines and not checked_in_lines:
        return "📅 今日所有時段已過"

    text = "📅 今日最新時段列表：\n" + "\n".join(msg_lines)
    if checked_in_lines:
        text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)
    return text

# -------------------------------
# 處理訊息
# -------------------------------
def handle_message(msg):
    try:
        text = msg.get("text", "").strip() if msg.get("text") else ""
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        chat_type = msg.get("chat", {}).get("type")

        add_group(chat_id, chat_type)
        if not text:
            return

        # -------------------------------
        # 設定服務員群
        if text == "/setstaffgroup" and user_id in ADMIN_IDS:
            set_staff_group(chat_id)
            send_message(chat_id, "✅ 已設定本群為服務員群組")
            return

        # -------------------------------
        # /list
        if text == "/list":
            send_message(chat_id, generate_latest_shift_list())
            return

        # -------------------------------
        # 管理員指令：上 HH:MM 名稱
        if user_id in ADMIN_IDS and text.startswith("上"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "⚠️ 格式錯誤：上 HH:MM 名稱")
                return
            hhmm = parts[1]
            name = " ".join(parts[2:])
            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s or name not in s.get("in_progress", []):
                send_message(chat_id, f"⚠️ {hhmm} {name} 尚未報到")
                return
            s["in_progress"].remove(name)
            save_json_file(path, data)

            # 通知服務員群
            staff_group = get_staff_group()
            if staff_group:
                send_message(staff_group, f"👥 業務報到通知\n{name}（{hhmm}）\n請準備接客")
            send_message(chat_id, f"✅ 已通知服務員群：{hhmm} {name}")
            return

    except Exception as e:
        traceback.print_exc()
        send_message(chat_id, f"⚠️ 發生錯誤: {e}")

# -------------------------------
# Flask webhook
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if "message" in update:
            handle_message(update["message"])
    except:
        traceback.print_exc()
    return {"ok": True}

# -------------------------------
# 自動整點公告（僅業務群）
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                broadcast_to_sales_groups(generate_latest_shift_list())
            except:
                traceback.print_exc()
            time.sleep(60)
        time.sleep(10)

# -------------------------------
# 啟動背景執行緒
# -------------------------------
threading.Thread(target=auto_announce, daemon=True).start()

# -------------------------------
# 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
