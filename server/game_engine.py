"""게임 엔진 (명세 6장).

핵심 구조 = 이벤트 소싱:
  - 상태를 직접 저장하지 않고, 모든 변화를 append-only 이벤트로만 기록.
  - 현재 상태 = 이벤트 전체 리플레이 결과 → 재시작 복구가 공짜로 따라옴.
  - !취소 = "undo 이벤트" 추가 후 전체 리플레이 → 버프·카운터까지 정확 복원.
  - 랜덤(주사위 값)은 이벤트에 기록된 값을 리플레이가 그대로 사용 → 결정적.
  - !취소로 되돌린 굴림을 다시 실행하면 '같은 값'이 다시 나온다 (fix15) —
    취소 후 재굴림으로 값을 바꾸는 악용 차단 + 실수 취소를 원상 복구.

상태 머신 (6.1):
  idle --[!다음, 큐 있음]--> (주사위 연출·이동은 클라 애니메이션) --> effect
  effect --[!다음/완료]--> idle        # 칸 효과 해소는 반드시 수동 게이팅
  effect(needs_choice) = await_choice --[칸 선택]--> effect
"""
import random
import re
import time

BOARD_N = 26      # 9×6 둘레형
CHAIN_LIMIT = 5   # move_to 연쇄 안전 상한

# ---------------------------------------------------------------- 커스텀 주사위 (fix27)
# 항목: {text, pct(직접입력% | None), even(균등분배), mode('move' 이동형 | 'show' 연출형)}
# 확률 규칙 (곡천 확정): 100 − 직접입력합 → 균등 체크 항목이 n등분 / 직접입력합 최대 99
# (균등 항목이 없으면 100) / 굴림 결과는 이벤트에 기록 → 리플레이 결정적.
NUM_FACE = re.compile(r"^\d{1,2}$")   # 숫자 항목 = 그만큼 이동 (1~25)


def default_faces() -> list:
    return [{"text": str(i), "pct": None, "even": True, "mode": "move"} for i in range(1, 7)]


def sanitize_faces(raw):
    """설정 저장/사용 전 검증. 통과하면 정리된 목록, 문제 있으면 (None, 사유) 반환."""
    if not isinstance(raw, list) or not raw:
        return None, "항목이 1개 이상 있어야 해요."
    faces = []
    for f in raw[:60]:                       # 상한 60개 (무한 추가 UI의 안전망)
        if not isinstance(f, dict):
            return None, "항목 형식이 이상해요."
        text = str(f.get("text", "")).strip()[:16]
        if not text:
            return None, "결과 칸이 비어 있는 항목이 있어요."
        if NUM_FACE.match(text) and not (1 <= int(text) <= 25):
            return None, f"숫자 항목은 1~25 사이여야 해요. ({text})"
        even = bool(f.get("even"))
        pct = None
        if not even:
            try:
                pct = round(float(f.get("pct", 0) or 0), 2)
            except (TypeError, ValueError):
                return None, "확률 %는 숫자여야 해요."
            if pct < 0:
                return None, "확률 %는 0 이상이어야 해요."
        mode = f.get("mode") if f.get("mode") in ("move", "show") else "move"
        faces.append({"text": text, "pct": pct, "even": even, "mode": mode})
    fixed = sum(f["pct"] for f in faces if not f["even"])
    evens = sum(1 for f in faces if f["even"])
    if evens and fixed > 99.001:
        return None, "직접 입력한 %의 합은 최대 99%까지예요 (남은 1%는 균등분배 몫)."
    if not evens and abs(fixed - 100) > 0.01:
        return None, "확률 합이 100%가 되어야 해요."
    if evens == 0 and fixed <= 0:
        return None, "확률 합이 100%가 되어야 해요."
    return faces, ""

UNDOABLE = {
    "donation_received", "guard_gift", "manual_roll_added",
    "roll_consumed", "tile_chosen", "guard_used", "guard_assigned",
    "effect_completed", "timer_started",
}


class CommandError(ValueError):
    """스트리머 조작이 현재 상태에서 불가능할 때 (사용자에게 그대로 표시)."""


