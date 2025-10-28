from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

TOKEN = "YOUR_BOT_TOKEN"  # 替換成你的 Bot Token

# 1️⃣ /start 指令，發送帶按鈕的訊息
def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("新增", callback_data='add')],
        [InlineKeyboardButton("修改", callback_data='edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("請選擇操作：", reply_markup=reply_markup)

# 2️⃣ 處理按鈕點擊事件
def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()  # 結束 loading

    data = query.data

    if data == "add":
        # 點擊新增，訊息更新文字
        query.edit_message_text(text="你選擇了新增功能")
    elif data == "edit":
        # 點擊修改，訊息更新文字，並修改按鈕
        keyboard = [
            [InlineKeyboardButton("返回", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text="你選擇了修改功能", reply_markup=reply_markup)
    elif data == "back":
        # 返回初始按鈕
        keyboard = [
            [InlineKeyboardButton("新增", callback_data='add')],
            [InlineKeyboardButton("修改", callback_data='edit')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text="請選擇操作：", reply_markup=reply_markup)

# 3️⃣ 主程式
def main():
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CallbackQueryHandler(button_callback))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
