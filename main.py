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
# 設定區
# -------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ 請在 Render/Zeabur 環境變數設定 BOT_TOKEN")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

PENDING_FILE = os.path.join(DATA_DIR, "pending.json")
GROUP_FILE = os.path.join(DATA_DIR, "groups.json")

app = Flask(__name__)
ADMIN_IDS = [7236880214, 7807558825, 7502175264]  # 管理員 Telegram ID，自行修改
TZ = ZoneInfo("Asia/Taipei")  # 台灣時區

double_staffs = {}  # 用於紀錄雙人服務
asked_shifts = set()

# -------------------------------
# pending 狀態（persist 到檔案，key = user_id 字串）
# -------------------------------
def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_pending(d):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def set_pending_for(user_id, payload):
    p = load_pending()
    p[str(user_id)] = payload
    save_pending(p)


def get_pending_for(user_id):
    return load_pending().get(str(user_id))


def clear_pending_for(user_id):
    p = load_pending()
    if str(user_id) in p:
        del p[str(user_id)]
        save_pending(p)


# -------------------------------
# 群組管理
# -------------------------------
def load_groups():
    if os.path.exists(GROUP_FILE):
        with open(GROUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_groups(groups):
    with open(GROUP_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def add_group(chat_id, chat_type, group_role="business"):
    groups = load_groups()
    for g in groups:
        if g["id"] == chat_id:
            g["type"] = group_role
            save_groups(groups)
            return

    if chat_type in ["group", "supergroup"]:
        groups.append({"id": chat_id, "type": group_role})
        save_groups(groups)


def get_group_ids_by_type(group_type=None):
    groups = load_groups()
    if group_type:
        return [g["id"] for g in groups if g.get("type") == group_type]
    return [g["id"] for g in groups]


# -------------------------------
# JSON 存取（每日檔）
# -------------------------------
def data_path_for(day):
    return os.path.join(DATA_DIR, f"{day}.json")


def load_json_file(path, default=None):
    if not os.path.exists(path):
        return default or {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_today_file(workers=3):
    today = datetime.now(TZ).date().isoformat()
    path = data_path_for(today)
    now = datetime.now(TZ)

    if os.path.exists(path):
        data = load_json_file(path)
        if data.get("date") != today:
            os.remove(path)

    if not os.path.exists(path):
        shifts = []
        for h in range(13, 23):  # 13:00 ~ 22:00
            shift_time = dt_time(h, 0)
            shift_dt = datetime.combine(now.date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({
                    "time": f"{h:02d}:00",
                    "limit": workers,
                    "bookings": [],
                    "in_progress": []
                })
        save_json_file(path, {"date": today, "shifts": shifts, "候補": []})

    return path


def find_shift(shifts, hhmm):
    return next((s for s in shifts if s.get("time") == hhmm), None)


def is_future_time(hhmm):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, hhmm.split(":"))
        shift_dt = datetime.combine(now.date(), dt_time(hh, mm)).replace(tzinfo=TZ)
        return shift_dt > now
    except ValueError:
        return False


# -------------------------------
# Telegram 發送（支援按鈕）
# -------------------------------
def send_request(method, payload):
    return requests.post(API_URL + method, json=payload).json()


def send_message(chat_id, text, buttons=None, parse_mode="Markdown"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return send_request("sendMessage", payload)


def answer_callback(callback_id, text=None, show_alert=False):
    payload = {"callback_query_id": callback_id, "show_alert": show_alert}
    if text:
        payload["text"] = text
    return send_request("answerCallbackQuery", payload)


def broadcast_to_groups(message, group_type=None, buttons=None):
    gids = get_group_ids_by_type(group_type)
    for gid in gids:
        try:
            send_message(gid, message, buttons=buttons)
        except Exception:
            traceback.print_exc()


# -------------------------------
# 生成最新時段列表（文字）
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

        shift_dt = datetime.combine(now.date(), datetime.strptime(time_label, "%H:%M").time()).replace(tzinfo=TZ)
        shift_is_past = shift_dt < now

        regular_in_progress = [x for x in in_progress if not str(x).endswith("(候補)")]
        backup_in_progress = [x for x in in_progress if str(x).endswith("(候補)")]

        checked_in_lines.extend(f"{time_label} {x} ✅" for x in regular_in_progress + backup_in_progress)

        for b in bookings:
            name = b.get("name") if isinstance(b, dict) else b
            msg_lines.append(f"{time_label} {name}")

        used_slots = len(bookings) + len(regular_in_progress)
        remaining = max(0, limit - used_slots)

        if not shift_is_past:
            msg_lines.extend(f"{time_label} " for _ in range(remaining))

    if not msg_lines and not checked_in_lines:
        return "📅 今日所有時段已過"

    text = "📅 今日最新時段列表（未到時段）：\n"
    text += "\n".join(msg_lines) if msg_lines else "（目前無未到時段）"
    if checked_in_lines:
        text += "\n\n【已報到】\n" + "\n".join(checked_in_lines)

    return text


# -------------------------------
# 工具函數：生成唯一名稱
# -------------------------------
def generate_unique_name(bookings, base_name):
    existing = [b["name"] for b in bookings if isinstance(b, dict)]
    if base_name not in existing:
        return base_name
    idx = 2
    while f"{base_name}({idx})" in existing:
        idx += 1
    return f"{base_name}({idx})"


# -------------------------------
# UI helpers for inline keyboards
# -------------------------------
def chunk_list(lst, n):
    """split list into rows of len n"""
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def build_shifts_buttons(shifts, row_size=3):
    btns = [{"text": s["time"], "callback_data": f"reserve|{s['time']}"} for s in shifts]
    rows = chunk_list(btns, row_size)
    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    return rows


def build_bookings_buttons(bookings, chat_id, prefix):
    btns = [{"text": b.get("name"), "callback_data": f"{prefix}|{b.get('name')}"} for b in bookings]
    if not btns:
        btns = [{"text": "（無）", "callback_data": "noop"}]

    btns_rows = chunk_list(btns, 2)
    btns_rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    return btns_rows
# -------------------------------
# 文字訊息處理入口（重構）
# -------------------------------
def handle_text_message(msg):
    text = msg.get("text", "").strip() if msg.get("text") else ""
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = msg.get("from", {})
    user_id = user.get("id")
    user_name = user.get("first_name", "")

    # 新群組自動記錄為 business
    add_group(chat_id, chat_type)

    # 1️⃣ 處理 pending（等待輸入的動作）
    pending = get_pending_for(user_id)
    if pending:
        return _handle_pending(user_id, chat_id, text, pending)

    # 2️⃣ 一般指令
    if text == "/help":
        return _cmd_help(chat_id)

    if text.startswith("/STAFF"):
        return _cmd_staff(chat_id, user_id)

    if text == "/list":
        return _cmd_list(chat_id)

    # 3️⃣ 管理員指令
    if user_id in ADMIN_IDS:
        if text.startswith("/addshift"):
            return _add_shift(chat_id, text)
        elif text.startswith("/updateshift"):
            return _update_shift(chat_id, text)
        elif text.startswith("刪除"):
            return _delete_shift_entry(chat_id, text)
# -------------------------------
# 管理員刪除功能入口
# -------------------------------
def _delete_shift_entry(chat_id, text):
    parts = text.split()
    if len(parts) < 3:
        send_message(chat_id, "❗ 格式錯誤\n請輸入：\n刪除 HH:MM 名稱 / 數量 / all")
        return

    hhmm, target = parts[1], " ".join(parts[2:])
    path = ensure_today_file()
    data = load_json_file(path)

    shift = find_shift(data.get("shifts", []), hhmm)
    if not shift:
        send_message(chat_id, f"⚠️ 找不到 {hhmm} 的時段")
        return

    # 根據 target 類型呼叫對應刪除函式
    if target.lower() == "all":
        _delete_all_entries(chat_id, shift, hhmm, data, path)
    elif target.isdigit():
        _delete_slots_by_number(chat_id, shift, hhmm, int(target), data, path)
    else:
        _delete_entry_by_name(chat_id, shift, hhmm, target, data, path)


# -------------------------------
# 刪除全部預約（未報到 + 已報到）
# -------------------------------
def _delete_all_entries(chat_id, shift, hhmm, data, path):
    count_b = len(shift.get("bookings", []))
    count_i = len(shift.get("in_progress", []))
    shift["bookings"].clear()
    shift["in_progress"].clear()
    save_json_file(path, data)
    send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單（未報到 {count_b}、已報到 {count_i}）")


# -------------------------------
# 刪除指定名額數量
# -------------------------------
def _delete_slots_by_number(chat_id, shift, hhmm, remove_count, data, path):
    old_limit = shift.get("limit", 1)
    shift["limit"] = max(0, old_limit - remove_count)
    save_json_file(path, data)
    send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額（原本 {old_limit} → 現在 {shift['limit']}）")


# -------------------------------
# 刪除指定姓名或候補
# -------------------------------
def _delete_entry_by_name(chat_id, shift, hhmm, name, data, path):
    removed_from = None

    # 嘗試從 bookings 移除
    for b in list(shift.get("bookings", [])):
        if b.get("name") == name:
            shift["bookings"].remove(b)
            removed_from = "bookings"
            break

    # 嘗試從 in_progress 移除
    if not removed_from:
        for i in list(shift.get("in_progress", [])):
            if (isinstance(i, dict) and i.get("name") == name) or (isinstance(i, str) and i == name):
                shift["in_progress"].remove(i)
                removed_from = "in_progress"
                break

    # 嘗試從候補移除
    if not removed_from:
        before_len = len(data.get("候補", []))
        data["候補"] = [c for c in data.get("候補", []) if not (c.get("time") == hhmm and c.get("name") == name)]
        if len(data["候補"]) < before_len:
            removed_from = "候補"

    if removed_from:
        save_json_file(path, data)
        type_label = {"bookings": "未報到", "in_progress": "已報到", "候補": "候補"}.get(removed_from, "")
        send_message(chat_id, f"✅ 已從 {hhmm} 移除 {name}（{type_label}）")
    else:
        send_message(chat_id, f"⚠️ {hhmm} 找不到 {name}")
# -------------------------------
# 新增時段指令 /addshift
# -------------------------------
def _add_shift(chat_id, text):
    parts = text.split()
    if len(parts) < 3:
        send_message(chat_id, "⚠️ 格式：/addshift HH:MM 限制")
        return

    hhmm, limit_text = parts[1], parts[2]
    try:
        limit = int(limit_text)
    except ValueError:
        send_message(chat_id, "⚠️ 限制人數必須為數字")
        return

    path = ensure_today_file()
    data = load_json_file(path)

    if find_shift(data.get("shifts", []), hhmm):
        send_message(chat_id, f"⚠️ {hhmm} 已存在")
        return

    data["shifts"].append({"time": hhmm, "limit": limit, "bookings": [], "in_progress": []})
    save_json_file(path, data)
    send_message(chat_id, f"✅ 新增 {hhmm} 時段，限制 {limit} 人")


# -------------------------------
# 更新時段限制指令 /updateshift
# -------------------------------
def _update_shift(chat_id, text):
    parts = text.split()
    if len(parts) < 3:
        send_message(chat_id, "⚠️ 格式：/updateshift HH:MM 限制")
        return

    hhmm, limit_text = parts[1], parts[2]
    try:
        limit = int(limit_text)
    except ValueError:
        send_message(chat_id, "⚠️ 限制人數必須為數字")
        return

    path = ensure_today_file()
    data = load_json_file(path)

    shift = find_shift(data.get("shifts", []), hhmm)
    if not shift:
        send_message(chat_id, f"⚠️ {hhmm} 不存在")
        return

    shift["limit"] = limit
    save_json_file(path, data)
    send_message(chat_id, f"✅ {hhmm} 時段限制已更新為 {limit}")
def _cmd_help(chat_id):
    help_text = """
📌 *Telegram 預約機器人指令說明* 📌

一般使用者：
- 按 /list 來查看時段並用按鈕操作

管理員：
- 上:上 12:00 王小明
- 刪除 13:00 all
- 刪除 13:00 2
- 刪除 13:00 小明
- /addshift HH:MM 限制
- /updateshift HH:MM 限制
- /STAFF 設定本群為服務員群組
"""
    send_message(chat_id, help_text)

def _cmd_staff(chat_id, user_id):
    if user_id not in ADMIN_IDS:
        send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
        return
    add_group(chat_id, "group", group_role="staff")
    send_message(chat_id, "✅ 已將本群組設定為服務員群組")

def _cmd_list(chat_id):
    shift_text = generate_latest_shift_list()
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    # parse_mode=None 避免 emoji 與 Markdown 解析錯誤
    send_message(chat_id, shift_text, buttons=buttons, parse_mode=None)

# -------------------------------
# Pending 分流
# -------------------------------
def _handle_pending(user_id, chat_id, text, pending):
    action = pending.get("action")

    if action == "reserve_wait_name":
        return _pending_reserve_wait_name(user_id, text, pending)

    elif action == "arrive_wait_amount":
        return _pending_arrive_wait_amount(user_id, text, pending)

    elif action == "input_client":
        return _pending_input_client(user_id, text, pending)

    elif action == "double_wait_second":
        return _pending_double_wait_second(user_id, text, pending)

    elif action == "complete_wait_amount":
        return _pending_complete_wait_amount(user_id, text, pending)

    elif action == "not_consumed_wait_reason":
        return _pending_not_consumed_wait_reason(user_id, text, pending)

    elif action == "modify_wait_name":
        return _pending_modify_wait_name(user_id, text, pending)

    else:
        clear_pending_for(user_id)
        return
# -------------------------------
# Pending 動作
# -------------------------------

# 預約輸入名字
def _pending_reserve_wait_name(user_id, text, pending):
    hhmm = pending.get("hhmm")
    group_chat = pending.get("group_chat")
    name_input = text.strip()

    path = ensure_today_file()
    data = load_json_file(path)
    shift = find_shift(data.get("shifts", []), hhmm)
    if not shift:
        send_message(group_chat, f"⚠️ 時段 {hhmm} 不存在或已過期。")
        clear_pending_for(user_id)
        return

    used = len(shift.get("bookings", [])) + len([x for x in shift.get("in_progress", []) if not str(x).endswith("(候補)")])
    limit = shift.get("limit", 1)
    if used >= limit:
        send_message(group_chat, f"⚠️ {hhmm} 已滿額，無法預約。")
        clear_pending_for(user_id)
        return

    unique_name = generate_unique_name(shift.get("bookings", []), name_input)
    shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)

    send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")

    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
    clear_pending_for(user_id)

# 客到輸入金額
def _pending_arrive_wait_amount(user_id, text, pending):
    hhmm = pending["hhmm"]
    name = pending["name"]
    group_chat = pending["group_chat"]
    amount_text = text.strip()

    try:
        amount = float(amount_text)
    except ValueError:
        send_message(group_chat, "⚠️ 金額格式錯誤，請輸入數字")
        return

    path = ensure_today_file()
    data = load_json_file(path)
    shift = find_shift(data.get("shifts", []), hhmm)
    if not shift:
        send_message(group_chat, f"⚠️ 找不到時段 {hhmm}")
        clear_pending_for(user_id)
        return

    booking = next((b for b in shift.get("bookings", []) if b.get("name") == name and b.get("chat_id") == group_chat), None)
    if booking:
        shift.setdefault("in_progress", []).append({"name": name, "amount": amount})
        shift["bookings"] = [b for b in shift.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == group_chat)]
        save_json_file(path, data)

        send_message(group_chat, f"✅ {hhmm} {name} 已客到，金額：{amount}")

        staff_message = f"🙋‍♀️ 客到通知\n時間：{hhmm}\n業務名：{name}\n金額：{amount}"
        staff_buttons = [[{"text": "上", "callback_data": f"staff_up|{hhmm}|{name}|{group_chat}"}]]
        broadcast_to_groups(staff_message, group_type="staff", buttons=staff_buttons)

    else:
        send_message(group_chat, f"⚠️ 找不到預約 {name} 或已被移除")
    clear_pending_for(user_id)

# 輸入客資    
def _pending_input_client(user_id, text, pending):
    chat_id = pending.get("chat_id")
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return {"ok": True}

    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]

    msg_business = f"📌 客\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}"
    send_message(int(business_chat_id), msg_business)

    staff_buttons = [
        [
            {"text": "雙", "callback_data": f"double|{hhmm}|{business_name}|{business_chat_id}"},
            {"text": "完成服務", "callback_data": f"complete|{hhmm}|{business_name}|{business_chat_id}"},
            {"text": "修正", "callback_data": f"fix|{hhmm}|{business_name}|{business_chat_id}"}
        ]
    ]
    send_message(chat_id, msg_business, buttons=staff_buttons)
    clear_pending_for(user_id)
    return {"ok": True}

# 完成服務輸入金額
def _pending_complete_wait_amount(user_id, text, pending):
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    staff_list = pending["staff_list"]
    staff_str = "、".join(staff_list)

    amount_text = text.strip()
    try:
        amount = float(amount_text)
    except ValueError:
        send_message(user_id, "⚠️ 金額格式錯誤，請輸入數字")
        return

    msg = f"✅ 完成服務通知\n{hhmm} {business_name}\n服務人員: {staff_str}\n金額: {amount}"
    send_message(user_id, msg)
    send_message(int(business_chat_id), msg)
    clear_pending_for(user_id)
    return {"ok": True}

# 未消輸入原因
def _pending_not_consumed_wait_reason(user_id, text, pending):
    hhmm = pending["hhmm"]
    name = pending["name"]
    business_chat_id = pending["business_chat_id"]
    reason = text.strip()

    msg = f"⚠️ 未消: {name} {reason}"
    send_message(user_id, f"掰掰謝謝光臨!!")  # 可發給服務員群確認
    send_message(int(business_chat_id), msg)  # 發給業務群

    clear_pending_for(user_id)
    return {"ok": True}

# 修改預約
def _pending_modify_wait_name(user_id, text, pending):
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
        clear_pending_for(user_id)
        return

    booking = next((b for b in old_shift.get("bookings", []) if b.get("name") == old_name and b.get("chat_id") == group_chat), None)
    if not booking:
        send_message(group_chat, f"⚠️ 找不到 {old_hhmm} 的預約 {old_name}。")
        clear_pending_for(user_id)
        return

    new_shift = find_shift(data.get("shifts", []), new_hhmm)
    if not new_shift:
        send_message(group_chat, f"⚠️ 新時段 {new_hhmm} 不存在。")
        clear_pending_for(user_id)
        return

    used_new = len(new_shift.get("bookings", [])) + len([x for x in new_shift.get("in_progress", []) if not str(x).endswith("(候補)")])
    if used_new >= new_shift.get("limit", 1):
        send_message(group_chat, f"⚠️ {new_hhmm} 已滿額，無法修改。")
        clear_pending_for(user_id)
        return

    old_shift["bookings"] = [b for b in old_shift.get("bookings", []) if not (b.get("name") == old_name and b.get("chat_id") == group_chat)]
    unique_name = generate_unique_name(new_shift.get("bookings", []), new_name_input)
    new_shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)

    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
    send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")
    clear_pending_for(user_id)

