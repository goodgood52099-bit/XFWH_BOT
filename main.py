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
    raise ValueError("❌ 請設定 BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
ADMIN_IDS = [7236880214,7807558825,7502175264]  # 管理員 ID
business_group_id = int(os.getenv("BUSINESS_GROUP"))  # 業務群
staff_group_id = int(os.getenv("STAFF_GROUP"))        # 服務員群
TZ = ZoneInfo("Asia/Taipei")

asked_shifts = set()
pending_pay = {}  # 暫存服務員輸入金額流程

# -------------------------------
# 群組管理
# -------------------------------
def add_group(chat_id, chat_type):
    pass  # 此版本固定群組，不需要動態添加

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
# 生成按鈕
# -------------------------------
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

# -------------------------------
# 生成最新時段列表
# -------------------------------
def generate_latest_shift_list():
    path = ensure_today_file()
    data = load_json_file(path)
    msg_lines = []
    checked_in_lines = []
    now = datetime.now(TZ)
    shifts = sorted(data.get("shifts", []), key=lambda s: s.get("time","00:00"))

    for s in shifts:
        hhmm = s["time"]
        bookings = s.get("bookings", [])
        in_progress = s.get("in_progress", [])

        # 已報到區
        for x in in_progress:
            checked_in_lines.append(f"{hhmm} {x} ✅")

        # 未報到
        for b in bookings:
            name = b.get("name") if isinstance(b, dict) else b
            msg_lines.append(f"{hhmm} {name}")

    if not msg_lines and not checked_in_lines:
        return "📅 今日所有時段已過"

    text = "📅 今日最新時段列表：\n" + ("\n".join(msg_lines) if msg_lines else "（目前無未到時段）")
    if checked_in_lines:
        text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)
    return text

# -------------------------------
# 處理文字訊息
# -------------------------------
def handle_message(msg):
    text = msg.get("text","").strip() if msg.get("text") else ""
    chat_id = msg.get("chat", {}).get("id")
    if not text: return
    if text == "/list":
        send_message(chat_id, generate_latest_shift_list())

# -------------------------------
# 處理按鈕 Callback Query
# -------------------------------
def handle_callback(cb):
    data = cb.get("data","")
    chat_id = cb["message"]["chat"]["id"]
    user_id = cb["from"]["id"]
    parts = data.split("|")
    if len(parts)!=4: return
    group, action, hhmm, name = parts

    path = ensure_today_file()
    data_json = load_json_file(path)
    s = find_shift(data_json.get("shifts", []), hhmm)
    if not s: return

    # 業務群客到
    if group=="business" and action=="arrival":
        for b in s.get("bookings", []):
            if b["name"].lower() == name.lower():
                s.setdefault("in_progress", []).append(name)
                s["bookings"] = [bk for bk in s.get("bookings", []) if bk["name"].lower()!=name.lower()]
                save_json_file(path, data_json)
                staff_markup = generate_inline_buttons("staff", hhmm, name)
                send_message(staff_group_id,
                             f"待確認上樓客戶：\n時間：{hhmm}\n姓名：{name}\n預估金額：{b.get('estimate','0')}",
                             reply_markup=staff_markup)
                break

    # 服務員群上樓
    elif group=="staff" and action=="up":
        send_message(business_group_id, f"上 {hhmm} {name}")
        save_json_file(path, data_json)

    # 服務員群完成服務
    elif group=="staff" and action=="pay":
        pending_pay[(chat_id, hhmm, name)] = True
        send_message(chat_id, f"請輸入 {name} 的實際金額（0 表示未消費）：")

# -------------------------------
# 處理金額輸入（reply）
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        chat_id = msg.get("chat",{}).get("id")
        text = msg.get("text","").strip() if msg.get("text") else ""
        # 判斷是否為金額輸入
        for key in list(pending_pay.keys()):
            if key[0]==chat_id:
                hhmm, name = key[1], key[2]
                try:
                    amount = int(text)
                    reason = None
                    if amount==0:
                        send_message(chat_id, f"{name} 金額為 0，請輸入原因：")
                        pending_pay[(chat_id, hhmm, name)] = "await_reason"
                        return {"ok": True}
                    else:
                        handle_pay_input(chat_id, hhmm, name, amount)
                        del pending_pay[key]
                        return {"ok": True}
                except:
                    if pending_pay[key]=="await_reason":
                        reason=text
                        handle_pay_input(chat_id, hhmm, name, 0, reason)
                        del pending_pay[key]
                        return {"ok": True}
        handle_message(msg)
    if "callback_query" in update:
        handle_callback(update["callback_query"])
    return {"ok": True}

def handle_pay_input(chat_id, hhmm, name, amount, reason=None):
    path = ensure_today_file()
    data_json = load_json_file(path)
    s = find_shift(data_json.get("shifts", []), hhmm)
    if not s: return
    s["in_progress"] = [c for c in s.get("in_progress", []) if c.lower()!=name.lower()]
    save_json_file(path, data_json)
    if amount>0:
        send_message(business_group_id, f"服務完成 {hhmm} {name}\n金額：{amount}")
    else:
        send_message(business_group_id, f"未消費客離 {hhmm} {name}\n原因：{reason}")

# -------------------------------
# 自動整點公告
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 13<=now.hour<=22 and now.minute==0:
            try:
                text = generate_latest_shift_list()
                send_message(business_group_id, text)
                send_message(staff_group_id, text)
            except: traceback.print_exc()
            time.sleep(60)
        time.sleep(10)

# -------------------------------
# 排隊通知
# -------------------------------
def ask_arrivals_thread():
    global asked_shifts
    while True:
        now = datetime.now(TZ)
        current_hm = f"{now.hour:02d}:00"
        today = now.date().isoformat()
        key = f"{today}|{current_hm}"
        if now.minute==0 and key not in asked_shifts:
            path = data_path_for(today)
            if os.path.exists(path):
                data_json = load_json_file(path)
                for s in data_json.get("shifts", []):
                    if s["time"] != current_hm: continue
                    for b in s.get("bookings", []):
                        name = b["name"]
                        staff_markup = generate_inline_buttons("staff", current_hm, name)
                        send_message(staff_group_id,
                                     f"待確認上樓客戶：\n時間：{current_hm}\n姓名：{name}\n預估金額：{b.get('estimate','0')}",
                                     reply_markup=staff_markup)
                        break
            asked_shifts.add(key)
        # 每日清理
        if now.hour==0 and now.minute==1:
            asked_shifts.clear()
            for f in os.listdir(DATA_DIR):
                if f.endswith(".json"): os.remove(os.path.join(DATA_DIR,f))
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
