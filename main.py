from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "YOUR_BOT_TOKEN"  # 替換成你的 Bot Token

# 1️⃣ /start 指令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("新增", callback_data='add')],
        [InlineKeyboardButton("修改", callback_data='edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 2️⃣ 處理按鈕點擊事件
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # 結束 loading

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

# 3️⃣ 主程式
if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.run_polling()
