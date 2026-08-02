; 보드게임 오버레이 — 설치마법사 (Inno Setup 6) — fix20
; 빌드.bat이 컴파일한다. 결과: 배포용\보드게임오버레이_설치.exe
; 하는 일: 프로그램 설치 + 바탕화면 바로가기 + 방화벽 자동 허용 (곡천 확정)

#define MyAppName "보드게임 오버레이"
; 버전은 빌드.bat이 server\version.py에서 읽어 /DAppVer= 로 넣어준다 (fix28 — 직접 수정 금지)
#ifndef AppVer
  #define AppVer "1.0"
#endif
#define MyAppVersion AppVer
#define MyAppExeName "BoardOverlay.exe"

[Setup]
AppId={{7C1B8E52-9A44-4C1D-BB6B-B0A4D6E0FB20}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=곡천
DefaultDirName={autopf}\BoardOverlay
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=배포용
OutputBaseFilename=보드게임오버레이_설치
SetupIconFile=dice.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
; 자동 업데이트(fix28): 실행 중인 앱을 자동으로 닫고 덮어쓰기 — 재설치·삭제 불필요
CloseApplications=force
RestartApplications=no

[Languages]
#if FileExists("Korean.isl")
Name: "korean"; MessagesFile: "Korean.isl"
#else
; Inno Setup 6.5부터 한국어가 공식 내장 (Languages\Korean.isl)
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 {#MyAppName} 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "dist\BoardOverlay\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "서버 런처 (송출컴에서 실행)"
Name: "{group}\{#MyAppName} 조작앱 (원컴용)"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--controlapp"; Comment: "런처가 켜져 있을 때 조작 창을 엽니다"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 방화벽 자동 허용 — 투컴(다른 PC의 OBS·조작앱) 접속 시 팝업이 안 뜨게 미리 등록
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""{#MyAppName}"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes"; Flags: runhidden; StatusMsg: "방화벽 허용 규칙 등록 중..."
Filename: "{app}\{#MyAppExeName}"; Description: "설치를 마치고 바로 실행하기"; Flags: postinstall nowait skipifsilent unchecked

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""{#MyAppName}"""; Flags: runhidden; RunOnceId: "DelFirewallRule"

[UninstallDelete]
; 사용자 데이터(%LOCALAPPDATA%\board_overlay — 보드·설정·스티커)는 남긴다. 지우고 싶으면 직접 삭제.
Type: filesandordirs; Name: "{app}"
