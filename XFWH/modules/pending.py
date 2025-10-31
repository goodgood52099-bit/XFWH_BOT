import json, os
from config import DATA_DIR, TZ
from . import shifts, utils, telegram_api

PENDING_FILE = os.path.join(DATA_DIR, "pending.json")
pending_data = {}

def load_pending():
    global pending_data
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending_data = json.load(f)
    else:
        pending_data = {}

def save_pending():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending_data, f, ensure_ascii=False, indent=2)

def set_pending(user_id, data):
    pending_data[str(user_id)] = data
    save_pending()

def get_pending(user_id):
    return pending_data.get(str(user_id))

def clear_pending(user_id):
    pending_data.pop(str(user_id), None)
    save_pending()

def handle_user_text(user_id, chat_id, text):
    # 這裡填入你的原本使用者文字邏輯
    ...

def handle_callback(user_id, chat_id, data, callback_id):
    # 這裡填入你的原本按鈕 callback 邏輯
    ...
