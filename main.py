import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta, time as dt_time
import threading
import traceback
import time

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

app = Flask(__name__)
TZ = ZoneInfo("Asia/Taipei")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

lock = threading.Lock()
asked_shifts = set()

# --- 環境變數 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")
PORT = int(os.environ.get("PORT", 5000))

# --- 群組設定 ---
STAFF_GROUP_IDS = []
BUSINESS_GROUP_IDS = []

# --- 預約資料 ---
appointments = {}  # {"HHMM": [{"name": str, "status": reserved/checkedin, "amount": int, "customer": {}, "staff": [], "unsold_reason": str, "business_group_id": int}]}

# -------------------------------
def data_path_for(day): return os.path.join(DATA_DIR, f"{day}.json")

def load_json_file(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def ensure_today_file(workers=3):
    today = datetime.now(TZ).date().isoformat()
    path = data_path_for(today)
    now = datetime.now(TZ)
    if os.path.exists(path):
        data = load_json_file(path)
        if data.get("date") != today: os.remove(path)
    if not os.path.exists(path):
        shifts = []
        for h in range(13, 23):  # 13:00~22:00
            shift_time = dt_time(h, 0)
            shift_dt = datetime.combine(datetime.now(TZ).date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
        save_json_file(path, {"date": today, "shifts": shifts, "候補":[]})
    return path

def generate_unique_name(bookings, base_name):
    existing = [b["name"] for b in bookings if isinstance(b, dict)]
    if base_name not in existing: return base_name
    idx = 2
    while f"{base_name}({idx})" in existing: idx += 1
    return f"{base_name}({idx})"

# =========================
# Telegram 相關工具
# =========================
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
    try: requests.post(url, data=payload)
    except Exception as e: print("Send message error:", e)

def create_inline_button(text, callback_data):
    return {"text": text, "callback_data": callback_data}

# =========================
# 生成最新時段列表
# =========================
def generate_latest_shift_list():
    msg_lines = []
    checked_in_lines = []
    now = datetime.now(TZ)
    for hhmm in sorted(appointments.keys()):
        for a in appointments[hhmm]:
            shift_dt = datetime.combine(now.date(), dt_time(int(hhmm[:2]), int(hhmm[2:]))).replace(tzinfo=TZ)
            shift_is_past = shift_dt < now
            checked_in_count = 0
            if a.get("status") == "checkedin":
                checked_in_lines.append(f"{hhmm} {a['name']} ✅")
                checked_in_count = 1
            limit = 3
            remaining = max(0, limit - checked_in_count)
            if a.get("status") == "reserved" or a.get("name","") == "":
                for _ in range(remaining): msg_lines.append(f"{hhmm}")
    if not msg_lines and not checked_in_lines: return "📅 今日所有時段已過"
    text = "📅 今日最新時段列表（未到時段）：\n" + ("\n".join(msg_lines) if msg_lines else "（目前無未到時段）")
    if checked_in_lines: text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)
    return text

def send_latest_slots(chat_id): send_message(chat_id, generate_latest_shift_list())

# =========================
# 點選預約按鈕 → 顯示時段按鈕
# =========================
def send_shift_buttons(chat_id):
    now = datetime.now(TZ)
    buttons = []
    for h in range(13, 23):
        hhmm = f"{h:02d}00"
        shift_dt = datetime.combine(now.date(), dt_time(h, 0)).replace(tzinfo=TZ)
        if shift_dt <= now: continue
        shift_list = appointments.get(hhmm, [])
        reserved_count = sum(1 for a in shift_list if a.get("status")=="reserved")
        checked_count = sum(1 for a in shift_list if a.get("status")=="checkedin")
        limit = 3
        if reserved_count + checked_count >= limit: btn = create_inline_button(f"{h:02d}:00 ❌", "disabled")
        else: btn = create_inline_button(f"{h:02d}:00", f"reserve:{hhmm}")
        buttons.append(btn)
    # 每行 3 個
    inline_keyboard = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    send_message(chat_id,"請選擇時段：",{"inline_keyboard": inline_keyboard})

# =========================
# 點選時段後輸入業務名稱/金額生成預約
# =========================
def handle_shift_reservation(chat_id, text):
    if chat_id not in asked_shifts: return False
    hhmm = asked_shifts.pop(chat_id)
    try:
        name_part, amount_part = text.split("/")
        name = name_part.strip()
        amount = int(amount_part.strip())
    except:
        send_message(chat_id, "格式錯誤，請輸入：業務名稱 / 金額")
        asked_shifts.add(chat_id)
        return True

    with lock:
        if hhmm not in appointments: appointments[hhmm] = []
        shift_list = appointments[hhmm]
        reserved_count = sum(1 for a in shift_list if a.get("status")=="reserved")
        checked_count = sum(1 for a in shift_list if a.get("status")=="checkedin")
        limit = 3
        if reserved_count + checked_count >= limit:
            send_message(chat_id,f"{hhmm} 時段已滿，請選擇其他時段")
            return True
        unique_name = generate_unique_name(shift_list, name)
        shift_list.append({"name": unique_name, "status":"reserved", "amount":amount, "customer":{}, "staff":[],"business_group_id":chat_id})
        send_message(chat_id,f"{hhmm} 已預約成功：{unique_name} / {amount}")
        broadcast_latest_to_all()
    return True

# =========================
# 發送業務功能選單
# =========================
def send_business_menu(chat_id):
    reply_markup = {
        "inline_keyboard":[
            [create_inline_button("預約","action:reserve"),create_inline_button("修改預約","action:modify")],
            [create_inline_button("取消預約","action:cancel"),create_inline_button("查看時段","action:view")],
            [create_inline_button("報到","action:checkin")]
        ]
    }
    send_message(chat_id,"請選擇操作功能：",reply_markup)

def broadcast_latest_to_all():
    for gid in BUSINESS_GROUP_IDS: send_latest_slots(gid)

# =========================
# 排程
# =========================
def announce_latest_slots():
    while True:
        now = datetime.now(TZ)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        for gid in BUSINESS_GROUP_IDS: send_latest_slots(gid)

def daily_reset_appointments():
    while True:
        now = datetime.now(TZ)
        if now.hour == 0 and now.minute == 1:
            ensure_today_file()
            appointments.clear()
        time.sleep(60)

def ask_clients_checkin():
    while True:
        now = datetime.now(TZ)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        now_hhmm = next_hour.strftime("%H%M")
        for hhmm in sorted(appointments.keys()):
            if hhmm < now_hhmm: continue
            for a in appointments[hhmm]:
                if a["status"]=="reserved":
                    reply_markup = {"inline_keyboard":[[
                        create_inline_button("上", f"checkin:{hhmm}|{a['name']}|{a['amount']}")
                    ]]}
                    business_gid = a.get("business_group_id")
                    if business_gid:
                        send_message(business_gid,f"現在是 {now_hhmm}，請問預約 {hhmm} 的 {a['name']} 到了嗎？",reply_markup=reply_markup)

# =========================
# 處理文字訊息
# =========================
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text","")
    user_id = str(message["from"]["id"])

    if handle_shift_reservation(chat_id, text): return

    # 設定服務員群
    if text.startswith("/STAFF") and user_id in ADMIN_IDS:
        if chat_id not in STAFF_GROUP_IDS: STAFF_GROUP_IDS.append(chat_id)
        send_message(chat_id,f"已設定本群為服務員群：{chat_id}")
        return

    if chat_id not in BUSINESS_GROUP_IDS:
        BUSINESS_GROUP_IDS.append(chat_id)
        send_business_menu(chat_id)
        send_message(chat_id,f"已將本群加入業務群列表：{chat_id}")
        return

    if chat_id not in BUSINESS_GROUP_IDS and chat_id not in STAFF_GROUP_IDS:
        send_message(chat_id,"⚠️ 只能在業務群或服務員群操作此功能")
        return

    # 處理客資輸入
    if " / " in text:
        try:
            parts = text.split("/")
            customer_info = parts[0].strip()
            staff_name = parts[1].strip()
            for hhmm,lst in appointments.items():
                for a in lst:
                    if a.get("awaiting_customer",False):
                        a["customer"] = customer_info
                        if staff_name not in a["staff"]: a["staff"].append(staff_name)
                        a["awaiting_customer"]=False
                        for gid in STAFF_GROUP_IDS:
                            send_message(gid,
                                f"{hhmm} – {a['name']} / {a.get('amount',0)}\n客稱年紀：{customer_info}\n服務人員：{staff_name}",
                                reply_markup={"inline_keyboard":[
                                    [create_inline_button("雙", f"double:{hhmm}|{a['name']}"),
                                     create_inline_button("完成服務", f"complete:{hhmm}|{a['name']}")],
                                    [create_inline_button("修改", f"modify:{hhmm}|{a['name']}"),
                                     create_inline_button("未消", f"unsold:{hhmm}|{a['name']}")]
                                ]})
                        business_gid = a.get("business_group_id")
                        if business_gid: send_message(business_gid,f"{a['name']} / {customer_info} / {staff_name}")
                        broadcast_latest_to_all()
                        return
        except: pass

    if text.startswith("原因："):
        reason = text.replace("原因：","").strip()
        for hhmm,lst in appointments.items():
            for a in lst:
                if a.get("awaiting_unsold",False):
                    a["unsold_reason"]=reason
                    a["awaiting_unsold"]=False
                    for gid in STAFF_GROUP_IDS: send_message(gid,f"已標記為未消 – 原因：{reason}")
                    business_gid = a.get("business_group_id")
                    if business_gid: send_message(business_gid,f"{a['name']} / 原因：{reason}")
                    broadcast_latest_to_all()
                    return

# =========================
# 處理按鈕回調
# =========================
def handle_callback(callback):
    data = callback["data"]
    message = callback["message"]
    chat_id = message["chat"]["id"]
    if chat_id not in BUSINESS_GROUP_IDS and chat_id not in STAFF_GROUP_IDS:
        send_message(chat_id,"⚠️ 只能在業務群或服務員群操作此功能")
        return
    parts = data.split(":")
    action = parts[0]
    key = parts[1] if len(parts)>1 else None

    if action == "action":
        cmd = key
        if cmd == "reserve": send_shift_buttons(chat_id)
        elif cmd=="modify": send_message(chat_id,"請選擇要修改的預約")
        elif cmd=="cancel": send_message(chat_id,"請選擇要取消的預約")
        elif cmd=="view": send_latest_slots(chat_id)
        elif cmd=="checkin": send_message(chat_id,"請選擇報到的客人")

    elif action=="reserve":
        hhmm = key
        asked_shifts.add(chat_id)
        send_message(chat_id,f"你選擇 {hhmm}，請輸入：業務名稱 / 金額")

# =========================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update=request.get_json()
        if "message" in update: handle_message(update["message"])
        elif "callback_query" in update: handle_callback(update["callback_query"])
    except: traceback.print_exc()
    return "OK"

@app.route("/", methods=["GET"])
def home(): return "Bot is running ✅"

# =========================
# 啟動排程
# =========================
def start_threads():
    threading.Thread(target=announce_latest_slots, daemon=True).start()
    threading.Thread(target=ask_clients_checkin, daemon=True).start()
    threading.Thread(target=daily_reset_appointments, daemon=True).start()

if __name__=="__main__":
    ensure_today_file()
    start_threads()
    app.run(host="0.0.0.0", port=PORT)
