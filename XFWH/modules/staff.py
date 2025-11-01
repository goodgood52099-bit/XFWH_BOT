import traceback
from modules.pending import clear_pending_for, save_json_file, load_json_file, ensure_today_file
from modules.utils import find_shift, generate_unique_name
from modules.telegram_api import send_message, broadcast_to_groups
from modules.shifts import generate_latest_shift_list

# 雙人服務記錄
double_staffs = {}

# -------------------------------
# pending 行為分流
# -------------------------------
def handle_pending_action(user_id, chat_id, text, pending):
    action = pending.get("action")
    success = True
    try:
        if action == "reserve_wait_name":
            handle_reserve_wait_name(user_id, chat_id, text, pending)
        elif action == "arrive_wait_amount":
            handle_arrive_wait_amount(user_id, chat_id, text, pending)
        elif action == "input_client":
            handle_input_client(user_id, chat_id, text, pending)
        elif action == "double_wait_second":
            handle_double_wait_second(user_id, chat_id, text, pending)
        elif action == "complete_wait_amount":
            handle_complete_wait_amount(user_id, chat_id, text, pending)
        elif action == "not_consumed_wait_reason":
            handle_not_consumed_wait_reason(user_id, chat_id, text, pending)
        elif action == "modify_wait_name":
            handle_modify_wait_name(user_id, chat_id, text, pending)
        else:
            send_message(chat_id, "⚠️ 未知動作，已清除暫存。")
    except Exception:
        traceback.print_exc()
        send_message(chat_id, f"❌ 執行動作 {action} 時發生錯誤")
        success = False
    if success:
        clear_pending_for(user_id)

# -------------------------------
# 各 pending action 函式
# -------------------------------
def handle_reserve_wait_name(user_id, chat_id, text, pending):
    hhmm = pending.get("hhmm")
    group_chat = pending.get("group_chat")
    name_input = text.strip()
    path = ensure_today_file()
    data = load_json_file(path)
    s = find_shift(data.get("shifts", []), hhmm)
    if not s:
        send_message(group_chat, f"⚠️ 時段 {hhmm} 不存在或已過期。")
        return
    used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
    if used >= s.get("limit", 1):
        send_message(group_chat, f"⚠️ {hhmm} 已滿額，無法預約。")
        return
    unique_name = generate_unique_name(s.get("bookings", []), name_input)
    s.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)
    send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)

def handle_arrive_wait_amount(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    name = pending["name"]
    group_chat = pending["group_chat"]
    try:
        amount = float(text.strip())
    except ValueError:
        send_message(group_chat, "⚠️ 金額格式錯誤，請輸入數字")
        return
    path = ensure_today_file()
    data = load_json_file(path)
    s = find_shift(data.get("shifts", []), hhmm)
    if not s:
        send_message(group_chat, f"⚠️ 找不到時段 {hhmm}")
        return
    booking = next((b for b in s.get("bookings", []) if b.get("name") == name and b.get("chat_id") == group_chat), None)
    if booking:
        s.setdefault("in_progress", []).append({"name": name, "amount": amount})
        s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == group_chat)]
        save_json_file(path, data)
        send_message(group_chat, f"✅ {hhmm} {name} 已標記到場，金額：{amount}")

def handle_input_client(user_id, chat_id, text, pending):
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return False
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    msg_business = f"📌 客\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}"
    send_message(int(business_chat_id), msg_business)
    return True

def handle_double_wait_second(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    first_staff = pending["first_staff"]
    second_staff = text.strip()
    key = f"{hhmm}|{business_name}"
    double_staffs[key] = [first_staff, second_staff]
    staff_list = "、".join(double_staffs[key])
    send_message(int(business_chat_id), f"👥 雙人服務更新：{staff_list}")

def handle_complete_wait_amount(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    staff_list = pending["staff_list"]
    staff_str = "、".join(staff_list)
    try:
        amount = float(text.strip())
    except ValueError:
        send_message(chat_id, "⚠️ 金額格式錯誤，請輸入數字")
        return
    msg = f"✅ 完成服務通知\n{hhmm} {business_name}\n服務人員: {staff_str}\n金額: {amount}"
    send_message(chat_id, msg)
    send_message(int(business_chat_id), msg)

def handle_not_consumed_wait_reason(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    name = pending["name"]
    business_chat_id = pending["business_chat_id"]
    reason = text.strip()
    send_message(chat_id, f"掰掰謝謝光臨!!")
    send_message(int(business_chat_id), f"⚠️ 未消: {name} {reason}")

def handle_modify_wait_name(user_id, chat_id, text, pending):
    old_hhmm = pending.get("old_hhmm")
    old_name = pending.get("old_name")
    new_hhmm = pending.get("new_hhmm")
    group_chat = pending.get("group_chat")
    new_name_input = text.strip()
    path = ensure_today_file()
    data = load_json_file(path)
    old_shift = find_shift(data.get("shifts", []), old_hhmm)
    if not old_shift:
        send_message(group_chat, f"⚠️ 原時段 {old_hhmm} 不存在。")
        return
    booking = next((b for b in old_shift.get("bookings", []) if b.get("name") == old_name and b.get("chat_id") == group_chat), None)
    if not booking:
        send_message(group_chat, f"⚠️ 找不到 {old_hhmm} 的預約 {old_name}。")
        return
    new_shift = find_shift(data.get("shifts", []), new_hhmm)
    if not new_shift:
        send_message(group_chat, f"⚠️ 新時段 {new_hhmm} 不存在。")
        return
    used_new = len(new_shift.get("bookings", [])) + len([x for x in new_shift.get("in_progress", []) if not str(x).endswith("(候補)")])
    if used_new >= new_shift.get("limit", 1):
        send_message(group_chat, f"⚠️ {new_hhmm} 已滿額，無法修改。")
        return
    old_shift["bookings"] = [b for b in old_shift.get("bookings", []) if not (b.get("name") == old_name and b.get("chat_id") == group_chat)]
    unique_name = generate_unique_name(new_shift.get("bookings", []), new_name_input)
    new_shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)
    send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")
