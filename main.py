import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta
import threading
import time
import traceback

app = Flask(__name__)

# --- 環境變數 ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")
PORT = int(os.environ.get("PORT", 5000))

# --- 群組設定（支援多群組） ---
STAFF_GROUP_IDS = []       # 服務員群
BUSINESS_GROUP_IDS = []    # 業務群

# --- 預約資料 ---
appointments = {}  # {"HHMM": [{"name": str, "amount": int, "status": reserved/checkedin, "customer": {}, "staff": [], "actual_amount": int, "unsold_reason": str, "business_group_id": int}]}

# --- 工具函數 ---
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

# --- 業務群操作按鈕 ---
def send_business_menu(chat_id):
    reply_markup = {
        "inline_keyboard": [
            [create_inline_button("預約", "action:reserve")],
            [create_inline_button("修改", "action:modify")],
            [create_inline_button("取消", "action:cancel")],
            [create_inline_button("查看", "action:view")],
            [create_inline_button("報到", "action:checkin")]
        ]
    }
    send_message(chat_id, "請選擇操作功能：", reply_markup)

# --- 發送最新時段列表 ---
def send_latest_slots(chat_id):
    message_lines = []
    now_hhmm = datetime.now().strftime("%H%M")
    for hhmm in sorted(list(appointments.keys())):
        if hhmm < now_hhmm:
            del appointments[hhmm]
            continue
        for a in appointments[hhmm]:
            if a["status"] == "reserved":
                message_lines.append(f"{hhmm} – {a['name']}")
    if message_lines:
        send_message(chat_id, "最新未到時段列表：\n" + "\n".join(message_lines))
    else:
        send_message(chat_id, "今日暫無未到時段")

# --- 每整點公告最新時段列表 ---
def announce_latest_slots():
    while True:
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        for gid in BUSINESS_GROUP_IDS:
            send_latest_slots(gid)

# --- 每整點詢問客人到達 ---
def ask_clients_checkin():
    while True:
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        time.sleep((next_hour - now).total_seconds())
        now_hhmm = next_hour.strftime("%H%M")
        for hhmm in sorted(list(appointments.keys())):
            if hhmm < now_hhmm:
                continue
            for a in appointments[hhmm]:
                if a["status"] == "reserved":
                    reply_markup = {"inline_keyboard": [[
                        create_inline_button("報到", f"checkin:{hhmm}|{a['name']}|{a['amount']}")
                    ]]}
                    business_gid = a.get("business_group_id")
                    if business_gid:
                        send_message(business_gid,
                                     f"現在是 {now_hhmm}，請問預約 {hhmm} 的 {a['name']} 到了嗎？",
                                     reply_markup=reply_markup)

# --- 處理文字訊息 ---
def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    user_id = str(message["from"]["id"])

    # 設定服務員群
    if text.startswith("/STAFF"):
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "你不是管理員，無法設定服務員群。")
            return
        if chat_id not in STAFF_GROUP_IDS:
            STAFF_GROUP_IDS.append(chat_id)
        send_message(chat_id, f"已設定本群為服務員群：{chat_id}")
        return

    # 自動加入業務群
    if chat_id not in BUSINESS_GROUP_IDS:
        BUSINESS_GROUP_IDS.append(chat_id)
        send_business_menu(chat_id)
        send_message(chat_id, f"已將本群加入業務群列表：{chat_id}")
        return

    # 限制操作權限
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
                                         reply_markup={"inline_keyboard":[[
                                             create_inline_button("雙", f"double:{hhmm}|{a['name']}"),
                                             create_inline_button("完成服務", f"complete:{hhmm}|{a['name']}"),
                                             create_inline_button("修改", f"modify:{hhmm}|{a['name']}")
                                         ]]})
                        # 只通知該預約所屬業務群
                        business_gid = a.get("business_group_id")
                        if business_gid:
                            send_message(business_gid, f"{a['name']} / {customer_info} / {staff_name}")
                        return
        except:
            pass

    # 處理未消原因
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
                    return

# --- 處理按鈕回調 ---
def handle_callback(callback):
    data = callback["data"]
    message = callback["message"]
    chat_id = message["chat"]["id"]

    # 限制操作權限
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
        for gid in BUSINESS_GROUP_IDS:
            send_message(gid, f"上 – {hhmm} – {name} / {amount}")
        send_message(chat_id, "已通知業務群")
    elif action in ["input_customer", "unsold", "double", "complete", "modify"]:
        # 找出對應預約
        hhmm, *rest = key.split("|")
        name = rest[0]
        amount = int(rest[1]) if len(rest) > 1 else None
        for a in appointments.get(hhmm, []):
            if a["name"] == name and (amount is None or a["amount"] == amount):
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
                
                # --- 僅通知該預約業務群 ---
                if business_gid:
                    send_message(business_gid, f"服務員操作通知 – {hhmm} – {a['name']} / {a['amount']}")

# --- Flask Webhook ---
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

# --- 啟動整點公告與詢問排程 ---
def start_announcement_thread():
    t1 = threading.Thread(target=announce_latest_slots, daemon=True)
    t1.start()
    t2 = threading.Thread(target=ask_clients_checkin, daemon=True)
    t2.start()

# --- 主程式 ---
@app.route("/", methods=["GET"])
def home():
    return "Bot is running ✅"

if __name__ == "__main__":
    start_announcement_thread()
    app.run(host="0.0.0.0", port=PORT)
