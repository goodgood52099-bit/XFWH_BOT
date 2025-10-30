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

def load_json_file(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

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

        for name in regular_in_progress:
            checked_in_lines.append(f"{time_label} {name} ✅")
        for name in backup_in_progress:
            checked_in_lines.append(f"{time_label} {name} ✅")

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
# message 處理（文字）
# -------------------------------
def handle_text_message(msg):
    text = msg.get("text", "").strip() if msg.get("text") else ""
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = msg.get("from", {})
    user_id = user.get("id")
    user_name = user.get("first_name", "")

    # 新群組自動記錄為 business（若管理員 later 可 /STAFF 變更）
    add_group(chat_id, chat_type)

    # 若該使用者存在 pending（等待輸入姓名或新姓名），用文字處理
    pending = get_pending_for(user_id)
    if pending:
        action = pending.get("action")
        path = ensure_today_file()
        data = load_json_file(path)
        # -------- reserve_wait_name --------
        if action == "reserve_wait_name":
            hhmm = pending.get("hhmm")
            group_chat = pending.get("group_chat")  # 記錄預約的群組
            name_input = text.strip()  # 使用者輸入業務名

            # 確保今天檔案存在
            path = ensure_today_file()
            data = load_json_file(path)

            # 尋找對應時段
            s = next((s for s in data.get("shifts", []) if s.get("time") == hhmm), None)
            if not s:
                send_message(group_chat, f"⚠️ 時段 {hhmm} 不存在或已過期。")
                clear_pending_for(user_id)
                return

            # 計算未滿額（排除候補）
            used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
            limit = s.get("limit", 1)
            if used >= limit:
                send_message(group_chat, f"⚠️ {hhmm} 已滿額，無法預約。")
                clear_pending_for(user_id)
                return

            # 生成唯一名稱，避免重名
            existing_names = [b["name"] for b in s.get("bookings", []) if isinstance(b, dict)]
            unique_name = name_input
            idx = 2
            while unique_name in existing_names:
                unique_name = f"{name_input}({idx})"
                idx += 1

            # 新增預約
            s.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
            save_json_file(path, data)

            send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")
            buttons = [
                [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
            ]
            broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)
            clear_pending_for(user_id)
            return
        if action == "arrive_wait_amount":
            hhmm = pending["hhmm"]
            name = pending["name"]
            group_chat = pending["group_chat"]
            amount_text = text.strip()

            # 檢查是否為數字
            try:
                amount = float(amount_text)
            except ValueError:
                send_message(group_chat, "⚠️ 金額格式錯誤，請輸入數字")
                return

            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s:
                send_message(group_chat, f"⚠️ 找不到時段 {hhmm}")
                clear_pending_for(user_id)
                return

            # 找 booking
            booking = next((b for b in s.get("bookings", []) if b.get("name") == name and b.get("chat_id") == group_chat), None)
            if booking:
                # 移到 in_progress，記錄金額
                s.setdefault("in_progress", []).append({"name": name, "amount": amount})
                s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == group_chat)]
                save_json_file(path, data)

                send_message(group_chat, f"✅ {hhmm} {name} 已標記到場，金額：{amount}")
                # ➡️ 新增：通知所有服務員群組
                staff_message = f"📌 客到通知\n時間：{hhmm}\n業務名：{name}\n金額：{amount}"
                staff_buttons = [[{"text": "上", "callback_data": f"staff_up|{hhmm}|{name}|{group_chat}"}]]
                broadcast_to_groups(staff_message, group_type="staff", buttons=staff_buttons)

            else:
                send_message(group_chat, f"⚠️ 找不到預約 {name} 或已被移除")
            clear_pending_for(user_id)
            return
        if action == "input_client":
            try:
                client_name, age, staff_name, amount = text.split()
            except ValueError:
                send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
                return {"ok": True}

            hhmm = pending["hhmm"]
            business_name = pending["business_name"]
            business_chat_id = pending["business_chat_id"]

            # 1️⃣ 發給業務群
            msg_business = f"📌 客\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}"
            send_message(int(business_chat_id), msg_business)

            # 2️⃣ 發給服務員群，附三個按鈕
            staff_buttons = [
                [
                    {"text": "雙", "callback_data": f"double|{hhmm}|{business_name}|{business_chat_id}"},
                    {"text": "完成服務", "callback_data": f"complete|{hhmm}|{business_name}|{business_chat_id}"},
                    {"text": "修正", "callback_data": f"fix|{hhmm}|{business_name}|{business_chat_id}"}
                ]
            ]
            send_message(chat_id, f"📌 客\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}", buttons=staff_buttons)

            # 3️⃣ 清除 pending
            clear_pending_for(user_id)
            return {"ok": True}
        # -------- modify_wait_name --------
        if action == "modify_wait_name":
            old_hhmm = pending.get("old_hhmm")
            old_name = pending.get("old_name")
            new_hhmm = pending.get("new_hhmm")
            group_chat = pending.get("group_chat")
            new_name_input = text
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
            # 移除舊預約
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
            return

        # 未知 pending 清除
        clear_pending_for(user_id)
        return

    # /help
    if text == "/help":
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
        return

    # /STAFF
    if text.startswith("/STAFF"):
        user_id = msg.get("from", {}).get("id")
        if user_id not in ADMIN_IDS:
            send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
            return
        add_group(chat_id, "group", group_role="staff")
        send_message(chat_id, "✅ 已將本群組設定為服務員群組")
        return

    # /list
    if text == "/list":
        shift_text = generate_latest_shift_list()
        buttons = [
            [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
            [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
        ]
        send_message(chat_id, shift_text, buttons=buttons)
        return

    # 管理員文字功能（保留原本刪除 /addshift /updateshift 等）
    user_id = msg.get("from", {}).get("id")
    if user_id in ADMIN_IDS:
        # /addshift HH:MM 限制
        if text.startswith("/addshift"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "⚠️ 格式：/addshift HH:MM 限制")
                return
            hhmm, limit = parts[1], int(parts[2])
            path = ensure_today_file()
            data = load_json_file(path)
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
            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s:
                send_message(chat_id, f"⚠️ {hhmm} 不存在")
                return
            s["limit"] = limit
            save_json_file(path, data)
            send_message(chat_id, f"✅ {hhmm} 時段限制已更新為 {limit}")
            return

        # 刪除指令（同你原本）
        if text.startswith("刪除"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "❗ 格式錯誤\n請輸入：\n刪除 HH:MM 名稱 / 數量 / all")
                return
            hhmm, target = parts[1], " ".join(parts[2:])
            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s:
                send_message(chat_id, f"⚠️ 找不到 {hhmm} 的時段")
                return
            if target.lower() == "all":
                count_b = len(s.get("bookings", []))
                count_i = len(s.get("in_progress", []))
                s["bookings"].clear()
                s["in_progress"].clear()
                save_json_file(path, data)
                send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單（未報到 {count_b}、已報到 {count_i}）")
                return
            if target.isdigit():
                remove_count = int(target)
                old_limit = s.get("limit", 1)
                s["limit"] = max(0, old_limit - remove_count)
                save_json_file(path, data)
                send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額（原本 {old_limit} → 現在 {s['limit']}）")
                return
            removed_from = None
            for b in list(s.get("bookings", [])):
                if b.get("name") == target:
                    s["bookings"].remove(b)
                    removed_from = "bookings"
                    break
            if not removed_from and target in s.get("in_progress", []):
                s["in_progress"].remove(target)
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
# callback_query 處理（按鈕）
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        # 普通訊息
        if "message" in update:
            handle_text_message(update["message"])
            return {"ok": True}

        # callback_query（按鈕）
        if "callback_query" in update:
            cq = update["callback_query"]
            data = cq.get("data")
            callback_id = cq.get("id")
            from_user = cq.get("from", {})
            user_id = from_user.get("id")
            user_name = from_user.get("first_name", "")
            message = cq.get("message", {}) or {}
            chat = message.get("chat", {}) or {}
            chat_id = chat.get("id")

            # 主按鈕 main|reserve / arrive / modify / cancel
            if data and data.startswith("main|"):
                _, action = data.split("|", 1)

                # -------- 預約：顯示可預約時段（每行 3 個） --------
                if action == "reserve":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    # 只顯示未過時段
                    shifts = [s for s in datafile.get("shifts", []) if is_future_time(s.get("time", ""))]
                    rows = []
                    row = []
                    for s in shifts:
                        used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
                        limit = s.get("limit", 1)
                        if used < limit:
                            row.append({"text": f"{s['time']} ({limit-used})", "callback_data": f"reserve_pick|{s['time']}"})
                        else:
                            row.append({"text": f"{s['time']} (滿)", "callback_data": "noop"})
                        if len(row) == 3:
                            rows.append(row); row = []
                    if row: rows.append(row)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請選擇要預約的時段：", buttons=rows)
                    answer_callback(callback_id)
                    return {"ok": True}

                # -------- 客到：列出該群未報到預約（點選即報到） --------
                if action == "arrive":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    bookings_for_group = []
                    for s in datafile.get("shifts", []):
                        for b in s.get("bookings", []):
                            if b.get("chat_id") == chat_id:
                                bookings_for_group.append({"time": s["time"], "name": b.get("name")})
                    if not bookings_for_group:
                        send_message(chat_id, "目前沒有未報到的預約。")
                        answer_callback(callback_id)
                        return {"ok": True}
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"arrive_select|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 2)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請點選要標記客到的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return {"ok": True}

                # -------- 修改預約：列出可修改的預約 --------
                if action == "modify":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    bookings_for_group = []
                    for s in datafile.get("shifts", []):
                        for b in s.get("bookings", []):
                            if b.get("chat_id") == chat_id:
                                bookings_for_group.append({"time": s["time"], "name": b.get("name")})
                    if not bookings_for_group:
                        send_message(chat_id, "目前沒有可修改的預約。")
                        answer_callback(callback_id)
                        return {"ok": True}
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"modify_pick|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 1)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請選擇要修改的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return {"ok": True}

                # -------- 取消預約：列出可取消的預約 --------
                if action == "cancel":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    bookings_for_group = []
                    for s in datafile.get("shifts", []):
                        for b in s.get("bookings", []):
                            if b.get("chat_id") == chat_id:
                                bookings_for_group.append({"time": s["time"], "name": b.get("name")})
                    if not bookings_for_group:
                        send_message(chat_id, "目前沒有可取消的預約。")
                        answer_callback(callback_id)
                        return {"ok": True}
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"cancel_pick|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 1)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請選擇要取消的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return {"ok": True}

            # -------- 選擇欲預約的時段（reserve_pick|HH:MM） -> 設定 pending 等待名字輸入 --------
            if data and data.startswith("reserve_pick|"):
                _, hhmm = data.split("|", 1)
                # 設定 pending（以使用者 id 為 key，避免群內多人互相影響）
                set_pending_for(user_id, {"action": "reserve_wait_name", "hhmm": hhmm, "group_chat": chat_id})
                send_message(chat_id, f"✏️ 請在此群輸入欲預約的/姓名（針對 {hhmm}）。\n輸入後即完成預約。")
                answer_callback(callback_id)
                return {"ok": True}

            # -------- 客到選擇（arrive_select|HH:MM|name） -> 直接標記 in_progress --------
            if data and data.startswith("arrive_select|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return {"ok": True}
                _, hhmm, name = parts

                # 設定 pending 等待輸入金額
                set_pending_for(user_id, {
                    "action": "arrive_wait_amount",
                    "hhmm": hhmm,
                    "name": name,
                    "group_chat": chat_id
                })
                send_message(chat_id, f"✏️ 請輸入 {hhmm} {name} 的金額（數字）：")
                answer_callback(callback_id)
                return {"ok": True}
                # 找 booking（需 match chat_id）
                booking = next((b for b in s.get("bookings", []) if b.get("name") == name and b.get("chat_id") == chat_id), None)
                if booking:
                    s.setdefault("in_progress", []).append(name)
                    s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == chat_id)]
                    save_json_file(path, datafile)
                    send_message(chat_id, f"✅ {hhmm} {name} （已報到）")
                    answer_callback(callback_id)
                    return {"ok": True}
                else:
                    answer_callback(callback_id, "找不到該預約或已被移除")
                    return {"ok": True}

            # -------- modify pick：選擇欲修改的預約（modify_pick|oldHH:MM|oldName） -> 顯示新時段按鈕 --------
            if data and data.startswith("modify_pick|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return {"ok": True}
                _, old_hhmm, old_name = parts
                path = ensure_today_file()
                datafile = load_json_file(path)
                shifts = [s for s in datafile.get("shifts", []) if is_future_time(s.get("time",""))]
                rows = []
                row = []
                for s in shifts:
                    row.append({"text": s["time"], "callback_data": f"modify_to|{old_hhmm}|{old_name}|{s['time']}"})
                    if len(row) == 3:
                        rows.append(row); row = []
                if row: rows.append(row)
                rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                send_message(chat_id, f"要將 {old_hhmm} {old_name} 修改到哪個時段？", buttons=rows)
                answer_callback(callback_id)
                return {"ok": True}

            # -------- modify_to：選好新時段（modify_to|old|oldname|new） -> 要求輸入新姓名或同名 --------
            if data and data.startswith("modify_to|"):
                parts = data.split("|", 3)
                if len(parts) < 4:
                    answer_callback(callback_id, "資料錯誤")
                    return {"ok": True}
                _, old_hhmm, old_name, new_hhmm = parts
                # 設成 pending 等待使用者輸入新名字
                set_pending_for(user_id, {"action": "modify_wait_name", "old_hhmm": old_hhmm, "old_name": old_name, "new_hhmm": new_hhmm, "group_chat": chat_id})
                send_message(chat_id, f"請輸入新的姓名（或輸入原姓名 `{old_name}` 保留）以完成從 {old_hhmm} → {new_hhmm} 的修改：")
                answer_callback(callback_id)
                return {"ok": True}

            # -------- cancel pick：確認取消（cancel_pick|HH:MM|name） -> 顯示確認按鈕 --------
            if data and data.startswith("cancel_pick|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return {"ok": True}
                _, hhmm, name = parts
                buttons = [[
                    {"text": "確認取消", "callback_data": f"confirm_cancel|{hhmm}|{name}"},
                    {"text": "取消", "callback_data": "cancel_flow"}
                ]]
                send_message(chat_id, f"確定要取消 {hhmm} {name} 的預約嗎？", buttons=buttons)
                answer_callback(callback_id)
                return {"ok": True}

            # -------- confirm_cancel：執行取消 --------
            if data and data.startswith("confirm_cancel|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return {"ok": True}
                _, hhmm, name = parts
                path = ensure_today_file()
                datafile = load_json_file(path)
                s = find_shift(datafile.get("shifts", []), hhmm)
                if not s:
                    answer_callback(callback_id, "找不到該時段")
                    return {"ok": True}
                # 只刪除屬於該群的預約（chat_id)
                before_len = len(s.get("bookings", []))
                s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == chat_id)]
                save_json_file(path, datafile)
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                broadcast_to_groups(generate_latest_shift_list(), group_type="business", buttons=buttons)

                send_message(chat_id, f"✅ 已取消 {hhmm} {name} 的預約")
                answer_callback(callback_id)
                return {"ok": True}

            # cancel_flow or noop
            if data in ("cancel_flow", "noop"):
                answer_callback(callback_id, "已取消")
                return {"ok": True}

            # fallback
            answer_callback(callback_id, "操作已接收。")
            return {"ok": True}

            if data and data.startswith("staff_up|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)

                # 1️⃣ 通知業務群組
                msg = f"📌 上 {hhmm} {name}"
                send_message(int(business_chat_id), msg)

                # 2️⃣ 回覆服務員群組訊息，附加按鈕
                staff_buttons = [
                    [
                        {"text": "輸入客資", "callback_data": f"input_client|{hhmm}|{name}|{business_chat_id}"},
                        {"text": "未消", "callback_data": f"not_consumed|{hhmm}|{name}|{business_chat_id}"}
                    ]
                ]
                send_message(chat_id, f"✅ 已通知業務 {name} ", buttons=staff_buttons)

                answer_callback(callback_id, "操作完成")
                return {"ok": True}
            # 服務員上 -> 輸入客資
            if data.startswith("input_client|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)
                # 改成帶業務名參數
                set_pending_for(user_id, {
                    "action": "input_client",
                    "hhmm": hhmm,
                    "business_name": name,
                    "business_chat_id": business_chat_id
                })
                send_message(chat_id, f"✏️ 請輸入客稱、年紀、服務人員與金額（格式：小美25 Alice 3000）")
                answer_callback(callback_id)
                return {"ok": True}

            # 服務員上 -> 未消
            if data.startswith("not_consumed|"):
                _, hhmm, name, business_chat_id = data.split("|", 3)
                set_pending_for(user_id, {
                    "action": "not_consumed_reason",
                    "hhmm": hhmm,
                    "name": name,
                    "business_chat_id": business_chat_id
                })
                send_message(chat_id, "✏️ 請輸入未消原因：")
                answer_callback(callback_id)
                return {"ok": True}
            if "callback_query" in req_json:
                cb = req_json["callback_query"]
                data = cb["data"]
                chat_id = cb["message"]["chat"]["id"]
                message_id = cb["message"]["message_id"]
                user_id = cb["from"]["id"]

                parts = data.split("|")
                action = parts[0]
                hhmm = parts[1]
                business_name = parts[2]
                business_chat_id = int(parts[3])

            if action == "double":
                # 取得當前使用者名稱
                staff_name = get_staff_name(user_id)

                # 檢查是否已有第一位雙人服務員
                if hhmm not in double_staffs:
                    double_staffs[hhmm] = [staff_name]
                    edit_message(chat_id, message_id, f"✅ {staff_name} 已加入雙人服務\n目前服務人員: {staff_name}")
                else:
                    # 加第二位，避免重複
                    if staff_name in double_staffs[hhmm]:
                        edit_message(chat_id, message_id, f"❌ {staff_name} 已經被選為雙人服務")
                    else:
                        double_staffs[hhmm].append(staff_name)
                        staff_list = "、".join(double_staffs[hhmm])
                        edit_message(chat_id, message_id, f"✅ 雙人服務確認\n{hhmm} {client_name} {age}  {business_name} {amount}\n服務人員: {staff_list}")

            elif action == "complete":
                    # 完成服務，記錄實際金額，並通知業務
                    actual_amount = get_actual_amount(user_id, hhmm)
                    edit_message(chat_id, message_id, f"✅ 完成服務通知\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}\n金額: {actual_amount}")
                    send_message(business_chat_id, f"✅ 完成服務通知\n{hhmm} {client_name}{age}  {business_name}{amount}\n服務人員: {staff_name}\n金額: {actual_amount}")

            elif action == "fix":
                    # 重新輸入客資
                    set_pending_for(user_id, action="input_client", hhmm=hhmm, business_name=business_name, business_chat_id=business_chat_id)
                    edit_message(chat_id, message_id, "✏️ 請重新輸入客資，格式：小美 25 Alice 3000")

    except Exception:
        traceback.print_exc()
    return {"ok": True}

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
# 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
