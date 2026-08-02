@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 보드게임 오버레이 - 배포판 빌드
echo.
echo  [보드게임 오버레이] 배포용 설치마법사 빌드를 시작해요.
echo  곡천 컴에서만 실행하면 되는 파일이에요 - 배포받는 사람은 필요 없음
echo.

echo  [1/4] 빌드 도구 준비...
python -m pip install --upgrade pyinstaller >nul 2>nul
python -m pip install -r requirements.txt >nul 2>nul
python -m PyInstaller --version >nul 2>nul
if errorlevel 1 goto :pierr

echo  [2/4] exe 빌드 중... 2~5분 걸려요, 창을 닫지 마세요
python -m PyInstaller BoardOverlay.spec --noconfirm
if errorlevel 1 goto :err

echo  [3/4] 리소스 복사...
xcopy /e /i /y static "dist\BoardOverlay\static" >nul
xcopy /e /i /y boards "dist\BoardOverlay\boards" >nul
copy /y 패치노트_fix22.txt "dist\BoardOverlay\" >nul 2>nul

echo  [4/4] 설치마법사 만들기...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto :haveiscc
set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto :haveiscc
echo.
echo  Inno Setup 6이 설치되어 있지 않아요. 지금 다운로드 페이지를 열게요.
echo  설치 후 이 빌드.bat을 다시 실행해 주세요. 기본 옵션으로 설치하면 돼요!
start https://jrsoftware.org/isdl.php
pause
exit /b 1

:haveiscc
rem 예전에 잘못 받아진 언어팩 정리 (404 응답이 저장된 경우)
findstr /m /c:"[Messages]" Korean.isl >nul 2>nul
if errorlevel 1 del Korean.isl >nul 2>nul
if exist Korean.isl goto :haveisl
echo  한국어 언어팩 확인 중...
curl -s -L -o Korean.isl https://raw.githubusercontent.com/jrsoftware/issrc/refs/heads/main/Files/Languages/Korean.isl
findstr /m /c:"[Messages]" Korean.isl >nul 2>nul
if errorlevel 1 del Korean.isl >nul 2>nul
if exist Korean.isl goto :haveisl
echo  내려받기는 실패했지만 Inno Setup 6.5 이상엔 한국어가 내장돼 있어서 계속 진행해요.

:haveisl
"%ISCC%" /Q 설치마법사.iss
if errorlevel 1 goto :err

echo.
echo  ════════════════════════════════════════════════
echo   완성! - 배포용\보드게임오버레이_설치.exe
echo   이 파일 하나만 다른 방송인에게 주면 돼요.
echo  ════════════════════════════════════════════════
echo.
pause
exit /b 0

:pierr
echo  [오류] PyInstaller 설치에 실패했어요. 인터넷 연결을 확인해 주세요.
pause
exit /b 1

:err
echo  [오류] 문제가 생겼어요 - 위 메시지를 복사해서 Claude에게 보여주세요.
pause
exit /b 1
