import os
import json
import requests
import queue
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

app = Flask(__name__)
ADMIN_IDS = [7236880214, 7807558825, 7502175264]  # 管理員 Telegram ID，自行修改
TZ = ZoneInfo("Asia/Taipei")  # 台灣時區

double_staffs = {}  # 用於紀錄雙人服務
asked_shifts = set()

pending_lock = threading.Lock()
double_lock = threading.Lock()
staff_buttons_lock = threading.Lock()
file_modify_lock = threading.Lock()
write_queue = queue.Queue()
# -------------------------------
# 已使用的服務員群按鈕（防止重複點擊）
# -------------------------------
USED_STAFF_BUTTONS = set()

def staff_button_used(callback_data):
    with staff_buttons_lock:
        if callback_data in USED_STAFF_BUTTONS:
            return True
        USED_STAFF_BUTTONS.add(callback_data)
        return False

def clear_used_staff_buttons():
    with staff_buttons_lock:
        USED_STAFF_BUTTONS.clear()

def background_writer():
    """統一背景寫檔執行緒"""
    while True:
        task = write_queue.get()
        if task is None:  # 遇到 None 可以停止執行緒
            break
        path, data = task
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"DEBUG: 背景寫檔完成 {path}")
        except Exception as e:
            print(f"ERROR: 背景寫檔失敗 {path}: {e}")
        write_queue.task_done()
# -------------------------------
# pending 狀態（persist 到檔案，key = user_id 字串）
# -------------------------------
def load_pending():
    if not os.path.exists(PENDING_FILE):
        return {}
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: 讀取 pending 檔案失敗: {e}")
        return {}

def save_pending(new_data):
    """將 new_data 合併到 pending.json 再推入 queue 背景寫入"""
    path = PENDING_FILE

    # 讀取現有檔案
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            print(f"WARNING: 讀取現有 pending.json 失敗: {e}")

    # 合併新的資料
    existing.update(new_data)

    # 推入背景寫檔 queue
    write_queue.put((path, existing))


def set_pending_for(user_id, payload):
    save_pending({str(user_id): payload})

    
def get_pending_for(user_id):
    pending = load_pending()
    # 合併 queue 中未寫入的 pending.json
    for path, data in list(write_queue.queue):
        if path == PENDING_FILE:
            pending.update(data)
    return pending.get(str(user_id))



def clear_pending_for(user_id):
    p = load_pending()
    if str(user_id) in p:
        del p[str(user_id)]
        save_pending(p)

def has_pending_for(user_id):
    return get_pending_for(user_id) is not None

# -------------------------------
# 群組管理
# -------------------------------
GROUP_FILE = os.path.join(DATA_DIR, "groups.json")
STAFF_GROUP_ID = -1003119493503

def load_groups():
    groups = load_json_file(GROUP_FILE, default=[])
    if not isinstance(groups, list):
        groups = []
    if not any(g.get("id") == STAFF_GROUP_ID for g in groups):
        groups.append({"id": STAFF_GROUP_ID, "type": "staff"})
        save_groups(groups)
    return groups

def save_groups(groups):
    write_queue.put((GROUP_FILE, groups))

def add_group(chat_id, chat_type, group_role=None):
    groups = load_groups()
    if any(g.get("id") == chat_id for g in groups):
        return
    role = group_role or ("staff" if chat_id == STAFF_GROUP_ID else "business")
    groups.append({"id": chat_id, "type": role})
    save_groups(groups)

def get_group_ids_by_type(group_type=None):
    """取得指定角色的群組ID"""
    groups = load_groups()
    if group_type:
        return [g.get("id") for g in groups if g.get("type") == group_type]
    return [g.get("id") for g in groups]

# -------------------------------
# JSON 存取（每日檔）
# -------------------------------
def data_path_for(day):
    return os.path.join(DATA_DIR, f"{day}.json")

def load_json_file(path, default=None):
    if not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: 讀取檔案失敗 {path}: {e}")
        return default or {}

def save_json_file(path, data):
    write_queue.put((path, data))  # 推入 Queue 背景寫檔

