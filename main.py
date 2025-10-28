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
ADMIN_IDS = [7236880214,7807558825,7502175264]  # 管理員 Telegram ID，自行修改
GROUP_FILE = os.path.join(DATA_DIR, "groups.json")
TZ = ZoneInfo("Asia/Taipei")  # 台灣時區

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

def is_future_time(hhmm):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, hhmm.split(":"))
        shift_dt = datetime.combine(datetime.now(TZ).date(), dt_time(hh, mm)).replace(tzinfo=TZ)
        return shift_dt > now
    except: return False

# -------------------------------
# Telegram 發送
# -------------------------------
def send_request(method, payload): return requests.post(API_URL + method, json=payload).json()

def send_message(chat_id, text):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    return send_request("sendMessage", payload)

def send_message_with_buttons(chat_id, text, buttons=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return send_request("sendMessage", payload)

def broadcast_to_groups(message, buttons=None):
    for gid in GROUP_IDS: 
        try:
            if buttons:
                send_message_with_buttons(gid, message, buttons)
            else:
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
        shift_is_past = shift_dt < now

        regular_in_progress = [x for x in in_progress if not str(x).endswith("(候補)")]
        backup_in_progress = [x for x in in_progress if str(x).endswith("(候補)")]

        for name in regular_in_progress:
            checked_in_lines.append(f"{time_label} {name} ✅")
        for name in backup_in_progress:
            checked_in_lines.append(f"{time_label} {name} ✅")
        for b in bookings:
            name = b.get("name") if isinstance(b, dict) else b
            msg_lines.append(f"{time_label} {name}")

        used_slots = len(bookings) + len(regular_in_progress)
        remaining = max(0, limit - used_slots)

        if not shift_is_past:
            for _ in range(remaining):
                msg_lines.append(f"{time_label} ")

    if not msg_lines and not checked_in_lines:
        return "📅 今日所有時段已過"

    text = "📅 今日最新時段列表（未到時段）：\n"
    text += "\n".join(msg_lines) if msg_lines else "（目前無未到時段）"
    if checked_in_lines:
        text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)

    return text

# -------------------------------
# 工具函數
# -------------------------------
def generate_unique_name(bookings, base_name):
    existing = [b["name"] for b in bookings if isinstance(b, dict)]
    if base_name not in existing:
        return base_name
    idx = 2
    while f"{base_name}({idx})" in existing:
        idx += 1
    return f"{base_name}({idx})"

def generate_buttons_for_shift(shift):
    buttons = []
    for b in shift.get("bookings", []):
        name = b["name"]
        hhmm = shift["time"]
        buttons.append([
            {"text": f"客到 {name}", "callback_data": f"arrival|{hhmm}|{name}"},
            {"text": f"取消 {name}", "callback_data": f"cancel|{hhmm}|{name}"}
        ])
    return buttons

# -------------------------------
# 處理訊息
# -------------------------------
def handle_message(msg):
    try:
        text = msg.get("text", "").strip() if msg.get("text") else ""
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        user_name = msg.get("from", {}).get("first_name")
        chat_type = msg.get("chat", {}).get("type")
        add_group(chat_id, chat_type)
        if not text:
            return

        # -------------------------------
        # /help 指令
        if text == "/help":
            help_text = """
📌 *Telegram 預約機器人指令說明* 📌

一般使用者：
- 預約:預約 12:00 王小明
- 取消:取消 12:00 王小明
- 客到:客到 12:00 王小明
- 修改:修改 原時段 原姓名 新時段/新姓名
- /list 查看今日未到時段列表

管理員：
- 上:上 12:00 王小明
- 刪除 13:00 all 清空該時段所有名單（預約＋報到）
- 刪除 13:00 2   名額減少 2
- 刪除 13:00 小明 刪除該時段的小明 （自動判斷預約/報到/候補） 
- /addshift 增加時段
- /updateshift 修改時段班數 
"""
            send_message(chat_id, help_text)
            return

        # -------------------------------
        # /list
        if text == "/list":
            path = ensure_today_file()
            data = load_json_file(path)
            text_msg = generate_latest_shift_list()
            buttons = []
            for shift in data.get("shifts", []):
                buttons.extend(generate_buttons_for_shift(shift))
            send_message_with_buttons(chat_id, text_msg, buttons if buttons else None)
            return

        # 以下原本的預約/取消/客到/修改/管理員指令維持不變
        # (保持原本程式碼，可直接複製貼上你現有的 handle_message 裡面邏輯)
        # ...

    except Exception as e:
        traceback.print_exc()
        send_message(chat_id, f"⚠️ 發生錯誤: {e}")

# -------------------------------
# Flask webhook
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update=request.get_json()
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            query = update["callback_query"]
            data = query["data"]
            chat_id = query["message"]["chat"]["id"]
            parts = data.split("|")
            action = parts[0]
            hhmm = parts[1]
            name = parts[2]
            if action == "arrival":
                msg = {"text": f"客到 {hhmm} {name}", "chat": {"id": chat_id}, "from": {"id": query["from"]["id"], "first_name": query["from"]["first_name"]}}
                handle_message(msg)
            elif action == "cancel":
                msg = {"text": f"取消 {hhmm} {name}", "chat": {"id": chat_id}, "from": {"id": query["from"]["id"], "first_name": query["from"]["first_name"]}}
                handle_message(msg)
    except:
        traceback.print_exc()
    return {"ok": True}

# -------------------------------
# 自動整點公告
# -------------------------------
def auto_announce():
    while True:
        now=datetime.now(TZ)
        if 12<=now.hour<=22 and now.minute==0:
            try: 
                path = ensure_today_file()
                data = load_json_file(path)
                text_msg = generate_latest_shift_list()
                buttons = []
                for shift in data.get("shifts", []):
                    buttons.extend(generate_buttons_for_shift(shift))
                broadcast_to_groups(text_msg, buttons if buttons else None)
            except: traceback.print_exc()
            time.sleep(60)
        time.sleep(10)

# -------------------------------
# 自動詢問預約者是否到場
# -------------------------------
def ask_arrivals_thread():
    global asked_shifts
    while True:
        now = datetime.now(TZ)
        current_hm = f"{now.hour:02d}:00"
        today = now.date().isoformat()
        key = f"{today}|{current_hm}"

        if now.minute == 0 and key not in asked_shifts:
            path = data_path_for(today)
            if os.path.exists(path):
                data = load_json_file(path)
                for s in data.get("shifts", []):
                    if s.get("time") != current_hm:
                        continue
                    waiting = []
                    groups_to_notify = set()
                    for b in s.get("bookings", []):
                        name = b.get("name")
                        gid = b.get("chat_id")
                        if name not in s.get("in_progress", []):
                            waiting.append(name)
                            groups_to_notify.add(gid)
                    if waiting:
                        names_text = "、".join(waiting)
                        text = f"⏰ 現在是 {current_hm}\n請問預約的「{names_text}」到了嗎？\n到了請回覆：客到 {current_hm} 名稱"
                        for gid in groups_to_notify:
                            send_message(gid, text)
            asked_shifts.add(key)
        if now.hour == 0 and now.minute == 1:
            asked_shifts.clear()
        time.sleep(10)

# -------------------------------
# 啟動背景執行緒
# -------------------------------
threading.Thread(target=auto_announce, daemon=True).start()
threading.Thread(target=ask_arrivals_thread, daemon=True).start()

# -------------------------------
# 啟動 Flask
# -------------------------------
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)))
