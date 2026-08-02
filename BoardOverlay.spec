# -*- mode: python ; coding: utf-8 -*-
# 보드게임 오버레이 — PyInstaller 빌드 명세 (fix20)
# 빌드.bat이 실행한다. 결과: dist/BoardOverlay/ (BoardOverlay.exe + _internal)
# static/·boards/ 폴더는 exe 옆에 있어야 하며 빌드.bat이 복사한다 (resolve_base_dir 참고).
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

hidden = collect_submodules("uvicorn") + [
    "websockets",
    "websockets.legacy",
    "engineio.async_drivers.threading",
]
datas = []
binaries = []

# 조작앱 창(pywebview) — 윈도우에서만 필요, 없어도 빌드는 계속 (브라우저 폴백 있음)
try:
    d, b, h = collect_all("webview")
    datas += d
    binaries += b
    hidden += h
    # pywebview의 윈도우 백엔드 (WebView2/pythonnet)
    for extra in ("clr", "clr_loader", "pythonnet"):
        try:
            __import__(extra)
            hidden.append(extra)
        except ImportError:
            pass
except Exception:
    pass

a = Analysis(
    ["런처.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BoardOverlay",
    debug=False,
    console=True,                    # 콘솔 런처 (곡천 확정: ③ 콘솔창 단장)
    icon="dice.ico" if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="BoardOverlay",
)
