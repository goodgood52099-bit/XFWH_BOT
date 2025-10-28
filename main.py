import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 讀取 Token
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    TOKEN = input("請輸入你的 Telegram Bot Token: ").strip()
if not TOKEN:
    raise ValueError("Telegram Bot Token 尚未設定！程式終止。")

# /start 指令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("新增", callback_data='add')],
        [InlineKeyboardButton("修改", callback_data='edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 按鈕事件處理
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

# 主程式
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()
