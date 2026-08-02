"""치지직 공식 오픈 API 실연동 클라이언트 (명세 4장).

전체 흐름:
  1) 스트리머가 조작앱에 Client ID/Secret 입력 → [저장하고 연결]
  2) 서버가 치지직 로그인 창을 브라우저로 염 (account-interlock)
  3) 로그인 완료 → http://localhost:{port}/auth/chzzk/callback 으로 code 수신
  4) code → Access Token(1일)/Refresh Token(30일, 일회용) 교환·저장
  5) 세션 URL 발급 (GET /open/v1/sessions/auth) → Socket.IO 연결
  6) SYSTEM connected 메시지의 sessionKey로 채팅+후원 이벤트 구독
  7) 끊기면 지수 백오프로 새 세션 URL 발급 → 재연결 → 재구독

프로토콜 참고:
  - 치지직 세션 서버는 Socket.IO 2.x(Engine.IO v3)만 허용 (공식 문서 명시:
    socket.io-client 2.0.3까지). 최신 python-socketio(5.x)는 프로토콜이 달라
    연결 자체가 거부되므로, 여기서는 websocket-client 위에 EIO v3 프레이밍을
    직접 구현했다 (웹소켓 전송만 사용, 프레임 규칙이 단순해서 가능).
  - EIO v3 텍스트 프레임: "0{json}"=open, "1"=close, "2"=ping(클라→서버),
    "3"=pong, "4..."=message. 메시지 안 Socket.IO 프레임: "0"=connect,
    "2[이벤트명, 데이터]"=event → 합쳐서 "40", "42[...]" 형태로 온다.
  - 이벤트 데이터는 JSON '문자열'로 한 번 감싸져 올 수 있어 이중 파싱 처리.
  - 발급된 세션 URL은 1회용에 가깝고(첫 연결 or 만료 시 무효화) 약 30초~1분
    내 연결하지 않으면 만료 → 재연결 때마다 새로 발급해야 한다.

주의:
  - 공식 API는 텍스트·영상 후원만 수신 가능 (미션 후원 수신 불가 — 명세 확정).
  - 이벤트 유실 복구 수단이 없으므로 조작앱 [수동 굴림 추가]가 최종 안전망.
  - Refresh Token은 '일회용': 갱신 성공 시 새 토큰 쌍을 즉시 원자적으로 저장.
"""
import json
import secrets
import threading
import time
import urllib.parse

from .config import atomic_write_json
from .paths import data_dir

OPENAPI_BASE = "https://openapi.chzzk.naver.com"
INTERLOCK_URL = "https://chzzk.naver.com/account-interlock"

# ---------------------------------------------------------------- 채팅 명령어
# 채팅 명령어 → 조작 명령 매핑 (명세 7장). 접두사는 설정값.
CHAT_COMMANDS = {
    "다음": "next",
    "방어": "defend",
    "취소": "undo",
    "추가": "add",
    "타이머": "timer",
}


def parse_chat_command(prefix: str, message: str):
    """채팅에서 명령어 추출. 해당 없으면 None.

    호출측(main.py)이 '채널 소유자 메시지만 인정'을 보장해야 함
    (senderChannelId == 본인 channelId 또는 userRoleCode == "streamer").
    """
    message = (message or "").strip()
    if not prefix or not message.startswith(prefix):
        return None
    rest = message[len(prefix):].strip()
    word = rest.split()[0] if rest else ""
    return CHAT_COMMANDS.get(word)


# ---------------------------------------------------------------- 프레임 파서 (순수 함수 — 오프라인 테스트 가능)
def parse_open_packet(raw: str):
    """EIO open 패킷 '0{...}' → 설정 dict (아니면 None)."""
    if not raw.startswith("0"):
        return None
    try:
        return json.loads(raw[1:])
    except (json.JSONDecodeError, TypeError):
        return None


