import os, json, datetime
from config import DATA_DIR, TZ

def ensure_today_file():
    today = datetime.date.today().isoformat()
    path = os.path.join(DATA_DIR, f"{today}.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([], f)
    return path

def load_today_shifts():
    path = ensure_today_file()
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_today_shifts(shifts_list):
    path = ensure_today_file()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(shifts_list, f, ensure_ascii=False, indent=2)

def find_shift(hhmm):
    shifts_list = load_today_shifts()
    for s in shifts_list:
        if s.get("hhmm") == hhmm:
            return s
    return None

def is_future_time(hhmm):
    now = datetime.datetime.now(TZ)
    shift_time = datetime.datetime.combine(now.date(), datetime.time.fromisoformat(hhmm))
    return shift_time > now
