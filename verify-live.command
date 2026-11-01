#!/bin/zsh
cd "${0:A:h}"
python3 backend/verify_live.py
result=$?
printf '\n按 Enter 關閉視窗。'
read reply
exit $result