def parse_socketio_event(raw: str):
    """'42["SYSTEM", {...} 또는 "json문자열"]' → (이벤트명, 데이터dict) / None."""
    if not raw.startswith("42"):
        return None
    try:
        arr = json.loads(raw[2:])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(arr, list) or not arr:
        return None
    name = arr[0]
    payload = arr[1] if len(arr) > 1 else None
    if isinstance(payload, str):  # 이중 인코딩(문자열로 감싸진 JSON) 대응
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            pass
    return (name, payload if isinstance(payload, dict) else {})


def session_url_to_ws(url: str) -> str:
    """세션 URL(https://ssioNN.nchat.naver.com:443?auth=T)
    → wss://.../socket.io/?EIO=3&transport=websocket&auth=T"""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"EIO": "3", "transport": "websocket"})
    host = parts.netloc
    return f"wss://{host}/socket.io/?{urllib.parse.urlencode(query)}"


# ---------------------------------------------------------------- 토큰 저장소
class TokenStore:
    """%LOCALAPPDATA%\\board_overlay\\chzzk_tokens.json — 개인 정보라 배포 zip에 절대 미포함."""

    def __init__(self):
        self.path = data_dir() / "chzzk_tokens.json"

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, access: str, refresh: str) -> None:
        atomic_write_json(self.path, {
            "access_token": access,
            "refresh_token": refresh,
            "issued_at": time.time(),
        })

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


class ChzzkError(Exception):
    pass


class AuthRequired(ChzzkError):
    """토큰 만료/철회 — 스트리머 재로그인 필요."""


