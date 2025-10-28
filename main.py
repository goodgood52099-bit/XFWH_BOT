import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 1️⃣ 讀取 Bot Token
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("請在 Zeabur 的 Environment Variables 設定 TELEGRAM_BOT_TOKEN")

# 2️⃣ /start 指令，發送內聯按鈕
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("新增", callback_data="add")],
        [InlineKeyboardButton("修改", callback_data="edit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 3️⃣ 按鈕點擊回調
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # 結束 loading
    data = query.data

    if data == "add":
        await query.edit_message_text("你選擇了新增功能")
    elif data == "edit":
        # 修改按鈕為返回
        keyboard = [[InlineKeyboardButton("返回", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("你選擇了修改功能", reply_markup=reply_markup)
    elif data == "back":
        # 回到初始按鈕
        keyboard = [
            [InlineKeyboardButton("新增", callback_data="add")],
            [InlineKeyboardButton("修改", callback_data="edit")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("請選擇操作：", reply_markup=reply_markup)

# 4️⃣ 主程式
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()
