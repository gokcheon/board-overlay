"""후원 라우터 (명세 5장).

후원 수신 → 즉시 이벤트 기록(후원ID 중복 방지) → 금액 판정:
  - 금액 == 방어권 가격 (정확 일치, 확정) → 큐를 거치지 않고 즉시 방어권 +1
  - 그 외 → 기준 금액·배수 설정 적용 → 연속 굴림 상한 → 굴림 큐 적재
"""
import time


class DonationRouter:
    def __init__(self, engine, cfg: dict):
        self.engine = engine
        self.cfg = cfg
        # 재시작 후에도 중복 방지 유지: 로그의 후원ID를 미리 수집
        self.seen = {
            e["data"].get("id")
            for e in engine.events
            if e["type"] in ("donation_received", "guard_gift")
        }
        self.seen.discard(None)

    def on_donation(self, donation_id: str, nick: str, amount: int,
                    ch: str = None, guard_piece=None) -> dict:
        """fix37 합방: ch = 채널 태그(말 이름 — 표시용), guard_piece = 방어권 자동 귀속 말 인덱스.
        둘 다 '수신 순간' 호출측(main.py)이 계산해 이벤트에 기록 → 리플레이 결정적."""
        if donation_id in self.seen:
            return {"kind": "duplicate", "nick": nick}
        self.seen.add(donation_id)
        amount = int(amount)

        if amount == int(self.cfg.get("guard_price", 10000)):  # 정확 일치만
            data = {"id": donation_id, "nick": nick, "amount": amount, "ts": time.time()}
            if ch:
                data["ch"] = ch
            if guard_piece is not None:
                data["auto_piece"] = int(guard_piece)   # 합방: 후원 터진 채널의 말에게 자동 (곡천 확정)
            self.engine.record("guard_gift", data)
            return {"kind": "guard", "nick": nick, "auto_piece": guard_piece}

        base = max(1, int(self.cfg.get("dice_base_amount", 2000)))
        if amount < base:
            rolls = 0  # 기준 미만 — 로그만 보존, 큐 미적재
        elif self.cfg.get("multiple_mode", "per_multiple") == "per_multiple":
            rolls = amount // base
        else:
            rolls = 1
        rolls = min(rolls, int(self.cfg.get("max_consecutive_rolls", 5)))

        data = {"id": donation_id, "nick": nick, "amount": amount,
                "rolls": rolls, "ts": time.time()}
        if ch:
            data["ch"] = ch                              # fix37: 채널 태그 (표시용, 큐→굴림으로 전파)
        self.engine.record("donation_received", data)
        return {"kind": "rolls" if rolls > 0 else "ignored", "nick": nick, "rolls": rolls}
