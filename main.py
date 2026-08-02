"""보드게임 오버레이 — 로컬 서버 진입점 (명세 3장 ①).

  치지직 서버 ──(후원·채팅)──▶ 본 서버(FastAPI) ──(WS 상태 push)──▶ ②/overlay ③/control

실행:  python main.py  →  http://127.0.0.1:{포트}/overlay (OBS 브라우저 소스 1920×1080)
                          http://127.0.0.1:{포트}/control (조작앱 / OBS 커스텀 독)

fix2: 치지직 공식 API 실연동 — OAuth 로그인, 후원 수신, 채팅 명령(채널 소유자만).
조작앱 버튼과 채팅 명령어는 같은 execute_command() 진입점을 공유한다 (병행 조작).
"""
import asyncio
import json
import os
import threading
import uuid
import webbrowser

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server import config as cfgmod
from server.chzzk_client import ChzzkClient, ChzzkError, parse_chat_command
from server.config import atomic_write_json, save_config
from server.donation_router import DonationRouter
from server.event_log import EventLog
from server.game_engine import CommandError, GameEngine, default_faces, sanitize_faces
from server.paths import data_dir, resolve_base_dir
from server import branding, updater
from server.collab import CollabManager, parse_redeem_text
from server.version import APP_FIX, APP_LABEL
from server.ws_hub import WsHub

BASE = resolve_base_dir()
STATIC = BASE / "static"


