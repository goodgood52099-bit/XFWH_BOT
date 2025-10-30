import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta, time as dt_time
import threading
import traceback

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # pip install backports.zoneinfo

app = Flask(__name__)
TZ = ZoneInfo("Asia/Taipei")

# --- 環境變數 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")
PORT = int(os.environ.get("PORT", 5000))

# --- 群組設定 ---
STAFF_GROUP_IDS = []
BUSINESS_GROUP_IDS = []

# --- 預約資料 ---
appointments = {}  # {"HHMM": [{"name": str, "status": reserved/checkedin, "amount": int, "customer": {}, "staff": [], "unsold_reason": str, "business_group_id": int}]}

# =========================
# 工具函數
# =========================
def send_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, data=payload)
        print(r.text)
    except Exception as e:
        print("Send message error:", e)

def create_inline_button(text, callback_data):
    return {"text": text, "callback_data": callback_data}

def generate_unique_name(bookings, base_name):
    existing = [b["name"] for b in bookings if isinstance(b, dict)]
    if base_name not in existing:
        return base_name
    idx = 2
    while f"{base_name}({idx})" in existing:
        idx += 1
    return f"{base_name}({idx})"

# =========================
# 生成最新時段列表，每個時段名額固定3
# =========================
def generate_latest_shift_list():
    msg_lines = []
    checked_in_lines = []
    now = datetime.now(TZ)

    for hhmm in sorted(appointments.keys()):
        for a in appointments[hhmm]:
            shift_dt = datetime.combine(now.date(), dt_time(int(hhmm[:2]), int(hhmm[2:]))).replace(tzinfo=TZ)
            shift_is_past = shift_dt < now

            # 已報到
            checked_in_count = 0
            if a.get("status") == "checkedin":
                checked_in_lines.append(f"{hhmm} {a['name']} ✅")
                checked_in_count = 1

            # 每個時段固定名額 3
            limit = 3
            remaining = max(0, limit - checked_in_count)

            # 未報到名額，每個空位單獨一行
            if a.get("status") == "reserved" or a.get("name","") == "":
                for _ in range(remaining):
                    msg_lines.append(f"{hhmm}")

    if not msg_lines and not checked_in_lines:
        return "📅 今日所有時段已過"

    text = "📅 今日最新時段列表（未到時段）：\n"
    text += "\n".join(msg_lines) if msg_lines else "（目前無未到時段）"
    if checked_in_lines:
        text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)
    return text

def send_latest_slots(chat_id):
    text = generate_latest_shift_list()
    send_message(chat_id, text)

# =========================
# 業務群操作按鈕（中文，一行兩個）
# =========================
def send_business_menu(chat_id):
    reply_markup = {
        "inline_keyboard": [
            [create_inline_button("預約", "action:reserve"), create_inline_button("修改預約", "action:modify")],
            [create_inline_button("取消預約", "action:cancel"), create_inline_button("查看時段", "action:view")],
            [create_inline_button("報到", "action:checkin")]
        ]
    }
    send_message(chat_id, "請選擇操作功能：", reply_markup)

# =========================
# 排程：整點公告、詢問客人、每日重置
# =========================
def announce_latest_slots():
    while True:
        now = datetime.now(TZ)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        for gid in BUSINESS_GROUP_IDS:
            send_latest_slots(gid)

def ask_clients_checkin():
    while True:
        now = datetime.now(TZ)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        now_hhmm = next_hour.strftime("%H%M")
        for hhmm in sorted(list(appointments.keys())):
            if hhmm < now_hhmm:
                continue
            for a in appointments[hhmm]:
                if a.get("status") == "reserved":
                    reply_markup = {"inline_keyboard":[[create_inline_button("報到", f"checkin:{hhmm}|{a['name']}|{a['amount']}")]]}
                    business_gid = a.get("business_group_id")
                    if business_gid:
                        send_message(business_gid, f"現在是 {now_hhmm}，請問預約 {hhmm} 的 {a['name']} 到了嗎？", reply_markup=reply_markup)

def daily_reset_appointments():
    while True:
        now = datetime.now(TZ)
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        sleep_seconds = (next_reset - now).total_seconds()
        time.sleep(sleep_seconds)

        appointments.clear()
        for hour in range(13, 23):
            hhmm = f"{hour:02d}00"
            appointments[hhmm] = [{"name": "", "status": "reserved", "amount": 3}]

        for gid in BUSINESS_GROUP_IDS:
            send_latest_slots(gid)
        print(f"已重置預約列表並生成新時段 – {datetime.now(TZ)}")

# =========================
# 廣播最新時段給所有業務群
# =========================
def broadcast_latest_to_all():
    for gid in BUSINESS_GROUP_IDS:
        send_latest_slots(gid)