# ---------------------------------------------------------------- 클라이언트 본체
class ChzzkClient:
    """OAuth + 세션 수신. 수신은 백그라운드 스레드에서 돌고,
    이벤트는 콜백으로 전달한다 (main.py가 asyncio 루프로 넘김)."""

    def __init__(self, cfg: dict, on_donation=None, on_chat=None, on_status=None):
        self.cfg = cfg
        self.on_donation = on_donation    # (payload dict) -> None
        self.on_chat = on_chat            # (payload dict) -> None
        self.on_status = on_status        # () -> None  (상태 변경 알림)
        self.tokens = TokenStore()
        self.channel_id = None
        self.channel_name = None
        self._status = "test_mode"        # test_mode | connecting | connected | auth_required | error
        self._thread = None
        self._gen = 0                     # 세대 번호 — stop() 시 +1 → 이전 스레드 자연 종료
        self._ws = None
        self._states = {}                 # OAuth state → 만료시각 (CSRF 방지)
        self._lock = threading.Lock()     # 토큰 갱신 직렬화 (refresh token은 일회용)

    # ---------------------------- 상태
    def status(self) -> str:
        return self._status

    def _set_status(self, s: str) -> None:
        if s != self._status:
            self._status = s
            if self.on_status:
                try:
                    self.on_status()
                except Exception:
                    pass

    def has_credentials(self) -> bool:
        return bool(self.cfg.get("chzzk_client_id")) and bool(self.cfg.get("chzzk_client_secret"))

    def has_tokens(self) -> bool:
        return bool(self.tokens.load().get("refresh_token"))

    # ---------------------------- OAuth
    def make_state(self) -> str:
        now = time.time()
        self._states = {k: v for k, v in self._states.items() if v > now}  # 만료 청소
        state = secrets.token_urlsafe(16)
        self._states[state] = now + 600  # 10분 유효
        return state

    def auth_url(self, redirect_uri: str, state: str) -> str:
        q = urllib.parse.urlencode({
            "clientId": self.cfg.get("chzzk_client_id", ""),
            "redirectUri": redirect_uri,
            "state": state,
        })
        return f"{INTERLOCK_URL}?{q}"

    def exchange_code(self, code: str, state: str) -> None:
        """콜백으로 받은 code → 토큰 교환·저장 + 내 채널 정보 조회."""
        if state not in self._states or self._states[state] < time.time():
            raise ChzzkError("로그인 요청이 만료되었거나 잘못되었어요. 다시 시도해 주세요.")
        del self._states[state]
        body = {
            "grantType": "authorization_code",
            "clientId": self.cfg.get("chzzk_client_id", ""),
            "clientSecret": self.cfg.get("chzzk_client_secret", ""),
            "code": code,
            "state": state,
        }
        content = self._post_json(f"{OPENAPI_BASE}/auth/v1/token", body)
        access = content.get("accessToken")
        refresh = content.get("refreshToken")
        if not access or not refresh:
            raise ChzzkError("토큰 발급 응답이 올바르지 않아요.")
        self.tokens.save(access, refresh)
        self._fetch_me()

    def revoke(self) -> None:
        """연결 해제 — 토큰 revoke 시도(실패 무시) 후 로컬 삭제."""
        data = self.tokens.load()
        token = data.get("access_token")
        if token and self.has_credentials():
            try:
                self._post_json(f"{OPENAPI_BASE}/auth/v1/token/revoke", {
                    "clientId": self.cfg.get("chzzk_client_id", ""),
                    "clientSecret": self.cfg.get("chzzk_client_secret", ""),
                    "token": token,
                    "tokenTypeHint": "access_token",
                }, allow_empty=True)
            except ChzzkError:
                pass
        self.tokens.clear()
        self.channel_id = None
        self.channel_name = None

    def _refresh_tokens(self) -> None:
        """Access Token 갱신. Refresh Token은 일회용 → 새 쌍 즉시 저장."""
        with self._lock:
            data = self.tokens.load()
            refresh = data.get("refresh_token")
            if not refresh:
                raise AuthRequired("로그인이 필요해요.")
            body = {
                "grantType": "refresh_token",
                "refreshToken": refresh,
                "clientId": self.cfg.get("chzzk_client_id", ""),
                "clientSecret": self.cfg.get("chzzk_client_secret", ""),
            }
            try:
                content = self._post_json(f"{OPENAPI_BASE}/auth/v1/token", body)
            except ChzzkError as e:
                self.tokens.clear()
                raise AuthRequired(f"토큰 갱신 실패 — 다시 로그인해 주세요. ({e})") from e
            access = content.get("accessToken")
            new_refresh = content.get("refreshToken")
            if not access or not new_refresh:
                self.tokens.clear()
                raise AuthRequired("토큰 갱신 응답이 올바르지 않아요 — 다시 로그인해 주세요.")
            self.tokens.save(access, new_refresh)

    # ---------------------------- REST 공통
    def _post_json(self, url: str, body: dict, allow_empty: bool = False) -> dict:
        import requests  # 지연 임포트 — 미설치 환경에서도 서버 자체는 뜨게
        try:
            res = requests.post(url, json=body, timeout=10)
        except requests.RequestException as e:
            raise ChzzkError(f"치지직 서버에 연결할 수 없어요: {e}") from e
        return self._unwrap(res, allow_empty=allow_empty)

    def _authed(self, method: str, path: str, retry: bool = True) -> dict:
        """Bearer 인증 요청. 401이면 토큰 갱신 후 1회 재시도."""
        import requests
        data = self.tokens.load()
        access = data.get("access_token")
        if not access:
            raise AuthRequired("로그인이 필요해요.")
        try:
            res = requests.request(
                method, f"{OPENAPI_BASE}{path}",
                headers={"Authorization": f"Bearer {access}"}, timeout=10,
            )
        except requests.RequestException as e:
            raise ChzzkError(f"치지직 서버에 연결할 수 없어요: {e}") from e
        if res.status_code == 401 and retry:
            self._refresh_tokens()
            return self._authed(method, path, retry=False)
        return self._unwrap(res)

    @staticmethod
    def _unwrap(res, allow_empty: bool = False) -> dict:
        if res.status_code == 401:
            raise AuthRequired("인증이 만료되었어요.")
        if res.status_code >= 400:
            detail = ""
            try:
                detail = (res.json() or {}).get("message") or ""
            except ValueError:
                pass
            raise ChzzkError(f"치지직 API 오류 {res.status_code} {detail}".strip())
        try:
            data = res.json()
        except ValueError:
            if allow_empty:
                return {}
            raise ChzzkError("치지직 API 응답을 해석할 수 없어요.")
        if isinstance(data, dict) and isinstance(data.get("content"), dict):
            return data["content"]  # 공통 래퍼 {code, message, content:{...}}
        return data if isinstance(data, dict) else {}

    def _fetch_me(self) -> None:
        me = self._authed("GET", "/open/v1/users/me")
        self.channel_id = me.get("channelId") or self.channel_id
        self.channel_name = me.get("channelName") or self.channel_name

    # ---------------------------- 수신 스레드 수명
    def start(self) -> None:
        """자격/토큰이 준비됐으면 수신 스레드 시작 (이미 돌고 있으면 무시)."""
        if not (self.has_credentials() and self.has_tokens()):
            self._set_status("test_mode")
            return
        if self._thread and self._thread.is_alive():
            return
        self._gen += 1
        gen = self._gen
        self._set_status("connecting")
        self._thread = threading.Thread(target=self._run, args=(gen,), daemon=True,
                                        name="chzzk-session")
        self._thread.start()

    def stop(self) -> None:
        self._gen += 1  # 기존 스레드는 세대 불일치로 루프 탈출
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._set_status("test_mode")

    def _run(self, gen: int) -> None:
        backoff = 3
        while gen == self._gen:
            started = time.time()
            try:
                self._set_status("connecting")
                if not self.channel_id:
                    self._fetch_me()  # 소유자 판별용 채널 ID 확보
                self._connect_once(gen)
            except AuthRequired:
                self._set_status("auth_required")
                return  # 재로그인 전까지 재시도 무의미
            except Exception:
                self._set_status("error")
            if gen != self._gen:
                return
            if time.time() - started > 60:
                backoff = 3  # 오래 버텼으면 백오프 리셋
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    # ---------------------------- 세션 1회 연결 (끊길 때까지 블로킹)
    def _connect_once(self, gen: int) -> None:
        import websocket  # websocket-client (지연 임포트)

        content = self._authed("GET", "/open/v1/sessions/auth")
        url = content.get("url")
        if not url:
            raise ChzzkError("세션 URL 발급 실패")
        ws = websocket.create_connection(session_url_to_ws(url), timeout=10)
        self._ws = ws
        try:
            ping_interval = 25.0
            first = ws.recv()
            info = parse_open_packet(first if isinstance(first, str) else "")
            if info and info.get("pingInterval"):
                ping_interval = max(5.0, float(info["pingInterval"]) / 1000.0)
            next_ping = time.time() + ping_interval * 0.7

            while gen == self._gen:
                if time.time() >= next_ping:
                    ws.send("2")  # EIO ping (v3는 클라이언트가 보냄)
                    next_ping = time.time() + ping_interval * 0.7
                ws.settimeout(max(0.5, next_ping - time.time()))
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not isinstance(raw, str) or not raw:
                    continue
                if raw == "1" or raw.startswith("41"):
                    break  # 서버 측 종료 → 재연결 루프로
                if raw in ("3", "40"):
                    continue  # pong / 네임스페이스 connect
                event = parse_socketio_event(raw)
                if event:
                    self._handle_event(*event)
        finally:
            self._ws = None
            try:
                ws.close()
            except Exception:
                pass
            if self._status == "connected" and gen == self._gen:
                self._set_status("connecting")

    def _handle_event(self, name: str, payload: dict) -> None:
        if name == "SYSTEM":
            kind = payload.get("type")
            if kind == "connected":
                key = (payload.get("data") or {}).get("sessionKey")
                if key:
                    self._subscribe(key)
                    self._set_status("connected")
            elif kind == "revoked":
                raise AuthRequired("권한이 철회되었어요.")
        elif name == "CHAT":
            if self.on_chat:
                self.on_chat(payload)
        elif name == "DONATION":
            if self.on_donation:
                self.on_donation(payload)

    def _subscribe(self, session_key: str) -> None:
        """채팅+후원 이벤트 구독. sessionKey는 반드시 쿼리 파라미터로 전달."""
        qs = urllib.parse.urlencode({"sessionKey": session_key})
        self._authed("POST", f"/open/v1/sessions/events/subscribe/chat?{qs}")
        self._authed("POST", f"/open/v1/sessions/events/subscribe/donation?{qs}")
