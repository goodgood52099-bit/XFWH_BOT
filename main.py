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
first_notify_sent = {}  # key = f"{hhmm}|{name}|business_chat_id"
asked_shifts = set()
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
    p = load_pending()
    return p.get(str(user_id))

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

# group_role: "staff" or "business"
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
def data_path_for(day): return os.path.join(DATA_DIR, f"{day}.json")

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
        # 13:00 ~ 22:00 (可按需修改)
        for h in range(13, 23):
            shift_time = dt_time(h, 0)
            shift_dt = datetime.combine(datetime.now(TZ).date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
        save_json_file(path, {"date": today, "shifts": shifts, "候補": []})
    return path

def find_shift(shifts, hhmm):
    for s in shifts:
        if s["time"] == hhmm:
            return s
    return None

def is_future_time(hhmm):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, hhmm.split(":"))
        shift_dt = datetime.combine(datetime.now(TZ).date(), dt_time(hh, mm)).replace(tzinfo=TZ)
        return shift_dt > now
    except:
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
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    payload["show_alert"] = show_alert
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
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def build_shifts_buttons(shifts, row_size=3):
    btns = []
    for s in shifts:
        btns.append({"text": s["time"], "callback_data": f"reserve|{s['time']}"})
    rows = chunk_list(btns, row_size)
    # add cancel row
    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    return rows

def build_bookings_buttons(bookings, chat_id, prefix):
    # bookings: list of dict {"name":..., "chat_id": ...}
    btns = []
    for b in bookings:
        name = b.get("name")
        # encode chat id so we know which group original booking belongs to (bookings store chat_id)
        btns.append({"text": name, "callback_data": f"{prefix}|{name}"})
    if not btns:
        btns = [{"text": "（無）", "callback_data": "noop"}]
    # add cancel
    btns_rows = chunk_list(btns, 2)
    btns_rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    return btns_rows

