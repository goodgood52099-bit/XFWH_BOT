import requests
from config import BOT_TOKEN
from . import pending, shifts, groups, admin, staff

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(BASE_URL + "sendMessage", json=payload)

def answer_callback(callback_id, text=""):
    requests.post(BASE_URL + "answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})

def handle_text_message(msg):
    text = msg.get("text", "").strip()
    user_id = msg.get("from", {}).get("id")
    chat_id = msg.get("chat", {}).get("id")

    if user_id in admin.ADMIN_IDS:
        admin.handle_admin_text(user_id, chat_id, text)
        return

    pending.handle_user_text(user_id, chat_id, text)

def handle_callback_query(query):
    user_id = query.get("from", {}).get("id")
    chat_id = query.get("message", {}).get("chat", {}).get("id")
    data = query.get("data")
    callback_id = query.get("id")

    staff_prefixes = ["staff_up|","input_client|","not_consumed|","double|","complete|","fix|"]
    if any(data.startswith(p) for p in staff_prefixes):
        staff.handle_staff_flow(user_id, chat_id, data, callback_id)
        return

    pending.handle_callback(user_id, chat_id, data, callback_id)
