import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 1️⃣ 取得 Token
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    # 如果沒有環境變數，提示手動輸入（方便本地測試）
    TOKEN = input("請輸入你的 Telegram Bot Token: ").strip()

if not TOKEN:
    raise ValueError("Telegram Bot Token 尚未設定！程式終止。")

# 2️⃣ /start 指令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("新增", callback_data='add')],
        [InlineKeyboardButton("修改", callback_data='edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 3️⃣ 處理按鈕事件
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "add":
        await query.edit_message_text(text="你選擇了新增功能")
    elif data == "edit":
        keyboard = [[InlineKeyboardButton("返回", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="你選擇了修改功能", reply_markup=reply_markup)
    elif data == "back":
        keyboard = [
            [InlineKeyboardButton("新增", callback_data='add')],
            [InlineKeyboardButton("修改", callback_data='edit')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="請選擇操作：", reply_markup=reply_markup)

# 4️⃣ 主程式
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.run_polling()
