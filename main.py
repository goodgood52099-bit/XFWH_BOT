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
# helpers for pending interactions
# -------------------------------
def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_pending(p):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)

def clear_pending_for(user_id):
    p = load_pending()
    if str(user_id) in p:
        del p[str(user_id)]
        save_pending(p)

def set_pending(user_id, payload):
    p = load_pending()
    p[str(user_id)] = payload
    save_pending(p)

def get_pending(user_id):
    p = load_pending()
    return p.get(str(user_id))

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

def broadcast_to_groups(message, group_type=None):
    gids = get_group_ids_by_type(group_type)
    for gid in gids:
        try:
            send_message(gid, message)
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
# 處理訊息（包含文字與按鈕回傳）
# -------------------------------
def handle_message(msg):
    try:
        text = msg.get("text", "").strip() if msg.get("text") else ""
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        user_name = msg.get("from", {}).get("first_name")
        chat_type = msg.get("chat", {}).get("type")
        # auto add group (default business)
        add_group(chat_id, chat_type)
        # 如果使用者正在等待輸入（pending），則把文字當成姓名或新姓名處理
        pending = get_pending(user_id)
        if text and pending:
            # pending example payloads:
            # {"action":"reserve_wait_name","hhmm":"13:00","group_chat":<chat_id>}
            # {"action":"modify_wait_name","old_hhmm":"13:00","old_name":"小明","new_hhmm":"14:00","group_chat":<chat_id>}
            action = pending.get("action")
            if action == "reserve_wait_name":
                hhmm = pending.get("hhmm")
                group_chat = pending.get("group_chat")
                name_input = text
                path = ensure_today_file()
                data = load_json_file(path)
                s = find_shift(data.get("shifts", []), hhmm)
                if not s:
                    send_message(group_chat, f"⚠️ 時段 {hhmm} 不存在或已過期")
                    clear_pending_for(user_id)
                    return
                # check limit
                used = len(s.get("bookings", [])) + len([x for x in s.get("in_progress", []) if not str(x).endswith("(候補)")])
                if used >= s.get("limit", 1):
                    send_message(group_chat, f"⚠️ {hhmm} 已滿額")
                    clear_pending_for(user_id)
                    return
                unique_name = generate_unique_name(s.get("bookings", []), name_input)
                s.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
                save_json_file(path, data)
                send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")
                broadcast_to_groups(generate_latest_shift_list(), group_type="business")
                clear_pending_for(user_id)
                return
            elif action == "modify_wait_name":
                old_hhmm = pending.get("old_hhmm")
                old_name = pending.get("old_name")
                new_hhmm = pending.get("new_hhmm")
                group_chat = pending.get("group_chat")
                new_name_input = text
                path = ensure_today_file()
                data = load_json_file(path)
                old_shift = find_shift(data.get("shifts", []), old_hhmm)
                if not old_shift:
                    send_message(group_chat, f"⚠️ 原時段 {old_hhmm} 不存在")
                    clear_pending_for(user_id)
                    return
                booking = next((b for b in old_shift.get("bookings", []) if b.get("name") == old_name and b.get("chat_id") == group_chat), None)
                if not booking:
                    send_message(group_chat, f"⚠️ 找不到 {old_hhmm} 的預約 {old_name}")
                    clear_pending_for(user_id)
                    return
                new_shift = find_shift(data.get("shifts", []), new_hhmm)
                if not new_shift:
                    send_message(group_chat, f"⚠️ 新時段 {new_hhmm} 不存在")
                    clear_pending_for(user_id)
                    return
                used_new = len(new_shift.get("bookings", [])) + len([x for x in new_shift.get("in_progress", []) if not str(x).endswith("(候補)")])
                if used_new >= new_shift.get("limit", 1):
                    send_message(group_chat, f"⚠️ {new_hhmm} 已滿額，無法修改")
                    clear_pending_for(user_id)
                    return
                # remove old booking
                old_shift["bookings"] = [b for b in old_shift.get("bookings", []) if not (b.get("name") == old_name and b.get("chat_id") == group_chat)]
                unique_name = generate_unique_name(new_shift.get("bookings", []), new_name_input)
                new_shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
                save_json_file(path, data)
                broadcast_to_groups(generate_latest_shift_list(), group_type="business")
                send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")
                clear_pending_for(user_id)
                return
            # 未知待處理內容，清除
            clear_pending_for(user_id)
        # 若非 pending 或沒有文字，繼續處理一般指令
        if not text:
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
            if user_id not in ADMIN_IDS:
                send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
                return
            add_group(chat_id, "group", group_role="staff")
            send_message(chat_id, "✅ 已將本群組設定為服務員群組")
            return

        # /list -> 顯示時段 + 4 主按鈕 (2x2)
        if text == "/list":
            shift_text = generate_latest_shift_list()
            buttons = [
                [
                    {"text": "預約", "callback_data": "main|reserve"},
                    {"text": "客到", "callback_data": "main|arrive"}
                ],
                [
                    {"text": "修改預約", "callback_data": "main|modify"},
                    {"text": "取消預約", "callback_data": "main|cancel"}
                ]
            ]
            send_message(chat_id, shift_text, buttons=buttons)
            return

        # 以下保留原本以文字為主的管理員功能（新增/修改/刪除等）
        # ---- 管理員功能（文字輸入版） ----
        if user_id in ADMIN_IDS:
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

            # 文字刪除（原有功能保留）
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

    except Exception as e:
        traceback.print_exc()
        # 如果 chat_id 取得不到，就不用發錯誤訊息
        try:
            send_message(chat_id, f"⚠️ 發生錯誤: {e}")
        except:
            pass

