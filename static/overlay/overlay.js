/* 오버레이 클라이언트.
   연출 순서 (명세 8.4 확정):
   !다음 → 주사위 흔들림+숫자 롤링(약 0.8s) → 값 확정
        → 말 한 칸씩 통통 점프 (가운데 가로지르기 금지 = 둘레 경로만)
        → 도착 칸 글로우 + 전광판 갱신 → 칸 효과 표시 → 완료 대기 */
(function () {
  "use strict";

  // ---------------- 보드 좌표 (9×6 둘레형 26칸 자동 계산 — 목업 v4 로직) ----------------
  const W = 1920, H = 1080;
  const COLS = 9, ROWS = 6, CORNER = 1.5, GAP = 12, PADX = 16, PADY = 32; // 상하 여백 2배 (fix10)
  const uw = (W - PADX * 2 - GAP * (COLS - 1)) / (COLS - 2 + 2 * CORNER);
  const uh = (H - PADY * 2 - GAP * (ROWS - 1)) / (ROWS - 2 + 2 * CORNER);
  const cw = uw * CORNER, ch = uh * CORNER;
  const xs = [PADX]; for (let c = 1; c < COLS; c++) xs.push(xs[c - 1] + (c === 1 ? cw : uw) + GAP);
  const ys = [PADY]; for (let r = 1; r < ROWS; r++) ys.push(ys[r - 1] + (r === 1 ? ch : uh) + GAP);
  const colW = c => (c === 0 || c === COLS - 1) ? cw : uw;
  const rowH = r => (r === 0 || r === ROWS - 1) ? ch : uh;
  // 둘레 순서: 좌하단 → 아랫줄 → 우측 위로 → 윗줄 ← → 좌측 아래로
  const pos = [];
  for (let c = 0; c < COLS; c++) pos.push([c, ROWS - 1]);
  for (let r = ROWS - 2; r >= 1; r--) pos.push([COLS - 1, r]);
  for (let c = COLS - 1; c >= 0; c--) pos.push([c, 0]);
  for (let r = 1; r <= ROWS - 2; r++) pos.push([0, r]);

  const CAT_COLOR = { drink: "--pink", move: "--mint", save: "--peach", guard: "--sky", mission: "--lav" };

  // ── 스티커·말 꾸미기 (fix16) — control.js와 동일 로직 (표시 전용)
  const stickerHtml = s => {
    const sc = s.scale && Number(s.scale) !== 1 ? ` style="--sc:${Number(s.scale)}"` : "";
    const slot = (s.slot || "a").replace(/[^abc]/g, "a");
    if (s.img) return `<img class="stk ${slot}" src="/userstickers/${encodeURIComponent(s.img)}"${sc} alt="">`;
    return `<svg class="stk ${slot}" viewBox="0 0 64 64"${sc}><use href="#s-${String(s.id).replace(/[^\w-]/g, "")}"/></svg>`;
  };
  let pieceStyles = [];
  const pcol = i => {
    const s = pieceStyles[i];
    return (s && Number.isInteger(Number(s.color))) ? ((Number(s.color) % 6) + 6) % 6 : i % 6;
  };
  const pface = (i, name) => {
    const s = pieceStyles[i];
    return (s && s.face) ? s.face : (name || "?").charAt(0);
  };
  const pimg = i => {
    const s = pieceStyles[i];
    return (s && s.img) ? "/userstickers/" + encodeURIComponent(s.img) : null;
  };

  const $ = id => document.getElementById(id);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = s => String(s ?? "").replace(/[&<>"']/g,
    m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  // 부분 소스 모드 (fix11): /overlay?part=board|scoreboard|timers|status|toast
  // part 없음 = 전체 합본(기존). board 제외 단독 파트는 좌상단 원본 크기 위젯으로.
  const PARTS = (new URLSearchParams(location.search).get("part") || "")
    .split(",").map(s => s.trim()).filter(Boolean);
  const WIDGET = PARTS.length > 0 && !PARTS.includes("board");
  if (PARTS.length) {
    document.body.classList.add("partial");
    if (WIDGET) document.body.classList.add("widget");
    PARTS.forEach(pt => document.body.classList.add("part-" + pt));
  }
  // fix25: 파츠별 설계 크기 — 조작앱 OBS 목록의 권장 크기와 반드시 일치 (두 곳 세트!)
  // 권장 크기로 소스를 만들면 1:1, 다른 크기면 비율 유지하며 자동 확대/축소.
  const PART_BOX = { scoreboard: [560, 760], timers: [420, 420], status: [1200, 90],
                     toast: [760, 120], minigame: [880, 560] };
  let BOX_W = W, BOX_H = H;
  if (WIDGET) {
    const boxes = PARTS.map(pt => PART_BOX[pt]).filter(Boolean);
    if (boxes.length) {
      BOX_W = Math.max(...boxes.map(b => b[0]));
      BOX_H = Math.max(...boxes.map(b => b[1]));
    }
  }

  let board = [];
  let rects = [];
  let latestState = null;
  let busy = false;          // 연출 중엔 상태 반영 보류
  let pendingState = null;
  let pieceEls = [];         // 말 요소 목록 (다인 지원 — fix8)
  let playersKey = "";

  // 칸별 글씨 색 (fix24) — #rgb/#rrggbb만 허용, 그 외엔 기본색 (표시 전용 — 엔진·이벤트 무관)
  const inkStyle = t => (t && typeof t.label_color === "string" && /^#[0-9a-fA-F]{3,8}$/.test(t.label_color))
    ? ` style="color:${t.label_color}"` : "";

  // ---------------- 보드 렌더 ----------------
  function renderBoard() {
    const wrap = $("board");
    wrap.innerHTML = "";
    rects = [];
    board.forEach((tile, i) => {
      const [c, r] = pos[i];
      const corner = (c === 0 || c === COLS - 1) && (r === 0 || r === ROWS - 1);
      const el = document.createElement("div");
      el.className = "tile" + (corner ? " corner" : "");
      el.style.left = xs[c] + "px";
      el.style.top = ys[r] + "px";
      el.style.width = colW(c) + "px";
      el.style.height = rowH(r) + "px";
      const catBar = tile.category && CAT_COLOR[tile.category]
        ? `<span class="cat" style="background:var(${CAT_COLOR[tile.category]})"></span>` : "";
      const stickers = (tile.stickers || []).map(stickerHtml).join("");
      const ink = inkStyle(tile);  // fix24: 칸별 글씨 색 (표시 전용)
      el.innerHTML = `${catBar}${stickers}<span class="lb disp"${ink}>${esc(tile.label)}</span>`;  // 부제 제거 — 큰 글씨 하나 (fix10)
      wrap.appendChild(el);
      rects.push({ cx: xs[c] + colW(c) / 2, cy: ys[r] + rowH(r) / 2, row: r, el });
    });
  }

  function buildPieces(players) {
    const key = JSON.stringify([players || [], pieceStyles]);  // 색·이모지 바뀌어도 다시 그림 (fix16)
    if (key === playersKey) return;
    playersKey = key;
    const box = $("pieces");
    box.innerHTML = "";
    pieceEls = (players || []).map((name, i) => {
      const el = document.createElement("div");
      el.className = "piece p" + pcol(i);
      const img = pimg(i);
      el.innerHTML = img
        ? `<div class="body"><img src="${img}" alt=""></div>`
        : `<div class="body"><span class="ch disp">${esc(pface(i, name))}</span></div>`;
      box.appendChild(el);
      return el;
    });
  }

  function placePiece(pi, tileIdx, highlight) {
    const rect = rects[tileIdx];
    const el = pieceEls[pi];
    if (!rect || !el) return;
    const n = pieceEls.length;
    const spread = n > 1 ? Math.min(34, 140 / (n - 1)) : 0;  // 인원 많으면 간격 자동 축소
    const dx = (pi - (n - 1) / 2) * spread;
    el.style.left = (rect.cx + dx) + "px";
    el.style.top = (rect.cy + rowH(rect.row) * 0.16) + "px";
    if (highlight) setGlow(tileIdx);
  }

  function setGlow(i) {
    rects.forEach(r => r.el.classList.remove("current"));
    if (rects[i]) rects[i].el.classList.add("current");
  }

  // ---------------- 상태 반영 ----------------
  function applyState(s) {
    latestState = s;
    if (busy) { pendingState = s; return; }
    buildPieces(s.players || ["?"]);
    $("pieces").style.display = s.running ? "" : "none";
    $("scoreboard").hidden = !s.running;
    const positions = s.positions || [s.position];
    positions.forEach((tileIdx, pi) => placePiece(pi, tileIdx, false));
    const n = positions.length;
    const lastMoved = n > 1 ? (s.turn - 1 + n) % n : 0;  // 직전에 움직인 말의 칸에 글로우
    setGlow(positions[lastMoved]);

    $("sbQueue").textContent = `대기 ${s.queue_rolls}건`;

    const hist = $("sbHist");
    // fix36: 오버레이 기록은 최신 1건만 — 전광판 세로 상한 확보 (조작앱은 계속 2건 표시)
    hist.innerHTML = s.last_rolls.slice(0, 1)
      .map(r => `<span>${r.ch ? `[${esc(r.ch)}] ` : ""}<b>${esc(r.nick)}</b> · ${r.value} → ${esc(r.label)}</span>`)
      .join("");
    // fix36: 칸 효과가 떠 있는 동안엔 기록 숨김 + 주사위 축소(포커스 모드) — 아래줄·상태바 침범 방지 (검수)
    const fxOn = !!(s.pending && (s.phase === "effect" || s.phase === "await_choice"));
    hist.style.display = (s.last_rolls.length && !fxOn) ? "" : "none";
    $("scoreboard").classList.toggle("with-fx", fxOn);

    // 칸 효과 표시 (수동 게이팅 대기)
    const fx = $("sbEffect");
    if (s.pending && (s.phase === "effect" || s.phase === "await_choice")) {
      fx.hidden = false;
      fx.classList.remove("defended");
      $("fxRule").textContent = (s.pending.special ? "★ " : "") + (s.pending.rule_text || s.pending.label);
      const flush = s.pending.flush;
      $("fxFlush").hidden = !(flush && flush.amount > 0);
      if (flush && flush.amount > 0) {
        const who = (s.players || []).length > 1
          ? `${s.players[s.pending.piece || 0]} 독박! ` : "";
        $("fxFlush").textContent = `${who}${flush.amount}잔 청산!`;
      }
      $("fxHint").textContent = s.phase === "await_choice" ? "이동할 칸 선택 대기 중…" : "완료 대기 중";
    } else {
      fx.hidden = true;
    }

    // 미니 상태바 (작게 · on/off — 명세 8.3)
    const bar = $("statusbar");
    if (s.running && s.statusbar_visible) {
      bar.hidden = false;
      bar.classList.toggle("dense", (s.players || []).length >= 4);   // fix36: 다인 컴팩트
      const many = (s.players || []).length > 1;
      // fix21: 시청자용 현황판 — 말 색 점 + 방어권을 방패 아이콘으로 그대로 보여준다
      // fix35: 1인 모드 방어권은 아래 전용 줄로 (시청자 가독성 피드백) · 다인 표시는 기존 유지(재피드백 예정)
      const per = (s.players || []).map((name, i) => {
        const dir = (s.directions || [])[i] >= 0 ? "▶" : "◀";
        const x2 = (s.x2_pieces || [])[i] ? " <b>×2</b>" : "";
        const g = (s.piece_guards || [])[i] || 0;
        const sh = many && g > 0 ? ` <span class="sh">${g <= 3 ? "🛡️".repeat(g) : `🛡️×${g}`}</span>` : "";  // VS16 — 컬러 이모지 강제
        const img = pimg(i);
        const dot = img
          ? `<i class="dot pimgbg" style="background-image:url('${img}')"></i>`
          : `<i class="dot p${pcol(i)}"></i>`;
        const label = many ? `${esc(name)} ` : "";
        return `<span class="pl">${dot}${label}${dir}${x2}${sh}</span>`;
      }).join("");
      // fix35: 안주 표시 제거 — 안주 칸은 상호작용 없는 쉬어가는 칸 (곡천 확정)
      // fix35: 1인 모드 쉴드 = 잔 적립과 같은 결의 전용 표기
      const shield = !many
        ? `<span>🛡\uFE0F 쉴드 현재 <b>${(s.piece_guards || [])[0] || 0}</b>개</span>` : "";  // VS16 — 컬러 이모지 강제 (fix21 교훈)
      bar.innerHTML = `<span>🍺 현재 <b>${s.counters["적립"] ?? 0}</b>잔 적립중</span>` + shield + per;
    } else {
      bar.hidden = true;
    }

    renderTimers(s.timers || []);
  }

  // ---------------- 타이머 독 (여러 개 동시, 같은 벌칙은 중첩됨 — fix8) ----------------
  let timerHandle = null;
  let liveTimers = [];
  function renderTimers(timers) {
    liveTimers = timers || [];
    clearInterval(timerHandle);
    drawTimers();
    if (liveTimers.length) timerHandle = setInterval(drawTimers, 500);
  }
  function drawTimers() {
    // fix21: 칩을 매번 새로 만들면 등장 애니메이션이 0.5초마다 재생돼 빤짝임 —
    // 타이머별로 칩을 유지하고 숫자만 제자리 갱신한다.
    const now = Date.now() / 1000;
    const act = liveTimers.filter(tm => tm.ends_at > now);
    const dock = $("timerdock");
    const players = (latestState && latestState.players) || [];
    const keyOf = tm => `${tm.piece || 0}|${tm.label}|${tm.ends_at}`;
    const wanted = new Set(act.map(keyOf));
    Array.from(dock.children).forEach(el => {
      if (!wanted.has(el.dataset.tkey)) el.remove();
    });
    act.forEach(tm => {
      let el = dock.querySelector(`[data-tkey="${CSS.escape(keyOf(tm))}"]`);
      if (!el) {
        const pi = tm.piece || 0;
        const img = pimg(pi);
        const who = players.length > 1
          ? (img ? `<i class="tp p${pcol(pi)} pimgbg" style="background-image:url('${img}')"></i>`
                 : `<i class="tp p${pcol(pi)}">${esc(pface(pi, players[pi]))}</i>`)
          : "";
        el = document.createElement("div");
        el.className = "timerchip";  // fix26: 전광판 스타일 — 라벨은 본문 폰트(주아), 숫자는 모노
        el.dataset.tkey = keyOf(tm);
        el.innerHTML = `<div class="fill"></div>${who}<span class="tlb"></span><b></b>`;
        el.querySelector(".tlb").textContent = tm.label;
        dock.appendChild(el);
      }
      const left = Math.max(0, Math.floor(tm.ends_at - now));
      el.querySelector("b").textContent =
        `${String(Math.floor(left / 60))}:${String(left % 60).padStart(2, "0")}`;
      // fix26: 배경 게이지 — 남은/전체 비율만큼 차 있음 (start 없는 옛 데이터는 3분 기준)
      const total = tm.ends_at - (typeof tm.start === "number" ? tm.start : tm.ends_at - 180);
      const frac = total > 0 ? Math.max(0, Math.min(1, (tm.ends_at - now) / total)) : 0;
      el.querySelector(".fill").style.width = (frac * 100) + "%";
      el.classList.toggle("warn", left <= 60 && left > 10);   // 1분 이하 — 호박색
      el.classList.toggle("crit", left <= 10);                // 10초 이하 — 빨강 (깜빡임 없이 색만)
    });
    dock.classList.toggle("compact", act.length > 6);  // fix26: 방식3 — 6개 초과 시 자동 축소
    if (!act.length) clearInterval(timerHandle);
  }
  // ---------------- 연출 ----------------
  function runRollAnim(anim) {
    busy = true;
    const die = $("sbDie");
    die.classList.remove("star");   // 직전 ★ 결과 흔적 제거 (fix27)
    $("scoreboard").hidden = false;
    $("sbEffect").hidden = true;
    $("sbNick").textContent = (anim.ch ? `[${anim.ch}] ` : "") + (anim.nick || "");  // fix37: 합방 채널 태그

    if (anim.special) {             // fix27: 커스텀 주사위 글자 항목 — 굴림 연출 후 ★ 결과
      die.classList.add("rolling");
      const spin2 = setInterval(() => { die.textContent = 1 + Math.floor(Math.random() * 6); }, 80);
      setTimeout(() => {
        clearInterval(spin2);
        die.classList.remove("rolling");
        die.classList.add("star");
        die.textContent = "★";
        $("sbDest").innerHTML = `★ <b>${esc(anim.special)}</b>!`;
        finishAnim();               // 말 이동 없음 — 이동형은 칸 선택 후 별도 연출로 이어짐
      }, reduced ? 150 : 800);
      return;
    }

    const pi = anim.piece || 0;
    const startHop = () => {
      const finalLabel = board[anim.final] ? board[anim.final].label : "";
      hopSegments(pi, anim.segments, () => {
        setGlow(anim.final);
        $("sbDest").innerHTML = `→ <b>${esc(finalLabel)}</b> 도착`;
        finishAnim();
      });
    };

    if (anim.value == null) {           // 칸 선택 이동 (주사위 연출 없음)
      $("sbDest").textContent = "칸 이동!";
      startHop();
      return;
    }
    die.classList.add("rolling");
    const spin = setInterval(() => { die.textContent = 1 + Math.floor(Math.random() * 6); }, 80);
    setTimeout(() => {                   // 약 0.8s 롤링 후 값 확정
      clearInterval(spin);
      die.classList.remove("rolling");
      die.textContent = anim.value;
      $("sbDest").innerHTML = anim.x2_used
        ? `굴림 <b>${anim.value}</b> ×2 = ${anim.moved}칸!`      // 옛 기록 재생 호환
        : anim.again
          ? `굴림 <b>${anim.value}</b> — 🎲 두배로! 같은 값 한 번 더`   // fix40
          : `굴림 <b>${anim.value}</b>`;
      startHop();
    }, reduced ? 150 : 800);
  }

  function hopSegments(pi, segments, done) {
    const el = pieceEls[pi];
    if (!el) { done(); return; }
    const steps = [];
    (segments || []).forEach(seg => seg.path.forEach(i => steps.push(i)));
    let k = 0;
    const step = () => {
      if (k >= steps.length) { done(); return; }
      el.classList.remove("hop"); void el.offsetWidth; el.classList.add("hop");
      placePiece(pi, steps[k], false);
      k += 1;
      setTimeout(step, reduced ? 40 : 240);
    };
    step();
  }

  function finishAnim() {
    busy = false;
    if (pendingState) { const s = pendingState; pendingState = null; applyState(s); }
  }

  // ---------------- 토스트 (방어권 선물/사용) ----------------
  let toastHandle = null;
  function showToast(text) {
    const toast = $("toast");
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(toastHandle);
    toastHandle = setTimeout(() => { toast.hidden = true; }, 3500);
  }

  function runAnim(anim) {
    if (anim.kind === "roll") runRollAnim(anim);
    else if (anim.kind === "guard_gift")
      showToast(`${anim.ch ? `[${anim.ch}] ` : ""}${anim.nick}님이 방어권을 선물!`);   // fix37
    else if (anim.kind === "guard_used") {
      showToast(anim.gifter ? `${anim.gifter}님의 방어권 사용!` : "방어권 사용!");
      const fx = $("sbEffect");
      fx.classList.add("defended");
      $("fxRule").textContent = "벌칙 무효!";
      $("fxHint").textContent = "";
    }
    // anim.kind === "timer" 는 state의 timer로 표시됨
  }

  // ---------------- 미니게임 창 (fix18 — 연출 전용) ----------------
  let mgPrevStage = null;
  let mgTimers = [];
  const mgClear = () => { mgTimers.forEach(clearTimeout); mgTimers = []; };
  const mgT = (fn, ms) => mgTimers.push(setTimeout(fn, ms));

  function ladderGeo(n) {
    const w = 700, top = 46, bottom = 320, left = 80;
    const colX = i => left + i * ((w - left * 2) / Math.max(1, n - 1));
    const rowY = r => top + (r + 0.5) * ((bottom - top) / 8);
    return { colX, rowY, top, bottom, w };
  }
  function ladderPathPts(g, geo) {
    const rset = new Set((g.rungs || []).map(([r, c]) => r + ":" + c));
    let col = g.pick;
    const pts = [[geo.colX(col), geo.top]];
    for (let r = 0; r < 8; r++) {
      const y = geo.rowY(r);
      if (rset.has(r + ":" + col)) { pts.push([geo.colX(col), y]); col += 1; pts.push([geo.colX(col), y]); }
      else if (rset.has(r + ":" + (col - 1))) { pts.push([geo.colX(col), y]); col -= 1; pts.push([geo.colX(col), y]); }
    }
    pts.push([geo.colX(col), geo.bottom]);
    return pts.map(p => p.join(",")).join(" ");
  }
  function renderMg(g) {
    const win = $("minigame");
    mgClear();
    if (!g || g.stage === "input" || g.stage === "ready") {
      win.hidden = !g;
      if (g) { $("mgwTitle").textContent = g.kind === "shuffle" ? "야바위" : "사다리타기";
               $("mgwBody").innerHTML = `<div class="mg-wait disp">준비 중…</div>`; }
      mgPrevStage = g && g.stage;
      return;
    }
    win.hidden = false;
    const body = $("mgwBody");
    if (g.kind === "ladder") {
      $("mgwTitle").textContent = "사다리타기";
      const geo = ladderGeo(g.count);
      let rails = "", rungs = "";
      for (let i = 0; i < g.count; i++)
        rails += `<line x1="${geo.colX(i)}" y1="${geo.top}" x2="${geo.colX(i)}" y2="${geo.bottom}" class="mg-rail"/>`;
      if (g.stage !== "setup" && g.rungs)
        g.rungs.forEach(([r, c]) => {
          rungs += `<line x1="${geo.colX(c)}" y1="${geo.rowY(r)}" x2="${geo.colX(c + 1)}" y2="${geo.rowY(r)}" class="mg-rung"/>`;
        });
      const tops = Array.from({ length: g.count }, (_, i) =>
        `<span class="mg-top disp${g.pick === i ? " on" : ""}" style="left:${geo.colX(i)}px">${i + 1}</span>`).join("");
      const bots = (g.results || []).map((r, i) =>
        `<span class="mg-bot${g.stage === "done" && g.result === i ? " hit" : ""}" style="left:${geo.colX(i)}px">${esc(r)}</span>`).join("");
      const path = g.stage === "done"
        ? `<polyline points="${ladderPathPts(g, geo)}" class="mg-path" pathLength="100"/>` : "";
      body.innerHTML = `${tops}<svg viewBox="0 0 700 330" class="mg-svg">${rails}${rungs}${path}</svg>${bots}`;
    } else {
      $("mgwTitle").textContent = "야바위";
      body.innerHTML = `<div class="mg-cups">` +
        [0, 1, 2].map(i => `<div class="mg-cup" id="mgCup${i}"><div class="cupbody"></div></div>`).join("") +
        `<div class="mg-ball" id="mgBall" hidden></div></div><div class="mg-wait disp" id="mgShMsg"></div>`;
      const slotX = s => 110 + s * 220;
      const posOf = [0, 1, 2];                     // posOf[컵]=현재 위치
      const place = () => [0, 1, 2].forEach(i => { $("mgCup" + i).style.left = slotX(posOf[i]) + "px"; });
      const cupAt = p => $("mgCup" + posOf.indexOf(p));
      const swapPos = (a, b) => { const i = posOf.indexOf(a), j = posOf.indexOf(b); posOf[i] = b; posOf[j] = a; };
      const ball = $("mgBall");
      const ballAt = p => { ball.style.left = (slotX(p) + 48) + "px"; };
      place();
      const POS = ["왼쪽", "가운데", "오른쪽"];
      if (g.stage === "pick" && mgPrevStage !== "pick") {
        ballAt(g.ball_start); ball.hidden = false;
        cupAt(g.ball_start).classList.add("lift");
        $("mgShMsg").textContent = `공을 잘 보세요…!`;
        mgT(() => {
          [0, 1, 2].forEach(i => $("mgCup" + i).classList.remove("lift"));
          ball.hidden = true;
          $("mgShMsg").textContent = "섞는 중…";
          (g.swaps || []).forEach((sw, k) => mgT(() => {
            swapPos(sw[0], sw[1]); place();
            if (k === (g.swaps.length - 1)) $("mgShMsg").textContent = "어느 컵일까요?";
          }, 600 + k * 460));
        }, 1500);
      } else if (g.stage === "done") {
        (g.swaps || []).forEach(sw => swapPos(sw[0], sw[1]));
        place();
        cupAt(g.pick).classList.add("lift");
        ballAt(g.ball);
        ball.hidden = g.pick !== g.ball;
        if (g.pick !== g.ball) mgT(() => { cupAt(g.ball).classList.add("lift"); ball.hidden = false; }, 900);
        $("mgShMsg").textContent = g.win ? `🎉 ${POS[g.pick]} — 공 찾았다!` : `꽝! 공은 ${POS[g.ball]} 컵`;
      } else {
        $("mgShMsg").textContent = "어느 컵일까요?";
      }
    }
    mgPrevStage = g.stage;
  }

  // ---------------- 팔레트 오버라이드 ----------------
  let appliedVars = [];   // fix27: 테마 전환 시 이전 오버라이드 청소용
  function applyPalette(palette) {
    appliedVars.forEach(k => document.documentElement.style.removeProperty(k));
    appliedVars = [];
    Object.entries(palette || {}).forEach(([k, v]) => {
      if (/^--[\w-]+$/.test(k)) {
        document.documentElement.style.setProperty(k, v);
        appliedVars.push(k);
      }
    });
  }
  // fix30: 합본 파츠 온오프 + 배경 표시 — 통합 소스에서만 (파츠 소스는 항상 그대로)
  const BG_FORCED = new URLSearchParams(location.search).get("bg") === "1";  // 위치 확인용은 계속 우선
  function applyOverlayCfg(parts, bg) {
    const off = k => parts && parts[k] === false;   // 값이 없으면 기본 표시
    ["board", "scoreboard", "timers", "status", "toast", "minigame"].forEach(k =>
      document.body.classList.toggle("off-" + k, off(k)));
    $("backdrop").hidden = !(BG_FORCED || (bg && PARTS.length === 0));  // 배경은 합본 전용
  }

  // fix27: 보드 테마(팔레트) + 폰트 + 크기 한 번에 적용 — 설정 창 [테마 설정]과 세트
  function applyBoardTheme(palette, fontStack, fontScale) {
    applyPalette(palette);
    const root = document.documentElement;
    if (fontStack) { root.style.setProperty("--board-font", fontStack); appliedVars.push("--board-font"); }
    if (fontScale) { root.style.setProperty("--board-fs", String(fontScale)); appliedVars.push("--board-fs"); }
  }

  // ---------------- WebSocket ----------------
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "init") {
        board = msg.board;
        pieceStyles = (msg.config || {}).piece_style || [];
        renderMg(msg.minigame || null);
        applyBoardTheme(msg.config.palette, msg.config.board_font_stack, msg.config.board_font_scale);
        applyOverlayCfg(msg.config.overlay_parts, msg.config.overlay_bg);   // fix30
        renderBoard();
        busy = false;
        applyState(msg.state);
      } else if (msg.type === "state") {
        applyState(msg.state);
      } else if (msg.type === "anim") {
        runAnim(msg.anim);
      } else if (msg.type === "minigame") {
        renderMg(msg.game);
      } else if (msg.type === "theme") {
        applyBoardTheme(msg.palette, msg.font_stack, msg.font_scale);   // fix27: 테마 즉시 반영
      } else if (msg.type === "overlay_cfg") {
        applyOverlayCfg(msg.parts, msg.bg);                             // fix30: 파츠·배경 즉시 반영
      } else if (msg.type === "board") {
        board = msg.board;
        renderBoard();
        if (latestState) { busy = false; applyState(latestState); }
      } else if (msg.type === "players_cfg") {
        if (msg.styles) pieceStyles = msg.styles;   // 말 색·이모지 즉시 반영 (fix16)
        if (latestState && !busy) applyState(latestState);
      }
    };
    ws.onclose = () => setTimeout(connect, 1500);  // 자동 재연결
  }
  connect();

  // ---------------- 스케일 ----------------
  // 합본·보드: OBS 1920×1080이면 1:1. 위젯 파츠(fix25): 권장 크기면 1:1, 그 외엔 비율 유지 자동 맞춤.
  function fit() {
    const scale = WIDGET
      ? Math.min(innerWidth / BOX_W, innerHeight / BOX_H)
      : Math.min(innerWidth / W, innerHeight / H);
    $("stage").style.transform = `scale(${scale})`;
  }
  addEventListener("resize", fit);
  fit();

  if (BG_FORCED) {
    $("backdrop").hidden = false;  // 위치 확인용 목업 배경 (?bg=1 — 설정과 무관하게 항상)
  }
})();
