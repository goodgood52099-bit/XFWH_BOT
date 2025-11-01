import threading, time, traceback
from datetime import datetime, time as dt_time
from modules.utils import chunk_list, generate_unique_name
from modules.pending import get_pending_for, set_pending_for, clear_pending_for
from modules.shifts import find_shift, generate_latest_shift_list, is_future_time
from modules.groups import broadcast_to_groups
from modules.telegram_api import send_message, answer_callback
from config import TZ

# 全域變數
double_staffs = {}
first_notify_sent = {}
asked_shifts = set()


# -------------------------------
# 主按鈕處理
# -------------------------------
def handle_main(user_id, chat_id, action, callback_id):
    path = ensure_today_file()
    datafile = load_json_file(path)

    def reply(text, buttons=None):
        send_message(chat_id, text, buttons=buttons)
        answer_callback(callback_id)

    # 預約時段
    if action == "reserve":
        now = datetime.now(TZ)
        shifts = [s for s in datafile.get("shifts", []) if s.get("time") and datetime.combine(now.date(), dt_time(*map(int, s["time"].split(":")))).replace(tzinfo=TZ) > now]
        if not shifts:
            return reply("📅 目前沒有可預約的時段。")

        rows = []
        row = []
        for s in shifts:
            used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
            limit = s.get("limit", 1)
            btn = {"text": f"{s['time']} ({limit - used})", "callback_data": f"reserve_pick|{s['time']}"} if used < limit else {"text": f"{s['time']} (滿)", "callback_data": "noop"}
            row.append(btn)
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請選擇要預約的時段：", buttons=rows)

    # 客到
    if action == "arrive":
        bookings_for_group = [{"time": s["time"], "name": b.get("name")} for s in datafile.get("shifts", []) for b in s.get("bookings", []) if b.get("chat_id") == chat_id]
        if not bookings_for_group:
            return reply("目前沒有未報到的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"arrive_select|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 2)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請點選要標記客到的預約：", buttons=rows)

    # 修改預約
    if action == "modify":
        bookings_for_group = [{"time": s["time"], "name": b.get("name")} for s in datafile.get("shifts", []) for b in s.get("bookings", []) if b.get("chat_id") == chat_id]
        if not bookings_for_group:
            return reply("目前沒有可修改的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"modify_pick|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 1)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請選擇要修改的預約：", buttons=rows)

    # 取消預約
    if action == "cancel":
        bookings_for_group = [{"time": s["time"], "name": b.get("name")} for s in datafile.get("shifts", []) for b in s.get("bookings", []) if b.get("chat_id") == chat_id]
        if not bookings_for_group:
            return reply("目前沒有可取消的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"cancel_pick|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 1)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請選擇要取消的預約：", buttons=rows)