# =========================
# 處理文字訊息
# =========================
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    user_id = str(message["from"]["id"])

    if text.startswith("/STAFF") and user_id in ADMIN_IDS:
        if chat_id not in STAFF_GROUP_IDS:
            STAFF_GROUP_IDS.append(chat_id)
        send_message(chat_id, f"已設定本群為服務員群：{chat_id}")
        return

    if chat_id not in BUSINESS_GROUP_IDS:
        BUSINESS_GROUP_IDS.append(chat_id)
        send_business_menu(chat_id)
        send_message(chat_id, f"已將本群加入業務群列表：{chat_id}")
        return

    if chat_id not in BUSINESS_GROUP_IDS and chat_id not in STAFF_GROUP_IDS:
        send_message(chat_id, "⚠️ 只能在業務群或服務員群操作此功能")
        return

    # 處理客資輸入
    if " / " in text:
        try:
            parts = text.split("/")
            customer_info = parts[0].strip()
            staff_name = parts[1].strip()
            for hhmm, lst in appointments.items():
                for a in lst:
                    if a.get("awaiting_customer", False):
                        a["customer"] = customer_info
                        if staff_name not in a["staff"]:
                            a["staff"].append(staff_name)
                        a["awaiting_customer"] = False
                        # 發送服務員群
                        for gid in STAFF_GROUP_IDS:
                            send_message(gid,
                                         f"{hhmm} – {a['name']} / {a['amount']}\n客稱年紀：{customer_info}\n服務人員：{staff_name}",
                                         reply_markup={"inline_keyboard":[
                                             [create_inline_button("雙", f"double:{hhmm}|{a['name']}"),
                                              create_inline_button("完成服務", f"complete:{hhmm}|{a['name']}")],
                                             [create_inline_button("修改", f"modify:{hhmm}|{a['name']}"),
                                              create_inline_button("未消", f"unsold:{hhmm}|{a['name']}")]
                                         ]})
                        # 通知原業務群
                        business_gid = a.get("business_group_id")
                        if business_gid:
                            send_message(business_gid, f"{a['name']} / {customer_info} / {staff_name}")
                        # 廣播最新時段給所有業務群
                        broadcast_latest_to_all()
                        return
        except:
            pass

    if text.startswith("原因："):
        reason = text.replace("原因：","").strip()
        for hhmm, lst in appointments.items():
            for a in lst:
                if a.get("awaiting_unsold", False):
                    a["unsold_reason"] = reason
                    a["awaiting_unsold"] = False
                    for gid in STAFF_GROUP_IDS:
                        send_message(gid, f"已標記為未消 – 原因：{reason}")
                    business_gid = a.get("business_group_id")
                    if business_gid:
                        send_message(business_gid, f"{a['name']} / 原因：{reason}")
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
        send_message(chat_id, "⚠️ 只能在業務群或服務員群操作此功能")
        return

    parts = data.split(":")
    action = parts[0]
    key = parts[1] if len(parts) > 1 else None

    if action == "action":
        cmd = key
        if cmd == "reserve":
            send_message(chat_id, "請選擇時段及輸入業務名稱與金額")
        elif cmd == "modify":
            send_message(chat_id, "請選擇要修改的預約")
        elif cmd == "cancel":
            send_message(chat_id, "請選擇要取消的預約")
        elif cmd == "view":
            send_latest_slots(chat_id)
        elif cmd == "checkin":
            send_message(chat_id, "請選擇報到的客人")

    elif action == "checkin":
        hhmm, name, amount = key.split("|")
        business_gid = None
        for a in appointments.get(hhmm, []):
            if a["name"] == name:
                business_gid = a.get("business_group_id")
                break
        if business_gid:
            send_message(business_gid, f"上 – {hhmm} – {name}")
        reply_markup = {"inline_keyboard":[
            [create_inline_button("輸入客資", f"input_customer:{hhmm}|{name}"),
             create_inline_button("未消", f"unsold:{hhmm}|{name}")]
        ]}
        send_message(chat_id, f"已通知 {name}", reply_markup)

    elif action in ["input_customer", "unsold", "double", "complete", "modify"]:
        hhmm, *rest = key.split("|")
        name = rest[0]
        amount = int(rest[1]) if len(rest) > 1 else None
        for a in appointments.get(hhmm, []):
            if a["name"] == name and (amount is None or a.get("amount") == amount):
                business_gid = a.get("business_group_id")
                if action == "input_customer":
                    a["awaiting_customer"] = True
                    send_message(chat_id, "請輸入客稱、年紀、服務人員（格式：客小美 28 / 小張）")
                elif action == "unsold":
                    a["awaiting_unsold"] = True
                    send_message(chat_id, "請輸入原因（格式：原因：XXXX）")
                elif action == "double":
                    a["awaiting_double"] = True
                    send_message(chat_id, "請輸入另一服務人員名稱（不可重複）")
                elif action == "complete":
                    a["awaiting_complete"] = True
                    send_message(chat_id, "請輸入實收金額（數字）")
                elif action == "modify":
                    a["awaiting_customer"] = True
                    send_message(chat_id, "請重新輸入客稱、年紀、服務人員（格式：客小美 28 / 小張）")
                
                if business_gid:
                    send_message(business_gid, f"服務員操作通知 – {hhmm} – {a['name']} / {a.get('amount','')}")
                broadcast_latest_to_all()

# =========================
# Flask Webhook
# =========================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        traceback.print_exc()
    return "OK"

@app.route("/", methods=["GET"])
def home():
    return "Bot is running ✅"

# =========================
# 啟動排程
# =========================
def start_announcement_thread():
    threading.Thread(target=announce_latest_slots, daemon=True).start()
    threading.Thread(target=ask_clients_checkin, daemon=True).start()
    threading.Thread(target=daily_reset_appointments, daemon=True).start()

if __name__ == "__main__":
    start_announcement_thread()
    app.run(host="0.0.0.0", port=PORT)
