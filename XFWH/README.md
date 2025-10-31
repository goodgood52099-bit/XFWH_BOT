telegram_booking_bot/
│
├── app.py                 # 主程式，啟動 Flask，載入各模組
├── config.py              # 設定區 (BOT_TOKEN, ADMIN_IDS, 時區 TZ, DATA_DIR)
├── requirements.txt       # 所需套件
│
├── modules/               # 功能模組
│   ├── telegram_api.py    # send_message, answer_callback, broadcast, send_request
│   ├── pending.py         # pending 相關: set/get/clear, handle_pending_action
│   ├── shifts.py          # 時段管理: load/save shifts, generate_latest_shift_list, find_shift
│   ├── groups.py          # 群組管理: load/save/add/get_groups
│   ├── admin.py           # 管理員指令: addshift, updateshift, delete
│   ├── staff.py           # 服務員群操作: handle_staff_callback, double staff, complete
│   └── utils.py           # 工具函數: chunk_list, generate_unique_name, safe_reply, is_future_time
│
├── data/                  # JSON 檔案存放
│   ├── pending.json
│   ├── groups.json
│   └── YYYY-MM-DD.json    # 每日檔
│
└── README.md              # 專案說明
