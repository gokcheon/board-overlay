# -*- coding: utf-8 -*-
"""보드게임 오버레이 — 콘솔 런처 (fix19)

시작하기.bat이 이 파일을 실행한다. 서버(main.py)를 하위 프로세스로 켜고,
컬러 콘솔 화면에서 상태·접속 주소·최근 로그를 보여주며 숫자 키로 조작한다.
투컴 기준: 이 런처는 송출컴에서 켜 두고, 조작앱은 다른 컴/폰 주소로 접속.
"""
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)          # PyInstaller exe로 실행 중인가 (fix20)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
os.chdir(ROOT)

try:                                              # fix28: 버전은 server/version.py 한 곳
    from server.version import APP_LABEL as VERSION
except Exception:
    VERSION = "fix28"
DEMO = "--demo" in sys.argv          # (개발용) 가짜 서버로 화면만 확인
UPDATE_EXIT = 42                     # 서버가 이 코드로 끝나면 = 자동 업데이트 설치 중 → 런처도 종료

# ── ANSI 색 (곡천: 눈 아프지 않게 — 어두운 배경에 은은한 색만)
def _enable_vt():
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)   # VT 처리 켜기
            k.SetConsoleTitleW("🎲 보드게임 오버레이 런처")
        except Exception:
            pass

PINK = "\033[38;5;218m"   # 연분홍
LAV  = "\033[38;5;183m"   # 연보라
GRN  = "\033[38;5;114m"   # 연초록
YEL  = "\033[38;5;222m"   # 연노랑
RED  = "\033[38;5;210m"   # 연빨강
DIM  = "\033[38;5;245m"   # 회색
RST  = "\033[0m"
BOLD = "\033[1m"
LINE = DIM + "─" * 58 + RST

# ── 주소
try:
    from server.config import load_config
    _cfg = load_config()
    PORT = int(_cfg.get("port", 8140))
except Exception:
    PORT = 8140

# ── 배포 exe 모드 (fix20): BoardOverlay.exe --server / --controlapp
if "--server" in sys.argv:
    import uvicorn
    import main as _main                     # FastAPI 앱 로드 (main.py의 배너는 __main__ 전용이라 안 뜸)
    uvicorn.run(_main.app, host="0.0.0.0", port=PORT)
    sys.exit(0)

if "--controlapp" in sys.argv:
    try:
        import webview
        webview.create_window("보드게임 오버레이 조작앱", f"http://127.0.0.1:{PORT}/control",
                              width=1620, height=980, min_size=(1200, 760))
        webview.start()
    except Exception:
        import webbrowser                     # 웹뷰가 안 되면 브라우저로라도
        webbrowser.open(f"http://127.0.0.1:{PORT}/control")
    sys.exit(0)


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"

# ── 서버 하위 프로세스
_SKIP = re.compile(
    r'HTTP/1\.1|connection (open|closed)|WebSocket /ws|Waiting for application'
    r'|^=+$|조작앱   http|오버레이  http|방화벽|원컴 방송'    # main.py 시작 배너 — 런처가 이미 보여줌
    r'|deprecat|on_event|fastapi\.tiangolo|Read more about|warnings\.warn'  # 라이브러리 경고 소음
)
DIRTY = threading.Event()

DEMO_CODE = (
    "import time,sys\n"
    "print('INFO:     Uvicorn running on http://0.0.0.0:8140'); sys.stdout.flush()\n"
    "for i in range(999): time.sleep(3); print(f'데모 로그 {i}'); sys.stdout.flush()\n"
)

def port_in_use() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.6):
            return True
    except OSError:
        return False


def looks_like_our_server() -> bool:
    """포트를 쓰는 게 (예전에 켜 둔) 우리 서버인지 확인."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/overlay", timeout=2) as r:
            return "보드게임" in r.read(6000).decode("utf-8", "replace")
    except Exception:
        return False


def kill_port_owner() -> bool:
    """포트 8140을 잡고 있는 프로세스를 정리 (윈도우)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
        pids = {ln.split()[-1] for ln in out.splitlines()
                if f":{PORT}" in ln and "LISTENING" in ln.upper()}
        ok = False
        for pid in pids - {"0", str(os.getpid())}:
            r = subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            ok = ok or r.returncode == 0
        return ok
    except Exception:
        return False


