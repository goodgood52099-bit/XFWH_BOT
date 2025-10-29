import os
import requests
from flask import Flask, request

TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()
    print(data)  # 可以在 Zeabur logs 看到訊息內容

    # 收到一般訊息
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if text == "/start":
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


@app.route("/", methods=["GET"])
def index():
    return "Bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
