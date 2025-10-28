import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 1️⃣ 讀取 Bot Token
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("請在 Zeabur 的 Environment Variables 設定 TELEGRAM_BOT_TOKEN")

# 2️⃣ /start 指令，發送按鈕
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ 新增", callback_data="add")],
        [InlineKeyboardButton("✏️ 修改", callback_data="edit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 3️⃣ 處理按鈕點擊
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # 結束 loading 動畫
    data = query.data

    if data == "add":
        await query.edit_message_text("✅ 你選擇了【新增】功能")
    elif data == "edit":
        keyboard = [[InlineKeyboardButton("🔙 返回", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📝 你選擇了【修改】功能", reply_markup=reply_markup)
    elif data == "back":
        keyboard = [
            [InlineKeyboardButton("➕ 新增", callback_data="add")],
            [InlineKeyboardButton("✏️ 修改", callback_data="edit")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("請選擇操作：", reply_markup=reply_markup)

# 4️⃣ 主程式入口
if __name__ == "__main__":
    print("🤖 Bot 正在啟動中...")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.run_polling()
