
# Telegram 多群組共用預約機器人 (JSON) — Render 部署

1. 在 Render 上建立專案並上傳檔案。
2. 在 Environment Variables 設定：BOT_TOKEN = <你的 Telegram Bot Token>
3. 部署後設置 webhook：
https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://<your-render-domain>/<YOUR_TOKEN>

指令:
- 管理員：/delbooking HH:MM [姓名] -> 刪除該時段的一個預約
- 文字預約：預約 12:00 王小明
- 查看預約：查看預約