# -------------------------------
# 安全檔案修改封裝（加鎖）
# -------------------------------
def safe_modify_today_file(callback):
    path = ensure_today_file()

    with file_modify_lock:  # 確保同一時間只有一個 callback 在修改
        data = load_json_file(path)

        # 合併 queue 中尚未寫入的資料
        for p, qdata in list(write_queue.queue):
            if p == path:
                # merge shifts 列表，避免覆蓋
                merge_shifts(data.get("shifts", []), qdata.get("shifts", []))
                # merge 候補列表
                if "候補" in qdata:
                    for item in qdata["候補"]:
                        if item not in data["候補"]:
                            data["候補"].append(item)
                # 可以合併其他 key 的字典
                for k, v in qdata.items():
                    if k not in ("shifts", "候補"):
                        data[k] = v

        # 執行 callback 修改資料
        callback(data)

        # 推入 queue 背景寫檔
        write_queue.put((path, data))

     
# -------------------------------
# 每日排班檔生成
# -------------------------------
def ensure_today_file(workers=3):
    today = datetime.now(TZ).date().isoformat()
    path = data_path_for(today)
    now = datetime.now(TZ)

    # 每天生成新檔時清空已使用按鈕
    clear_used_staff_buttons()

    # 如果檔案存在，但日期不是今天，刪除舊檔
    if os.path.exists(path):
        data = load_json_file(path)
        if data.get("date") != today:
            os.remove(path)

    # 如果檔案不存在，建立今天的檔案
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
        data = {"date": today, "shifts": shifts, "候補": []}
        save_json_file(path, data)
    else:
        # 確保已存在的檔案裡的 "shifts" 和 "候補" 是列表
        data = load_json_file(path)
        modified = False
        if "shifts" not in data or not isinstance(data["shifts"], list):
            data["shifts"] = []
            modified = True
        if "候補" not in data or not isinstance(data["候補"], list):
            data["候補"] = []
            modified = True
        if modified:
            save_json_file(path, data)

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


def send_message(chat_id, text, buttons=None, parse_mode=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    print(f"DEBUG: send_message payload={payload}")
    r = requests.post(url, json=payload)
    print(f"DEBUG: send_message response: {r.text}")
    return r.json()


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

        for entry in regular_in_progress + backup_in_progress:
            if isinstance(entry, dict):
                name = entry.get("name", "")
                amount = entry.get("amount", "")
                checked_in_lines.append(f"{time_label} {name} ✅")
            else:
                checked_in_lines.append(f"{time_label} {entry} ✅")

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
    
def merge_shifts(original, new_shifts):
    for new_shift in new_shifts:
        # 先確保 new_shift['time'] 是 datetime.time
        if isinstance(new_shift.get('time'), str):
            h, m = map(int, new_shift['time'].split(":"))
            new_shift['time'] = dt_time(h, m)

        # 找出是否已存在同時段
        shift = next((s for s in original if s['time'] == new_shift['time']), None)

        if shift:
            # 合併 bookings（避免重複）
            for b in new_shift.get('bookings', []):
                if b not in shift.get('bookings', []):
                    shift.setdefault('bookings', []).append(b)

            # 合併 in_progress（避免重複）
            for ip in new_shift.get('in_progress', []):
                if ip not in shift.get('in_progress', []):
                    shift.setdefault('in_progress', []).append(ip)
        else:
            # 新增 shift 前確保 bookings / in_progress 存在
            new_shift.setdefault('bookings', [])
            new_shift.setdefault('in_progress', [])
            original.append(new_shift)

    return original


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

    print(f"DEBUG: 收到訊息: user_id={user_id}, chat_id={chat_id}, text={text}")

    # 新群組自動記錄為 business
    add_group(chat_id, chat_type)
    print(f"DEBUG: 群組列表: {load_groups()}")

    # 1️⃣ 處理 pending（等待輸入的動作）
    pending = get_pending_for(user_id)
    print(f"DEBUG: pending={pending}")
    if pending:
        print("DEBUG: 有 pending，交給 _handle_pending")
        return _handle_pending(user_id, chat_id, text, pending)

    # 2️⃣ 一般指令
    if text == "/help":
        print("DEBUG: 執行 /help")
        return _cmd_help(chat_id)
    if text == "/list":
        print("DEBUG: 執行 /list")
        return _cmd_list(chat_id)
    # 如果輸入 /id，回傳群組 ID
    if text == "/id":
        send_message(chat_id, f"這個群組的 ID 是：{chat_id}")
        return
    # 3️⃣ 管理員指令
    if user_id in ADMIN_IDS:
        if text.startswith("/addshift"):
            print("DEBUG: 執行 /addshift")
            return _add_shift(chat_id, text)
        elif text.startswith("/updateshift"):
            print("DEBUG: 執行 /updateshift")
            return _update_shift(chat_id, text)
        elif text.startswith("刪除"):
            print("DEBUG: 執行 刪除")
            return _delete_shift_entry(chat_id, text)

    print("DEBUG: 未匹配的訊息指令")
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

    def callback(data):
        if find_shift(data.get("shifts", []), hhmm):
            send_message(chat_id, f"⚠️ {hhmm} 已存在")
            return
        data["shifts"].append({"time": hhmm, "limit": limit, "bookings": [], "in_progress": []})
        send_message(chat_id, f"✅ 新增 {hhmm} 時段，限制 {limit} 人")

    safe_modify_today_file(callback)


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

def _cmd_list(chat_id):
    print("DEBUG: _cmd_list 被呼叫")

    shift_text = generate_latest_shift_list()
    print(f"DEBUG: shift_text=\n{shift_text}")

    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"},
         {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"},
         {"text": "取消預約", "callback_data": "main|cancel"}],
    ]

    send_message(chat_id, shift_text, buttons=buttons, parse_mode=None)
    print("DEBUG: _cmd_list 發送訊息完成")

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

    def callback(data):
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            send_message(group_chat, f"⚠️ 時段 {hhmm} 不存在或已過期。")
            return

        used = len(shift.get("bookings", [])) + len([x for x in shift.get("in_progress", []) if not str(x).endswith("(候補)")])
        limit = shift.get("limit", 1)
        if used >= limit:
            send_message(group_chat, f"⚠️ {hhmm} 已滿額，無法預約。")
            return

        unique_name = generate_unique_name(shift.get("bookings", []), name_input)
        shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
        send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")

    safe_modify_today_file(callback)

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

    def callback(data):
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            send_message(group_chat, f"⚠️ 找不到時段 {hhmm}")
            return

        booking = next((b for b in shift.get("bookings", []) if b.get("name") == name and b.get("chat_id") == group_chat), None)
        if booking:
            shift.setdefault("in_progress", []).append({"name": name, "amount": amount})
            shift["bookings"] = [b for b in shift.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == group_chat)]
            send_message(group_chat, f"✅ {hhmm} {name} 已客到，金額：{amount}")

            staff_message = f"🙋‍♀️ 客到通知\n時間：{hhmm}\n業務名：{name}\n金額：{amount}"
            staff_buttons = [[{"text": "上", "callback_data": f"staff_up|{hhmm}|{name}|{group_chat}"}]]
            broadcast_to_groups(staff_message, group_type="staff", buttons=staff_buttons)
        else:
            send_message(group_chat, f"⚠️ 找不到預約 {name} 或已被移除")

    safe_modify_today_file(callback)
    clear_pending_for(user_id)

