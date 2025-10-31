from flask import Flask, request
from modules import telegram_api, pending, shifts, groups, admin, staff, utils
import os

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.json
    # 判斷訊息類型
    if "message" in update:
        telegram_api.handle_text_message(update["message"])
    elif "callback_query" in update:
        telegram_api.handle_callback_query(update["callback_query"])
    return "ok"