# 雙人服務
def _pending_double_wait_second(user_id, text, pending):
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    first_staff = pending["first_staff"]

    second_staff = text.strip()

    double_staffs[hhmm] = [first_staff, second_staff]
    staff_list = "、".join(double_staffs[hhmm])

    send_message(int(business_chat_id), f"👥 雙人服務更新：{staff_list}")
    clear_pending_for(user_id)
    return {"ok": True}

# -------------------------------
# callback_query 處理（按鈕）
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()

        if "message" in update:
            handle_text_message(update["message"])
            return {"ok": True}

        if "callback_query" in update:
            cq = update["callback_query"]
            data = cq.get("data")
            callback_id = cq.get("id")
            from_user = cq.get("from", {})
            user_id = from_user.get("id")
            chat_id = (cq.get("message", {}) or {}).get("chat", {}).get("id")

            # -------- Helper functions --------
            def respond(msg, buttons=None, answer=True):
                if buttons:
                    send_message(chat_id, msg, buttons=buttons)
                else:
                    send_message(chat_id, msg)
                if answer:
                    answer_callback(callback_id)
                return {"ok": True}

            def get_bookings_for_group():
                path = ensure_today_file()
                datafile = load_json_file(path)
                bookings = []
                for s in datafile.get("shifts", []):
                    for b in s.get("bookings", []):
                        if b.get("chat_id") == chat_id:
                            bookings.append({"time": s["time"], "name": b.get("name")})
                return bookings, datafile

            # -------- Main actions --------
            if data and data.startswith("main|"):
                _, action = data.split("|", 1)
                path = ensure_today_file()
                datafile = load_json_file(path)

                if action == "reserve":
                    shifts = [s for s in datafile.get("shifts", []) if is_future_time(s.get("time", ""))]
                    rows = []
                    row = []
                    for s in shifts:
                        used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
                        limit = s.get("limit", 1)
                        text = f"{s['time']} ({limit - used})" if used < limit else f"{s['time']} (滿)"
                        row.append({"text": text, "callback_data": f"reserve_pick|{s['time']}" if used < limit else "noop"})
                        if len(row) == 3:
                            rows.append(row)
                            row = []
                    if row: rows.append(row)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    return respond("請選擇要預約的時段：", buttons=rows)

                # actions: arrive / modify / cancel 都是同樣流程
                if action in ("arrive", "modify", "cancel"):
                    bookings, _ = get_bookings_for_group()
                    if not bookings:
                        return respond(f"目前沒有可{action}的預約。")

                    btns = []
                    for bk in bookings:
                        cb_prefix = f"{action}_pick" if action != "arrive" else "arrive_select"
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"{cb_prefix}|{bk['time']}|{bk['name']}"})
                    chunk_size = 2 if action == "arrive" else 1
                    rows = chunk_list(btns, chunk_size)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    return respond(f"請選擇要{action}的預約：", buttons=rows)

            # -------- Reserve pick --------
            if data and data.startswith("reserve_pick|"):
                _, hhmm = data.split("|", 1)
                set_pending_for(user_id, {"action": "reserve_wait_name", "hhmm": hhmm, "group_chat": chat_id})
                return respond(f"✏️ 請在此群輸入欲預約的/姓名（針對 {hhmm}）。")

            # -------- Arrive select --------
            if data and data.startswith("arrive_select|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    return answer_callback(callback_id, "資料錯誤")
                _, hhmm, name = parts
                set_pending_for(user_id, {"action": "arrive_wait_amount", "hhmm": hhmm, "name": name, "group_chat": chat_id})
                return respond(f"✏️ 請輸入 {hhmm} {name} 的金額（數字）：")

            # -------- Modify pick / to --------
            if data and data.startswith("modify_pick|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    return answer_callback(callback_id, "資料錯誤")
                _, old_hhmm, old_name = parts
                path = ensure_today_file()
                datafile = load_json_file(path)
                shifts = [s for s in datafile.get("shifts", []) if is_future_time(s.get("time",""))]
                rows, row = [], []
                for s in shifts:
                    row.append({"text": s["time"], "callback_data": f"modify_to|{old_hhmm}|{old_name}|{s['time']}"})
                    if len(row) == 3:
                        rows.append(row)
                        row = []
                if row: rows.append(row)
                rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                return respond(f"要將 {old_hhmm} {old_name} 修改到哪個時段？", buttons=rows)

            if data and data.startswith("modify_to|"):
                parts = data.split("|", 3)
                if len(parts) < 4:
                    return answer_callback(callback_id, "資料錯誤")
                _, old_hhmm, old_name, new_hhmm = parts
                set_pending_for(user_id, {"action": "modify_wait_name", "old_hhmm": old_hhmm, "old_name": old_name, "new_hhmm": new_hhmm, "group_chat": chat_id})
                return respond(f"請輸入新的姓名（或輸入原姓名 `{old_name}` 保留）以完成從 {old_hhmm} → {new_hhmm} 的修改：")

            # -------- Cancel pick / confirm --------
            if data and data.startswith("cancel_pick|"):
                _, hhmm, name = data.split("|", 2)
                buttons = [[
                    {"text": "確認取消", "callback_data": f"confirm_cancel|{hhmm}|{name}"},
                    {"text": "取消", "callback_data": "cancel_flow"}
                ]]
                return respond(f"確定要取消 {hhmm} {name} 的預約嗎？", buttons=buttons)

            if data and data.startswith("confirm_cancel|"):
                _, hhmm, name = data.split("|", 2)
                path = ensure_today_file()
                datafile = load_json_file(path)
                s = find_shift(datafile.get("shifts", []), hhmm)
                if not s:
                    return answer_callback(callback_id, "找不到該時段")
                s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == chat_id)]
                save_json_file(path, datafile)
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
                return respond(f"✅ 已取消 {hhmm} {name} 的預約")

            # -------- Cancel / No-op --------
            if data in ("cancel_flow", "noop"):
                return answer_callback(callback_id, "已取消")

            # -------- Staff / Business flow --------
            # staff_up -> 通知業務 + 顯示服務員按鈕
            if data and data.startswith("staff_up|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)
                send_message(int(business_chat_id), f"⬆️ 上 {hhmm} {name}")

                staff_buttons = [[
                    {"text": "輸入客資", "callback_data": f"input_client|{hhmm}|{name}|{business_chat_id}"},
                    {"text": "未消", "callback_data": f"not_consumed|{hhmm}|{name}|{business_chat_id}"}
                ]]
                send_message(chat_id, f"✅ 已通知業務 {name}", buttons=staff_buttons)
                answer_callback(callback_id)
                return {"ok": True}

            # 服務員 -> 輸入客資
            if data and data.startswith("input_client|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "input_client",
                    "hhmm": hhmm,
                    "business_name": name,
                    "business_chat_id": business_chat_id
                })
                send_message(chat_id, "✏️ 請輸入客稱、年紀、服務人員與金額（格式：小帥 25 小美 3000）")
                answer_callback(callback_id)
                return {"ok": True}

            # 服務員 -> 未消
            if data and data.startswith("not_consumed|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "not_consumed_wait_reason",
                    "hhmm": hhmm,
                    "name": name,
                    "business_chat_id": business_chat_id
                })
                send_message(chat_id, "✏️ 請輸入未消原因：")
                answer_callback(callback_id)
                return {"ok": True}

            # 雙人服務（按鈕觸發）
            if data and data.startswith("double|"):
                _, hhmm, business_name, business_chat_id = data.split("|")
                first_staff = get_staff_name(user_id)

                # 設定 pending 等待輸入第二位服務員
                set_pending_for(user_id, {
                    "action": "double_wait_second",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id,
                    "first_staff": first_staff
                })

                send_message(chat_id, f"✏️ 請輸入另一位服務員名字，與 {first_staff} 配合雙人服務")
                answer_callback(callback_id)
                return {"ok": True}

            # 完成服務
            if data and data.startswith("complete|"):
                _, hhmm, business_name, business_chat_id = data.split("|", 3)

                # 支援雙人服務
                staff_list = double_staffs.get(hhmm, [get_staff_name(user_id)])
                staff_str = "、".join(staff_list)

                # 設 pending 等待輸入實際金額
                set_pending_for(user_id, {
                    "action": "complete_wait_amount",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id,
                    "staff_list": staff_list
                })

                send_message(chat_id, f"✏️ 請輸入 {hhmm} {business_name} 的總金額（數字）：")
                answer_callback(callback_id)
                return {"ok": True} 

            # 修正 -> 重新輸入客資
            if data and data.startswith("fix|"):
                _, hhmm, business_name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "input_client",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id
                })
                send_message(chat_id, "✏️ 請重新輸入客資（格式：小美 25 Alice 3000）")
                answer_callback(callback_id)
                return {"ok": True}

            # fallback
            return answer_callback(callback_id, "無效操作。")

    except Exception:
        traceback.print_exc()
    return {"ok": True}


# -------------------------------
# 自動任務
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
            except:
                traceback.print_exc()
            time.sleep(60)
        time.sleep(10)


def ask_arrivals_thread():
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

        if now.hour == 0 and now.minute == 1:
            asked_shifts.clear()

        time.sleep(10)


# -------------------------------
# 啟動背景執行緒
# -------------------------------
threading.Thread(target=auto_announce, daemon=True).start()
threading.Thread(target=ask_arrivals_thread, daemon=True).start()

# -------------------------------
# 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))


