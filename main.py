# filename: telegram_reserve_bot.py
import os
import json
import threading
import time
import requests
import traceback
from flask import Flask, request
from datetime import datetime, time as dt_time
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# -------------------- 設定區 --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ 請在環境變數設定 BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

ADMIN_IDS = [7236880214, 7807558825, 7502175264]
TZ = ZoneInfo("Asia/Taipei")

# -------------------- Flask --------------------
app = Flask(__name__)

# -------------------- DataManager --------------------
class DataManager:
    def __init__(self):
        self.json_lock = threading.Lock()
        self.pending_file = os.path.join(DATA_DIR, "pending.json")
        self.group_file = os.path.join(DATA_DIR, "groups.json")

    def load_json(self, path, default=None):
        with self.json_lock:
            if not os.path.exists(path):
                return default or {}
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    def save_json(self, path, data):
        with self.json_lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # pending
    def get_pending(self, user_id):
        data = self.load_json(self.pending_file)
        return data.get(str(user_id))

    def set_pending(self, user_id, payload):
        data = self.load_json(self.pending_file)
        data[str(user_id)] = payload
        self.save_json(self.pending_file, data)

    def clear_pending(self, user_id):
        data = self.load_json(self.pending_file)
        if str(user_id) in data:
            del data[str(user_id)]
            self.save_json(self.pending_file, data)

    # groups
    def load_groups(self):
        return self.load_json(self.group_file, default=[])

    def save_groups(self, groups):
        self.save_json(self.group_file, groups)

    def add_group(self, chat_id, chat_type, role="business"):
        groups = self.load_groups()
        for g in groups:
            if g["id"] == chat_id:
                g["type"] = role
                self.save_groups(groups)
                return
        if chat_type in ["group", "supergroup"]:
            groups.append({"id": chat_id, "type": role})
            self.save_groups(groups)

    def get_group_ids_by_type(self, group_type=None):
        groups = self.load_groups()
        if group_type:
            return [g["id"] for g in groups if g.get("type") == group_type]
        return [g["id"] for g in groups]

data_manager = DataManager()

# -------------------- ShiftManager --------------------
class ShiftManager:
    def __init__(self):
        self.double_staffs = {}   # key: "hh:mm|business_name" -> [staff1, staff2]
        self.first_notify_sent = {}  # key: "hh:mm|name|business_chat_id"
        self.asked_shifts = set()

    def data_path_for(self, day):
        return os.path.join(DATA_DIR, f"{day}.json")

    def ensure_today_file(self, workers=3):
        today = datetime.now(TZ).date().isoformat()
        path = self.data_path_for(today)
        now = datetime.now(TZ)
        if os.path.exists(path):
            data = data_manager.load_json(path)
            if data.get("date") != today:
                os.remove(path)
        if not os.path.exists(path):
            shifts = []
            for h in range(13, 23):  # 13:00~22:00
                shift_time = dt_time(h, 0)
                shift_dt = datetime.combine(now.date(), shift_time).replace(tzinfo=TZ)
                if shift_dt > now:
                    shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
            data_manager.save_json(path, {"date": today, "shifts": shifts, "候補": []})
        return path

    def find_shift(self, shifts, hhmm):
        for s in shifts:
            if s["time"] == hhmm:
                return s
        return None

    def is_future_time(self, hhmm):
        now = datetime.now(TZ)
        try:
            hh, mm = map(int, hhmm.split(":"))
            shift_dt = datetime.combine(now.date(), dt_time(hh, mm)).replace(tzinfo=TZ)
            return shift_dt > now
        except:
            return False

    def generate_latest_shift_list(self):
        path = self.ensure_today_file()
        data = data_manager.load_json(path)
        msg_lines, checked_in_lines = [], []
        now = datetime.now(TZ)
        shifts = sorted(data.get("shifts", []), key=lambda s: s.get("time", "00:00"))
        for s in shifts:
            time_label = s["time"]
            limit = s.get("limit", 1)
            bookings = s.get("bookings", [])
            in_progress = s.get("in_progress", [])

            shift_dt = datetime.combine(now.date(), datetime.strptime(time_label, "%H:%M").time()).replace(tzinfo=TZ)
            shift_is_past = shift_dt < now

            regular = [x for x in in_progress if not str(x).endswith("(候補)")]
            backup = [x for x in in_progress if str(x).endswith("(候補)")]

            for item in regular:
                checked_in_lines.append(f"{time_label} {item['name'] if isinstance(item, dict) else item} ✅")
            for item in backup:
                checked_in_lines.append(f"{time_label} {item['name'] if isinstance(item, dict) else item} ✅ (候補)")

            for b in bookings:
                name = b.get("name") if isinstance(b, dict) else b
                msg_lines.append(f"{time_label} {name}")

            used_slots = len(bookings) + len(regular)
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

    def generate_unique_name(self, bookings, base_name):
        existing = [b["name"] for b in bookings if isinstance(b, dict)]
        if base_name not in existing:
            return base_name
        idx = 2
        while f"{base_name}({idx})" in existing:
            idx += 1
        return f"{base_name}({idx})"