def fresh_state(cfg: dict) -> dict:
    start = int(cfg.get("start_index", 0))
    base_dir = 1 if int(cfg.get("direction", 1)) >= 0 else -1
    return {
        "running": False,
        "paused": False,
        "phase": "idle",                 # idle | effect | await_choice
        "players": ["스트리머"],          # 말 이름 목록 (회차 시작 이벤트가 확정, 중도 참여로 증가 가능)
        "positions": [start],            # 말별 위치
        "turn": 0,                       # 다음에 움직일 말 (교차 진행)
        "directions": [base_dir],        # 말별 진행 방향 (돌아가는 걸린 말만 반전)
        "x2_pieces": [False],            # 말별 다음 굴림 ×2
        "counters": {"적립": 0.0, "안주": 0},  # ★ 공동 항아리 (fix13): 모두가 쌓고 정산자가 독박
        "queue": [],                     # 공용: {id, nick, amount, rolls_left, received_at}
        "last_rolls": [],                # 최근 2건 {nick, value, label}
        "pending": None,                 # 도착 칸 효과 (수동 게이팅 대기)
        "timers": [],                    # [{label, ends_at, piece}] — 말별, 같은 (말, 벌칙)만 중첩
        "piece_guards": [0],             # ★ 말별 방어권 (fix13: 지목제)
        "guard_gifters": [[]],           # ★ 말별 선물자 스택
        "unassigned_guards": [],         # ★ 지정 대기 방어권 [{id, nick}] — 실시간 지정용
    }


