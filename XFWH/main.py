from flask import Flask, request
from config import ADMIN_IDS
from modules.utils import build_shifts_buttons, build_bookings_buttons
from modules.admin import handle_admin_text
from modules.pending import get_pending_for, cleanup_expired_pending
from modules.groups import add_group
from modules.telegram_api import send_message
from modules.shifts import generate_latest_shift_list
from modules.background import auto_announce, ask_arrivals_thread
from modules.pending_action import handle_pending_action
import threading
import traceback

app = Flask(__name__)

# -------------------------------
# Webhook 入口
# -------------------------------
@app.route("/", methods=["POST"])
def webhook():
    try:
        data = request.json
        print("DEBUG: 收到 webhook:", data)  # DEBUG log

        if "message" in data:
            handle_text_message(data["message"])
        else:
            print("DEBUG: 非 message 更新，忽略")
    except Exception:
        print("ERROR: webhook 處理失敗")
        traceback.print_exc()
    return "OK"

# -------------------------------
# 文字訊息處理
# -------------------------------
def handle_text_message(msg):
    text = msg.get("text", "").strip() if msg.get("text") else ""
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = msg.get("from", {})
    user_id = user.get("id")
    user_name = user.get("first_name", "")

    print(f"DEBUG: 收到訊息 from {user_id} ({user_name}) in chat {chat_id}: {text}")

    cleanup_expired_pending()
    add_group(chat_id, chat_type)

    pending = get_pending_for(user_id)
    if pending:
        print(f"DEBUG: 使用者 {user_id} 有 pending 行為，處理中...")
        handle_pending_action(user_id, chat_id, text, pending)
        return

    if text == "/help":
        help_text = "📌 Telegram 預約機器人指令說明 📌\n一般使用者：按 /list 查看時段\n管理員：/addshift /updateshift /刪除 /STAFF"
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
        handle_admin_text(chat_id, text, ADMIN_IDS)
        return

    send_message(chat_id, "💡 請使用 /list 查看可預約時段。")

# -------------------------------
# 啟動背景線程（整點公告 + 自動詢問）
# -------------------------------
threading.Thread(target=auto_announce, daemon=True).start()
threading.Thread(target=ask_arrivals_thread, daemon=True).start()

# -------------------------------
# 啟動 Flask
# -------------------------------
if __name__ == "__main__":
    print("DEBUG: Flask 啟動中...")
    app.run(host="0.0.0.0", port=5000, debug=True)
