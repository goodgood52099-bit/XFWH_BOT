import os
import requests
from flask import Flask, request

TOKEN = os.environ.get("BOT_TOKEN")  # 在 Zeabur 環境變數中設置
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    data = request.get_json()
    print(data)  # Zeabur logs 會看到訊息內容

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "").lower()

        if text.startswith("/start"):
            # 回傳文字說明 + 按鈕
            message_text = "歡迎使用報到機器人，請選擇下面的操作按鈕："
            keyboard = {
                "inline_keyboard": [
                    [{"text": "報到 ✅", "callback_data": "checkin"}],
                    [{"text": "取消 ❌", "callback_data": "cancel"}]
                ]
            }
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": message_text,
                "reply_markup": keyboard
            })
        else:
            # 測試 webhook，可刪除
            requests.post(f"{BASE_URL}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"收到訊息：{text}"
            })

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
    return "Bot is running. Webhook test OK."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
