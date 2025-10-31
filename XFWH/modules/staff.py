from . import telegram_api, pending, shifts, utils

# 服務員流程處理
def handle_staff_flow(user_id, chat_id, data, callback_id):
    # 根據 data 前綴處理不同功能
    if data.startswith("staff_up|"):
        # 更新班表
        telegram_api.answer_callback(callback_id, "班表已更新")
    elif data.startswith("input_client|"):
        # 記錄客戶資料
        telegram_api.answer_callback(callback_id, "客戶資料已記錄")
    elif data.startswith("not_consumed|"):
        # 標記未消費
        telegram_api.answer_callback(callback_id, "已標記未消費")
    elif data.startswith("double|"):
        # 雙人服務邏輯
        telegram_api.answer_callback(callback_id, "雙人服務已登記")
    elif data.startswith("complete|"):
        # 標記完成
        telegram_api.answer_callback(callback_id, "已完成")
    elif data.startswith("fix|"):
        # 修正資料
        telegram_api.answer_callback(callback_id, "資料已修正")
    else:
        telegram_api.answer_callback(callback_id, "未知操作")