# -------------------------------
# 處理 callback_query（按鈕點擊）
# -------------------------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            cq = update["callback_query"]
            data = cq.get("data")
            callback_id = cq.get("id")
            from_user = cq.get("from", {})
            user_id = from_user.get("id")
            user_name = from_user.get("first_name")
            message = cq.get("message", {})
            chat_id = message.get("chat", {}).get("id")

            # 主按鈕入口： reserve / arrive / modify / cancel
            if data and data.startswith("main|"):
                _, action = data.split("|", 1)
                # reserve: 顯示可預約時段按鈕（1行3個）
                if action == "reserve":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    # 只顯示未過的時段（按之前 ensure_today_file 已只建立未過時段）
                    rows = build_shifts_buttons(datafile.get("shifts", []), row_size=3)
                    send_message(chat_id, "請選擇要預約的時段：", buttons=rows)
                    answer_callback(callback_id)
                    return
                # arrive: 顯示該群組的未報到預約名單（點選即標記報到）
                if action == "arrive":
                    path = ensure_today_file()
                    datafile = load_json_file(path)
                    # collect bookings that belong to this chat (group)
                    bookings_for_group = []
                    for s in datafile.get("shifts", []):
                        for b in s.get("bookings", []):
                            if b.get("chat_id") == chat_id:
                                bookings_for_group.append({"time": s["time"], "name": b.get("name")})
                    if not bookings_for_group:
                        send_message(chat_id, "目前沒有未報到的預約。")
                        answer_callback(callback_id)
                        return
                    # build buttons prefix "arrive|HH:MM|name"
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"arrive_select|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 2)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請點選要標記客到的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return
                # modify: 顯示群組的預約名單供修改
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
                        return
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"modify_pick|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 1)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請選擇要修改的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return
                # cancel: 顯示群組的預約名單供取消
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
                        return
                    btns = []
                    for bk in bookings_for_group:
                        btns.append({"text": f"{bk['time']} {bk['name']}", "callback_data": f"cancel_pick|{bk['time']}|{bk['name']}"})
                    rows = chunk_list(btns, 1)
                    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                    send_message(chat_id, "請選擇要取消的預約：", buttons=rows)
                    answer_callback(callback_id)
                    return

            # reserve flow: user chose a time: callback_data = "reserve|HH:MM"
            if data and data.startswith("reserve|"):
                _, hhmm = data.split("|", 1)
                # prompt user to input name (we need user's text so set pending)
                set_pending(user_id, {"action": "reserve_wait_name", "hhmm": hhmm, "group_chat": chat_id})
                send_message(chat_id, f"請在此群輸入欲預約的*姓名*（針對 {hhmm}）。\n輸入後即完成預約。")
                answer_callback(callback_id)
                return

            # arrive select: "arrive_select|HH:MM|name"
            if data and data.startswith("arrive_select|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return
                _, hhmm, name = parts
                path = ensure_today_file()
                datafile = load_json_file(path)
                s = find_shift(datafile.get("shifts", []), hhmm)
                if not s:
                    answer_callback(callback_id, "找不到該時段")
                    return
                # find booking matching (name, chat_id)
                booking = next((b for b in s.get("bookings", []) if b.get("name") == name and b.get("chat_id") == chat_id), None)
                if booking:
                    s.setdefault("in_progress", []).append(name)
                    s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == chat_id)]
                    save_json_file(path, datafile)
                    send_message(chat_id, f"✅ {hhmm} {name} 已標記為到場（已報到）")
                    answer_callback(callback_id)
                    return
                else:
                    answer_callback(callback_id, "找不到該預約或已被移除")
                    return

            # modify pick: user chose which booking to modify: "modify_pick|oldHH:MM|oldName"
            if data and data.startswith("modify_pick|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return
                _, old_hhmm, old_name = parts
                # Show new time options
                path = ensure_today_file()
                datafile = load_json_file(path)
                shifts = datafile.get("shifts", [])
                # Build buttons for shifting to other times
                btns = []
                for s in shifts:
                    btns.append({"text": s["time"], "callback_data": f"modify_to|{old_hhmm}|{old_name}|{s['time']}"})
                rows = chunk_list(btns, 3)
                rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
                send_message(chat_id, f"要將 {old_hhmm} {old_name} 修改到哪個時段？", buttons=rows)
                answer_callback(callback_id)
                return

            # modify_to: "modify_to|oldHH:MM|oldName|newHH:MM"
            if data and data.startswith("modify_to|"):
                parts = data.split("|", 3)
                if len(parts) < 4:
                    answer_callback(callback_id, "資料錯誤")
                    return
                _, old_hhmm, old_name, new_hhmm = parts
                # Prompt for new name (or allow same)
                set_pending(user_id, {"action": "modify_wait_name", "old_hhmm": old_hhmm, "old_name": old_name, "new_hhmm": new_hhmm, "group_chat": chat_id})
                send_message(chat_id, f"請輸入新的姓名（或輸入原姓名以保留 `{old_name}`）以完成從 {old_hhmm} → {new_hhmm} 的修改：")
                answer_callback(callback_id)
                return

            # cancel pick: "cancel_pick|HH:MM|name"
            if data and data.startswith("cancel_pick|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return
                _, hhmm, name = parts
                # confirm cancel with button yes/no
                buttons = [
                    [{"text": "確認取消", "callback_data": f"confirm_cancel|{hhmm}|{name}"},
                     {"text": "取消", "callback_data": "cancel_flow"}]
                ]
                send_message(chat_id, f"確定要取消 {hhmm} {name} 的預約嗎？", buttons=buttons)
                answer_callback(callback_id)
                return

            # confirm_cancel: "confirm_cancel|HH:MM|name"
            if data and data.startswith("confirm_cancel|"):
                parts = data.split("|", 2)
                if len(parts) < 3:
                    answer_callback(callback_id, "資料錯誤")
                    return
                _, hhmm, name = parts
                path = ensure_today_file()
                datafile = load_json_file(path)
                s = find_shift(datafile.get("shifts", []), hhmm)
                if not s:
                    answer_callback(callback_id, "找不到該時段")
                    return
                before_len = len(s.get("bookings", []))
                s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name") == name and b.get("chat_id") == chat_id)]
                save_json_file(path, datafile)
                broadcast_to_groups(generate_latest_shift_list(), group_type="business")
                send_message(chat_id, f"✅ 已取消 {hhmm} {name} 的預約")
                answer_callback(callback_id)
                return

            # cancel_flow or noop
            if data in ("cancel_flow", "noop"):
                answer_callback(callback_id, "已取消")
                return

            # default fallback
            answer_callback(callback_id, "處理中...")

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
                # 只公告給 business 群組（業務群）
                broadcast_to_groups(generate_latest_shift_list(), group_type="business")
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
                        if name not in s.get("in_progress", []):
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