def lan_ip() -> str:
    """같은 공유기의 다른 PC(송출컴)가 접속할 이 컴퓨터의 주소.
    UDP 소켓을 외부로 '여는 척'만 해서 OS가 고른 출구 IP를 읽는다 (실제 전송 없음)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


# ---------------------------------------------------------------- 보드 로드
def load_board() -> list:
    """사용자 보드(%LOCALAPPDATA%) 우선, 없으면 기본 프리셋을 복사해 사용."""
    user_board = data_dir() / "boards" / "current_board.json"
    if not user_board.exists():
        default = json.loads((BASE / "boards" / "default_board.json").read_text(encoding="utf-8"))
        atomic_write_json(user_board, default)
    return json.loads(user_board.read_text(encoding="utf-8"))


cfg = cfgmod.load_config()
board = load_board()
log = EventLog(data_dir() / "logs")
engine = GameEngine(board, cfg, log)
donations = DonationRouter(engine, cfg)
hub = WsHub()

# ---------------------------------------------------------------- 치지직 연동
# 수신 스레드 → asyncio 루프로 안전하게 넘기는 다리.
MAIN_LOOP = None


def _to_loop(coro) -> None:
    if MAIN_LOOP is not None:
        asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP)
    else:
        coro.close()  # 루프 준비 전 이벤트는 버림 (기동 직후 수 초)


chzzk = ChzzkClient(
    cfg,
    on_donation=lambda p: _to_loop(handle_chzzk_donation(p)),
    on_chat=lambda p: _to_loop(handle_chzzk_chat(p)),
    on_status=lambda: _to_loop(broadcast_chzzk()),
)

# ---------------------------------------------------------------- 합방 (fix37)
def _collab_ctx(cid=None):
    """수신 순간의 (채널 태그, 방어권 자동 귀속 말 인덱스). 단일 모드는 (None, None).
    cid=None = 내(메인) 채널."""
    if cfg.get("game_mode") != "collab":
        return None, None
    if cid is None:
        my = (cfg.get("collab") or {}).get("my_piece") or "내채널"
        return my, 0
    return (collab.tag_for_channel(cid) or "합방", collab.piece_for_channel(cid))


def handle_collab_donation(payload: dict, cid: str) -> None:
    """합방 멤버 채널 후원 (수신 스레드 → 루프). 메인 채널 핸들러와 같은 검증 규칙."""
    _to_loop(_handle_donation_async(payload, cid))


collab = CollabManager(cfg, on_donation=handle_collab_donation,
                       on_status=lambda: _to_loop(broadcast_collab()))

app = FastAPI(title="보드게임 오버레이 서버")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# 업로드 스티커 (fix16) — 사용자 데이터 폴더에 저장, /userstickers 로 서빙
STICKER_DIR = data_dir() / "stickers"
STICKER_DIR.mkdir(parents=True, exist_ok=True)
STICKER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
app.mount("/userstickers", StaticFiles(directory=str(STICKER_DIR)), name="userstickers")


@app.middleware("http")
async def _no_stale_cache(request, call_next):
    """업데이트 후 옛 CSS/JS가 캐시로 남는 사고 방지 (fix5 화면 깨짐 원인 재발 차단).
    HTML의 ?v=태그와 이중 안전장치."""
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in ("/overlay", "/control"):
        response.headers["Cache-Control"] = "no-cache"
    return response


UPDATE = {"info": None}   # fix28: 시작 시 확인한 새 버전 정보 (없으면 None)


def _update_check_bg():
    """기동 3초 후 새 버전 확인 — 실패는 조용히 (인터넷 없음/주소 미설정)."""
    import time as _t
    _t.sleep(3)
    info = updater.check_update(cfg)
    if info.get("available"):
        UPDATE["info"] = info
        _to_loop(hub.broadcast({"type": "update", "update": info,
                                "app": {"fix": APP_FIX, "label": APP_LABEL,
                                        "frozen": updater.is_frozen()}}))
        _to_loop(log_event(f'🔔 새 버전 fix{info["fix"]} 발견! (지금 {APP_LABEL}) — 설정 창에서 업데이트할 수 있어요'))


@app.on_event("startup")
async def _startup():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    # 자격+토큰이 이미 있으면 서버 기동과 함께 자동 연결
    threading.Thread(target=chzzk.start, daemon=True).start()
    threading.Thread(target=_update_check_bg, daemon=True).start()   # fix28
    if cfg.get("game_mode") == "collab":                             # fix37: 재시작 시 합방 세션 복구
        collab.start_sessions()


async def push_state() -> None:
    await hub.broadcast({"type": "state", "state": engine.public_state()})


def chzzk_public() -> dict:
    port = int(cfg.get("port", 8140))
    return {
        "status": chzzk.status(),
        "channel_name": chzzk.channel_name,
        "has_credentials": chzzk.has_credentials(),
        "has_tokens": chzzk.has_tokens(),
        "redirect_uri": f"http://localhost:{port}/auth/chzzk/callback",
    }


def net_public() -> dict:
    port = int(cfg.get("port", 8140))
    ip = lan_ip()
    return {
        "lan_ip": ip,
        "port": port,
        "overlay_url": f"http://{ip}:{port}/overlay",   # 송출컴 OBS 브라우저 소스에 넣는 주소
        "control_url": f"http://{ip}:{port}/control",
    }


@app.get("/api/netinfo")
async def api_netinfo():
    return net_public()


async def broadcast_chzzk() -> None:
    await hub.broadcast({"type": "chzzk", **chzzk_public()})


async def notice(msg: str) -> None:
    """조작앱 하단 안내줄에 표시할 정보 메시지."""
    await hub.broadcast({"type": "notice", "msg": msg})


async def log_event(msg: str) -> None:
    """조작앱 REALTIME 로그 한 줄 (시각은 수신 측에서 붙임)."""
    await hub.broadcast({"type": "log", "msg": msg})


def init_payload() -> dict:
    return {
        "type": "init",
        "board": engine.board,
        "state": engine.public_state(),
        "config": public_config(),
        "chzzk": chzzk_public(),
        "net": net_public(),
        "minigame": MINIGAME["game"],
        "app": {"fix": APP_FIX, "label": APP_LABEL, "frozen": updater.is_frozen(),
                "intro_seen": bool(cfg.get("intro_notice_seen"))},   # fix38: 인트로 버전 분기
        "update": UPDATE["info"],
        "collab": collab.public(),
    }


def public_config() -> dict:
    faces, _ = sanitize_faces(cfg.get("dice_faces") or [])
    return {
        "piece_style": cfg.get("piece_style", []),
        "command_prefix": cfg.get("command_prefix", "!"),
        "statusbar_visible": cfg.get("statusbar_visible", True),
        "palette": cfg.get("palette", {}),
        "chzzk_status": chzzk.status(),
        "dice_base_amount": cfg.get("dice_base_amount"),
        "multiple_mode": cfg.get("multiple_mode", "per_multiple"),
        "guard_price": cfg.get("guard_price"),
        "port": cfg.get("port", 8140),
        "players": cfg.get("players", ["스트리머"]),
        # fix27 — 테마·폰트·주사위 설정 (표시/규칙 설정, 조작앱 설정 창에서 편집)
        "board_theme": cfg.get("board_theme", "cotton"),
        "board_font": cfg.get("board_font", "jua"),
        "board_font_stack": cfg.get("board_font_stack", ""),
        "board_font_scale": cfg.get("board_font_scale", 1.0),
        "dice_faces": faces if faces else default_faces(),
        # fix30 — 오버레이 설정 (통합 소스 파츠 표시 + 배경)
        "overlay_parts": cfg.get("overlay_parts", {}),
        "overlay_bg": bool(cfg.get("overlay_bg", False)),
    }


# ---------------------------------------------------------------- 페이지
@app.get("/")
async def index():
    return RedirectResponse("/control")


@app.get("/overlay")
async def overlay_page():
    return FileResponse(STATIC / "overlay" / "index.html")


@app.get("/control")
async def control_page():
    return FileResponse(STATIC / "control" / "index.html")


# ---------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.register(ws)
    try:
        await ws.send_text(json.dumps(init_payload(), ensure_ascii=False))
        while True:
            await ws.receive_text()  # 클라 → 서버 메시지는 현재 미사용 (keepalive 겸용)
    except WebSocketDisconnect:
        hub.unregister(ws)
    except Exception:
        hub.unregister(ws)


def _piece_prefix(d: dict) -> str:
    """2인 이상일 때 로그에 어느 말인지 표기."""
    players = engine.state.get("players", [])
    if len(players) > 1:
        return f'[{players[int(d.get("piece", 0)) % len(players)]}] '
    return ""


# ---------------------------------------------------------------- 조작 명령 (공용 진입점)
# 조작앱 버튼과 채팅 명령어가 완전히 같은 함수를 사용한다 (명세: 병행 조작).
async def execute_command(cmd: str, payload: dict) -> dict:
    anims = []
    try:
        logs = []
        if cmd == "start":
            engine.cmd_start()
            logs.append("🎲 새 회차를 시작했어요")
        elif cmd == "pause":
            engine.cmd_pause_toggle()
            logs.append("⏸ 일시정지" if engine.public_state()["paused"] else "▶ 재개")
        elif cmd == "next":
            result = engine.cmd_next()
            if result["kind"] == "roll":
                d = result["data"]
                anims.append({"kind": "roll", **d})
                who = _piece_prefix(d)
                redo = " (취소한 굴림 재현)" if d.get("redone") else ""
                if d.get("special"):
                    # ★ 커스텀 주사위 글자 항목 (fix27)
                    tail = ("이동할 칸을 골라 주세요" if d.get("mode") == "move"
                            else "연출 후 [효과 완료 ✓]를 눌러 주세요")
                    logs.append(f'{who}{d["nick"]} 굴림 ★ {d["special"]} — {tail}{redo}')
                else:
                    label = engine.board[d["final"] % len(engine.board)].get("label", "")
                    x2 = f' ×2={d["moved"]}칸' if d.get("x2_used") else ""
                    logs.append(f'{who}{d["nick"]} 굴림 {d["value"]}{x2} → {label}{redo}')
            else:
                logs.append("✓ 칸 효과 완료")
        elif cmd == "choose":
            result = engine.cmd_choose(payload.get("index", 0))
            d = result["data"]
            anims.append({"kind": "roll", "nick": d["nick"], "value": None,
                          "segments": d["segments"], "final": d["final"], "chosen": True})
            label = engine.board[d["final"] % len(engine.board)].get("label", "")
            logs.append(f'{_piece_prefix(d)}{d["nick"]} 칸 선택 → {label}')
        elif cmd == "defend":
            result = engine.cmd_defend()
            anims.append({"kind": "guard_used", "gifter": result["gifter"]})
            who = f'{result["gifter"]}님의 ' if result.get("gifter") else ""
            cut = ""
            if result.get("timer_reduced"):
                m = result["timer_reduced"]
                cut = f' · {result.get("label", "")} 타이머 -{int(m)}분'
            pname = f'『{result["piece_name"]}』 ' if result.get("piece_name") else ""
            logs.append(f"🛡 {who}방어권 사용 — {pname}벌칙 무효!{cut}")
        elif cmd == "assign_guard":
            result = engine.cmd_assign_guard(payload.get("gift_id"), payload.get("piece", 0))
            who = f'{result["nick"]}님의 ' if result.get("nick") else ""
            logs.append(f'💝 {who}방어권 → 『{result["name"]}』 소유로 지정')
        elif cmd == "undo":
            engine.cmd_undo()
            logs.append("↩ 되돌리기")
        elif cmd == "add":
            engine.cmd_add_manual()
            logs.append("⊕ 수동 굴림 1개 추가")
        elif cmd == "assign_guard":
            result = engine.cmd_assign_guard(payload.get("gift_id", ""), payload.get("piece", 0))
            logs.append(f'💝 {result["nick"]}님의 방어권 → {result["name"]}에게 전달')
        elif cmd == "timer":
            result = engine.cmd_timer_start()
            anims.append({"kind": "timer", "label": result["label"]})
            if result.get("stacked"):
                left = max(0, int(result["ends_at"] - __import__("time").time()))
                logs.append(f'⏱ 같은 벌칙 중첩! — {result["label"]} 남은 시간 {left // 60}:{left % 60:02d}')
            else:
                logs.append(f'⏱ 타이머 시작 — {result["label"]}')
        else:
            return {"ok": False, "msg": f"알 수 없는 명령: {cmd}"}
    except CommandError as e:
        return {"ok": False, "msg": str(e)}

    for anim in anims:
        await hub.broadcast({"type": "anim", "anim": anim})
    for line in logs:
        await log_event(line)
    await push_state()
    return {"ok": True}


@app.post("/api/cmd")
async def api_cmd(payload: dict):
    return await execute_command((payload or {}).get("cmd", ""), payload or {})


@app.post("/api/players")
async def api_players(payload: dict):
    """진행 인원(말 1~6개) 설정.
    - 게임 진행 중 + 기존 말 뒤에 추가만 한 경우 → 즉시 중도 참여 (fix10)
    - 그 외(감소·이름 변경·게임 전) → 저장 후 다음 회차부터 적용"""
    names = [str(x).strip()[:8] for x in (payload or {}).get("names", [])]
    names = [x for x in names if x][:6]
    if not names:
        return {"ok": False, "msg": "말 이름을 1개 이상 입력해 주세요."}
    cfg["players"] = names
    # 말 색·이모지 (fix16) — 표시 전용이라 게임 진행 중에도 즉시 적용
    raw_styles = (payload or {}).get("styles")
    if isinstance(raw_styles, list):
        styles = []
        for i, s in enumerate(raw_styles[:6]):
            s = s if isinstance(s, dict) else {}
            try:
                color = int(s.get("color", i)) % 6
            except (TypeError, ValueError):
                color = i % 6
            img = str(s.get("img", ""))[:120]
            if "/" in img or "\\" in img or img.startswith("."):
                img = ""
            styles.append({"color": color, "face": str(s.get("face", ""))[:4], "img": img})
        cfg["piece_style"] = styles
    save_config(cfg)

    st = engine.state
    joined = []
    if st["running"] and len(names) > len(st["players"]) and names[:len(st["players"])] == st["players"]:
        try:
            for nm in names[len(st["players"]):]:
                engine.cmd_join(nm)
                joined.append(nm)
        except CommandError as e:
            return {"ok": False, "msg": str(e)}
    if joined:
        for nm in joined:
            await log_event(f"🙋 {nm} 중도 참여! (출발 칸에서 합류, 차례는 맨 뒤)")
    else:
        await log_event(f"👥 진행 인원 저장: {' · '.join(names)}"
                        + (" (다음 회차부터)" if st["running"] and names != st["players"] else ""))
    await hub.broadcast({"type": "players_cfg", "players": names,
                         "styles": cfg.get("piece_style", [])})
    await push_state()
    return {"ok": True, "players": names, "joined": joined}


# ---------------------------------------------------------------- 설정 창 API (fix27)
import re as _re

_CSSVAR_KEY = _re.compile(r"^--[A-Za-z0-9-]+$")
_CSSVAR_VAL = _re.compile(r"^[#A-Za-z0-9(),.%\s'\"-]{1,80}$")
_FONT_STACK = _re.compile(r"^[A-Za-z0-9\s,'\"-]{0,200}$")


@app.post("/api/theme")
async def api_theme(payload: dict):
    """보드 테마·폰트·크기 저장 (표시 전용 — 오버레이만 바뀜, 조작앱 색은 고정).
    control.js가 테마의 CSS 변수 묶음(palette)을 함께 보내고, 오버레이는 applyPalette로 적용."""
    payload = payload or {}
    pal_in = payload.get("palette")
    pal = {}
    if isinstance(pal_in, dict):
        for k, v in list(pal_in.items())[:40]:
            if _CSSVAR_KEY.match(str(k)) and _CSSVAR_VAL.match(str(v)):
                pal[str(k)] = str(v)
    stack = str(payload.get("font_stack", ""))
    if not _FONT_STACK.match(stack):
        stack = ""
    try:
        scale = float(payload.get("font_scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.5, min(2.0, scale))
    cfg["board_theme"] = str(payload.get("theme", "cotton"))[:24]
    cfg["board_font"] = str(payload.get("font", "jua"))[:24]
    cfg["board_font_stack"] = stack
    cfg["board_font_scale"] = scale
    cfg["palette"] = pal
    save_config(cfg)
    await hub.broadcast({"type": "theme", "palette": pal,
                         "font_stack": stack, "font_scale": scale})
    name = str(payload.get("theme_name", "") or cfg["board_theme"])[:24]
    await log_event(f"🎨 보드 테마 적용 — {name}")
    return {"ok": True}


@app.post("/api/dice")
async def api_dice(payload: dict):
    """커스텀 주사위 눈금 저장. 합계 100% 검증은 서버에서도 한 번 더 (이중 안전장치)."""
    faces, why = sanitize_faces((payload or {}).get("faces"))
    if faces is None:
        return {"ok": False, "msg": why or "주사위 설정이 이상해요."}
    cfg["dice_faces"] = faces
    save_config(cfg)
    await hub.broadcast({"type": "dice_cfg", "faces": faces})
    specials = sum(1 for f in faces if not f["text"].isdigit())
    tail = f" (글자 항목 {specials}개)" if specials else ""
    await log_event(f"🎲 주사위 눈금 저장 — {len(faces)}개 항목{tail}")
    return {"ok": True, "faces": faces}


@app.post("/api/rules")
async def api_rules(payload: dict):
    """게임 규칙 저장 — 기준 금액·굴림 계산·방어권 금액·채팅 접두사·포트.
    포트만 재시작 후 적용, 나머지는 다음 후원부터 바로."""
    payload = payload or {}
    try:
        base = max(100, int(payload.get("dice_base_amount", cfg.get("dice_base_amount", 2000))))
        guard = max(100, int(payload.get("guard_price", cfg.get("guard_price", 10000))))
        port = int(payload.get("port", cfg.get("port", 8140)))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "금액·포트는 숫자로 입력해 주세요."}
    if not (1024 <= port <= 65535):
        return {"ok": False, "msg": "포트는 1024~65535 사이여야 해요."}
    mode = payload.get("multiple_mode")
    if mode not in ("per_multiple", "single"):
        mode = cfg.get("multiple_mode", "per_multiple")
    prefix = str(payload.get("command_prefix", cfg.get("command_prefix", "!"))).strip()[:3] or "!"
    port_changed = port != int(cfg.get("port", 8140))
    cfg.update({"dice_base_amount": base, "guard_price": guard, "multiple_mode": mode,
                "command_prefix": prefix, "port": port})
    save_config(cfg)
    await log_event(f"⚙️ 게임 규칙 저장 — 기준 {base:,}원 · 방어권 {guard:,}원 · 접두사 {prefix}")
    if port_changed:
        await notice("포트 변경은 프로그램을 껐다 켜야 적용돼요.")
    await hub.broadcast({"type": "rules_cfg", "config": public_config()})
    return {"ok": True, "port_changed": port_changed}


# ---------------------------------------------------------------- 오버레이 설정 API (fix30)
OVERLAY_PART_KEYS = ("board", "scoreboard", "timers", "status", "toast", "minigame")


@app.post("/api/overlay_cfg")
async def api_overlay_cfg(payload: dict):
    """통합(합본) 소스 파츠 표시 온오프 + 배경 표시. 표시 전용 — 게임 로직·이벤트 무관."""
    payload = payload or {}
    parts_in = payload.get("parts")
    parts = dict(cfg.get("overlay_parts") or {})
    if isinstance(parts_in, dict):
        for k in OVERLAY_PART_KEYS:
            if k in parts_in:
                parts[k] = bool(parts_in[k])
    for k in OVERLAY_PART_KEYS:
        parts.setdefault(k, True)
    bg = bool(payload.get("bg", cfg.get("overlay_bg", False)))
    cfg["overlay_parts"] = parts
    cfg["overlay_bg"] = bg
    save_config(cfg)
    await hub.broadcast({"type": "overlay_cfg", "parts": parts, "bg": bg})
    off = [k for k in OVERLAY_PART_KEYS if not parts[k]]
    await log_event("🖥 오버레이 설정 저장 — "
                    + (f"통합에서 숨김 {len(off)}개" if off else "모든 파츠 표시")
                    + (" · 테마 배경 켬" if bg else " · 배경 투명"))
    return {"ok": True, "parts": parts, "bg": bg}


# ---------------------------------------------------------------- 인트로 (fix38 — 1주년 크레딧)
@app.post("/api/intro_ack")
async def api_intro_ack():
    """경고 문구 버전 [확인했습니다] — 이 컴퓨터에선 다시 안 보여줌 (이후엔 채널 버튼 버전)."""
    cfg["intro_notice_seen"] = True
    save_config(cfg)
    return {"ok": True}


@app.post("/api/open_channel")
async def api_open_channel():
    """[김띵띵띵 치지직 채널 놀러가기] — 서버(게임컴) 기본 브라우저로 열기."""
    opened = webbrowser.open(branding.CHANNEL_URL)
    return {"ok": True, "opened": bool(opened), "url": branding.CHANNEL_URL}


# ---------------------------------------------------------------- 합방 API (fix37)
async def broadcast_collab() -> None:
    await hub.broadcast({"type": "collab_cfg", **collab.public()})


@app.post("/api/collab/invite")
async def api_collab_invite(payload: dict):
    """[채널 추가] — 초대(로그인) 링크 생성. 멤버가 로그인만 하면 됨."""
    piece = str((payload or {}).get("piece", "")).strip()[:8]
    if not piece:
        return {"ok": False, "msg": "말 이름을 먼저 적어 주세요."}
    if not chzzk.has_credentials():
        return {"ok": False, "msg": "먼저 치지직 연동(Client ID/Secret)을 설정해 주세요."}
    port = int(cfg.get("port", 8140))
    try:
        inv = collab.create_invite(piece, f"http://localhost:{port}/auth/chzzk/callback")
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    await broadcast_collab()
    return {"ok": True, **inv}


@app.post("/api/collab/redeem")
async def api_collab_redeem(payload: dict):
    """원격 멤버 연결 — 멤버가 로그인 후 뜬 주소(?code=&state=)를 통째로 붙여넣기."""
    code, state = parse_redeem_text(str((payload or {}).get("text", "")))
    if not code or not state:
        return {"ok": False, "msg": "주소에서 코드를 찾지 못했어요. 주소창의 주소를 통째로 복사해 주세요."}
    iid = collab.find_invite_by_state(state)
    if not iid:
        return {"ok": False, "msg": "이 주소와 맞는 초대가 없어요. [채널 추가]를 다시 해주세요."}
    try:
        member = await asyncio.to_thread(collab.redeem, iid, code, state)
    except (ValueError, ChzzkError) as e:
        await broadcast_collab()
        return {"ok": False, "msg": str(e)}
    save_config(cfg)
    await log_event(f'🎊 합방 채널 연결: {member["name"]} → 말 『{member["piece"]}』 (적용하면 새 회차부터 참여)')
    await broadcast_collab()
    return {"ok": True, "member": {"cid": member["cid"], "name": member["name"], "piece": member["piece"]}}


@app.post("/api/collab/cancel")
async def api_collab_cancel(payload: dict):
    collab.cancel_invite(str((payload or {}).get("invite_id", "")))
    await broadcast_collab()
    return {"ok": True}


@app.post("/api/collab/remove")
async def api_collab_remove(payload: dict):
    try:
        member = await asyncio.to_thread(collab.remove, str((payload or {}).get("cid", "")))
    except ValueError as e:
        return {"ok": False, "msg": str(e)}
    save_config(cfg)
    await log_event(f'🔌 합방 채널 해제: {member["name"]} (구성 변경 — [적용]하면 새 회차에 반영)')
    await broadcast_collab()
    return {"ok": True}


@app.post("/api/mode")
async def api_mode(payload: dict):
    """[적용하고 새 회차 시작] — 모드·구성 확정 + 반드시 새 회차 (곡천 확정 규칙)."""
    payload = payload or {}
    mode = payload.get("mode")
    if mode not in ("solo", "collab"):
        return {"ok": False, "msg": "모드 값이 이상해요."}
    if mode == "collab":
        c = payload.get("collab") or {}
        my_piece = str(c.get("my_piece", "")).strip()[:8] or "스트리머"
        members_in = {str(m.get("cid")): m for m in (c.get("pieces") or []) if isinstance(m, dict)}
        coll = cfg.setdefault("collab", {"my_piece": "", "members": []})
        coll["my_piece"] = my_piece
        styles = [{"color": int(c.get("my_color", 0)) % 6, "face": str(c.get("my_face", ""))[:4], "img": ""}]
        for m in coll.get("members", []):
            upd = members_in.get(m["cid"], {})
            if upd.get("piece"):
                m["piece"] = str(upd["piece"]).strip()[:8] or m["piece"]
            m["color"] = int(upd.get("color", m.get("color", 0))) % 6
            m["face"] = str(upd.get("face", m.get("face", "")))[:4]
            styles.append({"color": m["color"], "face": m["face"], "img": ""})
        players = [my_piece] + [m["piece"] for m in coll.get("members", [])]
        if len(players) < 2:
            return {"ok": False, "msg": "합방 모드는 연결된 채널이 1개 이상 필요해요. [채널 추가]부터!"}
        cfg["players"] = players
        cfg["piece_style"] = styles
        cfg["game_mode"] = "collab"
        cfg["_main_channel_id"] = chzzk.channel_id or cfg.get("_main_channel_id", "")
        save_config(cfg)
        engine.cmd_start()
        collab.start_sessions()          # 이제부터 멤버 채널 후원 수신
        await log_event(f"🎊 합방 모드 적용 — 새 회차 시작! 채널 {len(players)}개: {' · '.join(players)}")
    else:
        sol = payload.get("solo") or {}
        names = [str(x).strip()[:8] for x in (sol.get("names") or []) if str(x).strip()][:6]
        if not names:
            names = cfg.get("players") or ["스트리머"]
        cfg["players"] = names
        raw_styles = sol.get("styles")
        if isinstance(raw_styles, list):
            cfg["piece_style"] = [
                {"color": int(st.get("color", i)) % 6 if isinstance(st, dict) else i % 6,
                 "face": str(st.get("face", ""))[:4] if isinstance(st, dict) else "",
                 "img": str(st.get("img", ""))[:120] if isinstance(st, dict) else ""}
                for i, st in enumerate(raw_styles[:6])]
        cfg["game_mode"] = "solo"
        save_config(cfg)
        collab.stop_sessions()           # 합방 수신 정지 (토큰·구성은 보존)
        engine.cmd_start()
        await log_event(f"🎤 단일 방송 모드 적용 — 새 회차 시작! ({' · '.join(names)})")
    await hub.broadcast({"type": "players_cfg", "players": cfg["players"],
                         "styles": cfg.get("piece_style", [])})
    await broadcast_collab()
    await push_state()
    return {"ok": True, "mode": mode}


# ---------------------------------------------------------------- 자동 업데이트 API (fix28)
@app.post("/api/update/check")
async def api_update_check():
    """[새 버전 확인] 버튼 — 지금 바로 확인해서 결과를 돌려준다."""
    info = await asyncio.to_thread(updater.check_update, cfg)
    if info.get("available"):
        UPDATE["info"] = info
        await hub.broadcast({"type": "update", "update": info,
                             "app": {"fix": APP_FIX, "label": APP_LABEL,
                                     "frozen": updater.is_frozen()}})
    return {"ok": True, **info, "frozen": updater.is_frozen(), "label": APP_LABEL}


@app.post("/api/update/run")
async def api_update_run():
    """[지금 업데이트] — 설치판 전용: 설치 exe 내려받아 실행 후 앱 스스로 종료.
    종료 코드 42 = 런처에게 '업데이트 중이니 너도 꺼져'라는 신호 (런처.py와 세트)."""
    info = UPDATE["info"] or {}
    if not info.get("available"):
        return {"ok": False, "msg": "지금은 새 버전이 없어요."}
    if not updater.is_frozen():
        return {"ok": False, "msg": "폴더판은 새 zip을 받아 폴더만 바꿔주면 돼요 (데이터는 유지)."}
    if not info.get("url"):
        return {"ok": False, "msg": "릴리스에 설치 파일(.exe)이 없어요. 릴리스 페이지에서 직접 받아 주세요."}
    try:
        path = await asyncio.to_thread(updater.download_installer, info["url"])
    except Exception:
        return {"ok": False, "msg": "다운로드에 실패했어요. 인터넷 연결을 확인하고 다시 눌러 주세요."}
    await log_event(f'⬇️ fix{info["fix"]} 설치 파일 다운로드 완료 — 설치 창이 뜨면 진행해 주세요')
    await notice("업데이트 설치를 시작해요 — 앱이 잠시 꺼졌다가 새 버전으로 만나요!")

    def _go():
        import time as _t
        _t.sleep(1.5)                 # 응답·안내가 화면에 닿을 시간
        try:
            updater.launch_installer(path)
        finally:
            _t.sleep(0.5)
            os._exit(42)              # 런처가 이 코드를 보고 함께 종료 (런처.py)
    threading.Thread(target=_go, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- 치지직 이벤트 핸들러 (수신 스레드 → 루프)
async def _handle_donation_async(payload: dict, cid=None) -> None:
    """DONATION 이벤트 (메인 채널 cid=None / 합방 멤버 채널 cid 지정 — fix37 공용).
    공식 이벤트에는 고유 후원ID가 없어 수신 건마다 UUID를 부여한다
    (세션은 같은 이벤트를 중복 전달하지 않음 — 유실 대비는 [수동 굴림 추가])."""
    try:
        amount = int(str(payload.get("payAmount", "0")).strip() or 0)
    except (TypeError, ValueError):
        return
    if amount <= 0:
        return
    nick = (payload.get("donatorNickname") or "").strip() or "익명"
    ch, guard_piece = _collab_ctx(cid)
    result = donations.on_donation(f"chzzk-{uuid.uuid4().hex[:12]}", nick, amount,
                                   ch=ch, guard_piece=guard_piece)
    tag = f"[{ch}] " if ch else ""
    if result["kind"] == "guard":
        await hub.broadcast({"type": "anim", "anim": {"kind": "guard_gift", "nick": nick, "ch": ch}})
        if result.get("auto_piece") is not None:
            who = engine.state["players"][result["auto_piece"]] if                 result["auto_piece"] < len(engine.state["players"]) else "?"
            await log_event(f"💝 {tag}{nick}님이 방어권을 선물! ({amount:,}원) → 『{who}』 자동 귀속")
        elif len(engine.state["players"]) > 1:
            await log_event(f"💝 {tag}{nick}님이 방어권을 선물! ({amount:,}원) — 누구에게 줄지 지정해 주세요")
        else:
            await log_event(f"💝 {tag}{nick}님이 방어권을 선물! ({amount:,}원)")
    elif result["kind"] == "rolls":
        await log_event(f'💰 {tag}{nick} {amount:,}원 → {result["rolls"]}굴림 대기')
    elif result["kind"] == "ignored":
        await log_event(f"💤 {tag}{nick} {amount:,}원 — 기준 금액 미만 (기록만)")
    await push_state()


async def handle_chzzk_donation(payload: dict) -> None:
    await _handle_donation_async(payload, None)


async def handle_chzzk_chat(payload: dict) -> None:
    """CHAT 이벤트 — 채널 소유자 메시지만 명령어로 인정 (명세 7장)."""
    is_owner = (
        payload.get("userRoleCode") == "streamer"
        or (chzzk.channel_id and payload.get("senderChannelId") == chzzk.channel_id)
    )
    if not is_owner:
        return
    cmd = parse_chat_command(cfg.get("command_prefix", "!"), payload.get("content", ""))
    if not cmd:
        return
    await log_event(f'💬 채팅 명령 수신: {payload.get("content", "").strip()}')
    result = await execute_command(cmd, {})
    if not result.get("ok") and result.get("msg"):
        await notice(f"채팅 명령 무시: {result['msg']}")


# ---------------------------------------------------------------- 치지직 연동 API
@app.get("/api/chzzk/status")
async def api_chzzk_status():
    return chzzk_public()


@app.post("/api/chzzk/connect")
async def api_chzzk_connect(payload: dict):
    """자격 저장 → 토큰 있으면 바로 연결, 없으면 치지직 로그인 창 열기."""
    payload = payload or {}
    client_id = str(payload.get("client_id", "")).strip()
    client_secret = str(payload.get("client_secret", "")).strip()
    if client_id:
        cfg["chzzk_client_id"] = client_id
    if client_secret:
        cfg["chzzk_client_secret"] = client_secret
    if client_id or client_secret:
        save_config(cfg)
    if not chzzk.has_credentials():
        return {"ok": False, "msg": "Client ID와 Secret을 먼저 입력해 주세요."}

    if chzzk.has_tokens():
        threading.Thread(target=chzzk.start, daemon=True).start()
        return {"ok": True, "mode": "reconnect"}

    port = int(cfg.get("port", 8140))
    redirect_uri = f"http://localhost:{port}/auth/chzzk/callback"
    url = chzzk.auth_url(redirect_uri, chzzk.make_state())
    opened = webbrowser.open(url)
    return {"ok": True, "mode": "login", "opened": bool(opened), "url": url}


@app.get("/auth/chzzk/callback")
async def chzzk_callback(code: str = "", state: str = "", error: str = ""):
    """치지직 로그인 완료 후 돌아오는 자리. 토큰 교환은 블로킹 IO라 스레드로."""
    style = ("<style>body{font-family:sans-serif;background:#FDF8EE;color:#5E4B78;"
             "display:flex;align-items:center;justify-content:center;height:100vh;"
             "text-align:center}div{background:#fff;padding:36px 44px;border-radius:20px;"
             "border:3px solid #EBDCC9}</style>")
    if error or not code:
        return HTMLResponse(style + f"<div><h2>로그인 실패</h2><p>{error or '코드가 없어요'}"
                                    "</p><p>조작앱에서 다시 시도해 주세요.</p></div>")
    # fix37: 합방 초대의 로그인이면 (같은 자리 합방 — 곡천 컴 브라우저) 즉석에서 채널 연결
    iid = collab.find_invite_by_state(state)
    if iid:
        try:
            member = await asyncio.to_thread(collab.redeem, iid, code, state)
        except (ValueError, ChzzkError) as e:
            await broadcast_collab()
            return HTMLResponse(style + f"<div><h2>합방 채널 연결 실패</h2><p>{e}</p></div>")
        save_config(cfg)
        await log_event(f'🎊 합방 채널 연결: {member["name"]} → 말 『{member["piece"]}』')
        await broadcast_collab()
        return HTMLResponse(style + f"<div><h2>합방 채널 연결 완료! 🎊</h2><p><b>{member['name']}</b> 채널이 "
                                    f"말 『{member['piece']}』로 연결됐어요.</p>"
                                    "<p>이 창은 닫으셔도 됩니다. (곡천이 [적용]하면 새 회차부터 참여!)</p></div>")
    try:
        await asyncio.to_thread(chzzk.exchange_code, code, state)
    except ChzzkError as e:
        return HTMLResponse(style + f"<div><h2>연동 실패</h2><p>{e}</p></div>")
    threading.Thread(target=chzzk.start, daemon=True).start()
    await broadcast_chzzk()
    name = chzzk.channel_name or ""
    return HTMLResponse(style + f"<div><h2>치지직 연동 완료! 🎉</h2><p><b>{name}</b> 채널의 "
                                "후원과 채팅 명령을 받기 시작해요.</p>"
                                "<p>이 창은 닫으셔도 됩니다.</p></div>")


@app.post("/api/chzzk/logout")
async def api_chzzk_logout():
    chzzk.stop()
    await asyncio.to_thread(chzzk.revoke)
    await broadcast_chzzk()
    return {"ok": True}


# ---------------------------------------------------------------- 테스트 모드 (명세 4장: 가짜 후원 주입)
@app.post("/api/test/donation")
async def api_test_donation(payload: dict):
    nick = str((payload or {}).get("nick") or "테스트").strip() or "테스트"
    try:
        amount = int((payload or {}).get("amount", 0))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "금액이 숫자가 아니에요."}
    if amount <= 0:
        return {"ok": False, "msg": "금액을 입력해 주세요."}
    result = donations.on_donation(f"test-{uuid.uuid4().hex[:10]}", nick, amount)
    if result["kind"] == "guard":
        await hub.broadcast({"type": "anim", "anim": {"kind": "guard_gift", "nick": nick}})
        if len(engine.state["players"]) > 1:
            await log_event(f"💝 {nick}님이 방어권을 선물! (테스트) — 누구에게 줄지 지정해 주세요")
        else:
            await log_event(f"💝 {nick}님이 방어권을 선물! (테스트)")
    elif result["kind"] == "rolls":
        await log_event(f'💰 {nick} {amount:,}원 → {result["rolls"]}굴림 대기 (테스트)')
    elif result["kind"] == "ignored":
        await log_event(f"💤 {nick} {amount:,}원 — 기준 금액 미만 (테스트)")
    await push_state()
    return {"ok": True, **result}


# ---------------------------------------------------------------- 미니게임 (fix18 — 연출 전용)
# 게임 진행 기록(이벤트 로그)과 완전 분리 — 결과는 화면 연출·REALTIME 로그로만 남고,
# 실제 벌칙 적용은 지금처럼 곡천이 수동으로 진행한다.
import random as _rnd

MINIGAME = {"game": None}   # 현재 열려 있는 미니게임 상태 (없으면 None)
LADDER_ROWS = 8


def _gen_rungs(n: int) -> list:
    """사다리 가로줄 생성 — 같은 줄에 이웃한 다리가 겹치지 않게."""
    rungs = []
    for r in range(LADDER_ROWS):
        used = set()
        cols = list(range(n - 1))
        _rnd.shuffle(cols)
        placed = False
        for c in cols:
            if c in used or (c - 1) in used or (c + 1) in used:
                continue
            if _rnd.random() < 0.5:
                rungs.append([r, c])
                used.add(c)
                placed = True
        if not placed and cols:            # 다리가 하나도 없는 줄 방지 (심심한 사다리 예방)
            c = _rnd.choice(cols)
            rungs.append([r, c])
    return rungs


def _ladder_trace(n: int, rungs: list, start: int) -> int:
    rset = {(r, c) for r, c in rungs}
    col = start
    for r in range(LADDER_ROWS):
        if (r, col) in rset:
            col += 1
        elif (r, col - 1) in rset:
            col -= 1
    return col


async def _mg_broadcast():
    await hub.broadcast({"type": "minigame", "game": MINIGAME["game"]})


@app.post("/api/minigame")
async def api_minigame(payload: dict):
    payload = payload or {}
    action = str(payload.get("action", ""))
    g = MINIGAME["game"]

    if action == "open":
        pend = engine.state.get("pending") or {}
        kind = pend.get("minigame")
        if not kind:
            return {"ok": False, "msg": "미니게임 칸에 도착했을 때만 열 수 있어요."}
        if kind == "shuffle":
            MINIGAME["game"] = {"kind": "shuffle", "stage": "ready"}
        else:
            saved = cfg.get("minigame_ladder", {}) or {}
            MINIGAME["game"] = {
                "kind": "ladder", "stage": "input",
                "count": int(saved.get("count", 3)),
                "results": list(saved.get("results", [])),   # 직전 입력 재사용 (곡천 요청)
            }
        await _mg_broadcast()
        return {"ok": True}

    if action == "close":
        MINIGAME["game"] = None
        await _mg_broadcast()
        return {"ok": True}

    if action == "ladder_setup":
        try:
            count = max(2, min(5, int(payload.get("count", 3))))
        except (TypeError, ValueError):
            count = 3
        results = [str(x).strip()[:10] or f"결과{i + 1}"
                   for i, x in enumerate((payload.get("results") or [])[:count])]
        while len(results) < count:
            results.append("통과")
        cfg["minigame_ladder"] = {"count": count, "results": results}   # 다음에 그대로 재사용
        save_config(cfg)
        MINIGAME["game"] = {"kind": "ladder", "stage": "setup", "count": count,
                            "results": results, "rungs": None, "pick": None, "result": None}
        await _mg_broadcast()
        return {"ok": True}

    if action == "ladder_pick":
        if not g or g.get("kind") != "ladder" or g.get("stage") != "setup":
            return {"ok": False, "msg": "먼저 [사다리 준비]를 눌러 주세요."}
        try:
            pick = int(payload.get("pick", 0))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "번호가 이상해요."}
        if not (0 <= pick < g["count"]):
            return {"ok": False, "msg": "없는 번호예요."}
        g["pick"] = pick
        g["rungs"] = _gen_rungs(g["count"])   # ★ 번호를 고른 뒤에야 사다리가 랜덤 공개 (곡천 요청)
        g["stage"] = "picked"
        await _mg_broadcast()
        return {"ok": True}

    if action == "ladder_run":
        if not g or g.get("kind") != "ladder" or g.get("stage") != "picked":
            return {"ok": False, "msg": "번호를 먼저 골라 주세요."}
        g["result"] = _ladder_trace(g["count"], g["rungs"], g["pick"])
        g["stage"] = "done"
        await _mg_broadcast()
        await log_event(f'🪜 사다리 결과: {g["pick"] + 1}번 → {g["results"][g["result"]]}')
        return {"ok": True}

    if action == "shuffle_start":
        if not g or g.get("kind") != "shuffle":
            return {"ok": False, "msg": "야바위가 열려 있지 않아요."}
        ball = _rnd.randint(0, 2)
        swaps = []
        for _ in range(_rnd.randint(6, 8)):
            a = _rnd.randint(0, 2)
            b = _rnd.choice([x for x in range(3) if x != a])
            swaps.append([a, b])
        MINIGAME["game"] = {"kind": "shuffle", "stage": "pick",
                            "ball_start": ball, "swaps": swaps, "pick": None}
        await _mg_broadcast()
        return {"ok": True}

    if action == "shuffle_pick":
        if not g or g.get("kind") != "shuffle" or g.get("stage") != "pick":
            return {"ok": False, "msg": "아직 고를 차례가 아니에요."}
        try:
            pick = int(payload.get("pick", 0)) % 3
        except (TypeError, ValueError):
            return {"ok": False, "msg": "컵 번호가 이상해요."}
        pos = g["ball_start"]
        for a, b in g["swaps"]:              # 섞기를 따라간 공의 최종 위치
            if pos == a:
                pos = b
            elif pos == b:
                pos = a
        g["pick"] = pick
        g["ball"] = pos
        g["win"] = (pick == pos)
        g["stage"] = "done"
        await _mg_broadcast()
        POS = ["왼쪽", "가운데", "오른쪽"]
        await log_event(f'🥤 야바위: {POS[pick]} 컵 → {"공 찾음! 🎉" if g["win"] else "꽝!"} (공은 {POS[pos]})')
        return {"ok": True}

    return {"ok": False, "msg": f"알 수 없는 동작: {action}"}


# ---------------------------------------------------------------- 업로드 스티커 API (fix16)
def _safe_sticker_name(name: str) -> str:
    """업로드 파일명 정리: 확장자 검증 + 위험 문자 제거 + 중복 시 -2, -3…."""
    import re
    from pathlib import Path as _P
    stem = re.sub(r"[^\w가-힣ㄱ-ㅎㅏ-ㅣ\- ]", "", _P(name).stem).strip()[:40] or "sticker"
    ext = _P(name).suffix.lower()
    if ext not in STICKER_EXTS:
        ext = ".png"
    candidate, k = f"{stem}{ext}", 2
    while (STICKER_DIR / candidate).exists():
        candidate = f"{stem}-{k}{ext}"
        k += 1
    return candidate


def _sticker_usage() -> dict:
    """이미지 파일명 → 사용처 라벨 (fix27 이미지 관리 탭 — 사용 중 삭제 잠금 표시용).
    삭제 차단 규칙 자체는 /api/stickers/delete 의 기존 검사가 계속 담당한다."""
    used = {}
    for i, t in enumerate(engine.board):
        for s in (t.get("stickers") or []):
            if s.get("img") and s["img"] not in used:
                used[s["img"]] = f"{i}번 칸"
    for st in cfg.get("piece_style", []):
        if isinstance(st, dict) and st.get("img") and st["img"] not in used:
            used[st["img"]] = "말 그림"
    return used


@app.get("/api/stickers")
async def api_stickers_list():
    files = [p for p in STICKER_DIR.iterdir()
             if p.is_file() and p.suffix.lower() in STICKER_EXTS]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # 최근 올린 것부터
    return {"files": [p.name for p in files], "usage": _sticker_usage()}


@app.post("/api/stickers/upload")
async def api_stickers_upload(payload: dict):
    """데이터URL(base64)로 업로드 — 추가 패키지 없이 처리 (2MB 제한)."""
    import base64
    payload = payload or {}
    data = str(payload.get("data", ""))
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data or "", validate=True)
    except Exception:
        return {"ok": False, "msg": "그림 파일을 읽지 못했어요. 다시 시도해 주세요."}
    if not raw:
        return {"ok": False, "msg": "빈 파일이에요."}
    if len(raw) > 2 * 1024 * 1024:
        return {"ok": False, "msg": "그림이 2MB를 넘어요. 조금 줄여서 올려 주세요."}
    fname = _safe_sticker_name(str(payload.get("name", "sticker.png")))
    (STICKER_DIR / fname).write_bytes(raw)
    await log_event(f"🖼 스티커 업로드: {fname}")
    return {"ok": True, "file": fname}


@app.post("/api/stickers/delete")
async def api_stickers_delete(payload: dict):
    fname = str((payload or {}).get("file", ""))
    if not fname or "/" in fname or "\\" in fname or fname.startswith("."):
        return {"ok": False, "msg": "잘못된 파일 이름이에요."}
    target = STICKER_DIR / fname
    if not target.is_file():
        return {"ok": False, "msg": "이미 없는 그림이에요."}
    used = [i for i, t in enumerate(engine.board)
            for s in (t.get("stickers") or []) if s.get("img") == fname]
    if used:
        tiles = ", ".join(f"{i}번" for i in used[:5])
        return {"ok": False, "msg": f"{tiles} 칸에 붙어 있어요. 먼저 떼고 삭제해 주세요."}
    pieces_using = [st for st in cfg.get("piece_style", [])
                    if isinstance(st, dict) and st.get("img") == fname]
    if pieces_using:
        return {"ok": False, "msg": "말 그림으로 쓰고 있어요. 설정에서 먼저 바꾸고 삭제해 주세요."}
    target.unlink()
    await log_event(f"🗑 스티커 삭제: {fname}")
    return {"ok": True}


# ---------------------------------------------------------------- 상태·보드 API
@app.get("/api/state")
async def api_state():
    return {"board": engine.board, "state": engine.public_state(), "config": public_config()}


@app.get("/api/board")
async def api_board_get():
    return {"board": engine.board, "locked": engine.board_locked()}


@app.post("/api/board")
async def api_board_set(payload: dict):
    """보드 프리셋 저장 (편집기는 2단계 — API만 선반영). 진행 중 잠금 적용."""
    if engine.board_locked():
        return {"ok": False, "msg": "게임 진행 중에는 보드를 수정할 수 없어요."}
    tiles = (payload or {}).get("board")
    if not isinstance(tiles, list) or len(tiles) != len(engine.board):
        return {"ok": False, "msg": f"보드는 {len(engine.board)}칸 배열이어야 해요."}
    engine.board[:] = tiles
    atomic_write_json(data_dir() / "boards" / "current_board.json", tiles)
    await hub.broadcast({"type": "board", "board": engine.board})
    await log_event("✏️ 보드 수정 저장")
    await push_state()
    return {"ok": True}


if __name__ == "__main__":
    port = int(cfg.get("port", 8140))
    ip = lan_ip()
    print("=" * 56)
    print(f"  이 컴퓨터(게임컴)  조작앱   http://127.0.0.1:{port}/control")
    print(f"  송출컴 OBS 소스   오버레이  http://{ip}:{port}/overlay")
    print("  * 처음 실행 시 윈도우 방화벽 창이 뜨면 [액세스 허용]을 눌러 주세요.")
    print("  * 원컴 방송이면 OBS에도 위 주소 아무거나 넣으면 됩니다.")
    print("=" * 56)
    # 0.0.0.0 = 같은 공유기 내 다른 PC(송출컴 OBS)의 접속 허용 — 투컴 환경 (fix3)
    uvicorn.run(app, host="0.0.0.0", port=port)
