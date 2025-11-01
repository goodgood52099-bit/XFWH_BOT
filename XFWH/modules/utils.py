from datetime import datetime
from config import TZ
import threading, json, os
from modules.pending import ensure_today_file, load_json_file
from datetime import datetime, time as dt_time

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def format_hhmm(hhmm):
    if isinstance(hhmm, str) and len(hhmm) == 4:
        return f"{hhmm[:2]}:{hhmm[2:]}"
    return hhmm
# -------------------------------
# JSON 讀寫鎖
# -------------------------------
json_lock = threading.Lock()

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

        shift_dt = datetime.combine(now.date(), dt_time(*map(int, time_label.split(":")))).replace(tzinfo=TZ)
        shift_is_past = shift_dt < now

        regular_in_progress = [x for x in in_progress if not str(x).endswith("(候補)")]
        backup_in_progress = [x for x in in_progress if str(x).endswith("(候補)")]

        for item in regular_in_progress:
            if isinstance(item, dict):
                checked_in_lines.append(f"{time_label} {item['name']} ✅ ")
            else:
                checked_in_lines.append(f"{time_label} {item} ✅")

        for item in backup_in_progress:
            if isinstance(item, dict):
                checked_in_lines.append(f"{time_label} {item['name']} ✅ (候補)")
            else:
                checked_in_lines.append(f"{time_label} {item} ✅ (候補)")

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

def generate_unique_name(bookings, base_name):
    existing = [b["name"] for b in bookings if isinstance(b, dict)]
    if base_name not in existing:
        return base_name
    idx = 2
    while f"{base_name}({idx})" in existing:
        idx += 1
    return f"{base_name}({idx})"
