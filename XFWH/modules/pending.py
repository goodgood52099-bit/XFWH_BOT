import threading
import os
import json
from config import PENDING_FILE, DATA_DIR
from modules.utils import TZ
from datetime import datetime

json_lock = threading.Lock()

# JSON 通用
def save_json_file(path, data):
    with json_lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def load_json_file(path, default=None):
    with json_lock:
        if not os.path.exists(path):
            return default or {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

# pending 操作
def load_pending():
    if os.path.exists(PENDING_FILE):
        return load_json_file(PENDING_FILE)
    return {}

def save_pending(d):
    save_json_file(PENDING_FILE, d)

def set_pending_for(user_id, payload):
    p = load_pending()
    p[str(user_id)] = payload
    save_pending(p)

def get_pending_for(user_id):
    pending_data = load_pending()
    return pending_data.get(str(user_id))

def clear_pending_for(user_id):
    p = load_pending()
    if str(user_id) in p:
        del p[str(user_id)]
        save_pending(p)

# 自動清理過期 pending（3 分鐘）
def cleanup_expired_pending():
    try:
        pending_data = load_pending()
        now = datetime.now().timestamp()
        expired = [uid for uid, p in pending_data.items() if now - p.get("created_at", 0) > 180]
        for uid in expired:
            del pending_data[uid]
        if expired:
            save_pending(pending_data)
            print(f"🧹 清除過期 pending: {expired}")
    except Exception as e:
        print("❌ pending 自動清理錯誤:", e)