# 輸入客資    
def _pending_input_client(user_id, text, pending):
    chat_id = pending.get("chat_id")
    business_chat_id = pending.get("business_chat_id")
    action_source = pending.get("action")  # 新增這行

    # 防呆檢查
    if chat_id is None or business_chat_id is None:
        print(f"❌ DEBUG: pending 資料錯誤: {pending}")
        return {"ok": False, "error": "chat_id or business_chat_id is None"}

    try:
        chat_id = int(chat_id)
        business_chat_id = int(business_chat_id)
    except ValueError:
        print(f"❌ DEBUG: chat_id 或 business_chat_id 不是整數: {pending}")
        return {"ok": False, "error": "chat_id or business_chat_id invalid"}

    # 拆文字
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return {"ok": True}

    hhmm = pending["hhmm"]
    business_name = pending["business_name"]

    # 判斷是否是修正
    if action_source == "fix":
        prefix = "🛠 修正後客資"
    else:
        prefix = "📌 客資"

    msg_business = f"{prefix}\n時間: {hhmm} {client_name}{age} {business_name}{amount}\n服務人員: {staff_name}"
    send_message(business_chat_id, msg_business, parse_mode="HTML")

    staff_buttons = [
        [
            {"text": "雙", "callback_data": f"double|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"},
            {"text": "完成服務", "callback_data": f"complete|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"},
            {"text": "修正", "callback_data": f"fix|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"}
        ]
    ]
    send_message(chat_id, msg_business, buttons=staff_buttons, parse_mode="HTML")

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
    send_message(int(business_chat_id), msg)
    clear_pending_for(user_id)
    return {"ok": True}

