@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 보드게임 오버레이 - 새 버전 올리기 (완전 자동 배포)
echo.
echo  [보드게임 오버레이] 새 버전을 GitHub에 올려요.
echo  올리고 나면 깃허브 서버가 알아서 빌드하고 릴리스까지 발행해요 (5~10분).
echo  곡천 컴에서 빌드할 필요 없음 - 이 창 하나면 끝!
echo.

rem ── 준비물 확인: git
git --version >nul 2>nul
if errorlevel 1 goto :nogit

rem ── 준비물 확인: python (버전·저장소 주소를 읽는 데 필요)
python -c "pass" >nul 2>nul
if errorlevel 1 goto :nopy

rem ── 저장소 주소 (업데이트주소.txt — 자동 업데이트와 같은 파일을 같은 방식으로 읽음)
set "REPO="
for /f %%a in ('python -m server.updater') do set "REPO=%%a"
if not defined REPO goto :norepo

rem ── 버전 태그 (server\version.py 단일 진실)
set "TAG="
for /f %%v in ('python -m server.version label') do set "TAG=%%v"
if not defined TAG goto :nover

echo  저장소 : https://github.com/%REPO%
echo  새 버전: %TAG%
echo.

rem ── 이 폴더를 새 스냅샷으로 올림 (버전마다 폴더가 새로 오므로 매번 새로 초기화)
if exist ".git" rmdir /s /q ".git"
git init -b main >nul 2>nul
git -C . config user.name "board-overlay" >nul
git -C . config user.email "board-overlay@local" >nul
git add -A >nul
git commit -m "%TAG%" >nul
git remote add origin "https://github.com/%REPO%.git"
git tag %TAG% >nul 2>nul

echo  GitHub에 올리는 중... (처음 한 번은 브라우저 로그인 창이 떠요 - 로그인만 해주세요)
echo.
git push --force origin main
if errorlevel 1 goto :pusherr
git push origin %TAG%
if errorlevel 1 goto :tagerr

echo.
echo  ════════════════════════════════════════════════
echo   업로드 완료! 깃허브가 지금부터 알아서 빌드해요 (5~10분)
echo   진행 보기: https://github.com/%REPO%/actions
echo   끝나면 설치판 앱에 자동으로 "새 버전" 알림이 떠요!
echo  ════════════════════════════════════════════════
echo.
pause
exit /b 0

:nogit
echo  Git이 설치되어 있지 않아요. 지금 다운로드 페이지를 열게요.
echo  설치 후(기본 옵션 그대로 다음다음) 이 파일을 다시 실행해 주세요.
start https://git-scm.com/download/win
pause
exit /b 1

:nopy
echo  파이썬이 없어요. 사전설치.bat을 먼저 실행해 주세요.
pause
exit /b 1

:norepo
echo  업데이트주소.txt에 저장소 주소가 없어요.
echo  메모장으로 열어 맨 아래 줄에 "아이디/저장소이름"을 적어 주세요. (예: gokcheon/board-overlay)
echo  처음이라면 자동업데이트_안내.txt 를 참고!
pause
exit /b 1

:nover
echo  버전을 읽지 못했어요. 이 파일이 앱 폴더(server 폴더 옆) 안에 있는지 확인해 주세요.
pause
exit /b 1

:pusherr
echo.
echo  업로드에 실패했어요. 이런 경우가 많아요:
echo   - 브라우저 로그인 창에서 취소함 → 다시 실행해서 로그인
echo   - 업데이트주소.txt의 "아이디/저장소이름" 오타 → GitHub에 만든 이름과 똑같은지 확인
echo   - 저장소를 아직 안 만듦 → github.com에서 New repository (Public!)
pause
exit /b 1

:tagerr
echo.
echo  이 버전(%TAG%)은 이미 GitHub에 올라가 있어요.
echo  같은 버전은 두 번 못 올려요 - Claude에게 다음 버전(fix 번호 +1)을 받아서 다시 실행해 주세요.
pause
exit /b 1