# -------------------------------
# Staff 按鈕處理
# -------------------------------
def handle_staff_callback(user_id, chat_id, action, parts, callback_id):
    def reply(text, buttons=None):
        send_message(chat_id, text, buttons=buttons)
        answer_callback(callback_id)

    if action == "staff_up":
        _, hhmm, name, business_chat_id = parts
        key = f"{hhmm}|{name}|{business_chat_id}"
        if key not in first_notify_sent:
            send_message(int(business_chat_id), f"⬆️ 上 {hhmm} {name}")
            first_notify_sent[key] = True
        staff_buttons = [[
            {"text": "輸入客資", "callback_data": f"input_client|{hhmm}|{name}|{business_chat_id}"},
            {"text": "未消", "callback_data": f"not_consumed|{hhmm}|{name}|{business_chat_id}"}
        ]]
        return reply(f"✅ 已通知業務 {name}", buttons=staff_buttons)

    elif action == "input_client":
        _, hhmm, business_name, business_chat_id = parts
        pending_data = {"action": "input_client", "hhmm": hhmm, "business_name": business_name, "business_chat_id": business_chat_id}
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請輸入客稱、年紀、服務人員與金額（格式：小美 25 Alice 3000）")

    elif action == "not_consumed":
        _, hhmm, name, business_chat_id = parts
        pending_data = {"action": "not_consumed_wait_reason", "hhmm": hhmm, "name": name, "business_chat_id": business_chat_id}
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請輸入未消原因：")

    elif action == "double":
        _, hhmm, business_name, business_chat_id = parts
        first_staff = get_staff_name(user_id)
        key = f"{hhmm}|{business_name}"
        if key in double_staffs:
            return reply(f"⚠️ {hhmm} {business_name} 已有人選擇第一位服務員：{double_staffs[key][0]}")
        pending_data = {"action": "double_wait_second", "hhmm": hhmm, "business_name": business_name, "business_chat_id": business_chat_id, "first_staff": first_staff}
        set_pending_for(user_id, pending_data)
        return reply(f"✏️ 請輸入另一位服務員名字，與 {first_staff} 配合雙人服務")

    elif action == "complete":
        _, hhmm, business_name, business_chat_id = parts
        key = f"{hhmm}|{business_name}"
        staff_list = double_staffs.get(key, [get_staff_name(user_id)])
        pending_data = {"action": "complete_wait_amount", "hhmm": hhmm, "business_name": business_name, "business_chat_id": business_chat_id, "staff_list": staff_list}
        set_pending_for(user_id, pending_data)
        return reply(f"✏️ 請輸入 {hhmm} {business_name} 的總金額（數字）：")

    elif action == "fix":
        _, hhmm, business_name, business_chat_id = parts
        pending_data = {"action": "input_client", "hhmm": hhmm, "business_name": business_name, "business_chat_id": business_chat_id}
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請重新輸入客資（格式：小美 25 Alice 3000）")

    else:
        return reply("⚠️ 無效按鈕")


# -------------------------------
# Callback Query 處理
# -------------------------------
def handle_callback_query(cq):
    callback_id = cq["id"]
    data = cq["data"]
    user_id = cq["from"]["id"]
    chat_id = cq["message"]["chat"]["id"]

    # 統一取消
    if data == "cancel_flow":
        clear_pending_for(user_id)
        send_message(chat_id, "❌ 已取消操作。")
        answer_callback(callback_id)
        return

    pending = get_pending_for(user_id)

    if data.startswith("main|"):
        handle_main(user_id, chat_id, data.split("|")[1], callback_id)
        return

    if data.startswith("reserve_pick|"):
        if pending:
            return send_message(chat_id, "⚠️ 你目前有未完成操作，請先完成或取消。")
        hhmm = data.split("|")[1]
        set_pending_for(user_id, {"action": "reserve_wait_name", "hhmm": hhmm, "group_chat": chat_id, "created_at": time.time()})
        send_message(chat_id, f"✏️ 請輸入要預約 {hhmm} 的姓名：")
        answer_callback(callback_id)
        return

    # staff 行為
    staff_actions = ["staff_up", "input_client", "not_consumed", "double", "complete", "fix"]
    for act in staff_actions:
        if data.startswith(act + "|"):
            handle_staff_callback(user_id, chat_id, act, data.split("|"), callback_id)
            return

    send_message(chat_id, "⚠️ 此按鈕暫時無效")
    answer_callback(callback_id)


# -------------------------------
# 自動整點公告
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                text = generate_latest_shift_list()
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                broadcast_to_groups(text, group_type="business", buttons=buttons)
            except:
                traceback.print_exc()
            time.sleep(60)
        time.sleep(10)


# -------------------------------
# 自動詢問預約者是否到場
# -------------------------------
def ask_arrivals_thread():
    global asked_shifts
    while True:
        now = datetime.now(TZ)
        current_hm = f"{now.hour:02d}:00"
        today = now.date().isoformat()
        key = f"{today}|{current_hm}"
        if now.minute == 0 and key not in asked_shifts:
            asked_shifts.add(key)
        if now.hour == 0 and now.minute == 1:
            asked_shifts.clear()
        time.sleep(10)
