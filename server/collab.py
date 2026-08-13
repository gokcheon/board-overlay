"""합방(다인 후원) 관리자 (fix37 — 곡천 확정 스펙).

구조:
  - 합방 멤버 채널마다 ChzzkClient를 1개씩 띄워 후원(DONATION)을 직접 수신한다
    (릴레이 없음 — 멤버는 로그인 동의 1번이 전부).
  - 토큰은 %LOCALAPPDATA%\\board_overlay\\collab\\<채널ID>.json (zip 미포함 영역).
  - 초대 흐름: create_invite(말 이름) → 로그인 링크 →
      · 같은 자리: 곡천 컴 브라우저에서 로그인 → 콜백이 바로 redeem
      · 원격: 멤버가 자기 컴에서 로그인 → 주소창의 localhost 주소(?code=&state=)를
        통째로 곡천에게 전달 → 곡천이 붙여넣기 → redeem_text()
  - 세션은 [적용하고 새 회차 시작](api_mode)에서만 시작/정지 — 적용 전 채널은
    "연결됨(대기)"로 두어 후원이 게임에 흘러들지 않는다 (곡천 확정: 모드 밖 후원 금지).

리플레이 결정성: 어느 말에 귀속할지는 '수신 순간' 계산해 이벤트에 기록 —
합방 구성을 나중에 바꿔도 과거 이벤트 재현은 불변.
"""
import threading
import urllib.parse

from .chzzk_client import ChzzkClient
from .paths import data_dir

MAX_CHANNELS = 6   # 말 6개와 세트 (본인 포함)


def parse_redeem_text(text: str):
    """붙여넣은 주소/쿼리에서 (code, state) 추출. 실패 시 (None, None).

    허용 형태: 전체 URL(http://localhost:8140/...?code=..&state=..),
    쿼리만(code=..&state=..), 물음표 뒤만.
    """
    text = (text or "").strip()
    if not text:
        return None, None
    query = text
    if "?" in text:
        query = text.split("?", 1)[1]
    query = query.split("#", 1)[0]
    params = dict(urllib.parse.parse_qsl(query))
    return params.get("code"), params.get("state")


