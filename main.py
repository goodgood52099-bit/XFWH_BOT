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
    raise ValueError("❌ 請在 Render 環境變數設定 BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)
ADMIN_IDS = [7236880214,7807558825,7502175264]  # 管理員 Telegram ID，自行修改
GROUP_FILE = os.path.join(DATA_DIR, "groups.json")
TZ = ZoneInfo("Asia/Taipei")  # 台灣時區

asked_shifts = set()

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
# JSON 存取
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
        for h in range(13, 23):
            shift_time = dt_time(h, 0)
            shift_dt = datetime.combine(datetime.now(TZ).date(), shift_time).replace(tzinfo=TZ)
            if shift_dt > now:
                shifts.append({"time": f"{h:02d}:00", "limit": workers, "bookings": [], "in_progress": []})
        save_json_file(path, {"date": today, "shifts": shifts, "候補":[]})
    return path

def load_json_file(path, default=None):
    if not os.path.exists(path): return default or {}
    with open(path, "r", encoding="utf-8") as f: return json.load(f)

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def find_shift(shifts, hhmm): 
    for s in shifts:
        if s["time"] == hhmm: return s
    return None

def is_future_time(hhmm):
    now = datetime.now(TZ)
    try:
        hh, mm = map(int, hhmm.split(":"))
        shift_dt = datetime.combine(datetime.now(TZ).date(), dt_time(hh, mm)).replace(tzinfo=TZ)
        return shift_dt > now
    except: return False

# -------------------------------
# Telegram 發送（支援按鈕）
# -------------------------------
def send_request(method, payload): return requests.post(API_URL + method, json=payload).json()
def send_message(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return send_request("sendMessage", payload)

def broadcast_to_groups(message, group_type=None):
    gids = get_group_ids_by_type(group_type)
    for gid in gids: 
        try:
            send_message(gid, message)
        except Exception:
            traceback.print_exc()

# -------------------------------
# 生成最新時段列表
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
# 處理訊息
# -------------------------------
def handle_message(msg):
    try:
        text = msg.get("text", "").strip() if msg.get("text") else ""
        chat_id = msg.get("chat", {}).get("id")
        user_id = msg.get("from", {}).get("id")
        user_name = msg.get("from", {}).get("first_name")
        chat_type = msg.get("chat", {}).get("type")
        add_group(chat_id, chat_type)  # 預設 business
        if not text:
            return

        # -------------------------------
        # /help 指令
        if text == "/help":
            help_text = """
📌 *Telegram 預約機器人指令說明* 📌

一般使用者：
- 預約:預約 12:00 王小明
- 取消:取消 12:00 王小明
- 客到:客到 12:00 王小明
- 修改:修改 原時段 原姓名 新時段/新姓名
- /list 查看今日未到時段列表

管理員：
- 上:上 12:00 王小明
- 刪除 13:00 all	清空該時段所有名單（預約＋報到）
- 刪除 13:00 2	    名額減少 2
- 刪除 13:00 小明	刪除該時段的小明 （自動判斷預約/報到/候補） 
- /addshift 增加時段
- /updateshift 修改時段班數
- /STAFF 設定本群為服務員群組
"""
            send_message(chat_id, help_text)
            return

        # -------------------------------
        # /STAFF 指令：設定本群為服務員群組
        if text.startswith("/STAFF"):
            if user_id not in ADMIN_IDS:
                send_message(chat_id, "⚠️ 你沒有權限設定服務員群組")
                return
            add_group(chat_id, "group", group_role="staff")
            send_message(chat_id, "✅ 已將本群組設定為服務員群組")
            return

        # -------------------------------
        # /list
        if text == "/list":
            shift_text = generate_latest_shift_list()
            buttons = [
                [
                    {"text": "預約", "callback_data": "預約"},
                    {"text": "客到", "callback_data": "客到"}
                ],
                [
                    {"text": "修改預約", "callback_data": "修改預約"},
                    {"text": "取消預約", "callback_data": "取消預約"}
                ]
            ]
            send_message(chat_id, shift_text, buttons=buttons)
            return

        # -------------------------------
        # 預約
        if text.startswith("預約"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "⚠️ 格式：預約 HH:MM 姓名")
                return
            hhmm, name_input = parts[1], " ".join(parts[2:])
            if not is_future_time(hhmm):
                send_message(chat_id, f"⚠️ {hhmm} 時段已過或格式錯誤，無法預約")
                return
            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s:
                send_message(chat_id, f"⚠️ {hhmm} 不存在")
                return
            if len(s.get("bookings", [])) >= s.get("limit", 1):
                send_message(chat_id, f"⚠️ {hhmm} 已滿額")
                return
            unique_name = generate_unique_name(s.get("bookings", []), name_input)
            s.setdefault("bookings", []).append({"name": unique_name, "chat_id": chat_id})
            save_json_file(path, data)
            send_message(chat_id, f"✅ {unique_name} 已預約 {hhmm}")
            broadcast_to_groups(generate_latest_shift_list())
            return

        # -------------------------------
        # 取消預約
        if text.startswith("取消"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "⚠️ 格式錯誤：取消 HH:MM 名稱")
                return
            hhmm = parts[1]
            name_to_cancel = " ".join(parts[2:])
            path = ensure_today_file()
            data = load_json_file(path)
            s = find_shift(data.get("shifts", []), hhmm)
            if not s:
                send_message(chat_id, f"⚠️ {hhmm} 時段不存在")
                return
            bookings = s.get("bookings", [])
            new_bookings = [b for b in bookings if not (b.get("name") == name_to_cancel and b.get("chat_id") == chat_id)]
            if len(new_bookings) == len(bookings):
                send_message(chat_id, f"⚠️ {hhmm} 沒有你的預約（或你不屬於該群組）")
                return
            s["bookings"] = new_bookings
            save_json_file(path, data)
            broadcast_to_groups(generate_latest_shift_list())
            send_message(chat_id, f"✅ 已取消 {hhmm} {name_to_cancel} 的預約")
            return

        # -------------------------------
        # 客到報到
        if text.startswith("客到"):
            parts = text.split()
            if len(parts) < 3:
                send_message(chat_id, "❗請輸入格式：客到 HH:MM 姓名")
                return

            hhmm = parts[1]
            name = " ".join(parts[2:])
            path = ensure_today_file()
            data = load_json_file(path)
            found = False

            # 🔹 先尋找該時段的預約者
            for s in data.get("shifts", []):
                if s.get("time") == hhmm:
                    for b in s.get("bookings", []):
                        if b.get("name") == name and b.get("chat_id") == chat_id:
                            s.setdefault("in_progress", []).append(name)
                            s["bookings"] = [bk for bk in s["bookings"] if bk.get("name") != name]
                            found = True
                            send_message(chat_id, f"✅ {hhmm} {name} 已報到")
                            break
                    if found:
                        break       

            # 🔹 未預約者 → 直接加入已報到區，備註 (候補)
            if not found:
                for s in data.get("shifts", []):
                    if s.get("time") == hhmm:
                        s.setdefault("in_progress", []).append(f"{name} (候補)")
                        send_message(chat_id, f"✅ {hhmm} {name} 未預約加入 (候補)")
                        break

            save_json_file(path, data)
            return

        # -------------------------------
        # 修改預約
        if text.startswith("修改"):
            parts = text.split()
            if len(parts) < 4:
                send_message(chat_id, "⚠️ 格式：修改 HH:MM 原姓名 新時段 [新姓名]")
                return
            old_hhmm, old_name = parts[1], parts[2]
            new_hhmm = parts[3]
            new_name_input = " ".join(parts[4:]) if len(parts) > 4 else old_name

            if not is_future_time(new_hhmm):
                send_message(chat_id, f"⚠️ {new_hhmm} 時段已過或格式錯誤，無法修改")
                return

            path = ensure_today_file()
            data = load_json_file(path)

            old_shift = find_shift(data.get("shifts", []), old_hhmm)
            if not old_shift:
                send_message(chat_id, f"⚠️ {old_hhmm} 不存在")
                return
            booking = next((b for b in old_shift.get("bookings", []) if b.get("name") == old_name and b.get("chat_id") == chat_id), None)
            if not booking:
                send_message(chat_id, f"⚠️ {old_hhmm} 沒有你的預約")
                return

            new_shift = find_shift(data.get("shifts", []), new_hhmm)
            if not new_shift:
                send_message(chat_id, f"⚠️ {new_hhmm} 時段不存在")
                return
            if len(new_shift.get("bookings", [])) >= new_shift.get("limit", 1):
                send_message(chat_id, f"⚠️ {new_hhmm} 已滿額，無法修改")
                return

            old_shift["bookings"] = [b for b in old_shift.get("bookings", []) if not (b.get("name") == old_name and b.get("chat_id") == chat_id)]
            unique_name = generate_unique_name(new_shift.get("bookings", []), new_name_input)
            new_shift.setdefault("bookings", []).append({"name": unique_name, "chat_id": chat_id})
            save_json_file(path, data)
            broadcast_to_groups(generate_latest_shift_list())
            send_message(chat_id, f"✅ 已修改預約：{old_hhmm} {old_name} → {new_hhmm} {unique_name}")
            return

        # -------------------------------
        # 管理員指令
        if user_id in ADMIN_IDS:

            # 上 HH:MM 名稱
            if text.startswith("上"):
                parts = text.split()
                if len(parts) < 3:
                    send_message(chat_id, "⚠️ 格式錯誤：上 HH:MM 名稱")
                    return
                hhmm = parts[1]
                name = " ".join(parts[2:])
                path = ensure_today_file()
                data = load_json_file(path)
                s = find_shift(data.get("shifts", []), hhmm)
                if not s:
                    send_message(chat_id, f"⚠️ {hhmm} 時段不存在")
                    return
                if name not in s.get("in_progress", []):
                    send_message(chat_id, f"⚠️ {hhmm} {name} 尚未報到，無法標記上")
                    return
                if name in s.get("in_progress", []):
                    s["in_progress"].remove(name)
                bookings = s.get("bookings", [])
                s["bookings"] = [b for b in bookings if b.get("name") != name]
                data["候補"] = [c for c in data.get("候補", []) if not (c.get("time") == hhmm and c.get("name") == name)]
                save_json_file(path, data)
                return

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

            # -------------------------------
            # 管理員刪除功能（簡潔輸入版升級）
            if text.startswith("刪除"):
                if user_id not in ADMIN_IDS:
                    send_message(chat_id, "⚠️ 你沒有權限使用刪除功能。")
                    return

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

                # ✅ 特殊指令：清空整時段
                if target.lower() == "all":
                    count_b = len(s.get("bookings", []))
                    count_i = len(s.get("in_progress", []))
                    s["bookings"].clear()
                    s["in_progress"].clear()
                    save_json_file(path, data)
                    send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單（未報到 {count_b}、已報到 {count_i}）")
                    return

                # ✅ 如果是數字 → 減少名額
                if target.isdigit():
                    remove_count = int(target)
                    if remove_count <= 0:
                        send_message(chat_id, "❗ 名額數量必須大於 0")
                        return
                    old_limit = s.get("limit", 1)
                    s["limit"] = max(0, old_limit - remove_count)
                    save_json_file(path, data)
                    send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額（原本 {old_limit} → 現在 {s['limit']}）")
                    return

                # ✅ 否則 → 刪除人名
                removed_from = None
                # 刪除預約名單
                for b in s.get("bookings", []):
                    if b.get("name") == target:
                        s["bookings"].remove(b)
                        removed_from = "bookings"
                        break

                # 刪除已報到名單
                if not removed_from and target in s.get("in_progress", []):
                    s["in_progress"].remove(target)
                    removed_from = "in_progress"

                # 刪除候補區（若有）
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
        send_message(chat_id, f"⚠️ 發生錯誤: {e}")

    except Exception as e:
        traceback.print_exc()
        send_message(chat_id, f"⚠️ 發生錯誤: {e}")

# -------------------------------
# Flask webhook（含 callback_query）
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
            chat_id = cq["message"]["chat"]["id"]

            # 根據按鈕執行對應提示
            if data == "預約":
                send_message(chat_id, "請輸入格式：預約 HH:MM 姓名")
            elif data == "客到":
                send_message(chat_id, "請輸入格式：客到 HH:MM 姓名")
            elif data == "修改預約":
                send_message(chat_id, "請輸入格式：修改 HH:MM 原姓名 新時段 [新姓名]")
            elif data == "取消預約":
                send_message(chat_id, "請輸入格式：取消 HH:MM 名稱")
    except:
        traceback.print_exc()
    return {"ok": True}

# -------------------------------
# 自動整點公告
# -------------------------------
def auto_announce():
    while True:
        now = datetime.now(TZ)
        if 12 <= now.hour <= 22 and now.minute == 0:
            try: broadcast_to_groups(generate_latest_shift_list())
            except: traceback.print_exc()
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
                        text = f"⏰ 現在是 {current_hm}\n請問預約的「{names_text}」到了嗎？\n到了請回覆：客到 {current_hm} 名稱"
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
