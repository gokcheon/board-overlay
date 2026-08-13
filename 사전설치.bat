@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [보드게임 오버레이] 사전 설치를 시작합니다...
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않아요.
    echo https://www.python.org/downloads/ 에서 Python 3.11을 설치해 주세요.
    echo 설치 화면에서 "Add python.exe to PATH" 체크 필수!
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [오류] 설치 중 문제가 발생했어요. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.
) else (
    echo.
    echo 사전 설치 완료! 이제 [서버실행.bat]을 실행하면 됩니다.
)
pause
