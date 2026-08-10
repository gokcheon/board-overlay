@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [보드게임 오버레이] 조작앱을 엽니다... (송출컴에서 시작하기.bat이 먼저 켜져 있어야 해요)
python control_app.py
pause