class GameEngine:
    def __init__(self, board: list, config: dict, log):
        self.board = board
        self.config = config
        self.log = log
        self.events = log.read_all()     # 재시작 시 로그 로드
        self.state = self._replay()      # → 리플레이 복원 (명세 6.5)

    # ---------------------------------------------------------- 이벤트 기록
    def record(self, etype: str, data: dict) -> dict:
        seq = (self.events[-1]["seq"] + 1) if self.events else 1
        ev = {"seq": seq, "ts": time.time(), "type": etype, "data": data}
        self.log.append(ev)
        self.events.append(ev)
        if etype == "undo":
            self.state = self._replay()  # 되돌리기는 전체 리플레이로 정확 복원
        else:
            self._apply(self.state, ev)  # 평시엔 증분 적용
        return ev

    def _replay(self) -> dict:
        undone = {e["data"]["target_seq"] for e in self.events if e["type"] == "undo"}
        st = fresh_state(self.config)
        for e in self.events:
            if e["type"] == "undo" or e["seq"] in undone:
                continue
            self._apply(st, e)
        return st

    # ---------------------------------------------------------- 이벤트 적용
    def _apply(self, st: dict, e: dict) -> None:
        t, d = e["type"], e["data"]
        if t == "game_started":
            new = fresh_state(self.config)
            new["running"] = True
            start = int(d.get("start_index", 0))
            base_dir = 1 if int(d.get("direction", 1)) >= 0 else -1
            players = [str(x) for x in (d.get("players") or ["스트리머"])][:6] or ["스트리머"]
            n = len(players)
            new["players"] = players
            new["positions"] = [start] * n
            new["directions"] = [base_dir] * n
            new["x2_pieces"] = [False] * n
            new["piece_guards"] = [0] * n
            new["guard_gifters"] = [[] for _ in range(n)]
            st.clear()
            st.update(new)
        elif t == "player_joined":
            # ★ 중도 참여 (fix10): 출발 칸에서 합류, 차례는 맨 뒤 (되돌리기 대상 아님)
            st["players"].append(str(d.get("name", f"말{len(st['players']) + 1}")))
            st["positions"].append(int(d.get("start_index", 0)))
            st["directions"].append(1 if int(d.get("direction", 1)) >= 0 else -1)
            st["x2_pieces"].append(False)
            st["piece_guards"].append(0)
            st["guard_gifters"].append([])
        elif t == "game_paused":
            st["paused"] = True
        elif t == "game_resumed":
            st["paused"] = False
        elif t == "donation_received":
            if d.get("rolls", 0) > 0:
                st["queue"].append({
                    "id": d["id"], "nick": d["nick"], "amount": d["amount"],
                    "rolls_left": d["rolls"], "received_at": d.get("ts", 0),
                })
            # rolls==0(기준 금액 미만)은 로그 보존만 하고 큐 미적재
        elif t == "guard_gift":
            gid = str(d.get("id") or f'{d.get("nick", "?")}-{d.get("ts", 0)}')
            if len(st["players"]) <= 1:      # 1인: 지정 단계 생략, 즉시 귀속
                st["piece_guards"][0] += 1
                st["guard_gifters"][0].append(d["nick"])
            else:                            # 다인: 스트리머가 실시간 지정 (fix13 지목제)
                st["unassigned_guards"].append({"id": gid, "nick": d["nick"]})
        elif t == "guard_assigned":
            gid = str(d.get("gift_id", ""))
            entry = next((g for g in st["unassigned_guards"] if g["id"] == gid), None)
            if entry is not None:
                st["unassigned_guards"].remove(entry)
                piece = int(d.get("piece", 0)) % max(1, len(st["players"]))
                st["piece_guards"][piece] += 1
                st["guard_gifters"][piece].append(entry["nick"])
        elif t == "manual_roll_added":
            st["queue"].append({
                "id": d["id"], "nick": d.get("nick", "수동 추가"), "amount": 0,
                "rolls_left": 1, "received_at": d.get("ts", 0),
            })
        elif t == "roll_consumed":
            self._apply_roll(st, d)
        elif t == "tile_chosen":
            piece = int(d.get("piece", 0)) % max(1, len(st["positions"]))
            st["positions"][piece] = d["final"]
            self._land(st, d["final"], d.get("nick", ""), piece)
            self._advance_turn(st, d["final"])
        elif t == "guard_used":
            gp = int(st["pending"].get("piece", 0)) if st["pending"] else 0
            gp %= max(1, len(st["piece_guards"]))
            st["piece_guards"][gp] = max(0, st["piece_guards"][gp] - 1)
            if st["guard_gifters"][gp]:
                st["guard_gifters"][gp].pop()
            if st["pending"]:
                # 방어권 = 벌칙 1회 무효 → 이 칸에서 '시작해 둔' 타이머 1회분만 차감 (fix9)
                # 말별 타이머이므로 (말, 벌칙) 둘 다 일치해야 차감 (fix10)
                pend = st["pending"]
                if pend.get("timer_started") and pend.get("timer_minutes"):
                    sub = float(pend["timer_minutes"]) * 60
                    pc = int(pend.get("piece", 0))
                    for tm in st["timers"]:
                        if tm["label"] == pend["label"] and int(tm.get("piece", 0)) == pc:
                            tm["ends_at"] -= sub
                            break
            st["pending"] = None
            st["phase"] = "idle"
        elif t == "effect_completed":
            st["pending"] = None
            st["phase"] = "idle"
        elif t == "timer_started":
            # 같은 (말, 벌칙)이 아직 진행 중이면 시간을 이어붙임: 2:30 남음 + 3분 → 5:30
            at = float(d.get("at", d.get("ends_at", 0) - float(d.get("minutes", 0)) * 60))
            add = float(d["minutes"]) * 60
            piece = int(d.get("piece", 0))
            for tm in st["timers"]:
                if (tm["label"] == d["label"] and int(tm.get("piece", 0)) == piece
                        and tm["ends_at"] > at):
                    tm["ends_at"] += add
                    break
            else:
                # fix26: start 기록 — 오버레이 게이지(남은/전체) 계산용 (표시용 파생값, 이벤트에서 결정적으로 재현)
                st["timers"].append({"label": d["label"], "ends_at": at + add, "piece": piece, "start": at})
            if st["pending"] and st["pending"].get("label") == d["label"]:
                st["pending"]["timer_started"] = True  # 같은 칸에서 중복 시작 방지용

    def _apply_roll(self, st: dict, d: dict) -> None:
        if st["queue"]:  # 큐 앞에서 1굴림 소비
            head = st["queue"][0]
            head["rolls_left"] -= 1
            if head["rolls_left"] <= 0:
                st["queue"].pop(0)
        piece = int(d.get("piece", 0)) % max(1, len(st["positions"]))
        if d.get("special"):
            # ★ 커스텀 주사위 글자 항목 (fix27) — 말 이동 없음, x2 미소진 (이동이 아니므로)
            st["last_rolls"].insert(0, {"nick": d["nick"], "value": "★", "label": d["special"]})
            del st["last_rolls"][2:]
            needs_choice = d.get("mode", "move") == "move"   # 이동형 = 세계여행식 칸 선택
            st["pending"] = {
                "index": st["positions"][piece], "piece": piece, "timer_started": False,
                "label": d["special"], "sub": None, "rule_text": "",
                "category": None, "nick": d["nick"], "flush": None,
                "timer_minutes": None, "minigame": None,
                "needs_choice": needs_choice, "special": True,
            }
            st["phase"] = "await_choice" if needs_choice else "effect"
            if not needs_choice and len(st["positions"]) > 1:
                st["turn"] = (st["turn"] + 1) % len(st["positions"])  # 연출형은 여기서 차례 넘김
            return
        if d.get("x2_used"):
            st["x2_pieces"][piece] = False  # 1회성 버프 소진 (말별)
        st["positions"][piece] = d["final"]
        label = self._tile(d["final"]).get("label", "")
        st["last_rolls"].insert(0, {"nick": d["nick"], "value": d["value"], "label": label})
        del st["last_rolls"][2:]
        self._land(st, d["final"], d["nick"], piece)
        if not st["pending"]["needs_choice"]:
            self._advance_turn(st, d["final"])

    def _advance_turn(self, st: dict, final: int) -> None:
        """굴림 1건이 끝나면 다음 말로 교차 (fix8). 예외: '한 번 더' 칸은 같은 말 유지,
        칸 선택(세계여행)은 선택 확정 시점에 판단."""
        n = len(st["positions"])
        if n <= 1:
            return
        ops = self._tile(final).get("ops", [])
        if any(o.get("op") == "extra_roll" for o in ops):
            return
        st["turn"] = (st["turn"] + 1) % n

    def _land(self, st: dict, idx: int, nick: str, piece: int = 0) -> None:
        """도착 칸 효과 처리. 상태를 즉시 바꾸는 op는 여기서 적용하고,
        표시/수행형 op(drink·timer·mission·custom)는 pending으로 수동 게이팅."""
        tile = self._tile(idx)
        flush_info = None
        timer_minutes = None
        needs_choice = False
        minigame_kind = None
        for op in tile.get("ops", []):
            k = op.get("op")
            if k == "counter_add":
                name = op.get("name", "적립")
                if name == "방어":  # 보드 찬스 방어권 = 밟은 말 소유 (fix13)
                    st["piece_guards"][piece] += int(op.get("n", 1))
                    st["guard_gifters"][piece].append(None)
                else:               # 잔·안주 = 공동 항아리에 적립 (fix13 교정)
                    st["counters"][name] = round(st["counters"].get(name, 0) + op.get("n", 1), 2)
            elif k == "counter_flush":
                name = op.get("name", "적립")
                flush_info = {"name": name, "amount": st["counters"].get(name, 0), "piece": piece}
                st["counters"][name] = 0  # 밟은 말이 전부 독박 → 0부터 다시 (fix13)
            elif k == "reverse":
                st["directions"][piece] *= -1  # 걸린 말만 역방향 (fix10)
            elif k == "dice_multiplier":
                st["x2_pieces"][piece] = True  # 얻은 말의 다음 굴림만 ×2
            elif k == "extra_roll":
                st["queue"].insert(0, {
                    "id": f"extra-{idx}-{nick}", "nick": f"{nick} (한번 더)",
                    "amount": 0, "rolls_left": 1, "received_at": 0,
                })
            elif k == "timer":
                timer_minutes = op.get("minutes", 3)
            elif k == "minigame":
                minigame_kind = op.get("kind", "ladder")  # 미니게임 칸 (fix18 — 연출 전용)
            elif k == "move_to" and op.get("choice"):
                needs_choice = True
            # drink / custom / minigame / move_to(index·offset)는 여기서 상태 변화 없음
            #  - move_to(index·offset)는 굴림 계산 시 세그먼트로 이미 최종 칸에 반영됨
        st["pending"] = {
            "index": idx,
            "piece": piece,
            "timer_started": False,
            "label": tile.get("label", ""),
            "sub": tile.get("sub"),
            "rule_text": tile.get("rule_text", ""),
            "category": tile.get("category"),
            "nick": nick,
            "flush": flush_info,
            "timer_minutes": timer_minutes,
            "minigame": minigame_kind,
            "needs_choice": needs_choice,
        }
        st["phase"] = "await_choice" if needs_choice else "effect"

    def _tile(self, idx: int) -> dict:
        return self.board[idx % len(self.board)]

    # ---------------------------------------------------------- 이동 계산
    def _walk(self, cur: int, direction: int, steps: int):
        path = []
        for _ in range(steps):
            cur = (cur + direction) % BOARD_N
            path.append(cur)
        return path, cur

    def _chain(self, cur: int, direction: int, segments: list) -> int:
        """도착 칸에 move_to(index/offset)가 있으면 연쇄 이동 세그먼트 추가."""
        for _ in range(CHAIN_LIMIT):
            ops = self._tile(cur).get("ops", [])
            mt = next((o for o in ops if o.get("op") == "move_to" and not o.get("choice")), None)
            if not mt:
                break
            if "index" in mt:
                cur = int(mt["index"]) % BOARD_N
                segments.append({"path": [cur], "to": cur, "jump": True})
            elif "offset" in mt:
                off = int(mt["offset"])
                step_dir = direction if off > 0 else -direction
                path, cur = self._walk(cur, step_dir, abs(off))
                segments.append({"path": path, "to": cur})
            else:
                break
        return cur

    # ---------------------------------------------------------- 조작 명령
    def cmd_start(self) -> None:
        self.log.rotate()          # 이전 회차 로그 보관
        self.events = []
        players = [str(x).strip() for x in (self.config.get("players") or ["스트리머"])]
        players = [x for x in players if x][:6] or ["스트리머"]
        self.record("game_started", {
            "start_index": int(self.config.get("start_index", 0)),
            "direction": int(self.config.get("direction", 1)),
            "players": players,
        })

    def cmd_pause_toggle(self) -> bool:
        self._require_running()
        if self.state["paused"]:
            self.record("game_resumed", {})
        else:
            self.record("game_paused", {})
        return self.state["paused"]

    def _pending_redo_outcome(self):
        """취소된 굴림이 있으면 다음 굴림에 재사용할 결과 (fix15, fix27 확장).

        undo(roll_consumed)가 결과를 스택에 쌓고, 새 roll_consumed가 하나씩 꺼내 쓴다
        → "3 굴림 - 취소 - 다시 굴림"이면 다시 3. 커스텀 주사위 글자 항목(★)도
        기록된 결과 그대로 재현된다 (규칙을 바꿔도 취소분은 원래 결과 유지).
        이벤트 로그만으로 계산하므로 서버를 재시작해도 결과가 같다 (리플레이 결정성)."""
        by_seq = {e["seq"]: e for e in self.events}
        stack = []
        for e in self.events:
            if e["type"] == "undo" and e["data"].get("target_type") == "roll_consumed":
                target = by_seq.get(e["data"]["target_seq"])
                if target is not None:
                    stack.append(target["data"])   # 결과 전체 (숫자 value 또는 special/mode)
            elif e["type"] == "roll_consumed" and stack:
                stack.pop()          # 재실행이 결과 하나를 소비
        return stack[-1] if stack else None

    # ---------------------------------------------------------- 커스텀 주사위 (fix27)
    def _dice_faces(self) -> list:
        faces, _ = sanitize_faces(self.config.get("dice_faces") or [])
        return faces if faces else default_faces()

    def _roll_face(self) -> dict:
        """가중치 랜덤으로 항목 1개 선택 → 결과 서술자 반환 (이벤트에 기록될 값).
        숫자 항목 = {"value": n}, 글자 항목 = {"special": 텍스트, "mode": move|show}."""
        faces = self._dice_faces()
        fixed = sum(f["pct"] for f in faces if not f["even"])
        evens = sum(1 for f in faces if f["even"])
        share = max(0.0, 100.0 - fixed) / evens if evens else 0.0
        weights = [share if f["even"] else (f["pct"] or 0.0) for f in faces]
        total = sum(weights) or 1.0
        r = random.random() * total
        idx = len(faces) - 1
        for i, w in enumerate(weights):
            r -= w
            if r < 0:
                idx = i
                break
        face = faces[idx]
        if NUM_FACE.match(face["text"]):
            return {"value": int(face["text"])}
        return {"special": face["text"], "mode": face.get("mode", "move")}

    def cmd_next(self) -> dict:
        """idle이면 큐 1건 소화, effect면 완료 처리 (명세 7: !다음 겸용)."""
        st = self.state
        self._require_running()
        if st["paused"]:
            raise CommandError("일시정지 상태예요. 먼저 재개해 주세요.")
        if st["phase"] == "await_choice":
            raise CommandError("이동할 칸을 먼저 선택해 주세요 (조작앱 미니 보드).")
        if st["phase"] == "effect":
            self.record("effect_completed", {})
            return {"kind": "completed"}
        if not st["queue"]:
            raise CommandError("대기 중인 굴림이 없어요. [수동 굴림 추가]를 쓸 수 있어요.")
        head = st["queue"][0]
        redo = self._pending_redo_outcome()
        piece = st["turn"] % max(1, len(st["positions"]))
        if redo is not None:                       # 취소한 굴림 재현 — 기록된 결과 그대로
            outcome = ({"special": redo["special"], "mode": redo.get("mode", "move")}
                       if redo.get("special") else {"value": int(redo["value"])})
        else:
            outcome = self._roll_face()            # 커스텀 주사위 (기본 1~6 균등)
        if outcome.get("special"):
            # ★ 글자 항목 (fix27) — 말 이동 없음. 이동형은 칸 선택으로, 연출형은 완료 게이팅으로.
            data = {"id": head["id"], "nick": head["nick"], "special": outcome["special"],
                    "mode": outcome["mode"], "piece": piece, "ts": time.time()}
            if redo is not None:
                data["redone"] = True
            self.record("roll_consumed", data)
            return {"kind": "roll", "data": data}
        value = outcome["value"]
        x2 = bool(st["x2_pieces"][piece])
        moved = value * (2 if x2 else 1)
        direction = st["directions"][piece]
        segments = []
        path, cur = self._walk(st["positions"][piece], direction, moved)
        segments.append({"path": path, "to": cur})
        final = self._chain(cur, direction, segments)
        data = {
            "id": head["id"], "nick": head["nick"], "value": value, "moved": moved,
            "x2_used": x2, "piece": piece, "segments": segments, "final": final, "ts": time.time(),
        }
        if redo is not None:
            data["redone"] = True    # 취소했던 굴림의 재현임을 로그에 표시
        self.record("roll_consumed", data)
        return {"kind": "roll", "data": data}

    def cmd_choose(self, index: int) -> dict:
        st = self.state
        if st["phase"] != "await_choice":
            raise CommandError("지금은 칸 선택 상태가 아니에요.")
        index = int(index) % BOARD_N
        nick = st["pending"]["nick"] if st["pending"] else ""
        piece = int(st["pending"].get("piece", 0)) if st["pending"] else 0
        segments = [{"path": [index], "to": index, "jump": True}]
        final = self._chain(index, st["directions"][piece % len(st["directions"])], segments)
        data = {"nick": nick, "piece": piece, "segments": segments, "final": final, "ts": time.time()}
        self.record("tile_chosen", data)
        return {"kind": "chosen", "data": data}

    def cmd_defend(self) -> dict:
        st = self.state
        if st["phase"] not in ("effect", "await_choice"):
            raise CommandError("지금은 방어권을 쓸 벌칙이 없어요.")
        gp = int(st["pending"].get("piece", 0)) % max(1, len(st["piece_guards"]))
        if st["piece_guards"][gp] < 1:
            who = st["players"][gp] if gp < len(st["players"]) else "이 말"
            raise CommandError(f"{who}의 방어권이 없어요.")
        gifter = st["guard_gifters"][gp][-1] if st["guard_gifters"][gp] else None
        pend = st["pending"] or {}
        reduced = (float(pend.get("timer_minutes") or 0)
                   if pend.get("timer_started") and pend.get("timer_minutes") else 0)
        self.record("guard_used", {})
        pname = st["players"][gp] if gp < len(st["players"]) else ""
        return {"kind": "defended", "gifter": gifter, "piece_name": pname,
                "timer_reduced": reduced, "label": pend.get("label", "")}

    def cmd_undo(self) -> dict:
        undone = {e["data"]["target_seq"] for e in self.events if e["type"] == "undo"}
        target = next(
            (e for e in reversed(self.events)
             if e["type"] in UNDOABLE and e["seq"] not in undone),
            None,
        )
        if target is None:
            raise CommandError("되돌릴 이벤트가 없어요.")
        self.record("undo", {"target_seq": target["seq"], "target_type": target["type"]})
        return {"kind": "undone", "target_type": target["type"]}

    def cmd_add_manual(self) -> None:
        self._require_running()
        ts = time.time()
        self.record("manual_roll_added", {"id": f"manual-{ts:.3f}", "ts": ts})

    def cmd_timer_start(self) -> dict:
        st = self.state
        pend = st["pending"]
        if not pend or not pend.get("timer_minutes"):
            raise CommandError("타이머 칸이 아니에요.")
        if pend.get("timer_started"):
            raise CommandError("이 칸의 타이머는 이미 시작했어요.")
        minutes = float(pend["timer_minutes"])
        piece = int(pend.get("piece", 0))
        now = time.time()
        self.record("timer_started", {"label": pend["label"], "minutes": minutes,
                                      "at": now, "piece": piece})
        tm = next((t for t in st["timers"]
                   if t["label"] == pend["label"] and int(t.get("piece", 0)) == piece), None)
        ends_at = tm["ends_at"] if tm else now + minutes * 60
        stacked = bool(tm and ends_at > now + minutes * 60 + 1)
        return {"kind": "timer", "label": pend["label"], "ends_at": ends_at, "stacked": stacked}

    def cmd_assign_guard(self, gift_id: str, piece: int) -> dict:
        """방어권 지정 (fix13): 지정 대기 방어권을 말에게 귀속 — 큐와 무관, 즉시."""
        self._require_running()
        st = self.state
        entry = next((g for g in st["unassigned_guards"] if g["id"] == str(gift_id)), None)
        if entry is None:
            raise CommandError("지정 대기 중인 방어권이 아니에요.")
        piece = int(piece)
        if not (0 <= piece < len(st["players"])):
            raise CommandError("없는 말이에요.")
        self.record("guard_assigned", {"gift_id": str(gift_id), "piece": piece})
        return {"kind": "assigned", "nick": entry["nick"], "piece": piece,
                "name": st["players"][piece]}

    def cmd_join(self, name: str) -> None:
        """중도 참여 (fix10): 진행 중에 새 말 합류 — 출발 칸, 차례 맨 뒤."""
        self._require_running()
        if len(self.state["players"]) >= 6:
            raise CommandError("말은 최대 6개까지예요.")
        name = str(name).strip()[:8] or f"말{len(self.state['players']) + 1}"
        self.record("player_joined", {
            "name": name,
            "start_index": int(self.config.get("start_index", 0)),
            "direction": int(self.config.get("direction", 1)),
        })

    def _require_running(self) -> None:
        if not self.state["running"]:
            raise CommandError("게임 시작 전이에요. 조작앱에서 [게임 시작]을 눌러 주세요.")

    # ---------------------------------------------------------- 외부 노출 상태
    def board_locked(self) -> bool:
        """진행 중 잠금 (명세 10): 게임 진행 중엔 편집 차단. 일시정지 중엔 허용."""
        return self.state["running"] and not self.state["paused"]

    def public_state(self) -> dict:
        st = self.state
        now = time.time()
        timers = sorted(
            (tm for tm in st["timers"] if tm["ends_at"] > now),
            key=lambda tm: tm["ends_at"],
        )
        return {
            "running": st["running"],
            "paused": st["paused"],
            "phase": st["phase"],
            "players": st["players"],
            "positions": st["positions"],
            "turn": st["turn"],
            "position": st["positions"][st["turn"] % max(1, len(st["positions"]))],
            "directions": st["directions"],
            "x2_pieces": st["x2_pieces"],
            "piece_guards": st["piece_guards"],
            "unassigned_guards": st["unassigned_guards"],
            "counters": st["counters"],
            "queue": [
                {"nick": q["nick"], "amount": q["amount"], "rolls_left": q["rolls_left"]}
                for q in st["queue"]
            ],
            "queue_rolls": sum(q["rolls_left"] for q in st["queue"]),
            "last_rolls": st["last_rolls"],
            "pending": st["pending"],
            "timers": timers,
            "board_locked": self.board_locked(),
            "statusbar_visible": bool(self.config.get("statusbar_visible", True)),
        }
