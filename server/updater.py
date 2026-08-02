"""자동 업데이트 (fix28) — GitHub Releases에서 새 버전 확인·다운로드.

동작 흐름:
  ① 저장소 주소 알아내기 — 업데이트주소.txt(배포판 동봉) 또는 config "update_repo"
  ② GitHub 최신 릴리스 조회 → 태그의 숫자(fix29 → 29)를 내 버전(APP_FIX)과 비교
  ③ 새 버전이면 조작앱에 알림 → [지금 업데이트] 시 설치 exe를 내려받아 실행
     (설치판 전용 — 폴더판(곡천 컴)은 zip 교체 안내만)

실패는 전부 조용히 넘어간다 (인터넷 없음/주소 미설정 = 기능 잠자기).
곡천 눈높이 설정법은 자동업데이트_안내.txt 참고.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

from .paths import resolve_base_dir
from .version import APP_FIX

REPO_FILE = "업데이트주소.txt"          # 배포판에 동봉 — "아이디/저장소" 한 줄
TIMEOUT = 8
UA = {"User-Agent": f"board-overlay-fix{APP_FIX}", "Accept": "application/vnd.github+json"}


def get_repo(cfg: dict) -> str:
    """저장소 주소 — config가 우선, 없으면 업데이트주소.txt. 형식: '아이디/저장소'."""
    repo = str(cfg.get("update_repo", "") or "").strip()
    if not repo:
        try:
            for line in (resolve_base_dir() / REPO_FILE).read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    repo = line
                    break
        except OSError:
            pass
    return repo if re.match(r"^[\w.-]+/[\w.-]+$", repo) else ""


def tag_fix(tag: str):
    """태그에서 fix 번호 추출 — 'fix29'·'v1.0.29'·'29' 전부 마지막 숫자 사용."""
    nums = re.findall(r"\d+", str(tag or ""))
    return int(nums[-1]) if nums else None


def check_update(cfg: dict) -> dict:
    """최신 릴리스 확인. 반환: {configured, available, tag, fix, current, url, name, page, notes}"""
    base = {"configured": False, "available": False, "current": APP_FIX}
    repo = get_repo(cfg)
    if not repo:
        return base
    base["configured"] = True
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                         headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return base
        rel = r.json()
    except Exception:
        return base
    fix = tag_fix(rel.get("tag_name"))
    if fix is None or fix <= APP_FIX:
        return base
    asset = next((a for a in rel.get("assets") or []
                  if str(a.get("name", "")).lower().endswith(".exe")), None)
    return {
        **base, "available": True, "tag": str(rel.get("tag_name", "")), "fix": fix,
        "url": (asset or {}).get("browser_download_url", ""),
        "name": (asset or {}).get("name", ""),
        "page": str(rel.get("html_url", "")),
        "notes": str(rel.get("body", "") or "")[:400],
    }


def download_installer(url: str) -> Path:
    """설치 exe를 임시 폴더로 내려받는다 (스트리밍 — 대용량 대비)."""
    dest = Path(tempfile.gettempdir()) / "board_overlay_update.exe"
    with requests.get(url, headers={"User-Agent": UA["User-Agent"]},
                      timeout=TIMEOUT, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    if dest.stat().st_size < 1024 * 100:      # 100KB 미만이면 설치 파일이 아님 (오류 페이지 등)
        raise RuntimeError("내려받은 파일이 설치 파일이 아니에요.")
    return dest


def launch_installer(path: Path) -> None:
    """설치 마법사 실행. 앱은 곧 스스로 종료(종료 코드 42 → 런처도 함께 종료)."""
    if os.name == "nt":
        os.startfile(str(path))               # 관리자 승격(UAC) 창이 뜨는 표준 경로
    else:                                      # 개발 환경 폴백 (실사용 아님)
        subprocess.Popen([str(path)])


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


if __name__ == "__main__":            # `python -m server.updater` → 업데이트주소.txt의 저장소 주소 (올리기.bat용)
    print(get_repo({}))