shift_manager = ShiftManager()

# -------------------- TelegramBot --------------------
class TelegramBot:
    def __init__(self):
        self.api_url = API_URL

    def send_request(self, method, payload):
        return requests.post(self.api_url + method, json=payload).json()

    def send_message(self, chat_id, text, buttons=None, parse_mode="Markdown"):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self.send_request("sendMessage", payload)

    def answer_callback(self, callback_id, text=None, show_alert=False):
        payload = {"callback_query_id": callback_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return self.send_request("answerCallbackQuery", payload)

    def broadcast_to_groups(self, message, group_type=None, buttons=None):
        gids = data_manager.get_group_ids_by_type(group_type)
        for gid in gids:
            try:
                self.send_message(gid, message, buttons=buttons)
            except:
                traceback.print_exc()

    # helpers
    def chunk_list(self, lst, n):
        return [lst[i:i+n] for i in range(0, len(lst), n)]

    def build_shifts_buttons(self, shifts, row_size=3):
        btns = [{"text": s["time"], "callback_data": f"reserve|{s['time']}"} for s in shifts]
        rows = self.chunk_list(btns, row_size)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return rows

    def build_bookings_buttons(self, bookings, prefix):
        btns = [{"text": b.get("name"), "callback_data": f"{prefix}|{b.get('name')}"} for b in bookings]
        if not btns:
            btns = [{"text": "（無）", "callback_data": "noop"}]
        rows = self.chunk_list(btns, 2)
        rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
        return rows

bot = TelegramBot()

# -------------------- 小工具 --------------------
def used_slots(shift):
    return len(shift.get("bookings", [])) + len([x for x in shift.get("in_progress", []) if not str(x).endswith("(候補)")])

def shift_is_full(shift):
    return used_slots(shift) >= shift.get("limit", 1)

def safe_float(text):
    try:
        return float(text.strip())
    except Exception:
        return None

def get_double_staff_key(hhmm, business_name):
    return f"{hhmm}|{business_name}"
    
def normalize_shift_data(data):
    # 確保每個 shift 的 bookings/in_progress 是 list
    for s in data.get("shifts", []):
        if not isinstance(s.get("bookings"), list):
            s["bookings"] = []
        if not isinstance(s.get("in_progress"), list):
            s["in_progress"] = []
    # 確保候補是 list
    if not isinstance(data.get("候補"), list):
        data["候補"] = []
    return data

# aliases for clarity (wrap DataManager / ShiftManager calls used in old code)
def get_pending_for(user_id): return data_manager.get_pending(user_id)
def set_pending_for(user_id, payload): return data_manager.set_pending(user_id, payload)
def clear_pending_for(user_id): return data_manager.clear_pending(user_id)
def load_json_file(path): return data_manager.load_json(path, default={})
def save_json_file(path, data): return data_manager.save_json(path, data)
def broadcast_to_groups(msg, group_type=None, buttons=None): return bot.broadcast_to_groups(msg, group_type=group_type, buttons=buttons)
def generate_latest_shift_list(): return shift_manager.generate_latest_shift_list()
def ensure_today_file(): return shift_manager.ensure_today_file()
def find_shift(shifts, hhmm): return shift_manager.find_shift(shifts, hhmm)
def generate_unique_name(bookings, base_name): return shift_manager.generate_unique_name(bookings, base_name)

# -------------------- Pending 清理執行緒 --------------------
def pending_cleaner_thread():
    while True:
        try:
            now_ts = time.time()
            pending_data = data_manager.load_json(data_manager.pending_file)
            expired = [uid for uid, p in pending_data.items() if now_ts - p.get("created_at", 0) > 180]
            for uid in expired:
                del pending_data[uid]
            if expired:
                data_manager.save_json(data_manager.pending_file, pending_data)
                print(f"🧹 清除過期 pending: {expired}")
        except Exception as e:
            print("❌ pending 自動清理錯誤:", e)
        time.sleep(60)

# start pending cleaner immediately so pending file is managed even before threads are started
threading.Thread(target=pending_cleaner_thread, daemon=True).start()

# -------------------- 管理員文字功能 --------------------
def handle_admin_text(chat_id, text):
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    if text.startswith("/addshift"):
        parts = text.split()
        if len(parts) < 3:
            return bot.send_message(chat_id, "⚠️ 格式：/addshift HH:MM 限制")
        hhmm, limit = parts[1], int(parts[2])
        if find_shift(data.get("shifts", []), hhmm):
            return bot.send_message(chat_id, f"⚠️ {hhmm} 已存在")
        data["shifts"].append({"time": hhmm, "limit": limit, "bookings": [], "in_progress": []})
        save_json_file(path, data)
        return bot.send_message(chat_id, f"✅ 新增 {hhmm} 時段，限制 {limit} 人")

    if text.startswith("/updateshift"):
        parts = text.split()
        if len(parts) < 3:
            return bot.send_message(chat_id, "⚠️ 格式：/updateshift HH:MM 限制")
        hhmm, limit = parts[1], int(parts[2])
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            return bot.send_message(chat_id, f"⚠️ {hhmm} 不存在")
        shift["limit"] = limit
        save_json_file(path, data)
        return bot.send_message(chat_id, f"✅ {hhmm} 時段限制已更新為 {limit}")

    if text.startswith("刪除"):
        parts = text.split()
        if len(parts) < 3:
            return bot.send_message(chat_id, "❗ 格式錯誤\n請輸入：\n刪除 HH:MM 名稱 / 數量 / all")
        hhmm, target = parts[1], " ".join(parts[2:])
        shift = find_shift(data.get("shifts", []), hhmm)
        if not shift:
            return bot.send_message(chat_id, f"⚠️ 找不到 {hhmm} 的時段")

        # 全部清空
        if target.lower() == "all":
            count_b = len(shift.get("bookings", []))
            count_i = len(shift.get("in_progress", []))
            shift["bookings"].clear()
            shift["in_progress"].clear()
            save_json_file(path, data)
            return bot.send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單（未報到 {count_b}、已報到 {count_i}）")

        # 刪除數量
        if target.isdigit():
            remove_count = int(target)
            old_limit = shift.get("limit", 1)
            shift["limit"] = max(0, old_limit - remove_count)
            save_json_file(path, data)
            return bot.send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額（原 {old_limit} → 現在 {shift['limit']}）")

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
            return bot.send_message(chat_id, f"✅ 已從 {hhmm} 移除 {target}（{type_label}）")
        else:
            return bot.send_message(chat_id, f"⚠️ {hhmm} 找不到 {target}")

# -------------------- 文字訊息處理 --------------------
def handle_text_message(msg):
    text = msg.get("text", "").strip()
    chat_id = msg.get("chat", {}).get("id")
    chat_type = msg.get("chat", {}).get("type")
    user = msg.get("from", {})
    user_id = user.get("id")
    user_name = user.get("first_name", "")

    # 自動記錄新群組（預設 business）
    data_manager.add_group(chat_id, chat_type)

    pending = data_manager.get_pending(user_id)
    print(f"DEBUG: user_id={user_id}, pending={pending}, text='{text}'")
    if pending:
        # pending 邏輯會由 handle_pending_action 處理 (下方註冊)
        return

    if text == "/help":
        help_text = """📌 *Telegram 預約機器人指令說明* 📌

一般使用者：
- 按 /list 查看時段並用按鈕操作

管理員：
- 刪除 13:00 all
- 刪除 13:00 2
- 刪除 13:00 小明
- /addshift HH:MM 限制
- /updateshift HH:MM 限制
- /STAFF 設定本群為服務員群組
"""
        return bot.send_message(chat_id, help_text)

    if text.startswith("/STAFF"):
        if user_id not in ADMIN_IDS:
            return bot.send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
        data_manager.add_group(chat_id, chat_type, role="staff")
        return bot.send_message(chat_id, "✅ 已將本群組設定為服務員群組")

    if text == "/list":
        data_manager.clear_pending(user_id)
        shift_text = shift_manager.generate_latest_shift_list()
        buttons = [
            [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
            [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
        ]
        return bot.send_message(chat_id, shift_text, buttons=buttons)

    if user_id in ADMIN_IDS:
        return handle_admin_text(chat_id, text)

    return bot.send_message(chat_id, "💡 請使用 /list 查看可預約時段。")

# -------------------- Pending 機制（註冊式） --------------------
PENDING_HANDLERS = {}
def register_pending(action_name):
    def decorator(func):
        PENDING_HANDLERS[action_name] = func
        return func
    return decorator

def handle_pending_action(user_id, chat_id, text, pending):
    handler = PENDING_HANDLERS.get(pending.get("action"))
    if not handler:
        bot.send_message(chat_id, "⚠️ 未知動作，已清除暫存。")
        data_manager.clear_pending(user_id)
        return
    try:
        if handler(user_id, chat_id, text, pending):
            data_manager.clear_pending(user_id)
    except Exception:
        traceback.print_exc()
        bot.send_message(chat_id, f"❌ 執行動作 {pending.get('action')} 時發生錯誤")

# ----- 下面註冊各 pending handler（功能等於你原本的） -----
@register_pending("reserve_wait_name")
def _reserve_wait_name(user_id, chat_id, text, pending):
    hhmm = pending["hhmm"]
    group_chat = pending["group_chat"]
    name_input = text.strip()
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    s = find_shift(data["shifts"], hhmm)
    if not s or shift_is_full(s):
        bot.send_message(group_chat, f"⚠️ {hhmm} 不存在或已滿額")
        return False
    unique_name = generate_unique_name(s.get("bookings", []), name_input)
    s.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)
    bot.send_message(group_chat, f"✅ {unique_name} 已預約 {hhmm}")
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    bot.broadcast_to_groups(shift_manager.generate_latest_shift_list(), group_type="business", buttons=buttons)
    return True

@register_pending("arrive_wait_amount")
def _arrive_wait_amount(user_id, chat_id, text, pending):
    hhmm, name, group_chat = pending["hhmm"], pending["name"], pending["group_chat"]
    amount = safe_float(text)
    if amount is None:
        bot.send_message(group_chat, "⚠️ 金額格式錯誤")
        return False
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    s = find_shift(data["shifts"], hhmm)
    booking = next((b for b in s.get("bookings", []) if b.get("name")==name and b.get("chat_id")==group_chat), None)
    if not booking:
        bot.send_message(group_chat, f"⚠️ 找不到預約 {name}")
        return False
    s.setdefault("in_progress", []).append({"name": name, "amount": amount})
    s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name")==name and b.get("chat_id")==group_chat)]
    save_json_file(path, data)
    bot.send_message(group_chat, f"✅ {hhmm} {name} 已標記到場，金額：{amount}")
    staff_message = f"🙋‍♀️ 客到通知\n時間：{hhmm}\n業務名：{name}\n金額：{amount}"
    staff_buttons = [[{"text": "上", "callback_data": f"staff_up|{hhmm}|{name}|{group_chat}"}]]
    bot.broadcast_to_groups(staff_message, group_type="staff", buttons=staff_buttons)
    return True

@register_pending("input_client")
def _input_client(user_id, chat_id, text, pending):
    try:
        client_name, age, staff_name, amount = text.split()
    except ValueError:
        bot.send_message(chat_id, "❌ 格式錯誤，請輸入：小美 25 Alice 3000")
        return False
    hhmm, business_name, business_chat_id = pending["hhmm"], pending["business_name"], pending["business_chat_id"]
    msg = f"📌 客\n{hhmm} {client_name}{age} {business_name}{amount}\n服務人員: {staff_name}"
    bot.send_message(int(business_chat_id), msg)
    staff_buttons = [
        [
            {"text": "雙", "callback_data": f"double|{hhmm}|{business_name}|{business_chat_id}"},
            {"text": "完成服務", "callback_data": f"complete|{hhmm}|{business_name}|{business_chat_id}"},
            {"text": "修正", "callback_data": f"fix|{hhmm}|{business_name}|{business_chat_id}"}
        ]
    ]
    bot.send_message(chat_id, msg, buttons=staff_buttons)
    return True

@register_pending("double_wait_second")
def _double_wait_second(user_id, chat_id, text, pending):
    key = get_double_staff_key(pending["hhmm"], pending["business_name"])
    shift_manager.double_staffs[key] = [pending["first_staff"], text.strip()]
    bot.send_message(int(pending["business_chat_id"]), f"👥 雙人服務更新：{'、'.join(shift_manager.double_staffs[key])}")
    return True

@register_pending("complete_wait_amount")
def _complete_wait_amount(user_id, chat_id, text, pending):
    amount = safe_float(text)
    if amount is None:
        bot.send_message(chat_id, "⚠️ 金額格式錯誤")
        return False
    staff_str = "、".join(pending["staff_list"])
    msg = f"✅ 完成服務通知\n{pending['hhmm']} {pending['business_name']}\n服務人員: {staff_str}\n金額: {amount}"
    bot.send_message(chat_id, msg)
    bot.send_message(int(pending["business_chat_id"]), msg)
    return True

@register_pending("not_consumed_wait_reason")
def _not_consumed(user_id, chat_id, text, pending):
    bot.send_message(chat_id, "掰掰謝謝光臨!!")
    bot.send_message(int(pending["business_chat_id"]), f"⚠️ 未消: {pending['name']} {text.strip()}")
    return True

@register_pending("modify_wait_name")
def _modify_wait_name(user_id, chat_id, text, pending):
    old_hhmm, old_name, new_hhmm, group_chat = pending["old_hhmm"], pending["old_name"], pending["new_hhmm"], pending["group_chat"]
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    old_shift = find_shift(data["shifts"], old_hhmm)
    new_shift = find_shift(data["shifts"], new_hhmm)
    if not old_shift or not new_shift or shift_is_full(new_shift):
        bot.send_message(group_chat, "⚠️ 時段不存在或已滿額")
        return False
    old_shift["bookings"] = [b for b in old_shift.get("bookings", []) if not (b.get("name")==old_name and b.get("chat_id")==group_chat)]
    unique_name = generate_unique_name(new_shift.get("bookings", []), text.strip())
    new_shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": group_chat})
    save_json_file(path, data)
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    bot.broadcast_to_groups(shift_manager.generate_latest_shift_list(), group_type="business", buttons=buttons)
    bot.send_message(group_chat, f"✅ 已修改：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")
    return True

# -------------------- main 按鈕處理 --------------------
def handle_main(user_id, chat_id, action, callback_id):
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)

    def reply(text, buttons=None):
        bot.send_message(chat_id, text, buttons=buttons)
        bot.answer_callback(callback_id)

    shifts = data.get("shifts", [])
    now = datetime.now(TZ)

    def future_shifts():
        res = []
        for s in shifts:
            t = s.get("time")
            if not t: continue
            hh, mm = map(int, t.split(":"))
            dt_s = datetime.combine(now.date(), dt_time(hh, mm)).replace(tzinfo=TZ)
            if dt_s > now:
                res.append(s)
        return res

    def group_bookings():
        res = []
        for s in shifts:
            for b in s.get("bookings", []):
                if b.get("chat_id") == chat_id:
                    res.append({"time": s["time"], "name": b["name"]})
        return res

    if action == "reserve":
        fs = future_shifts()
        if not fs: return reply("📅 目前沒有可預約的時段。")
        rows = []
        row = []
        for s in fs:
            btn = {"text": f"{s['time']} ({s.get('limit',1)-used_slots(s)})" if not shift_is_full(s) else f"{s['time']} (滿)",
                   "callback_data": f"reserve_pick|{s['time']}" if not shift_is_full(s) else "noop"}
            row.append(btn)
            if len(row)==3: rows.append(row); row=[]
        if row: rows.append(row)
        rows.append([{"text":"取消","callback_data":"cancel_flow"}])
        return reply("請選擇要預約的時段：", buttons=rows)

    elif action in ["arrive","modify","cancel"]:
        bks = group_bookings()
        if not bks: return reply("目前沒有相關預約。")
        btns = [{"text": f"{bk['time']} {bk['name']}", "callback_data": f"{action}_pick|{bk['time']}|{bk['name']}"} for bk in bks]
        rows = bot.chunk_list(btns, 2 if action=="arrive" else 1)
        rows.append([{"text":"取消","callback_data":"cancel_flow"}])
        return reply(f"請選擇要{action}的預約：", buttons=rows)

# -------------------- 回應/Callback 主處理 --------------------
def safe_reply(chat_id, text, buttons=None, callback_id=None):
    bot.send_message(chat_id, text, buttons=buttons)
    if callback_id:
        bot.answer_callback(callback_id)

def handle_callback_query(cq):
    callback_id = cq["id"]
    data = cq["data"]
    user_id = cq["from"]["id"]
    chat_id = cq["message"]["chat"]["id"]

    print(f"DEBUG callback_query: {data} from {user_id} in {chat_id}")

    if data == "cancel_flow":
        data_manager.clear_pending(user_id)
        return safe_reply(chat_id, "❌ 已取消操作。", callback_id)

    pending = data_manager.get_pending(user_id)

    if data.startswith("main|"):
        action = data.split("|", 1)[1]
        handle_main(user_id, chat_id, action, callback_id)
        return

    if data.startswith("reserve_pick|"):
        if pending:
            return safe_reply(chat_id, "⚠️ 請先完成或取消目前操作。", callback_id)
        hhmm = data.split("|", 1)[1]
        data_manager.set_pending(user_id, {
            "action": "reserve_wait_name",
            "hhmm": hhmm,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        return safe_reply(chat_id, f"✏️ 請輸入要預約 {hhmm} 的姓名：", callback_id)

    if data.startswith("arrive_select|"):
        if pending:
            return safe_reply(chat_id, "⚠️ 請先完成或取消目前操作。", callback_id)
        _, hhmm, name = data.split("|")
        data_manager.set_pending(user_id, {
            "action": "arrive_wait_amount",
            "hhmm": hhmm,
            "name": name,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        return safe_reply(chat_id, f"✏️ 請輸入 {hhmm} {name} 的金額：", callback_id)

    if data.startswith("modify_pick|"):
        if pending:
            return safe_reply(chat_id, "⚠️ 請先完成或取消目前操作。", callback_id)
        _, old_hhmm, old_name = data.split("|")
        handle_modify_pick(user_id, chat_id, old_hhmm, old_name)
        return safe_reply(chat_id, "請選擇修改目標時段", callback_id)

    if data.startswith("modify_to|"):
        if pending:
            return safe_reply(chat_id, "⚠️ 請先完成或取消目前操作。", callback_id)
        _, old_hhmm, old_name, new_hhmm = data.split("|")
        data_manager.set_pending(user_id, {
            "action": "modify_wait_name",
            "old_hhmm": old_hhmm,
            "old_name": old_name,
            "new_hhmm": new_hhmm,
            "group_chat": chat_id,
            "created_at": time.time()
        })
        return safe_reply(chat_id, f"✏️ 請輸入新名稱來修改 {old_hhmm} {old_name} → {new_hhmm}", callback_id)

    if data.startswith("cancel_pick|"):
        _, hhmm, name = data.split("|")
        handle_confirm_cancel(chat_id, user_id, hhmm, name, callback_id)
        return

    staff_actions = ["staff_up", "input_client", "not_consumed", "double", "complete", "fix"]
    for act in staff_actions:
        if data.startswith(act + "|"):
            parts = data.split("|")
            if act == "double" and len(parts) >= 4:
                _, hhmm, business_name, business_chat_id = parts
                key = get_double_staff_key(hhmm, business_name)
                if key in shift_manager.double_staffs:
                    return safe_reply(chat_id, f"⚠️ {hhmm} {business_name} 已被 {shift_manager.double_staffs[key][0]} 選擇", callback_id)
            handle_staff_callback(user_id, chat_id, act, parts, callback_id)
            return

    safe_reply(chat_id, "⚠️ 此按鈕暫時無效", callback_id)

# -------------------- staff callback & helpers --------------------
def handle_modify_pick(user_id, chat_id, old_hhmm, old_name):
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    shifts = [s for s in data.get("shifts", []) if shift_manager.is_future_time(s.get("time",""))]
    rows = []
    row = []
    for s in shifts:
        row.append({"text": s["time"], "callback_data": f"modify_to|{old_hhmm}|{old_name}|{s['time']}"} )
        if len(row) == 3:
            rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([{"text": "取消", "callback_data": "cancel_flow"}])
    bot.send_message(chat_id, f"要將 {old_hhmm} {old_name} 修改到哪個時段？", buttons=rows)
    bot.answer_callback(None)

def handle_confirm_cancel(chat_id, user_id, hhmm, name, callback_id):
    path = ensure_today_file()
    data = load_json_file(path)
    data = normalize_shift_data(data)
    s = find_shift(data.get("shifts", []), hhmm)
    if not s:
        return bot.answer_callback(callback_id, "找不到該時段")
    s["bookings"] = [b for b in s.get("bookings", []) if not (b.get("name")==name and b.get("chat_id")==chat_id)]
    save_json_file(path, data)
    data_manager.clear_pending(user_id)
    buttons = [
        [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
        [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
    ]
    bot.broadcast_to_groups(shift_manager.generate_latest_shift_list(), group_type="business", buttons=buttons)
    bot.send_message(chat_id, f"✅ 已取消 {hhmm} {name} 的預約")
    bot.answer_callback(callback_id)

def get_staff_name(user_id):
    # placeholder: 如果有 staff 名單可以擷取，否則用 id
    return f"staff_{user_id}"

def handle_staff_callback(user_id, chat_id, action, parts, callback_id):
    def reply(text, buttons=None):
        bot.send_message(chat_id, text, buttons=buttons)
        bot.answer_callback(callback_id)

    if action == "staff_up":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, name, business_chat_id = parts
        key = f"{hhmm}|{name}|{business_chat_id}"
        if key not in shift_manager.first_notify_sent:
            bot.send_message(int(business_chat_id), f"⬆️ 上 {hhmm} {name}")
            shift_manager.first_notify_sent[key] = True
        staff_buttons = [[
            {"text": "輸入客資", "callback_data": f"input_client|{hhmm}|{name}|{business_chat_id}"},
            {"text": "未消", "callback_data": f"not_consumed|{hhmm}|{name}|{business_chat_id}"}
        ]]
        return reply(f"✅ 已通知業務 {name}", buttons=staff_buttons)

    if action == "input_client":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        data_manager.set_pending(user_id, {
            "action": "input_client",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id
        })
        return reply("✏️ 請輸入客稱、年紀、服務人員與金額（格式：小美 25 Alice 3000）")

    if action == "not_consumed":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, name, business_chat_id = parts
        data_manager.set_pending(user_id, {
            "action": "not_consumed_wait_reason",
            "hhmm": hhmm,
            "name": name,
            "business_chat_id": business_chat_id
        })
        return reply("✏️ 請輸入未消原因：")

    if action == "double":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        first_staff = get_staff_name(user_id)
        key = get_double_staff_key(hhmm, business_name)
        if key in shift_manager.double_staffs:
            return reply(f"⚠️ {hhmm} {business_name} 已有人選擇第一位服務員：{shift_manager.double_staffs[key][0]}")
        data_manager.set_pending(user_id, {
            "action": "double_wait_second",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id,
            "first_staff": first_staff
        })
        return reply(f"✏️ 請輸入另一位服務員名字，與 {first_staff} 配合雙人服務")

    if action == "complete":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        key = get_double_staff_key(hhmm, business_name)
        staff_list = shift_manager.double_staffs.get(key, [get_staff_name(user_id)])
        data_manager.set_pending(user_id, {
            "action": "complete_wait_amount",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id,
            "staff_list": staff_list
        })
        return reply(f"✏️ 請輸入 {hhmm} {business_name} 的總金額（數字）：")

    if action == "fix":
        if len(parts) < 4:
            return reply("❌ 資料格式錯誤")
        _, hhmm, business_name, business_chat_id = parts
        data_manager.set_pending(user_id, {
            "action": "input_client",
            "hhmm": hhmm,
            "business_name": business_name,
            "business_chat_id": business_chat_id
        })
        return reply("✏️ 請重新輸入客資（格式：小美 25 Alice 3000）")

    return reply("⚠️ 無效按鈕")

# -------------------- 背景任務（多執行緒） --------------------
def task_auto_announce():
    """自動每整點公告（僅發業務群）"""
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try:
                text = shift_manager.generate_latest_shift_list()
                buttons = [
                    [{"text": "預約", "callback_data": "main|reserve"}, {"text": "客到", "callback_data": "main|arrive"}],
                    [{"text": "修改預約", "callback_data": "main|modify"}, {"text": "取消預約", "callback_data": "main|cancel"}],
                ]
                bot.broadcast_to_groups(text, group_type="business", buttons=buttons)
                print(f"[INFO] 已自動公告：{now.strftime('%Y-%m-%d %H:%M')}")
            except Exception:
                traceback.print_exc()
            time.sleep(60)
        time.sleep(10)

def task_ask_arrivals():
    """整點詢問預約者是否到場（發給原本預約那個群）"""
    shift_manager.asked_shifts = set()
    while True:
        try:
            now = datetime.now(TZ)
            current_hm = f"{now.hour:02d}:00"
            today = now.date().isoformat()
            key = f"{today}|{current_hm}"

            if now.minute == 0 and key not in shift_manager.asked_shifts:
                data_path = shift_manager.data_path_for(today)
                if os.path.exists(data_path):
                    data = load_json_file(data_path)
                    for s in data.get("shifts", []):
                        if s.get("time") != current_hm:
                            continue
                        waiting, groups = [], set()
                        in_progress_names = [x["name"] if isinstance(x, dict) else x for x in s.get("in_progress", [])]
                        for b in s.get("bookings", []):
                            name = b.get("name")
                            gid = b.get("chat_id")
                            if name not in in_progress_names:
                                waiting.append(name)
                                groups.add(gid)
                        if waiting:
                            msg = f"⏰ 現在是 {current_hm}\n請問預約的「{'、'.join(waiting)}」到了嗎？\n可使用 /list → 客到"
                            for gid in groups:
                                bot.send_message(gid, msg)
                shift_manager.asked_shifts.add(key)

            # 每天清理
            if now.hour == 0 and now.minute == 1:
                shift_manager.asked_shifts.clear()
        except Exception:
            traceback.print_exc()
        time.sleep(10)

def start_background_threads():
    threads = [
        threading.Thread(target=task_auto_announce, daemon=True),
        threading.Thread(target=task_ask_arrivals, daemon=True),
        # pending_cleaner_thread 已經在上方啟動
    ]
    for t in threads:
        t.start()

# -------------------- Webhook 主入口 --------------------
@app.route("/", methods=["POST"])
def webhook():
    try:
        update = request.json
        print("DEBUG webhook 收到:", update)
        if "message" in update:
            handle_text_message(update["message"])
        elif "callback_query" in update:
            handle_callback_query(update["callback_query"])
    except Exception:
        traceback.print_exc()
    return "OK"

# -------------------- 啟動 --------------------
if __name__ == "__main__":
    start_background_threads()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

