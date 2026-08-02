"""경로 유틸 (공용 재사용 컴포넌트).

- resolve_base_dir(): exe(PyInstaller)/스크립트 어느 쪽으로 실행돼도 실행 폴더 기준.
- data_dir(): 사용자 데이터는 %LOCALAPPDATA%\\board_overlay 로 분리
  (OneDrive 동기화 회피 — 독바 fix23 패턴 동일 적용).
"""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "board_overlay"


def resolve_base_dir() -> Path:
    """실행 기준 폴더 (main.py가 있는 프로젝트 루트 또는 exe 위치)."""
    if getattr(sys, "frozen", False):  # PyInstaller 빌드
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """사용자 데이터 루트. 없으면 생성해서 반환."""
    root = os.environ.get("LOCALAPPDATA")
    base = (Path(root) / APP_DIR_NAME) if root else (resolve_base_dir() / "userdata")
    base.mkdir(parents=True, exist_ok=True)
    return base