# -------------------------------
# 文字訊息處理入口
# -------------------------------
def handle_text_message(msg):
    text = msg.get("text", "").strip() if msg.get("text") else ""
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = msg.get("from", {})
    user_id = user.get("id")
    user_name = user.get("first_name", "")

    pending_dict = load_pending()    
    print("DEBUG: pending_dict =", pending_dict)
    # ----------------- 自動清理過期 pending（3 分鐘） -----------------
    try:
        pending_data = load_json_file("data/pending.json")
        now = time.time()
        expired = [uid for uid, p in pending_data.items() if now - p.get("created_at", 0) > 180]
        for uid in expired:
            del pending_data[uid]
        if expired:
            save_json_file("data/pending.json", pending_data)
            print(f"🧹 清除過期 pending: {expired}")
    except Exception as e:
        print("❌ pending 自動清理錯誤:", e)
    # ----------------- 新群組自動記錄 -----------------
    add_group(chat_id, chat_type)      
    # ----------------- pending 處理 -----------------
    pending = get_pending_for(user_id)
    if pending:
        handle_pending_action(user_id, chat_id, text, pending)
        return
    # ----------------- 指令處理 -----------------
    if text == "/help":
        help_text = """
📌 *Telegram 預約機器人指令說明* 📌

一般使用者：
- 按 /list 來查看時段並用按鈕操作

管理員：
- 刪除 13:00 all
- 刪除 13:00 2
- 刪除 13:00 小明
- /addshift HH:MM 限制
- /updateshift HH:MM 限制
- /STAFF 設定本群為服務員群組
"""
        send_message(chat_id, help_text)
        return    

    if text.startswith("/STAFF"):
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
            return
        add_group(chat_id, "group", group_role="staff")
        send_message(chat_id, "✅ 已將本群組設定為服務員群組")
        return

    if text == "/list":
        shift_text = generate_latest_shift_list() 
        buttons = [
            [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
            [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
        ]
        send_message(chat_id, shift_text, buttons=buttons)
        return


    if user_id in ADMIN_IDS:
        handle_admin_text(chat_id, text)
        return

    send_message(chat_id, "💡 請使用 /list 查看可預約時段。")
    
# -------------------------------
# 管理員文字功能（/addshift /updateshift /刪除）
# -------------------------------
def handle_admin_text(chat_id, text):
    path = ensure_today_file()
    data = load_json_file(path)

    # /addshift HH:MM 限制
    if text.startswith("/addshift"):
        parts = text.split()
        if len(parts) < 3:
            send_message(chat_id, "⚠️ 格式：/addshift HH:MM 限制")
            return
        hhmm, limit = parts[1], int(parts[2])
        if find_shift(data.get("shifts", []), hhmm):
            send_message(chat_id, f"⚠️ {hhmm} 已存在")
            return
        data["shifts"].append({"time": hhmm, "limit": limit, "bookings": [], "in_progress": []})
        save_json_file(path, data)
        send_message(chat_id, f"✅ 新增 {hhmm} 時段，限制 {limit} 人")
        return

    # /updateshift HH:MM 限制
    if text.startswith("/updateshift"):
        parts = text.split()
        if len(parts) < 3:
            send_message(chat_id, "⚠️ 格式：/updateshift HH:MM 限制")
            return
        hhmm, limit = parts[1], int(parts[2])
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            send_message(chat_id, f"⚠️ {hhmm} 不存在")
            return
        shift["limit"] = limit
        save_json_file(path, data)
        send_message(chat_id, f"✅ {hhmm} 時段限制已更新為 {limit}")
        return

    # 刪除指令
    if text.startswith("刪除"):
        parts = text.split()
        if len(parts) < 3:
            send_message(chat_id, "❗ 格式錯誤\n請輸入：\n刪除 HH:MM 名稱 / 數量 / all")
            return
        hhmm, target = parts[1], " ".join(parts[2:])
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            send_message(chat_id, f"⚠️ 找不到 {hhmm} 的時段")
            return

        # 清空全部
        if target.lower() == "all":
            count_b = len(shift.get("bookings", []))
            count_i = len(shift.get("in_progress", []))
            shift["bookings"].clear()
            shift["in_progress"].clear()
            save_json_file(path, data)
            send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單（未報到 {count_b}、已報到 {count_i}）")
            return

        # 刪除指定數量
        if target.isdigit():
            remove_count = int(target)
            old_limit = shift.get("limit", 1)
            shift["limit"] = max(0, old_limit - remove_count)
            save_json_file(path, data)
            send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額（原本 {old_limit} → 現在 {shift['limit']}）")
            return

        # 刪除指定姓名
        removed_from = None
        for b in list(shift.get("bookings", [])):
            if b.get("name") == target:
                shift["bookings"].remove(b)
                removed_from = "bookings"
                break
        if not removed_from and target in shift.get("in_progress", []):
            shift["in_progress"].remove(target)
            removed_from = "in_progress"
        if not removed_from:
            before_len = len(data.get("候補", []))
            data["候補"] = [c for c in data.get("候補", []) if not (c.get("time") == hhmm and c.get("name") == target)]
            if len(data["候補"]) < before_len:
                removed_from = "候補"

        if removed_from:
            save_json_file(path, data)
            type_label = {"bookings": "未報到", "in_progress": "已報到", "候補": "候補"}.get(removed_from, "")
            send_message(chat_id, f"✅ 已從 {hhmm} 移除 {target}（{type_label}）")
        else:
            send_message(chat_id, f"⚠️ {hhmm} 找不到 {target}")
        return


# -------------------------------
# pending 行為分流
# -------------------------------
def handle_pending_action(user_id, chat_id, text, pending):
    action = pending.get("action")
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
    finally:
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
    # 生成唯一名稱
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
        staff_message = f"🙋‍♀️ 客到通知\n時間：{hhmm}\n業務名：{name}\n金額：{amount}"
        staff_buttons = [[{"text": "上", "callback_data": f"staff_up|{hhmm}|{name}|{group_chat}"}]]
        broadcast_to_groups(staff_message, group_type="staff", buttons=staff_buttons)
    else:
        send_message(group_chat, f"⚠️ 找不到預約 {name} 或已被移除")


def handle_input_client(user_id, chat_id, text, pending):
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return
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


def handle_double_wait_second(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    business_name = pending["business_name"]
    business_chat_id = pending["business_chat_id"]
    first_staff = pending["first_staff"]
    second_staff = text.strip()
    key = f"{hhmm}|{business_name}"
    double_staffs[key] = [first_staff, second_staff]
    staff_list = "、".join(double_staffs[key])  # ✅ 這裡用 key
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
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
    send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")


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
        shifts = []
        for s in datafile.get("shifts", []):
            hhmm = s.get("time")
            if not hhmm:
                continue
            # 計算是否未來時段
            hh, mm = map(int, hhmm.split(":"))
            shift_dt = datetime.combine(now.date(), dt_time(hh, mm)).replace(tzinfo=TZ)
            if shift_dt <= now:
                continue
            shifts.append(s)

        if not shifts:
            return reply("📅 目前沒有可預約的時段。")

        rows = []
        row = []
        for s in shifts:
            used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
            limit = s.get("limit", 1)
            if used < limit:
                btn = {"text": f"{s['time']} ({limit - used})", "callback_data": f"reserve_pick|{s['time']}"}
            else:
                btn = {"text": f"{s['time']} (滿)", "callback_data": "noop"}
            row.append(btn)
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        # 加上取消按鈕
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])

        return reply("請選擇要預約的時段：", buttons=rows)

    if action == "arrive":
        bookings_for_group = []
        for s in datafile.get("shifts", []):
            for b in s.get("bookings", []):
                if b.get("chat_id") == chat_id:
                    bookings_for_group.append({"time": s["time"], "name": b.get("name")})
        if not bookings_for_group:
            return reply("目前沒有未報到的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"arrive_select|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 2)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請點選要標記客到的預約：", buttons=rows)

    if action == "modify":
        bookings_for_group = []
        for s in datafile.get("shifts", []):
            for b in s.get("bookings", []):
                if b.get("chat_id") == chat_id:
                    bookings_for_group.append({"time": s["time"], "name": b.get("name")})
        if not bookings_for_group:
            return reply("目前沒有可修改的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"modify_pick|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 1)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請選擇要修改的預約：", buttons=rows)

    if action == "cancel":
        bookings_for_group = []
        for s in datafile.get("shifts", []):
            for b in s.get("bookings", []):
                if b.get("chat_id") == chat_id:
                    bookings_for_group.append({"time": s["time"], "name": b.get("name")})
        if not bookings_for_group:
            return reply("目前沒有可取消的預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"cancel_pick|{bk['time']}|{bk['name']}"} for bk in bookings_for_group]
        rows = chunk_list(btns, 1)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return reply("請選擇要取消的預約：", buttons=rows)

# -------------------------------
# 修改選擇處理
# -------------------------------
def handle_modify_pick(user_id, chat_id, old_hhmm, old_name):
    path = ensure_today_file()
    datafile = load_json_file(path)
    shifts = [s for s in datafile.get("shifts", []) if is_future_time(s.get("time",""))]
    rows = []
    row = []
    for s in shifts:
        row.append({"text": s["time"], "callback_data": f"modify_to|{old_hhmm}|{old_name}|{s['time']}"} )
        if len(row) == 3:
            rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    send_message(chat_id, f"要將 {old_hhmm} {old_name} 修改到哪個時段？", buttons=rows)
    answer_callback(None)

# -------------------------------
# 確認取消處理
# -------------------------------
def handle_confirm_cancel(chat_id, user_id, hhmm, name, callback_id):
    path = ensure_today_file()
    datafile = load_json_file(path)
    s = find_shift(datafile.get("shifts", []), hhmm)
    if not s:
        return answer_callback(callback_id, "找不到該時段")
    s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name")==name and b.get("chat_id")==chat_id)]
    save_json_file(path, datafile)
    clear_pending_for(user_id)
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
    send_message(chat_id, f"✅ 已取消 {hhmm} {name} 的預約")
    answer_callback(callback_id)
# -------------------------------
# 服務員群按鈕統一回覆函式
# -------------------------------
def handle_staff_callback(user_id, chat_id, action, parts, callback_id):
    # parts 是 callback_data 拆分後的 list
    def reply(text, buttons=None):
        send_message(chat_id, text, buttons=buttons)
        answer_callback(callback_id)

    if action == "staff_up":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
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
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        pending_data = {
            "action": "input_client",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id
        }
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請輸入客稱、年紀、服務人員與金額（格式：小美 25 Alice 3000）")

    elif action == "not_consumed":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, name, business_chat_id = parts
        pending_data = {
            "action": "not_consumed_wait_reason",
            "hhmm": hhmm,
            "name": name,
            "business_chat_id": business_chat_id
        }
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請輸入未消原因：")

    elif action == "double":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        first_staff = get_staff_name(user_id)
        key = f"{hhmm}|{business_name}"
        # 檢查是否已有人按過第一位
        if key in double_staffs:
            return reply(f"⚠️ {hhmm} {business_name} 已有人選擇第一位服務員：{double_staffs[key][0]}")
        pending_data = {
            "action": "double_wait_second",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id,
            "first_staff": first_staff
        }
        set_pending_for(user_id, pending_data)
        return reply(f"✏️ 請輸入另一位服務員名字，與 {first_staff} 配合雙人服務")

    elif action == "complete":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        key = f"{hhmm}|{business_name}"
        staff_list = double_staffs.get(key, [get_staff_name(user_id)])
        pending_data = {
            "action": "complete_wait_amount",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id,
            "staff_list": staff_list
        }
        set_pending_for(user_id, pending_data)
        return reply(f"✏️ 請輸入 {hhmm} {business_name} 的總金額（數字）：")

    elif action == "fix":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        pending_data = {
            "action": "input_client",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id
        }
        set_pending_for(user_id, pending_data)
        return reply("✏️ 請重新輸入客資（格式：小美 25 Alice 3000）")

    else:
        return reply("⚠️ 無效按鈕")

# -------------------------------
# Telegram callback query 處理
# -------------------------------
def handle_callback_query(cq):
    callback_id = cq["id"]
    data = cq["data"]
    user_id = cq["from"]["id"]
    chat_id = cq["message"]["chat"]["id"]

    print(f"DEBUG callback_query: {data} from {user_id} in {chat_id}")

    # ---------------- 主按鈕（預約 / 客到 / 修改 / 取消） ----------------
    if data.startswith("main|"):
        action = data.split("|")[1]
        handle_main(user_id, chat_id, action, callback_id)
        return

    # ---------------- 預約選擇時段 ----------------
    if data.startswith("reserve_pick|"):
        hhmm = data.split("|")[1]
        set_pending_for(user_id, {
            "action": "reserve_wait_name",
            "hhmm": hhmm,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        send_message(chat_id, f"✏️ 請輸入要預約 {hhmm} 的姓名：")
        answer_callback(callback_id)
        return

    # ---------------- 客到選擇 ----------------
    if data.startswith("arrive_select|"):
        _, hhmm, name = data.split("|")
        set_pending_for(user_id, {
            "action": "arrive_wait_amount",
            "hhmm": hhmm,
            "name": name,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        send_message(chat_id, f"✏️ 請輸入 {hhmm} {name} 的金額：")
        answer_callback(callback_id)
        return

    # ---------------- 修改預約選擇 ----------------
    if data.startswith("modify_pick|"):
        _, old_hhmm, old_name = data.split("|")
        handle_modify_pick(user_id, chat_id, old_hhmm, old_name)
        answer_callback(callback_id)
        return

    # 修改目標時段
    if data.startswith("modify_to|"):
        _, old_hhmm, old_name, new_hhmm = data.split("|")
        set_pending_for(user_id, {
            "action": "modify_wait_name",
            "old_hhmm": old_hhmm,
            "old_name": old_name,
            "new_hhmm": new_hhmm,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        send_message(chat_id, f"✏️ 請輸入新的名稱來修改 {old_hhmm} {old_name} → {new_hhmm}")
        answer_callback(callback_id)
        return

    # ---------------- 取消預約 ----------------
    if data.startswith("cancel_pick|"):
        _, hhmm, name = data.split("|")
        handle_confirm_cancel(chat_id, user_id, hhmm, name, callback_id)
        return

    # ---------------- staff 流程 ----------------
    staff_actions = ["staff_up", "input_client", "not_consumed", "double", "complete", "fix"]
    for act in staff_actions:
        if data.startswith(act + "|"):
            parts = data.split("|")
            handle_staff_callback(user_id, chat_id, act, parts, callback_id)
            return
    # ---------------- 取消 流程 ----------------   
    if data == "cancel_flow":
        clear_pending_for(user_id)
        send_message(chat_id, "❌ 已取消操作。")
        answer_callback(callback_id)
        return
    # ---------------- noop 按鈕（無效） ----------------
    answer_callback(callback_id, text="⚠️ 此按鈕暫時無效")

# -------------------------------
# 自動整點公告
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                text = generate_latest_shift_list()
                # 建立按鈕（同 /list）
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
# Flask Webhook 入口
# -------------------------------
@app.route("/", methods=["POST"])
def webhook():
    try:
        update = request.json
        print("DEBUG webhook 收到:", update)

        if "message" in update:
            handle_text_message(update["message"])  # 用正式版
        elif "callback_query" in update:
            cq = update["callback_query"]
            handle_callback_query(cq)
    except Exception:
        traceback.print_exc()
    return "OK"


# -------------------------------
# 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
