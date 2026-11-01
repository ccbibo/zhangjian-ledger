#!/bin/zsh
set -e
cd "$(dirname "$0")"
echo "帳見 LEDGER｜本機財務工作台"
echo "啟動後請開啟 http://127.0.0.1:8791/"
echo "資料存於此資料夾 data/ledger.sqlite3；關閉視窗或 Ctrl+C 可停止。"
python3 backend/app.py --open
