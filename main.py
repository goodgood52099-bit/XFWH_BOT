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
# Telegram Bot Config
# -------------------------------
TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}/"
app = Flask(__name__)

# -------------------------------
# 全域資料
# -------------------------------
staff_group_id = None
business_groups = set()
pending_pay = {}

# -------------------------------
# 共用函數
# -------------------------------
def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(API_URL + "sendMessage", json=payload)


def generate_inline_buttons(group_type, hhmm, name):
    """產生不同群組的互動按鈕"""
    if group_type == "business":
        buttons = [[{"text": "客到", "callback_data": f"{group_type}|arrival|{hhmm}|{name}"}]]
    else:  # staff
        buttons = [[
            {"text": "上", "callback_data": f"{group_type}|up|{hhmm}|{name}"},
            {"text": "完成", "callback_data": f"{group_type}|pay|{hhmm}|{name}"}
        ]]
    return json.dumps({"inline_keyboard": buttons})


def generate_main_menu(group_type):
    """產生主選單按鈕"""
    if group_type == "business":
        keyboard = {
            "keyboard": [
                [{"text": "📋 查看列表"}, {"text": "🕒 今日時段"}],
                [{"text": "👥 候補名單"}]
            ],
            "resize_keyboard": True
        }
    else:
        keyboard = {
            "keyboard": [
                [{"text": "📋 查看列表"}, {"text": "💰 結帳紀錄"}],
                [{"text": "📢 全部公告"}]
            ],
            "resize_keyboard": True
        }
    return json.dumps(keyboard)

# -------------------------------
# 設定服務員群組
# -------------------------------
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()

    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])

    return "ok", 200


def handle_message(message):
    global staff_group_id

    try:
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()
        user = message.get("from", {})
        user_name = user.get("first_name", "使用者")

        # 🔹 若該服務員正在輸入金額
        if chat_id in pending_pay:
            pay_info = pending_pay.pop(chat_id)
            hhmm, name = pay_info["hhmm"], pay_info["name"]
            amount = text.strip()
            if not amount.isdigit():
                send_message(chat_id, "⚠️ 請輸入正確的數字金額")
                pending_pay[chat_id] = pay_info
                return

            amount = int(amount)
            for gid in business_groups:
                send_message(gid, f"✅ 完成 {hhmm} {name}\n金額：{amount}")
            send_message(chat_id, f"💰 已回報 {hhmm} {name} 金額 {amount}")
            return

        # -------------------------------
        # 管理員設定服務員群組
        # -------------------------------
        if text == "/set_staff_group":
            staff_group_id = chat_id
            send_message(chat_id, "✅ 此群組已設定為『服務員群』", reply_markup=generate_main_menu("staff"))
            return

        # -------------------------------
        # 若不是服務員群，視為業務群
        # -------------------------------
        if chat_id != staff_group_id:
            if chat_id not in business_groups:
                business_groups.add(chat_id)
                send_message(chat_id, "✅ 此群組已設定為『業務群』", reply_markup=generate_main_menu("business"))
            return

        # -------------------------------
        # 其他互動可擴充
        # -------------------------------

    except Exception as e:
        traceback.print_exc()
        send_message(chat_id, f"⚠️ 發生錯誤：{e}")


# -------------------------------
# 按鈕回傳互動 callback
# -------------------------------
def handle_callback(callback):
    try:
        data = callback.get("data", "")
        msg = callback.get("message", {})
        from_user = callback.get("from", {})
        query_id = callback.get("id")

        if not data:
            return

        parts = data.split("|")
        if len(parts) < 4:
            return

        group_type, action, hhmm, name = parts
        user_name = from_user.get("first_name", "服務員")

        # 回覆按鈕點擊結果
        requests.post(API_URL + "answerCallbackQuery", json={"callback_query_id": query_id})

        # -------------------------------
        # 業務群按「客到」
        # -------------------------------
        if group_type == "business" and action == "arrival":
            if not staff_group_id:
                send_message(msg["chat"]["id"], "⚠️ 尚未設定服務員群組。")
                return

            text = f"📢 有客人報到\n時間：{hhmm}\n姓名：{name}\n請服務員確認："
            markup = generate_inline_buttons("staff", hhmm, name)
            send_message(staff_group_id, text, reply_markup=markup)
            send_message(msg["chat"]["id"], f"✅ 已通知服務員群：{hhmm} {name}")
            return

        # -------------------------------
        # 服務員群按「上」
        # -------------------------------
        if group_type == "staff" and action == "up":
            text = f"🟢 上 {hhmm} {name} 由 {user_name}"
            for gid in business_groups:
                send_message(gid, text)
            send_message(msg["chat"]["id"], f"✅ 已回報上：{hhmm} {name}")
            return

        # -------------------------------
        # 服務員群按「完成」
        # -------------------------------
        if group_type == "staff" and action == "pay":
            staff_chat = msg["chat"]["id"]
            pending_pay[staff_chat] = {"hhmm": hhmm, "name": name}
            send_message(staff_chat, f"💰 請輸入 {hhmm} {name} 的實際金額：")
            return

    except Exception as e:
        traceback.print_exc()
        send_message(msg["chat"]["id"], f"⚠️ callback 錯誤：{e}")


# -------------------------------
# 自動推播 (範例，每整點推播)
# -------------------------------
def auto_push_updates():
    while True:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        if now.minute == 0 and now.second < 3:
            for gid in business_groups:
                send_message(gid, f"⏰ 每整點推播：目前時間 {now.strftime('%H:%M')}")
            time.sleep(3)
        time.sleep(1)


# -------------------------------
# 啟動背景執行緒
# -------------------------------
threading.Thread(target=auto_push_updates, daemon=True).start()

# -------------------------------
# 主入口
# -------------------------------
@app.route("/", methods=["GET"])
def index():
    return "Bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
