from datetime import datetime
from config import TZ

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def format_hhmm(hhmm):
    if isinstance(hhmm, str) and len(hhmm) == 4:
        return f"{hhmm[:2]}:{hhmm[2:]}"
    return hhmm