class CollabManager:
    def __init__(self, cfg: dict, on_donation=None, on_status=None):
        """on_donation(payload, cid) — 멤버 채널 후원 콜백 (main.py가 채널→말 매핑)."""
        self.cfg = cfg
        self.on_donation = on_donation
        self.on_status = on_status
        self.dir = data_dir() / "collab"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clients = {}        # cid → ChzzkClient (세션은 적용 시에만 start)
        self.invites = {}        # invite_id → {"piece", "client", "state"}
        self._lock = threading.Lock()

    # ---------------------------- 조회
    def members(self) -> list:
        c = self.cfg.get("collab") or {}
        return list(c.get("members") or [])

    def member_status(self, cid: str) -> str:
        cl = self.clients.get(cid)
        return cl.status() if cl else "ready"     # ready = 토큰 있음·세션 미시작(적용 대기)

    def public(self) -> dict:
        """설정 창 표시용 — 토큰 등 비밀 값은 절대 포함하지 않음."""
        return {
            "mode": self.cfg.get("game_mode", "solo"),
            "my_piece": (self.cfg.get("collab") or {}).get("my_piece", ""),
            "members": [{"cid": m["cid"], "name": m.get("name", ""),
                         "piece": m.get("piece", ""), "color": m.get("color", 0),
                         "face": m.get("face", ""), "img": m.get("img", ""),   # fix40: 말 그림
                         "status": self.member_status(m["cid"])}
                        for m in self.members()],
            "invites": [{"id": iid, "piece": inv["piece"]}
                        for iid, inv in self.invites.items()],
        }

    def piece_for_channel(self, cid: str):
        """수신 순간 채널→말 인덱스 (0=내 채널). 매핑 없으면 None — 이벤트에 기록됨."""
        for i, m in enumerate(self.members()):
            if m["cid"] == cid:
                return i + 1
        return None

    def tag_for_channel(self, cid: str):
        for m in self.members():
            if m["cid"] == cid:
                return m.get("piece") or m.get("name") or ""
        return ""

    # ---------------------------- 초대 → 연결
    def create_invite(self, piece: str, redirect_uri: str) -> dict:
        if len(self.members()) + len(self.invites) + 1 >= MAX_CHANNELS + 1:
            raise ValueError(f"채널은 최대 {MAX_CHANNELS}개(내 채널 포함)까지예요.")
        import secrets
        invite_id = secrets.token_urlsafe(8)
        pending_path = self.dir / f"pending_{invite_id}.json"
        client = ChzzkClient(self.cfg, token_path=pending_path)
        state = client.make_state()
        url = client.auth_url(redirect_uri, state)
        self.invites[invite_id] = {"piece": str(piece).strip()[:8] or "게스트",
                                   "client": client, "state": state}
        return {"invite_id": invite_id, "auth_url": url}

    def cancel_invite(self, invite_id: str) -> None:
        inv = self.invites.pop(invite_id, None)
        if inv:
            inv["client"].tokens.clear()

    def find_invite_by_state(self, state: str):
        for iid, inv in self.invites.items():
            if inv["state"] == state:
                return iid
        return None

    def redeem(self, invite_id: str, code: str, state: str) -> dict:
        """코드 교환 → 채널 확인 → 토큰을 채널 파일로 승격 → 멤버 등록 (세션은 적용 시)."""
        with self._lock:
            inv = self.invites.get(invite_id)
            if not inv:
                raise ValueError("초대가 만료되었거나 이미 처리됐어요. 다시 [채널 추가]를 해주세요.")
            client = inv["client"]
            client.exchange_code(code, state)      # 토큰 저장(pending 파일) + 채널 정보 조회
            cid = client.channel_id
            name = client.channel_name or "이름없음"
            if not cid:
                client.tokens.clear()
                raise ValueError("채널 정보를 가져오지 못했어요. 다시 시도해 주세요.")
            main_cid = self.cfg.get("_main_channel_id")  # main.py가 세팅 (본인 채널 중복 방지)
            if main_cid and cid == main_cid:
                client.tokens.clear()
                self.invites.pop(invite_id, None)
                raise ValueError("내 채널이에요! 내 채널은 이미 첫 줄에 연결돼 있어요.")
            if any(m["cid"] == cid for m in self.members()):
                client.tokens.clear()
                self.invites.pop(invite_id, None)
                raise ValueError(f"'{name}' 채널은 이미 연결돼 있어요.")
            # pending → 채널 파일로 이동
            final_path = self.dir / f"{cid}.json"
            data = client.tokens.load()
            client.tokens.path = final_path
            client.tokens.save(data.get("access_token", ""), data.get("refresh_token", ""),
                               extra={"channel_id": cid, "channel_name": name})
            try:
                (self.dir / f"pending_{invite_id}.json").unlink(missing_ok=True)
            except OSError:
                pass
            member = {"cid": cid, "name": name, "piece": inv["piece"],
                      "color": (len(self.members()) + 1) % 6, "face": "", "img": ""}
            collab = self.cfg.setdefault("collab", {"my_piece": "", "members": []})
            collab.setdefault("members", []).append(member)
            self.clients[cid] = client             # 세션은 api_mode 적용 때 start
            self.invites.pop(invite_id, None)
            return member

    # ---------------------------- 해제
    def remove(self, cid: str) -> dict:
        with self._lock:
            collab = self.cfg.setdefault("collab", {"my_piece": "", "members": []})
            member = next((m for m in collab.get("members", []) if m["cid"] == cid), None)
            if member is None:
                raise ValueError("연결되어 있지 않은 채널이에요.")
            cl = self.clients.pop(cid, None)
            if cl:
                cl.stop()
                try:
                    cl.revoke()
                except Exception:
                    cl.tokens.clear()
            else:
                try:
                    (self.dir / f"{cid}.json").unlink(missing_ok=True)
                except OSError:
                    pass
            collab["members"] = [m for m in collab["members"] if m["cid"] != cid]
            return member

    # ---------------------------- 세션 수명 (적용 시에만)
    def _client_for(self, member: dict) -> ChzzkClient:
        cid = member["cid"]
        if cid not in self.clients:
            self.clients[cid] = ChzzkClient(
                self.cfg, token_path=self.dir / f"{cid}.json")
        cl = self.clients[cid]
        data = cl.tokens.load()
        cl.channel_id = data.get("channel_id") or cid
        cl.channel_name = data.get("channel_name") or member.get("name")
        if self.on_donation and cl.on_donation is None:
            cl.on_donation = (lambda payload, _cid=cid: self.on_donation(payload, _cid))
        if self.on_status and cl.on_status is None:
            cl.on_status = self.on_status
        return cl

    def start_sessions(self) -> None:
        """합방 모드 적용 시 — 모든 멤버 채널 수신 시작."""
        for m in self.members():
            cl = self._client_for(m)
            threading.Thread(target=cl.start, daemon=True).start()

    def stop_sessions(self) -> None:
        for cl in self.clients.values():
            cl.stop()
