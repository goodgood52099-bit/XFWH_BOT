from modules.utils import ensure_today_file, find_shift, save_json_file
from modules.telegram_api import send_message

def handle_admin_text(chat_id, text, ADMIN_IDS):
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
            shift["bookings"].clear()
            shift["in_progress"].clear()
            save_json_file(path, data)
            send_message(chat_id, f"🧹 已清空 {hhmm} 的所有名單")
            return

        # 刪除指定數量
        if target.isdigit():
            remove_count = int(target)
            old_limit = shift.get("limit", 1)
            shift["limit"] = max(0, old_limit - remove_count)
            save_json_file(path, data)
            send_message(chat_id, f"🗑 已刪除 {hhmm} 的 {remove_count} 個名額")
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

        if removed_from:
            save_json_file(path, data)
            send_message(chat_id, f"✅ 已從 {hhmm} 移除 {target}")
        else:
            send_message(chat_id, f"⚠️ {hhmm} 找不到 {target}")