class Server:
    def __init__(self):
        self.proc = None
        self.logs = deque(maxlen=8)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running():
            return
        stamp = lambda m: (self.logs.append(time.strftime("%H:%M:%S ") + m), DIRTY.set())
        if not DEMO and port_in_use():
            if looks_like_our_server():
                stamp("예전 서버 창이 아직 켜져 있네요 — 정리하고 새로 켤게요.")
                kill_port_owner()
                for _ in range(20):                     # 최대 4초 대기
                    time.sleep(0.2)
                    if not port_in_use():
                        break
            if port_in_use():
                stamp(f"포트 {PORT}을 다른 프로그램이 쓰고 있어요. 그 프로그램을 끄고 [1]을 눌러 주세요.")
                return
        if DEMO:
            cmd = [sys.executable, "-X", "utf8", "-c", DEMO_CODE]
        elif FROZEN:
            cmd = [sys.executable, "--server"]            # 같은 exe를 서버 역할로
        else:
            cmd = [sys.executable, "-X", "utf8", "main.py"]
        env = dict(os.environ, PYTHONWARNINGS="ignore")   # 라이브러리 경고 소음 차단
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace")
        except Exception as e:
            self.logs.append(time.strftime("%H:%M:%S ") + f"서버 실행 실패: {e}")
            DIRTY.set()
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        proc = self.proc
        for line in proc.stdout:
            line = line.strip()
            if not line or _SKIP.search(line):
                continue
            if "Uvicorn running" in line:
                line = "서버 시작 완료! OBS·조작앱에서 접속하면 돼요."
            elif "10048" in line or "attempting to bind" in line:
                line = f"포트 {PORT}이 이미 사용 중 — [1]을 눌러 다시 켜면 정리 후 시작해요."
            elif "Application startup complete" in line or line.startswith("INFO:"):
                continue
            self.logs.append(time.strftime("%H:%M:%S ") + line[:96])
            DIRTY.set()
        if proc is self.proc:                       # 내가 끈 게 아니라 스스로 죽었을 때도 표시
            if proc.poll() == UPDATE_EXIT:          # fix28: 자동 업데이트 — 설치 창에 자리를 내주고 런처도 종료
                sys.stdout.write(RST + "\n  🔄 업데이트 설치가 시작됐어요! 설치가 끝나면 새 버전으로 다시 켜 주세요.\n")
                sys.stdout.flush()
                time.sleep(2.5)
                os._exit(0)
            self.logs.append(time.strftime("%H:%M:%S ") + "서버가 종료됐어요.")
            DIRTY.set()

    def stop(self):
        if not self.running():
            return
        proc, self.proc = self.proc, None           # _pump 종료 메시지 중복 방지
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

# ── 키 입력 (창을 다시 그릴 수 있게 타임아웃 대기)
def get_key(timeout=0.5):
    if os.name == "nt":
        import msvcrt
        t0 = time.time()
        while time.time() - t0 < timeout:
            if msvcrt.kbhit():
                return msvcrt.getwch()
            time.sleep(0.04)
        return None
    import select                                    # (개발용 리눅스 폴백)
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if r:
        line = sys.stdin.readline()
        if line == "":
            return "0"                               # EOF → 종료
        return (line.strip() or " ")[0]
    return None

# ── 클립보드 (윈도우 clip)
def copy(text: str) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["clip"], input=text, text=True, check=True)
            return True
        for tool in (["xclip", "-selection", "clipboard"], ["pbcopy"]):
            try:
                subprocess.run(tool, input=text, text=True, check=True)
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    except Exception:
        pass
    return False

# ── 패키지 점검
_REQUIRED = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
             ("websockets", "websockets"), ("pywebview", "webview")]

def missing_packages():
    if FROZEN:                                # exe 배포판은 전부 내장 — 점검 불필요
        return []
    import importlib.util
    return [name for name, mod in _REQUIRED if importlib.util.find_spec(mod) is None]

def install_packages():
    print(RST + "\n패키지를 설치할게요. 잠시만요…\n")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    input(GRN + "\n설치 끝! Enter를 누르면 런처로 돌아가요." + RST if r.returncode == 0
          else RED + "\n설치 중 문제가 있었어요. 인터넷 연결을 확인해 주세요. Enter로 돌아가기." + RST)

