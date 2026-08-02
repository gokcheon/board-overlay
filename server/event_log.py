"""append-only 이벤트 로그 (명세 6.5).

- 모든 이벤트를 JSONL 한 줄씩 추가 기록. 수정/삭제 없음.
- 재시작 시 read_all() → 엔진이 리플레이로 상태 복원.
- rotate(): 새 회차([게임 시작]) 때 이전 회차 로그를 타임스탬프 파일로 보관.
"""
import json
import time
from pathlib import Path


class EventLog:
    def __init__(self, dirpath: Path):
        self.dir = Path(dirpath)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "events.jsonl"

    def read_all(self) -> list:
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 손상 라인은 건너뛰고 복구 계속
        return events

    def append(self, event: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()

    def rotate(self) -> None:
        """이전 회차 로그 보관 후 새 파일 시작. 복구 대상은 현재 회차만."""
        if self.path.exists() and self.path.stat().st_size > 0:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.path.rename(self.dir / f"events_{stamp}.jsonl")
