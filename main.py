import os
import requests
from flask import Flask, request

TOKEN = os.environ.get("BOT_TOKEN")  # 在 Zeabur 環境變數中設置
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

# Webhook 入口，處理 Telegram 訊息
@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()
    print(data)  # Zeabur logs 會看到訊息內容，用於測試 webhook

    # 收到訊息
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        # 判斷 /start 或 /start@BotUsername
        if text.startswith("/start"):
            keyboard = {
                "inline_keyboard": [
                    [{"text": "報到 ✅", "callback_data": "checkin"}],
                    [{"text": "取消 ❌", "callback_data": "cancel"}]
                ]
            }
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": "請選擇操作：",
                "reply_markup": keyboard
            })
        else:
            # 測試 webhook，可刪除
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"收到訊息：{text}"
            })

    # 收到按鈕回傳
    elif "callback_query" in data:
        callback = data["callback_query"]
        chat_id = callback["message"]["chat"]["id"]
        query_data = callback["data"]

        if query_data == "checkin":
            reply_text = "✅ 你已成功報到！"
        elif query_data == "cancel":
            reply_text = "❌ 已取消報到"
        else:
            reply_text = "未知指令"

        requests.post(f"{BASE_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": reply_text
        })

    return "OK", 200


# 瀏覽器測試用
@app.route("/", methods=["GET"])
def index():
    return "Bot is running. Webhook test OK."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