# ── 화면
def draw(sv: Server, note: str):
    ip = lan_ip()
    on = sv.running()
    o = []
    o.append("")
    o.append(f"  {PINK}{BOLD}🎲 보드게임 오버레이{RST}  {DIM}{VERSION} · 송출컴에서 이 창만 켜 두면 돼요{RST}")
    o.append("  " + LINE)
    if on:
        o.append(f"   {GRN}●{RST} {GRN}서버 켜짐{RST} {DIM}— 포트 {PORT}{RST}")
    else:
        o.append(f"   {RED}●{RST} {RED}서버 꺼짐{RST} {DIM}— [1]을 누르면 켜져요{RST}")
    o.append("")
    o.append(f"   {LAV}조작앱{RST} {DIM}(곡천 컴·폰){RST}   http://{ip}:{PORT}/control")
    o.append(f"   {LAV}오버레이{RST} {DIM}(OBS 소스){RST}  http://{ip}:{PORT}/overlay")
    o.append(f"   {DIM}이 컴에서 열 땐 {ip} 대신 127.0.0.1 도 돼요{RST}")
    o.append("  " + LINE)
    o.append(f"   {YEL}[1]{RST} 서버 {'끄기' if on else '켜기'}   "
             f"{YEL}[2]{RST} 조작앱 주소 복사   {YEL}[3]{RST} 오버레이 주소 복사")
    o.append(f"   {YEL}[4]{RST} 이 컴에서 조작앱 열기   {YEL}[5]{RST} 패키지 점검   {YEL}[0]{RST} 종료")
    o.append("  " + LINE)
    for ln in list(sv.logs)[-6:]:
        o.append(f"   {DIM}{ln}{RST}")
    if not sv.logs:
        o.append(f"   {DIM}(로그가 여기 표시돼요){RST}")
    if note:
        o.append("")
        o.append(f"   {GRN}{note}{RST}")
    o.append("")
    sys.stdout.write("\033[H\033[2J" + "\n".join(o) + "\n")
    sys.stdout.flush()

# ── 메인
def main():
    _enable_vt()
    sv = Server()
    note = ""

    miss = missing_packages()
    if miss and not DEMO:
        print(f"{YEL}처음 실행이네요! 필요한 패키지가 없어요: {', '.join(miss)}{RST}")
        ans = input("지금 설치할까요? (Enter=예 / n=아니오) ").strip().lower()
        if ans != "n":
            install_packages()
        if missing_packages() and not DEMO:
            print(f"{RED}패키지가 아직 없어서 서버를 켤 수 없어요. 사전설치.bat을 먼저 실행해 주세요.{RST}")
            input("Enter를 누르면 닫혀요.")
            return

    sv.start()                                        # 런처 켜면 서버도 바로 시작
    draw(sv, note)
    while True:
        key = get_key(0.5)
        if DIRTY.is_set():
            DIRTY.clear()
            draw(sv, note)
        if key is None:
            continue
        note = ""
        ip = lan_ip()
        if key == "1":
            if sv.running():
                sv.stop()
                note = "서버를 껐어요."
            else:
                sv.start()
                note = "서버를 켜는 중…"
        elif key == "2":
            url = f"http://{ip}:{PORT}/control"
            note = f"복사했어요 → {url}" if copy(url) else f"복사 실패 — 직접 입력: {url}"
        elif key == "3":
            url = f"http://{ip}:{PORT}/overlay"
            note = f"복사했어요 → {url}" if copy(url) else f"복사 실패 — 직접 입력: {url}"
        elif key == "4":
            try:
                subprocess.Popen(
                    [sys.executable, "--controlapp"] if FROZEN
                    else [sys.executable, "control_app.py"], cwd=str(ROOT))
                note = "조작앱 창을 여는 중… (서버가 켜져 있어야 해요)"
            except Exception:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{PORT}/control")
                note = "브라우저로 조작앱을 열었어요."
        elif key == "5":
            miss = missing_packages()
            if miss:
                install_packages()
            else:
                note = "패키지 모두 정상! 설치할 게 없어요."
        elif key == "0":
            sv.stop()
            sys.stdout.write(RST + "\n또 만나요! 서버를 끄고 닫을게요.\n")
            return
        draw(sv, note)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write(RST + "\n종료!\n")