# 未消輸入原因
def _pending_not_consumed_wait_reason(user_id, text, pending):
    hhmm = pending["hhmm"]
    name = pending["name"]
    business_chat_id = pending["business_chat_id"]
    reason = text.strip()

    # 發給服務員群（如果 chat_id 存在）
    staff_chat_id = pending.get("chat_id")
    if staff_chat_id:
        try:
            send_message(int(staff_chat_id), "掰掰謝謝光臨!!")  # 發給服務員群確認
        except Exception as e:
            print(f"❌ DEBUG: 發送給服務員群失敗: {e}, chat_id={staff_chat_id}")

    # 發給業務群
    business_chat_id = pending.get("business_chat_id")
    if business_chat_id:
        try:
            send_message(int(business_chat_id), f"⚠️ 未消: {name} {reason}")
        except Exception as e:
            print(f"❌ DEBUG: 發送給業務群失敗: {e}, chat_id={business_chat_id}")

    clear_pending_for(user_id)
    return {"ok": True}


# 修改預約
def _pending_modify_wait_name(user_id, text, pending):
    old_hhmm = pending.get("old_hhmm")
    old_name = pending.get("old_name")
    new_hhmm = pending.get("new_hhmm")
    group_chat = pending.get("group_chat")
    new_name_input = text.strip()

    def callback(data):
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
        send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")

    safe_modify_today_file(callback)

    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
    clear_pending_for(user_id)


# 雙人服務
def _pending_double_wait_second(user_id, text, pending):
    hhmm = pending["hhmm"]
    first_staff = pending["first_staff"]
    second_staff = text.strip()

    double_staffs[hhmm] = [first_staff, second_staff]
    staff_list = "、".join(double_staffs[hhmm])

    business_chat_id = pending.get("business_chat_id")
    if business_chat_id:
        send_message(int(business_chat_id), f"👥 雙人服務更新：{staff_list}")
    clear_pending_for(user_id)
    return {"ok": True}

# -------------------------------
# 修正客資
# -------------------------------
def _pending_fix(user_id, text, pending):
    chat_id = pending.get("chat_id")
    business_chat_id = pending.get("business_chat_id")

    # 防呆檢查
    if chat_id is None or business_chat_id is None:
        print(f"❌ DEBUG: pending 資料錯誤: {pending}")
        return {"ok": False, "error": "chat_id or business_chat_id is None"}

    try:
        chat_id = int(chat_id)
        business_chat_id = int(business_chat_id)
    except ValueError:
        print(f"❌ DEBUG: chat_id 或 business_chat_id 不是整數: {pending}")
        return {"ok": False, "error": "chat_id or business_chat_id invalid"}

    # 嘗試解析使用者輸入
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return {"ok": True}

    hhmm = pending.get("hhmm")
    business_name = pending.get("business_name")

    # 發訊息給業務群
    msg_business = f"📌 客資修正\n時間: {hhmm} {client_name}{age} {business_name}{amount}\n服務人員: {staff_name}"
    send_message(business_chat_id, msg_business, parse_mode="HTML")

    # 發訊息給服務員群，附上按鈕
    staff_buttons = [
        [
            {"text": "雙", "callback_data": f"double|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"},
            {"text": "完成服務", "callback_data": f"complete|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"},
            {"text": "修正", "callback_data": f"fix|{hhmm}|{business_name}|{business_chat_id}|{staff_name}"}
        ]
    ]

    send_message(chat_id, msg_business, buttons=staff_buttons, parse_mode="HTML")

    # 清除 pending
    clear_pending_for(user_id)
    return {"ok": True}
# -------------------------------
# 服務員群按「上」 → 從已報到中移除該客人（只刪除，不通知）
# -------------------------------
def handle_staff_up(user_id, chat_id, data, callback_id):
    try:
        _, hhmm, name, business_chat_id = data.split("|")
    except ValueError:
        answer_callback(callback_id, "⚠️ callback 資料錯誤")
        return

    def callback_func(data_json):
        shift = find_shift(data_json.get("shifts", []), hhmm)
        if not shift:
            answer_callback(callback_id, f"⚠️ 找不到時段 {hhmm}")
            return

        removed_item = None
        in_progress = shift.get("in_progress", [])
        for i, item in enumerate(in_progress):
            if isinstance(item, dict) and item.get("name") == name:
                removed_item = in_progress.pop(i)
                break
            elif str(item) == name:
                removed_item = in_progress.pop(i)
                break

        shift["bookings"] = [
            b for b in shift.get("bookings", [])
            if (isinstance(b, dict) and b.get("name") != name) or b == name
        ]

        if removed_item:
            print(f"DEBUG: 已刪除 {hhmm} {name} 從 in_progress")

    safe_modify_today_file(callback_func)
    answer_callback(callback_id)

   
