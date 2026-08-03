"""설정 (명세 12장) — %LOCALAPPDATA%\\board_overlay\\config.json

atomic_write_json(): 임시 파일 → os.replace 원자 교체 (공용 재사용 컴포넌트).
"""
import json
import os
import tempfile
from pathlib import Path

from .paths import data_dir

DEFAULTS = {
    "port": 8140,

    # 후원 → 굴림 규칙
    "dice_base_amount": 2000,        # 주사위 1회 기준 금액
    "multiple_mode": "per_multiple",  # per_multiple(배수만큼 굴림) | single(무조건 1회)
    "max_consecutive_rolls": 5,       # 연속 굴림 상한
    "guard_price": 10000,             # 방어권 가격 — 정확 일치만 (확정)

    # 진행
    "command_prefix": "!",
    "start_index": 0,
    "direction": 1,                   # 1 정방향 / -1 역방향

    # 표시
    "statusbar_visible": True,
    "palette": {},                    # CSS 변수 오버라이드 {"--pink": "#..."} — 기본은 솜사탕 프리셋

    # 보드 테마 (fix27) — 오버레이(시청자 화면) 전용 표시 설정. 조작앱 색은 딥네이비 고정.
    "board_theme": "cotton",          # 테마 id (control.js THEMES — palette는 저장 시 함께 기록)
    "board_font": "jua",              # 보드 칸 글자 폰트 key
    "board_font_stack": "",           # 폰트 CSS 스택 ("" = 기본 주아체)
    "board_font_scale": 1.0,          # 글자 크기 배율 (0.9 / 1 / 1.15 / 1.3)

    # 커스텀 주사위 (fix27) — [{text, pct(직접입력% | null), even(균등분배), mode('move'|'show')}]
    # 빈 목록 = 기본 1~6 균등. 굴림 결과는 이벤트에 기록돼 리플레이가 결정적 (규칙 변경과 무관).
    "dice_faces": [],

    # 진행 인원 — 말 이름 목록 (1~2명, 다음 회차 시작부터 적용) (fix8)
    "players": ["스트리머"],
    # 말 꾸미기 (fix16) — 표시 전용 [{color: 0~5, face: "이모지/글자"}], 인덱스=말 순서
    "piece_style": [],

    # 치지직 연동 (조작앱 [치지직 연동] 패널에서 입력 — 개인 정보라 zip 미포함 영역에 저장)
    "chzzk_client_id": "",
    "chzzk_client_secret": "",

    # 공지
    "global_rule_text": "",
    "auto_pin_notice": True,

    # 자동 업데이트 (fix28) — "아이디/저장소" (비우면 업데이트주소.txt 파일을 읽음)
    "update_repo": "",

    # 인트로 팝업 (fix38) — 경고 문구 버전을 [확인했습니다] 했는지 (컴퓨터당 1회)
    "intro_notice_seen": False,

    # 게임 모드 (fix37 합방 패치) — solo(단일 방송) | collab(합방). 두 모드는 동시 사용 불가 (곡천 확정)
    # 모드·구성 변경 적용 = 반드시 새 회차 (api_mode가 cmd_start까지 수행)
    "game_mode": "solo",
    # 합방 구성: my_piece = 내 채널의 말 이름 / members = [{cid, name(채널명), piece(말 이름), color, face}]
    # 채널 추가 = 말 추가 한 몸 (연결 안 된 채널 없음 — UI 강제). 토큰은 %LOCALAPPDATA%\board_overlay\collab\
    "collab": {"my_piece": "", "members": []},

    # 오버레이 설정 (fix30) — 통합(합본) 소스에서만 적용, ?part= 파츠 소스는 항상 표시
    # 사용법: 합본에서 전광판만 끄고 → 전광판 파츠 소스를 따로 추가하면 위치를 자유 배치
    "overlay_parts": {"board": True, "scoreboard": True, "timers": True,
                      "status": True, "toast": True, "minigame": True},
    "overlay_bg": False,              # True = 테마 배경 표시 (배경 준비 안 된 사용자용), False = 투명(기본)
}


def _config_path() -> Path:
    return data_dir() / "config.json"


def atomic_write_json(path: Path, obj) -> None:
    """임시 파일에 쓴 뒤 os.replace로 원자 교체 (쓰다 만 파일 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    p = _config_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except (json.JSONDecodeError, OSError):
            pass  # 손상 시 기본값으로 계속 (원본은 보존)
    return cfg


def save_config(cfg: dict) -> None:
    atomic_write_json(_config_path(), cfg)
