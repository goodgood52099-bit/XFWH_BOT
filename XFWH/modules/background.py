import threading
import time
import os
import traceback
from datetime import datetime
from config import TZ
from modules.shifts import generate_latest_shift_list, data_path_for, load_json_file
from modules.telegram_api import send_message

asked_shifts = set()  # 紀錄已詢問過的時段

def auto_announce():
    """每整點公告班表給業務群"""
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                text = generate_latest_shift_list()
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                send_message(None, text, buttons=buttons, broadcast_type="business")
            except Exception:
                traceback.print_exc()
            time.sleep(60)
        time.sleep(10)

def ask_arrivals_thread():
    """自動詢問預約者是否到場"""
    global asked_shifts
    while True:
        now = datetime.now(TZ)
        current_hm = f"{now.hour:02d}:00"
        today = now.date().isoformat()
        key = f"{today}|{current_hm}"

        if now.minute == 0 and key not in asked_shifts:
            path = data_path_for(today)
            if os.path.exists(path):
                data = load_json_file(path)
                for s in data.get("shifts", []):
                    if s.get("time") != current_hm:
                        continue
                    waiting = []
                    groups_to_notify = set()
                    for b in s.get("bookings", []):
                        name = b.get("name")
                        gid = b.get("chat_id")
                        if name not in [x["name"] if isinstance(x, dict) else x for x in s.get("in_progress", [])]:
                            waiting.append(name)
                            groups_to_notify.add(gid)
                    if waiting:
                        names_text = "、".join(waiting)
                        text = f"⏰ 現在是 {current_hm}\n請問預約的「{names_text}」到了嗎？\n到了請回覆：客到 {current_hm} 名稱 或使用按鈕 /list → 客到"
                        for gid in groups_to_notify:
                            send_message(gid, text)
            asked_shifts.add(key)

        # 每天 00:01 清空
        if now.hour == 0 and now.minute == 1:
            asked_shifts.clear()

        time.sleep(10)