# -------------------------------
# callback_query 處理（按鈕）
# -------------------------------
@app.route("/", methods=["POST"])
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
                        if action == "arrive":
                            return respond("⚠️ 目前沒有可報到的預約。")
                        elif action == "modify":
                            return respond("⚠️ 目前沒有可修改的預約。")
                        elif action == "cancel":
                            return respond("⚠️ 目前沒有可取消的預約。")

                    btns = []
                    for bk in bookings:
                        if action == "arrive":
                            cb_prefix = "arrive_select"
                            btn_text = f"{bk['time']} {bk['name']}"
                        elif action == "modify":
                            cb_prefix = "modify_pick"
                            btn_text = f"{bk['time']} {bk['name']}"
                        elif action == "cancel":
                            cb_prefix = "cancel_pick"
                            btn_text = f"{bk['time']} {bk['name']}"
                        btns.append({"text": btn_text, "callback_data": f"{cb_prefix}|{bk['time']}|{bk['name']}"})

                    chunk_size = 2 if action == "arrive" else 1
                    rows = chunk_list(btns, chunk_size)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])

                    if action == "arrive":
                        return respond("📋 請選擇要報到的預約：", buttons=rows)
                    elif action == "modify":
                        return respond("✏️ 請選擇要修改的預約：", buttons=rows)
                    elif action == "cancel":
                        return respond("❌ 請選擇要取消的預約：", buttons=rows)


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
                if staff_button_used(data):
                    answer_callback(callback_id, "⚠️ 此按鈕已被使用過。")
                    return {"ok": True}

                # 先處理移除 in_progress
                handle_staff_up(user_id, chat_id, data, callback_id)

                # 再發通知給業務 + 顯示服務員按鈕
                _, hhmm, name, business_chat_id = data.split("|", 3)
                send_message(int(business_chat_id), f"⬆️ 上 {hhmm} {name}")

                staff_buttons = [[
                    {"text": "輸入客資", "callback_data": f"input_client|{hhmm}|{name}|{business_chat_id}"},
                    {"text": "未消", "callback_data": f"not_consumed|{hhmm}|{name}|{business_chat_id}"}
                ]]
                send_message(chat_id, f"✅ 已通知業務 {name}", buttons=staff_buttons)
                # answer_callback 已在 handle_staff_up 處理，不需要再呼叫一次
                return {"ok": True}

                
            # 服務員 -> 輸入客資
            if data and data.startswith("input_client|"):
                if staff_button_used(data):
                    answer_callback(callback_id, "⚠️ 此按鈕已被使用過。")
                    return {"ok": True}

                if has_pending_for(user_id):
                    answer_callback(callback_id, "⚠️ 你已有進行中的操作，請先完成。")
                    return {"ok": True}

                _, hhmm, name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "input_client",
                    "hhmm": hhmm,
                    "business_name": name,
                    "business_chat_id": business_chat_id,
                    "chat_id": chat_id
                })
                send_message(chat_id, "✏️ 請輸入客稱、年紀、服務人員與金額（格式：小帥 25 小美 3000）")
                answer_callback(callback_id)
                return {"ok": True}

            # 服務員 -> 未消
            if data and data.startswith("not_consumed|"):
                if staff_button_used(data):
                    answer_callback(callback_id, "⚠️ 此按鈕已被使用過。")
                    return {"ok": True}

                _, hhmm, name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "not_consumed_wait_reason",
                    "hhmm": hhmm,
                    "name": name,
                    "business_chat_id": business_chat_id,
                    "chat_id": chat_id
                })
                send_message(chat_id, "✏️ 請輸入未消原因：")
                answer_callback(callback_id)
                return {"ok": True}

            # 雙人服務（按鈕觸發）
            if data and data.startswith("double|"):
                if staff_button_used(data):
                    answer_callback(callback_id, "⚠️ 此按鈕已被使用過。")
                    return {"ok": True}

                try:
                    # 新格式：double|hhmm|business_name|business_chat_id|staff_name
                    _, hhmm, business_name, business_chat_id, first_staff = data.split("|")
                except ValueError:
                    # 若舊資料結構（沒有 staff_name），仍相容處理
                    _, hhmm, business_name, business_chat_id = data.split("|")
                    first_staff = "未知服務員"

                # 設定 pending 等待輸入第二位服務員
                set_pending_for(user_id, {
                    "action": "double_wait_second",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id,
                    "first_staff": first_staff,
                    "chat_id": chat_id
                })

                send_message(chat_id, f"✏️ 請輸入另一位服務員名字，與 {first_staff} 配合雙人服務")
                answer_callback(callback_id)
                return {"ok": True}


            # 完成服務
            if data and data.startswith("complete|"):
                if staff_button_used(data):
                    answer_callback(callback_id, "⚠️ 此按鈕已被使用過。")
                    return {"ok": True}

                try:
                    # 新格式：complete|hhmm|business_name|business_chat_id|staff_name
                    _, hhmm, business_name, business_chat_id, staff_name = data.split("|")
                except ValueError:
                    # 若舊格式（沒有 staff_name），仍相容處理
                    _, hhmm, business_name, business_chat_id = data.split("|")
                    staff_name = "未知服務員"

                # 支援雙人服務
                staff_list = double_staffs.get(hhmm, [staff_name])
                staff_str = "、".join(staff_list)

                # 設 pending 等待輸入實際金額
                set_pending_for(user_id, {
                    "action": "complete_wait_amount",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id,
                    "staff_list": staff_list,
                    "chat_id": chat_id
                })

                send_message(chat_id, f"✏️ 請輸入 {hhmm} {business_name} 的總金額（數字）：")
                answer_callback(callback_id)
                return {"ok": True}
 

            # 修正服務紀錄
            if data and data.startswith("fix|"):
                if has_pending_for(user_id):
                    answer_callback(callback_id, "⚠️ 你已有進行中的操作，請先完成。")
                    return {"ok": True}

                try:
                    # 新格式：fix|hhmm|business_name|business_chat_id|staff_name
                    _, hhmm, business_name, business_chat_id, staff_name = data.split("|")
                except ValueError:
                    # 舊格式相容
                    _, hhmm, business_name, business_chat_id = data.split("|")
                    staff_name = "未知服務員"

                # 設 pending 等待重新輸入客資
                set_pending_for(user_id, {
                    "action": "input_client",
                    "hhmm": hhmm,
                    "business_name": business_name,
                    "business_chat_id": business_chat_id,
                    "staff_name": staff_name,
                    "chat_id": chat_id
                })

                send_message(chat_id, f"✏️ 請重新輸入客資（格式：客稱 年齡 服務人員 金額）")
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
                print(f"[AUTO ANNOUNCE] {now} 發送公告")
            except Exception as e:
                print(f"❌ [AUTO ANNOUNCE] 發送失敗: {e}")
            time.sleep(60)  # 避免整點重複
        else:
            time.sleep(10)  # 其他時間每 10 秒檢查一次


def ask_arrivals_thread():
    global asked_shifts
    while True:
        now = datetime.now(TZ)
        current_hm = f"{now.hour:02d}:00"
        today = now.date().isoformat()
        key = f"{today}|{current_hm}"

        if now.minute == 0 and key not in asked_shifts:
            path = data_path_for(today)
            try:
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
                            in_prog_names = [x["name"] if isinstance(x, dict) else x for x in s.get("in_progress", [])]
                            if name not in in_prog_names:
                                waiting.append(name)
                                groups_to_notify.add(gid)
                        if waiting:
                            names_text = "、".join(waiting)
                            text = f"⏰ 現在是 {current_hm}\n請問預約的「{names_text}」到了嗎？\n到了請回覆：客到 {current_hm} 名稱 或使用按鈕 /list → 客到"
                            for gid in groups_to_notify:
                                try:
                                    send_message(gid, text)
                                except Exception as e:
                                    print(f"❌ [ASK ARRIVALS] 發送訊息失敗 gid={gid}: {e}")
                    asked_shifts.add(key)
            except Exception as e:
                print(f"❌ [ASK ARRIVALS] 讀取檔案失敗: {e}")

        # 每天 00:01 清空
        if now.hour == 0 and now.minute == 1:
            asked_shifts.clear()
            print("[ASK ARRIVALS] 已清空今日記錄")

        time.sleep(10)

# -------------------------------
# 啟動背景執行緒 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    # 啟動背景執行緒
    threading.Thread(target=auto_announce, daemon=True).start()
    threading.Thread(target=ask_arrivals_thread, daemon=True).start()
    threading.Thread(target=background_writer, daemon=True).start()
    # 關閉 reloader 避免多次啟動
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), use_reloader=False)





