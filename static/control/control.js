/* 조작 대시보드 (fix27)
   - 보드 미리보기 = 실시간 미러 + 칸 선택 리모컨 + (게임 전/일시정지) 칸 클릭 즉시 편집
   - 편집·설정은 오른쪽 슬라이드 서랍 (평소엔 완전히 숨김 — hidden 가드로 보장)
   - 대기 큐는 채팅창처럼 세로 로그형, 내용이 바뀔 때만 다시 그려 깜빡임 방지 */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g,
    m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

  // ---------------- 보드 좌표 (오버레이와 동일 — 9×6 둘레형 26칸) ----------------
  const W = 1920, H = 1080;
  const COLS = 9, ROWS = 6, CORNER = 1.5, GAP = 12, PADX = 16, PADY = 32; // 상하 여백 2배 (fix10)
  const uw = (W - PADX * 2 - GAP * (COLS - 1)) / (COLS - 2 + 2 * CORNER);
  const uh = (H - PADY * 2 - GAP * (ROWS - 1)) / (ROWS - 2 + 2 * CORNER);
  const cw = uw * CORNER, ch = uh * CORNER;
  const xs = [PADX]; for (let c = 1; c < COLS; c++) xs.push(xs[c - 1] + (c === 1 ? cw : uw) + GAP);
  const ys = [PADY]; for (let r = 1; r < ROWS; r++) ys.push(ys[r - 1] + (r === 1 ? ch : uh) + GAP);
  const colW = c => (c === 0 || c === COLS - 1) ? cw : uw;
  const rowH = r => (r === 0 || r === ROWS - 1) ? ch : uh;
  const pos = [];
  for (let c = 0; c < COLS; c++) pos.push([c, ROWS - 1]);
  for (let r = ROWS - 2; r >= 1; r--) pos.push([COLS - 1, r]);
  for (let c = COLS - 1; c >= 0; c--) pos.push([c, 0]);
  for (let r = 1; r <= ROWS - 2; r++) pos.push([0, r]);

  const CAT_COLOR = { drink: "--pink", move: "--mint", save: "--peach", guard: "--sky", mission: "--lav" };

  // ── 스티커 (fix16) ──────────────────────────────────────────
  // 기본 SVG 세트 (양쪽 HTML defs와 세트 — 추가 시 3곳 동기화: control/overlay defs + 이 목록)
  const STICKERS = [
    ["flag", "깃발"], ["dice", "주사위"], ["beer", "맥주"], ["wine", "와인"], ["halfglass", "반잔"],
    ["soju", "초록병"], ["skewer", "꼬치"], ["cheese", "치즈"], ["chicken", "치킨"], ["fork", "포크"],
    ["heart", "하트"], ["lips", "입술"], ["bubble", "말풍선"], ["mic", "마이크"], ["ban", "금지"],
    ["uturn", "유턴"], ["back", "되감기"], ["plane", "비행기"], ["wing", "날개"], ["ladder", "사다리"],
    ["receipt", "영수증"], ["bed", "침대"], ["shield", "방패"], ["clock", "시계"], ["crown", "왕관"],
    ["star", "별"], ["gift", "선물"], ["cake", "케이크"], ["clover", "클로버"], ["note", "음표"],
    ["bomb", "폭탄"], ["moon", "달"],
  ];
  // 스티커 1개 → HTML (SVG 기본 세트 또는 업로드 그림). scale은 CSS 변수 --sc로.
  const stickerHtml = s => {
    const sc = s.scale && Number(s.scale) !== 1 ? ` style="--sc:${Number(s.scale)}"` : "";
    const slot = esc(s.slot || "a");
    if (s.img) return `<img class="stk ${slot}" src="/userstickers/${encodeURIComponent(s.img)}"${sc} alt="">`;
    return `<svg class="stk ${slot}" viewBox="0 0 64 64"${sc}><use href="#s-${esc(s.id)}"/></svg>`;
  };

  // ── 말 꾸미기 (fix16) — 표시 전용, 엔진·이벤트와 무관 ──────────
  let pieceStyles = [];   // [{color: 0~5, face: "이모지/글자"}] — config.piece_style
  const pcol = i => {
    const s = pieceStyles[i];
    return (s && Number.isInteger(Number(s.color))) ? ((Number(s.color) % 6) + 6) % 6 : i % 6;
  };
  const pface = (i, name) => {
    const s = pieceStyles[i];
    return (s && s.face) ? s.face : (name || "?").charAt(0);
  };
  // 말 그림 (fix17) — 있으면 이모지/글자 대신 이미지로 표시
  const pimg = i => {
    const s = pieceStyles[i];
    return (s && s.img) ? "/userstickers/" + encodeURIComponent(s.img) : null;
  };
  // 작은 동그라미(상태 칩·타이머·방어권 버튼)용 — 이미지면 배경으로 깔고 글자는 생략
  const pdot = (i, cls, text) => {
    const img = pimg(i);
    return img
      ? `<i class="${cls} p${pcol(i)} pimgbg" style="background-image:url('${img}')"></i>`
      : `<i class="${cls} p${pcol(i)}">${text}</i>`;
  };

  let board = [];
  let rects = [];
  let state = null;
  let msgHandle = null;
  let editIndex = null;
  let lastQueueJson = "";
  let lastGaJson = "";

  function setPill(id, cls, text) {
    const el = $(id);
    el.className = "pill " + cls;
    el.innerHTML = '<i class="dot"></i><span>' + esc(text) + "</span>";
  }

  function showMsg(text) {
    $("msg").textContent = text || "";
    clearTimeout(msgHandle);
    if (text) msgHandle = setTimeout(() => { $("msg").textContent = ""; }, 4000);
  }

  async function post(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await res.json();
      if (!data.ok && data.msg) showMsg(data.msg);
      return data;
    } catch (e) {
      showMsg("서버에 연결할 수 없어요.");
      return { ok: false };
    }
  }
  const cmd = (name, extra) => post("/api/cmd", Object.assign({ cmd: name }, extra || {}));

  // ══ 그림 업로드 공통 (fix41) — 자동 축소 후 전송 ══════════════════════════
  // 왜 축소하나: OBS 브라우저 소스는 그림을 '표시 크기'가 아니라 '원본 픽셀 수'만큼
  // 메모리에 펼쳐서 들고 있다. 화면에 실제로 그려지는 크기는 판 장식이 최대 177px
  // (모서리 칸 136px × 크게 1.3), 말 그림이 48px 이라, 원본을 그대로 저장하면
  // 화질 이득은 0인데 오버레이 메모리 사용량만 커진다.
  // 그래서 업로드 직전에 긴 변을 IMG_MAX_EDGE 로 줄여서 서버로 보낸다.
  //  · 긴 변이 이미 IMG_MAX_EDGE 이하면 재인코딩하지 않고 원본 그대로 보낸다 (화질 손실 방지)
  //  · 출력은 PNG 고정 — 판 장식의 투명 배경이 유지돼야 한다 (JPEG 로 바꾸면 배경이 흰색이 됨)
  // fix42: 512 → 1024. 칸 통이미지가 2배 규격으로 최대 778px(모서리 칸 캔버스)까지 오기 때문.
  // 그림 보관함이 스티커·말 그림·칸 그림 공용이라 상한도 하나로 둔다.
  // 상한이 있는 한 최악값이 묶인다 — 26칸 전부 1024로 채워도 약 86MB (상한이 없으면 GB 단위).
  const IMG_MAX_EDGE = 1024;                   // 축소 기준: 긴 변 픽셀 수
  const IMG_MAX_BYTES = 20 * 1024 * 1024;      // 원본 파일 용량 상한 (서버 main.py 와 같은 값)

  function readAsDataURL(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(new Error("read"));
      r.readAsDataURL(file);
    });
  }

  function decodeImage(dataUrl) {
    return new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error("decode"));
      im.src = dataUrl;
    });
  }

  // File → { name, data }  (필요할 때만 축소된 PNG 로 바꿔서 돌려준다)
  async function shrinkForUpload(file) {
    const src = await readAsDataURL(file);
    const im = await decodeImage(src);
    const long = Math.max(im.naturalWidth || 0, im.naturalHeight || 0);
    if (!long) throw new Error("decode");
    if (long <= IMG_MAX_EDGE) return { name: file.name, data: src };   // 원본 유지
    const k = IMG_MAX_EDGE / long;
    const cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.round(im.naturalWidth * k));
    cv.height = Math.max(1, Math.round(im.naturalHeight * k));
    const ctx = cv.getContext("2d");
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(im, 0, 0, cv.width, cv.height);
    return { name: file.name.replace(/\.[^.\\/]*$/, "") + ".png", data: cv.toDataURL("image/png") };
  }

  // 업로드 3곳(칸 스티커·이미지 관리·말 그림) 공용. 성공하면 서버 응답, 실패하면 null.
  async function uploadImageFile(file) {
    if (file.size > IMG_MAX_BYTES) {
      showMsg("그림이 20MB를 넘어요. 조금 줄여서 올려 주세요.");
      return null;
    }
    let up;
    try {
      up = await shrinkForUpload(file);
    } catch (e) {
      showMsg("그림 파일을 읽지 못했어요. PNG·JPG·WEBP 파일인지 확인해 주세요.");
      return null;
    }
    const res = await post("/api/stickers/upload", { name: up.name, data: up.data });
    return res && res.ok ? res : null;     // 실패 안내 문구는 post() 가 이미 띄운다
  }

  const canEdit = () => !state || !state.running || state.paused;  // 서버 잠금 규칙과 동일

  // 칸별 글씨 색 (fix24) — #rgb/#rrggbb만 허용, 그 외엔 기본색 (표시 전용)
  const INK_OK = v => typeof v === "string" && /^#[0-9a-fA-F]{3,8}$/.test(v);
  const inkStyle = t => (t && INK_OK(t.label_color)) ? ` style="color:${t.label_color}"` : "";

  // ── 칸 통이미지 (fix42) — overlay.js 와 같은 규격이어야 미리보기가 실제 화면과 일치한다 ──
  // 캔버스 = 칸 크기 + 사방 TILE_IMG_MARGIN. .tile 이 border-box 라 절대배치 자식의 기준은
  // padding box(테두리 3px 안쪽)여서, 테두리까지 덮으려면 -(여백 + 3) 만큼 민다.
  const TILE_IMG_MARGIN = 60;
  const TILE_BORDER = 3;
  let tileSkin = "basic";                       // "basic" | "image"

  // 칸 종류별 권장 캔버스 (칸 편집 서랍 안내용) — [칸w, 칸h, 캔버스w, 캔버스h]
  function tileSpec(i) {
    const [c, r] = pos[i];
    const w = Math.round(colW(c)), h = Math.round(rowH(r));
    const corner = (c === 0 || c === COLS - 1) && (r === 0 || r === ROWS - 1);
    const kind = corner ? "모서리 칸" : (r === 0 || r === ROWS - 1) ? "위·아래 줄 칸" : "좌·우 줄 칸";
    return { w, h, cw: w + TILE_IMG_MARGIN * 2, chh: h + TILE_IMG_MARGIN * 2, kind };
  }

  function bgImgHtml(tile, w, h) {
    if (!tile.bg_img) return "";
    const m = TILE_IMG_MARGIN, off = -(m + TILE_BORDER);
    return `<img class="bgimg" src="/userstickers/${encodeURIComponent(tile.bg_img)}" alt=""` +
           ` style="left:${off}px;top:${off}px;width:${w + m * 2}px;height:${h + m * 2}px">`;
  }

  // 칸끼리 겹치는 순서 — 화면 아래쪽 칸이 위, 같은 줄이면 오른쪽 칸이 위 (곡천 확정).
  // 기본 방식일 땐 안 건다 (지금까지의 그림 순서 유지).
  function applyTileOrder() {
    if (tileSkin !== "image") { rects.forEach(rc => { rc.el.style.zIndex = ""; }); return; }
    rects.map((rc, i) => i)
      .sort((a, b) => (rects[a].cy - rects[b].cy) || (rects[a].cx - rects[b].cx))
      .forEach((i, n) => { rects[i].el.style.zIndex = String(n + 1); });
  }

  function applyTileSkin(skin) {
    tileSkin = skin === "image" ? "image" : "basic";
    $("stage").classList.toggle("skin-image", tileSkin === "image");
    document.querySelectorAll("#skinSeg button").forEach(b =>
      b.classList.toggle("on", b.dataset.skin === tileSkin));
    if (rects.length) applyTileOrder();
  }

  // ---------------- 보드 미리보기 ----------------
  function renderBoard() {
    const wrap = $("board");
    wrap.innerHTML = "";
    rects = [];
    board.forEach((tile, i) => {
      const [c, r] = pos[i];
      const corner = (c === 0 || c === COLS - 1) && (r === 0 || r === ROWS - 1);
      const w = colW(c), h = rowH(r);
      const el = document.createElement("div");
      el.className = "tile" + (corner ? " corner" : "") + (tile.bg_img ? " hasimg" : "");
      el.dataset.idx = i;
      el.style.left = xs[c] + "px";
      el.style.top = ys[r] + "px";
      el.style.width = w + "px";
      el.style.height = h + "px";
      const catBar = tile.category && CAT_COLOR[tile.category]
        ? `<span class="cat" style="background:var(${CAT_COLOR[tile.category]})"></span>` : "";
      const stickers = (tile.stickers || []).map(stickerHtml).join("");
      const ink = inkStyle(tile);  // fix24: 칸별 글씨 색 (표시 전용)
      el.innerHTML = `${bgImgHtml(tile, w, h)}${catBar}${stickers}` +
                     `<span class="lb disp"${ink}>${esc(tile.label)}</span>`;  // 부제 제거 — 큰 글씨 하나 (fix10)
      wrap.appendChild(el);
      rects.push({ cx: xs[c] + w / 2, cy: ys[r] + h / 2, row: r, el });
    });
    applyTileOrder();
    if (state) placePieces(state);
    if (editIndex !== null && rects[editIndex]) rects[editIndex].el.classList.add("editing");
  }

  let pieceEls = [];
  let playersKey = "";
  function buildPieces(players) {
    const key = JSON.stringify([players || [], pieceStyles]);  // 스타일 바뀌어도 다시 그림 (fix16)
    if (key === playersKey) return;
    playersKey = key;
    const box = $("pieces");
    box.innerHTML = "";
    pieceEls = (players || []).map((name, i) => {
      const el = document.createElement("div");
      el.className = "piece p" + pcol(i);
      const img = pimg(i);
      el.innerHTML = img ? `<img src="${img}" alt="">`
                         : `<span class="disp">${esc(pface(i, name))}</span>`;
      box.appendChild(el);
      return el;
    });
  }

  function placePieces(s) {
    const positions = s.positions || [s.position];
    const n = positions.length;
    positions.forEach((tileIdx, pi) => {
      const rect = rects[tileIdx];
      const el = pieceEls[pi];
      if (!rect || !el) return;
      const spread = n > 1 ? Math.min(34, 140 / (n - 1)) : 0;
      const dx = (pi - (n - 1) / 2) * spread;
      el.style.left = (rect.cx + dx) + "px";
      el.style.top = (rect.cy + rowH(rect.row) * 0.16) + "px";
    });
    const lastMoved = n > 1 ? (s.turn - 1 + n) % n : 0;
    rects.forEach(r => r.el.classList.remove("current"));
    const cur = rects[positions[lastMoved]];
    if (cur) cur.el.classList.add("current");
  }

  function fit() {
    const wrap = $("boardWrap");
    const scale = Math.min(wrap.clientWidth / W, wrap.clientHeight / H);
    const st = $("stage");
    st.style.transform = `scale(${scale})`;
    st.style.left = (wrap.clientWidth - W * scale) / 2 + "px";
    st.style.top = (wrap.clientHeight - H * scale) / 2 + "px";
  }
  addEventListener("resize", fit);
  // 왼쪽 열 크기가 변하면 미리보기를 다시 맞춤 (fix14 도입, fix15에서도 유지 — 창 크기 변화 등 안전망)
  new ResizeObserver(fit).observe($("boardWrap"));

  // ---------------- 상태 렌더 ----------------
  function render() {
    if (!state) return;
    const s = state;

    $("btnStart").textContent = s.running ? "새 회차 시작" : "게임 시작";
    $("btnPause").textContent = s.paused ? "재개" : "일시정지";
    $("btnPause").disabled = !s.running;

    const next = $("btnNext");
    if (!s.running) { next.textContent = "게임 시작 대기"; next.disabled = true; }
    else if (s.paused) { next.textContent = "일시정지 중"; next.disabled = true; }
    else if (s.phase === "effect") { next.textContent = "효과 완료 ✓"; next.disabled = false; }
    else if (s.phase === "await_choice") { next.textContent = "보드에서 칸을 눌러 주세요"; next.disabled = true; }
    else { next.textContent = `다음 굴림 ▶ (대기 ${s.queue_rolls})`; next.disabled = s.queue_rolls === 0; }

    const pendingOn = s.pending && (s.phase === "effect" || s.phase === "await_choice");
    const pt = $("pendingText");
    if (pendingOn) {
      const p = s.pending;
      let flush = "";
      if (p.flush && p.flush.amount > 0) {
        const who = (s.players || []).length > 1 ? `${s.players[p.piece || 0]} 독박! ` : "";
        flush = ` — ${who}${p.flush.amount}잔 청산!`;
      }
      if (p.special) {   // fix27: 커스텀 주사위 글자 항목 (★)
        pt.textContent = `★ ${p.label} — ${p.nick || "?"}님 당첨! ` +
          (s.phase === "await_choice" ? "보드에서 이동할 칸을 눌러 주세요"
                                      : "진행 후 [효과 완료 ✓]를 눌러 주세요");
      } else {
        // fix32: 설명 없는 칸에서 "라벨 · " 꼬리 제거
        pt.textContent = (p.rule_text ? `${p.label} · ${p.rule_text}` : p.label) + flush;
      }
      pt.className = "sv";
      $("btnTimer").hidden = !p.timer_minutes;
      if (p.timer_minutes) $("timerLabel").textContent = `타이머 시작 (${p.timer_minutes}분)`;
      $("btnMinigame").hidden = !p.minigame;   // 미니게임 칸에 도착했을 때만 (fix18)
      if (p.minigame) $("mgBtnLabel").textContent =
        p.minigame === "shuffle" ? "야바위 열기" : "사다리타기 열기";
    } else {
      pt.textContent = "주사위가 멈춘 칸의 효과가 여기에 떠요";
      pt.className = "sv dimtext";
      $("btnTimer").hidden = true;
      $("btnMinigame").hidden = true;
    }

    // 상태 스트립 — 공동 항아리(잔·안주) + 말별 칩(방향·×2·방어권) (fix13)
    $("stCredit").textContent = s.counters["적립"] ?? 0;
    // fix35: 안주 표시 제거 — 쉬어가는 칸이라 집계 표시는 불필요 (엔진 카운터는 무해하게 유지)
    const unassigned = s.unassigned_guards || [];
    $("stGuardChip").hidden = unassigned.length === 0;
    $("stGuard").textContent = unassigned.length;
    const many = (s.players || []).length > 1;
    $("stPieces").innerHTML = (s.players || []).map((name, i) => {
      const dir = (s.directions || [])[i] >= 0 ? "▶" : "◀";
      const x2 = (s.x2_pieces || [])[i] ? ' <span class="x2">×2</span>' : "";
      const g = (s.piece_guards || [])[i] || 0;
      const guard = g > 0 ? ` 🛡<b>${g}</b>` : "";
      const tag = many ? pdot(i, "", esc(pface(i, name))) : "";
      return `<span class="pc">${tag}${dir}${x2}${guard}</span>`;
    }).join("");

    // 방어권 지정 대기 — 큐와 무관하게 즉시 (fix13)
    const gaJson = JSON.stringify([unassigned, s.players]);
    if (gaJson !== lastGaJson) {
      lastGaJson = gaJson;
      $("guardAssign").innerHTML = unassigned.map(g =>
        `<div class="ga"><span class="ga-nick">💝 ${esc(g.nick)}님의 방어권</span><span class="ga-q">누구에게?</span>` +
        (s.players || []).map((nm, i) =>
          `<button class="ga-btn" data-gift="${esc(g.id)}" data-piece="${i}">${pdot(i, "", "")}${esc(nm)}</button>`
        ).join("") + `</div>`).join("");
    }
    $("stHist").innerHTML = s.last_rolls
      .map(r => `<span>${r.ch ? `[${esc(r.ch)}] ` : ""}<b>${esc(r.nick)}</b> · ${r.value ?? "선택"} → ${esc(r.label)}</span>`)
      .join("");

    // 대기 큐 — 내용이 바뀔 때만 다시 그림 (등장 애니메이션 중복 방지)
    const qJson = JSON.stringify(s.queue);
    if (qJson !== lastQueueJson) {
      lastQueueJson = qJson;
      const list = $("queueList");
      if (s.queue.length === 0) {
        list.innerHTML = '<li class="dim empty">아직 비어 있어요 — 후원이 오면 여기에 쌓여요</li>';
      } else {
        list.innerHTML = s.queue.map(q => {
          const initial = (q.nick || "?").trim().charAt(0) || "?";
          const ch = q.ch ? `<span class="qch">${esc(q.ch)}</span>` : "";   // fix37: 합방 채널 태그
          return `<li><span class="av">${esc(initial)}</span>${ch}<span class="nm">${esc(q.nick)}</span><b>${q.rolls_left}굴림</b></li>`;
        }).join("");
      }
    }
    $("queueTotal").textContent = [
      s.queue_rolls > 0 ? `총 ${s.queue_rolls}굴림` : "",
      unassigned.length > 0 ? `💝 지정 대기 ${unassigned.length}` : "",   // fix32
    ].filter(Boolean).join(" · ");

    const pg = s.piece_guards || [];
    const pendGuards = pendingOn ? (pg[s.pending.piece || 0] || 0) : 0;
    $("guardCount").textContent = pendingOn ? pendGuards : pg.reduce((a, b) => a + b, 0);
    $("btnDefend").disabled = !(pendingOn && pendGuards > 0);
    $("btnUndo").disabled = !s.running;
    $("btnAdd").disabled = !s.running;

    renderTimers();  // 타이머 시작·취소가 1초 기다리지 않고 즉시 반영되게 (fix14)

    // 사람 말 상태 문장 (인사해줘 2.0 방식)
    const sub = $("subline");
    if (!s.running) sub.textContent = "게임 시작 전이에요 · 칸을 눌러 보드를 꾸밀 수 있어요";
    else if (s.paused) sub.textContent = "일시정지 중이에요 · 지금은 보드를 수정할 수 있어요";
    else if (s.phase === "await_choice") sub.textContent = "이동할 칸을 고르는 중이에요";
    else if (s.phase === "effect") sub.textContent = "칸 효과 진행 중 — 다 하면 [효과 완료 ✓]를 눌러 주세요";
    else if (s.queue_rolls > 0) sub.textContent = `진행 중이에요 · 주사위 ${s.queue_rolls}개가 기다리고 있어요`;
    else sub.textContent = "진행 중이에요 · 후원을 기다리고 있어요";
    if (s.running && (s.players || []).length > 1 && !s.paused) {
      sub.textContent += ` · 다음 말: ${s.players[s.turn]}`;
    }

    // 말·글로우
    buildPieces(s.players || []);
    $("pieces").style.display = s.running ? "" : "none";
    if (rects.length) placePieces(s);

    // 보드 모드: 칸 선택 > 직접 편집 가능
    const choosing = s.phase === "await_choice" && !s.paused;
    const stage = $("stage");
    stage.classList.toggle("choice", choosing);
    stage.classList.toggle("editable", !choosing && canEdit());

    $("editHint").textContent = canEdit()
      ? "보드의 칸을 누르면 바로 수정할 수 있어요."
      : "보드 수정은 게임 시작 전이나 일시정지 중에 할 수 있어요.";

    const note = $("boardNote");
    if (choosing) {
      note.textContent = `${s.pending && s.pending.nick ? s.pending.nick + "님 — " : ""}이동할 칸을 눌러 주세요!`;
      note.hidden = false;
    } else if (s.running && s.paused) {
      note.textContent = "⏸ 일시정지 중 — 칸을 눌러 수정할 수 있어요";
      note.hidden = false;
    } else if (!s.running) {
      note.textContent = "칸을 눌러 규칙을 고치고, 준비되면 [게임 시작]!";
      note.hidden = false;
    } else {
      note.hidden = true;
    }

    // 편집 중이었는데 게임이 재개되면 서랍 닫기 (서버도 어차피 저장을 막음)
    if (editIndex !== null && !canEdit()) closeEditor();
  }

  // ---------------- 보드 클릭: 선택 리모컨 / 즉시 편집 ----------------
  $("board").addEventListener("click", e => {
    const el = e.target.closest(".tile");
    if (!el || !state) return;
    const idx = Number(el.dataset.idx);
    if (state.phase === "await_choice" && !state.paused) { cmd("choose", { index: idx }); return; }
    if (canEdit()) openEditor(idx);
  });

  // ---------------- 칸 편집 서랍 ----------------
  const PRESETS = {
    none:           { param: null,                        build: () => [] },
    counter_credit: { param: ["적립량 (잔)", 0.5, 0.5],   build: n => [{ op: "counter_add", name: "적립", n }] },
    counter_snack:  { param: ["적립량 (개)", 1, 1],       build: n => [{ op: "counter_add", name: "안주", n }] },
    counter_guard:  { param: null,                        build: () => [{ op: "counter_add", name: "방어", n: 1 }] },
    flush:          { param: null,                        build: () => [{ op: "counter_flush", name: "적립" }] },
    reverse:        { param: null,                        build: () => [{ op: "reverse" }] },
    x2:             { param: null,                        build: () => [{ op: "dice_multiplier", factor: 2 }] },
    extra:          { param: null,                        build: () => [{ op: "extra_roll" }] },
    timer:          { param: ["시간 (분)", 3, 0.5],       build: n => [{ op: "timer", minutes: n }] },
    back:           { param: ["칸 수 (음수=뒤로)", -1, 1], build: n => [{ op: "move_to", offset: n }] },
    goto:           { param: ["칸 번호 (0~25)", 0, 1],    build: n => [{ op: "move_to", index: n }] },
    choice:         { param: null,                        build: () => [{ op: "move_to", choice: true }] },
    minigame_ladder:  { param: null, build: () => [{ op: "minigame", kind: "ladder" }] },
    minigame_shuffle: { param: null, build: () => [{ op: "minigame", kind: "shuffle" }] },
  };

  function detectPreset(ops) {
    ops = ops || [];
    const mg = ops.find(o => o.op === "minigame");   // 미니게임 칸 (fix18)
    const fx = ops.filter(o => !["drink", "custom", "minigame"].includes(o.op)); // 표시전용 무시
    if (fx.length === 0 && mg)
      return { key: mg.kind === "shuffle" ? "minigame_shuffle" : "minigame_ladder", param: null };
    if (fx.length === 0) return { key: "none", param: null };
    if (fx.length > 1) return { key: "advanced", param: null };
    const o = fx[0];
    if (o.op === "counter_add" && o.name === "적립") return { key: "counter_credit", param: o.n ?? 1 };
    if (o.op === "counter_add" && o.name === "안주") return { key: "counter_snack", param: o.n ?? 1 };
    if (o.op === "counter_add" && o.name === "방어") return { key: "counter_guard", param: null };
    if (o.op === "counter_flush") return { key: "flush", param: null };
    if (o.op === "reverse") return { key: "reverse", param: null };
    if (o.op === "dice_multiplier") return { key: "x2", param: null };
    if (o.op === "extra_roll") return { key: "extra", param: null };
    if (o.op === "timer") return { key: "timer", param: o.minutes ?? 3 };
    if (o.op === "move_to" && o.choice) return { key: "choice", param: null };
    if (o.op === "move_to" && "offset" in o) return { key: "back", param: o.offset };
    if (o.op === "move_to" && "index" in o) return { key: "goto", param: o.index };
    return { key: "advanced", param: null };
  }

  let selectedCat = "";
  let selectedInk = "";   // fix24: 칸 글씨 색 ("" = 기본 남색)
  function syncInkUI() {
    const btns = document.querySelectorAll("#edInk button");
    let matched = false;
    btns.forEach(b => {
      const on = b.dataset.ink === selectedInk;
      b.classList.toggle("sel", on);
      if (on) matched = true;
    });
    const cust = $("edInkCustom");
    cust.classList.toggle("sel", !!selectedInk && !matched);   // 직접 고른 색이면 색상 입력에 테두리
    if (selectedInk) cust.value = selectedInk.length === 4
      ? "#" + [...selectedInk.slice(1)].map(c => c + c).join("")   // #rgb → #rrggbb (input 규격)
      : selectedInk.slice(0, 7);
    else cust.value = "#1F2C52";
  }
  function openEditor(idx) {
    editIndex = idx;
    const t = board[idx];
    editStickers = { a: null, b: null, c: null };
    (t.stickers || []).forEach(s => { editStickers[s.slot || "a"] = { ...s }; });
    editBgImg = typeof t.bg_img === "string" ? t.bg_img : "";   // fix42: 칸 통이미지
    renderStkRows();
    renderBgRow();
    closePicker();
    $("editor").hidden = false;
    $("editorVeil").hidden = false;
    $("edIndex").textContent = `${idx}번 · ${t.label}`;
    $("edLabel").value = t.label || "";
    $("edRule").value = t.rule_text || "";
    selectedCat = t.category || "";
    document.querySelectorAll("#edCat button").forEach(b =>
      b.classList.toggle("sel", b.dataset.cat === selectedCat));
    selectedInk = INK_OK(t.label_color) ? t.label_color : "";   // fix24: 글씨 색
    syncInkUI();
    const det = detectPreset(t.ops);
    const sel = $("edPreset");
    sel.value = det.key;
    sel.querySelector('[value="advanced"]').disabled = det.key !== "advanced";
    updateParamRow(det.key, det.param);
    rects.forEach(r => r.el.classList.remove("editing"));
    if (rects[idx]) rects[idx].el.classList.add("editing");
    $("edLabel").focus();
  }

  function updateParamRow(key, value) {
    const spec = PRESETS[key] && PRESETS[key].param;
    $("edParamRow").hidden = !spec;
    if (spec) {
      $("edParamLabel").textContent = spec[0];
      $("edParam").step = spec[2];
      $("edParam").value = value ?? spec[1];
    }
  }

  function closeEditor() {
    editIndex = null;
    $("editor").hidden = true;
    $("editorVeil").hidden = true;
    rects.forEach(r => r.el.classList.remove("editing"));
  }
  $("btnEdClose").addEventListener("click", closeEditor);
  $("editorVeil").addEventListener("click", closeEditor);

  $("edCat").addEventListener("click", e => {
    const b = e.target.closest("button[data-cat]");
    if (!b) return;
    selectedCat = b.dataset.cat;
    document.querySelectorAll("#edCat button").forEach(x =>
      x.classList.toggle("sel", x === b));
  });

  // fix24: 글씨 색 선택 (스와치 + 직접 고르기)
  $("edInk").addEventListener("click", e => {
    const b = e.target.closest("button[data-ink]");
    if (!b) return;
    selectedInk = b.dataset.ink;
    syncInkUI();
  });
  $("edInkCustom").addEventListener("input", () => {
    selectedInk = $("edInkCustom").value;
    syncInkUI();
  });

  $("edPreset").addEventListener("change", () => updateParamRow($("edPreset").value, null));

  $("btnEdSave").addEventListener("click", async () => {
    if (editIndex === null) return;
    const t = { ...board[editIndex] };
    t.label = $("edLabel").value.trim() || t.label;
    t.rule_text = $("edRule").value.trim();
    t.category = selectedCat || null;
    if (selectedInk && INK_OK(selectedInk)) t.label_color = selectedInk;   // fix24: 글씨 색 (표시 전용)
    else delete t.label_color;
    const key = $("edPreset").value;
    if (key !== "advanced") {
      const spec = PRESETS[key];
      const n = spec.param ? Number($("edParam").value) : null;
      if (spec.param && !Number.isFinite(n)) { showMsg("효과 값을 숫자로 입력해 주세요."); return; }
      if (key === "goto" && (n < 0 || n >= board.length)) { showMsg(`칸 번호는 0~${board.length - 1} 사이여야 해요.`); return; }
      t.ops = spec.build(n);
    }
    t.stickers = ["a", "b", "c"].filter(sl => editStickers[sl]).map(sl => {
      const o = { ...editStickers[sl], slot: sl };
      if (!o.scale || Number(o.scale) === 1) delete o.scale;
      return o;
    });
    if (editBgImg) t.bg_img = editBgImg; else delete t.bg_img;   // fix42: 칸 통이미지 (표시 전용)
    const newBoard = board.slice();
    newBoard[editIndex] = t;
    const res = await post("/api/board", { board: newBoard });
    if (res.ok) { showMsg(`${editIndex}번 칸을 저장했어요.`); closeEditor(); }
    // 보드는 서버 브로드캐스트({"type":"board"})로 전 화면에 동시 반영됨
  });

  // ---------------- 스티커 선택기 (fix16) ----------------
  const SLOTS = [["a", "오른쪽 위 (크게)"], ["b", "왼쪽 아래"], ["c", "오른쪽 아래"]];
  const SCALES = [["0.8", "작게"], ["1", "보통"], ["1.3", "크게"]];
  let editStickers = { a: null, b: null, c: null };
  let editBgImg = "";      // fix42: 이 칸의 통이미지 파일명 ("" = 안 씀)
  let pickSlot = null;     // "a"|"b"|"c" 또는 "bg"(칸 통이미지)
  let userStickers = [];   // 업로드된 그림 파일명 목록
  let stickerSizes = {};   // fix42: 파일명 → [가로, 세로] (서버가 헤더에서 읽어 보내 줌)

  const mbOf = bytes => (bytes / 1024 / 1024).toFixed(1);

  // fix42: 칸 통이미지 한 줄 — 썸네일 + 비우기 + 이 칸 권장 캔버스 안내
  function renderBgRow() {
    const has = !!editBgImg;
    $("edBgRow").innerHTML = `<div class="stk-row">
      <button type="button" class="stk-slot" id="edBgPick" title="눌러서 칸 그림 고르기">${
        has ? `<img src="/userstickers/${encodeURIComponent(editBgImg)}" alt="">`
            : '<span class="stk-none">＋</span>'}</button>
      <span class="stk-nm">${has ? esc(editBgImg) : "칸 기본 모양 사용 (그림 없음)"}</span>
      ${has ? '<button type="button" class="stk-clear" id="edBgClear" title="칸 그림 빼기">✕</button>' : ""}
    </div>`;
    const sp = editIndex === null ? null : tileSpec(editIndex);
    let hint = sp ? `이 칸은 <b class="goldtext">${sp.kind}</b> — 권장 캔버스
      <b class="goldtext">${sp.cw} × ${sp.chh}</b> (칸 ${sp.w} × ${sp.h} + 사방 여백 ${TILE_IMG_MARGIN}),
      내보내기는 2배인 <b class="goldtext">${sp.cw * 2} × ${sp.chh * 2}</b> 권장.` : "";
    const wh = stickerSizes[editBgImg];
    if (has && wh) {
      const want = sp ? sp.cw / sp.chh : 0;
      const got = wh[0] / wh[1];
      const off = want ? Math.abs(got - want) / want : 0;
      hint += `<br>지금 그림 <b>${wh[0]} × ${wh[1]}</b> · 예상 메모리
        <b>${mbOf(wh[0] * wh[1] * 4)}MB</b>`;
      if (off > 0.06) {
        hint += ` — <b class="warntext">가로세로 비율이 이 칸과 달라요.</b>
          그대로 늘려서 채우기 때문에 그림이 눌리거나 늘어나 보여요.`;
      }
    }
    $("edBgHint").innerHTML = hint;
  }

  const stkThumb = s => !s ? '<span class="stk-none">＋</span>'
    : (s.img ? `<img src="/userstickers/${encodeURIComponent(s.img)}" alt="">`
             : `<svg viewBox="0 0 64 64"><use href="#s-${esc(s.id)}"/></svg>`);

  function renderStkRows() {
    $("edStkRows").innerHTML = SLOTS.map(([sl, nm]) => {
      const s = editStickers[sl];
      const scale = s && s.scale ? String(s.scale) : "1";
      const sizeSel = s ? `<select class="stk-size" data-slot="${sl}">` +
        SCALES.map(([v, t]) => `<option value="${v}"${v === scale ? " selected" : ""}>${t}</option>`).join("") +
        `</select>` : "";
      const clear = s ? `<button type="button" class="stk-clear" data-slot="${sl}" title="이 자리 비우기">✕</button>` : "";
      return `<div class="stk-row">
        <button type="button" class="stk-slot" data-slot="${sl}" title="눌러서 그림 고르기">${stkThumb(s)}</button>
        <span class="stk-nm">${nm}</span>${sizeSel}${clear}</div>`;
    }).join("");
  }

  async function fetchUserStickers() {
    try {
      const res = await fetch("/api/stickers");
      const data = await res.json();
      userStickers = data.files || [];
      stickerSizes = data.sizes || {};          // fix42: 파일별 픽셀 크기
      return data;
    } catch (e) { userStickers = []; stickerSizes = {}; return null; }
  }

  function buildGrid() {
    const mine = userStickers.map(f => {
      const wh = stickerSizes[f];
      const cap = wh ? `${esc(f)} — ${wh[0]}×${wh[1]}` : esc(f);
      return `<span class="stk-cell mine"><button type="button" class="pickimg" data-img="${esc(f)}" title="${cap}">
        <img src="/userstickers/${encodeURIComponent(f)}" alt=""></button>
        <button type="button" class="stk-del" data-del="${esc(f)}" title="이 그림 삭제">✕</button></span>`;
    }).join("");
    // fix42: 칸 통이미지는 '올린 그림'만 쓸 수 있다 (기본 SVG 세트는 칸을 채우는 용도가 아님)
    const base = pickSlot === "bg" ? "" : STICKERS.map(([id, nm]) =>
      `<span class="stk-cell"><button type="button" class="pickid" data-id="${id}" title="${nm}">
        <svg viewBox="0 0 64 64"><use href="#s-${id}"/></svg></button></span>`).join("");
    $("stkGrid").innerHTML = mine + base ||
      '<span class="dimtext small">아직 올린 그림이 없어요 — 아래 [＋ 내 그림 올리기]로 올려 주세요.</span>';
  }

  async function openPicker(slot) {
    pickSlot = slot;
    $("stkPickTitle").textContent = slot === "bg"
      ? "칸 그림 고르기 — 칸 하나를 통째로 덮어요"
      : `그림 고르기 — ${SLOTS.find(x => x[0] === slot)[1]}`;
    await fetchUserStickers();
    buildGrid();
    $("stkPicker").hidden = false;
  }
  function closePicker() { $("stkPicker").hidden = true; pickSlot = null; }
  $("stkPickClose").addEventListener("click", closePicker);

  $("edStkRows").addEventListener("click", e => {
    const slotBtn = e.target.closest(".stk-slot");
    if (slotBtn) { openPicker(slotBtn.dataset.slot); return; }
    const clr = e.target.closest(".stk-clear");
    if (clr) { editStickers[clr.dataset.slot] = null; renderStkRows(); }
  });

  // fix42: 칸 통이미지 줄
  $("edBgRow").addEventListener("click", e => {
    if (e.target.closest("#edBgClear")) { editBgImg = ""; renderBgRow(); return; }
    if (e.target.closest("#edBgPick")) openPicker("bg");
  });
  $("edStkRows").addEventListener("change", e => {
    const sel = e.target.closest(".stk-size");
    if (!sel) return;
    const s = editStickers[sel.dataset.slot];
    if (s) s.scale = Number(sel.value);
  });

  $("stkGrid").addEventListener("click", async e => {
    const del = e.target.closest(".stk-del");
    if (del) {
      if (!(await appConfirm("🗑", "그림 삭제", `'<b>${esc(del.dataset.del)}</b>' 그림을 삭제할까요?`))) return;
      const res = await post("/api/stickers/delete", { file: del.dataset.del });
      if (res.ok) { await fetchUserStickers(); buildGrid(); }
      else if (res.msg) appPopup("🔒", "삭제할 수 없어요", esc(res.msg));
      return;
    }
    const byId = e.target.closest(".pickid");
    const byImg = e.target.closest(".pickimg");
    if (!pickSlot || (!byId && !byImg)) return;
    if (pickSlot === "bg") {                       // fix42: 칸 통이미지 (올린 그림만)
      if (!byImg) return;
      editBgImg = byImg.dataset.img;
      renderBgRow();
      closePicker();
      return;
    }
    const prev = editStickers[pickSlot];
    editStickers[pickSlot] = byId
      ? { id: byId.dataset.id, slot: pickSlot, scale: prev && prev.scale }
      : { img: byImg.dataset.img, slot: pickSlot, scale: prev && prev.scale };
    if (!editStickers[pickSlot].scale) delete editStickers[pickSlot].scale;
    renderStkRows();
    closePicker();
  });

  $("btnStkUpload").addEventListener("click", () => $("stkFile").click());
  $("stkFile").addEventListener("change", async () => {
    const f = $("stkFile").files[0];
    $("stkFile").value = "";
    if (!f) return;
    const res = await uploadImageFile(f);     // fix41: 20MB 검사 + 자동 축소 공용 처리
    if (!res) return;
    await fetchUserStickers();
    if (pickSlot === "bg") {                  // fix42: 칸 그림 고르는 중이면 바로 그 칸에 적용
      editBgImg = res.file;
      renderBgRow();
      closePicker();
      showMsg(`'${res.file}' 업로드 완료 — [이 칸 저장]을 눌러야 적용돼요.`);
      return;
    }
    showMsg(`'${res.file}' 업로드 완료 — 눌러서 붙여 보세요.`);
    buildGrid();
  });

  // ---------------- 미니게임 리모컨 (fix18 — 연출 전용, 결과 반영은 수동) ----------------
  let mgGame = null;
  let mgCountLocal = 3;
  const MG_POS = ["왼쪽", "가운데", "오른쪽"];
  const mgApi = a => post("/api/minigame", a);
  const currentMgResults = () =>
    Array.from(document.querySelectorAll("#mgResults .mg-res")).map(x => x.value);
  function openMgModal() { $("mgModal").hidden = false; $("mgVeil").hidden = false; }
  function closeMgModal() { $("mgModal").hidden = true; $("mgVeil").hidden = true; }
  $("btnMinigame").addEventListener("click", async () => {
    const res = await mgApi({ action: "open" });
    if (res.ok) openMgModal();
  });
  $("btnMgClose").addEventListener("click", () => mgApi({ action: "close" }));
  // 베일 클릭으로는 안 닫힘 — 방송 중 실수 방지 (✕로만 닫기)

  function mgResultInputs(count, values) {
    $("mgResults").innerHTML = Array.from({ length: count }, (_, i) =>
      `<input type="text" class="mg-res" maxlength="10" value="${esc(values[i] || "")}"
        placeholder="${i + 1}번 결과 (예: 원샷)">`).join("");
  }
  function renderMg() {
    const g = mgGame;
    if (!g) { closeMgModal(); return; }
    openMgModal();
    $("mgLadder").hidden = g.kind !== "ladder";
    $("mgShuffle").hidden = g.kind !== "shuffle";
    if (g.kind === "ladder") {
      $("mgTitle").textContent = "🪜 사다리타기";
      const st = g.stage;
      $("mgMinus").disabled = $("mgPlus").disabled = $("btnMgSetup").disabled = st !== "input";
      if (st === "input") {
        mgCountLocal = Math.max(2, Math.min(5, g.count || 3));
        $("mgCount").textContent = mgCountLocal;
        mgResultInputs(mgCountLocal, g.results || []);
        $("mgPickRow").innerHTML = "";
        $("btnMgRun").hidden = true;
        $("mgLadderMsg").textContent = "결과 문구를 확인하고 [사다리 준비]를 눌러 주세요 — 지난 입력이 저장돼 있어요.";
      } else {
        $("mgCount").textContent = g.count;
        $("mgResults").innerHTML = (g.results || []).map((r, i) =>
          `<span class="schip"><em>${i + 1}</em><b>${esc(r)}</b></span>`).join("");
        $("mgPickRow").innerHTML = Array.from({ length: g.count }, (_, i) =>
          `<button class="hbtn mg-pick${g.pick === i ? " sel" : ""}" data-pick="${i}"
            ${st !== "setup" ? "disabled" : ""}>${i + 1}번</button>`).join("");
        $("btnMgRun").hidden = st !== "picked";
        $("mgLadderMsg").textContent =
          st === "setup" ? "번호를 고르면 방송 화면에 사다리가 랜덤으로 공개돼요!"
          : st === "picked" ? `${g.pick + 1}번 선택! 사다리가 공개됐어요 — [실행]을 누르면 내려가요.`
          : g.result != null ? `결과: ${g.pick + 1}번 → ${g.results[g.result]} (벌칙 적용은 평소처럼 수동!)` : "";
      }
    } else {
      $("mgTitle").textContent = "🥤 야바위";
      const st = g.stage;
      $("btnMgShStart").hidden = st !== "ready";
      $("mgCups").innerHTML = (st === "pick" || st === "done")
        ? [0, 1, 2].map(i =>
            `<button class="hbtn mg-cup${g.pick === i ? " sel" : ""}" data-cup="${i}"
              ${st !== "pick" ? "disabled" : ""}>🥤 ${MG_POS[i]}</button>`).join("")
        : "";
      $("mgShuffleMsg").textContent =
        st === "ready" ? "시작을 누르면 방송 화면에서 공을 보여주고 컵을 섞어요."
        : st === "pick" ? "섞는 연출이 끝나면 컵 하나를 골라 주세요."
        : st === "done" ? (g.win ? `🎉 ${MG_POS[g.pick]} 컵 — 공 찾음!`
                                 : `꽝! 공은 ${MG_POS[g.ball]} 컵에 있었어요.`) : "";
    }
  }
  $("mgMinus").addEventListener("click", () => {
    if (mgGame && mgGame.stage === "input" && mgCountLocal > 2) {
      const v = currentMgResults(); mgCountLocal--;
      $("mgCount").textContent = mgCountLocal; mgResultInputs(mgCountLocal, v);
    }
  });
  $("mgPlus").addEventListener("click", () => {
    if (mgGame && mgGame.stage === "input" && mgCountLocal < 5) {
      const v = currentMgResults(); mgCountLocal++;
      $("mgCount").textContent = mgCountLocal; mgResultInputs(mgCountLocal, v);
    }
  });
  $("btnMgSetup").addEventListener("click", () =>
    mgApi({ action: "ladder_setup", count: mgCountLocal, results: currentMgResults() }));
  $("mgPickRow").addEventListener("click", e => {
    const b = e.target.closest(".mg-pick");
    if (b && !b.disabled) mgApi({ action: "ladder_pick", pick: Number(b.dataset.pick) });
  });
  $("btnMgRun").addEventListener("click", () => mgApi({ action: "ladder_run" }));
  $("btnMgShStart").addEventListener("click", () => mgApi({ action: "shuffle_start" }));
  $("mgCups").addEventListener("click", e => {
    const b = e.target.closest(".mg-cup");
    if (b && !b.disabled) mgApi({ action: "shuffle_pick", pick: Number(b.dataset.cup) });
  });

  // ---------------- 앱 테마 팝업 (fix27 — 브라우저 기본 alert/confirm 금지, 곡천 확정) ----------------
  function appPopup(icon, title, msgHtml, ok) {
    const v = document.createElement("div");
    v.className = "veil pop";
    v.innerHTML = `<div class="warnbox${ok ? " ok" : ""}">
      <div class="wico">${icon}</div><div class="wtitle">${esc(title)}</div>
      <div class="wmsg">${msgHtml}</div>
      <div class="wrow"><button class="hbtn primary-sm">확인</button></div></div>`;
    v.querySelector("button").onclick = () => v.remove();
    v.addEventListener("click", e => { if (e.target === v) v.remove(); });
    document.body.appendChild(v);
  }
  function appConfirm(icon, title, msgHtml) {
    return new Promise(resolve => {
      const v = document.createElement("div");
      v.className = "veil pop";
      v.innerHTML = `<div class="warnbox">
        <div class="wico">${icon}</div><div class="wtitle">${esc(title)}</div>
        <div class="wmsg">${msgHtml}</div>
        <div class="wrow"><button class="hbtn" data-r="0">취소</button>
        <button class="hbtn primary-sm" data-r="1">확인</button></div></div>`;
      const done = r => { v.remove(); resolve(r); };
      v.querySelectorAll("button").forEach(b =>
        b.addEventListener("click", () => done(b.dataset.r === "1")));
      v.addEventListener("click", e => { if (e.target === v) done(false); });
      document.body.appendChild(v);
    });
  }

  // ---------------- 설정 창 (fix27: 4분류 탭 — 시안 v3 통과) ----------------
  function openSettings() {
    $("settings").hidden = false; $("settingsVeil").hidden = false;
    if (!$("tabImages").hidden && $("tabImages").classList.contains("on")) renderImages();
    refreshMemLine();                                   // fix42: 예상 메모리 갱신
  }

  // fix42: 칸 꾸미기 방식 전환 (설정 → 테마) — 표시 전용이라 진행 중에도 바꿀 수 있다
  $("skinSeg").addEventListener("click", async e => {
    const b = e.target.closest("button[data-skin]");
    if (!b || b.dataset.skin === tileSkin) return;
    const res = await post("/api/tileskin", { skin: b.dataset.skin });
    if (!res || !res.ok) return;
    applyTileSkin(res.tile_skin);
    renderBoard();                                      // 미리보기 즉시 반영
    refreshMemLine();
    showMsg(res.tile_skin === "image"
      ? "칸 통이미지로 바꿨어요 — 그림을 안 넣은 칸은 지금까지 모습 그대로예요."
      : "기본 칸 꾸미기로 되돌렸어요.");
  });
  function closeSettings() { $("settings").hidden = true; $("settingsVeil").hidden = true; closeFacePick(); }
  $("setTabs").addEventListener("click", e => {
    const t = e.target.closest(".tab");
    if (!t) return;
    document.querySelectorAll("#setTabs .tab").forEach(x => x.classList.toggle("on", x === t));
    document.querySelectorAll("#settings .pane").forEach(pn =>
      pn.classList.toggle("on", pn.id === t.dataset.p));
    if (t.dataset.p === "tabImages") renderImages();
  });

  // ══ 테마 설정 (보드/오버레이 전용 — 조작앱 색은 딥네이비 고정) ══
  // 팔레트 = tokens.css 변수 오버라이드 묶음. 서버에 저장 → 오버레이 applyPalette로 적용.
  const THEMES = [
    { id: "cotton", nm: "솜사탕 (기본)", cap: "지금 쓰는 파스텔 원색",
      bg: ["#D8C6F0", "#C2ABE4"], tile: "#FDF8EE", ac: "#F472B6", pt: "#8FE3C8", ink: "#1F2C52",
      edge: "#EBDCC9", vars: {} },
    { id: "dding", nm: "김띵띵띵 ★", cap: "남색×금발×캔디핑크 — 퍼스널 컬러",
      bg: ["#C7D0EE", "#A9B6E2"], tile: "#FBFCFF", ac: "#F04FA0", pt: "#EAD27F", ink: "#22305C",
      edge: "#CBD4EE", vars: {
        "--board-bg0": "#C7D0EE", "--board-bg1": "#A9B6E2",
        "--tile": "#FBFCFF", "--tile-edge": "#CBD4EE",
        "--hotpink": "#F04FA0", "--hotpink-deep": "#D63A8C",
        "--board-ink": "#22305C", "--cream": "#F4F6FF", "--corner-edge": "#BFCAEC",
        "--tile-hi": "#FFEAF4", "--fx-bg": "#FDF3F9", "--panel": "rgba(250,251,255,.93)",
        "--point-deep": "#A8862B", "--ink": "#4A5788", "--ink-dim": "#8A94BD", "--plum": "#33417A",
      } },
    { id: "sakura", nm: "벚꽃 소다", cap: "분홍 가득, 봄 느낌",
      bg: ["#F6D8E8", "#EEC0D8"], tile: "#FFF8F5", ac: "#E85C97", pt: "#A8CFF5", ink: "#5C2440",
      edge: "#F2D8CE", vars: {
        "--board-bg0": "#F6D8E8", "--board-bg1": "#EEC0D8",
        "--tile": "#FFF8F5", "--tile-edge": "#F2D8CE",
        "--hotpink": "#E85C97", "--hotpink-deep": "#C93E7A",
        "--board-ink": "#5C2440", "--cream": "#FFF2F0", "--corner-edge": "#F0CFC4",
        "--tile-hi": "#FFE9F2", "--fx-bg": "#FFF1F7", "--panel": "rgba(255,250,248,.93)",
        "--point-deep": "#5B8FD0", "--ink": "#7A4E60", "--ink-dim": "#B08D9C", "--plum": "#8A4E6B",
      } },
    { id: "mint", nm: "민트 소다", cap: "시원한 민트×하늘",
      bg: ["#C6E8DC", "#A5D6C6"], tile: "#F7FFFA", ac: "#2FA57E", pt: "#F5C98A", ink: "#14453A",
      edge: "#CFE8DA", vars: {
        "--board-bg0": "#C6E8DC", "--board-bg1": "#A5D6C6",
        "--tile": "#F7FFFA", "--tile-edge": "#CFE8DA",
        "--hotpink": "#2FA57E", "--hotpink-deep": "#1F7D5E",
        "--board-ink": "#14453A", "--cream": "#F0FBF4", "--corner-edge": "#C2E2CE",
        "--tile-hi": "#E8F9F0", "--fx-bg": "#F0FBF5", "--panel": "rgba(248,255,250,.93)",
        "--point-deep": "#C4863B", "--ink": "#3E6B5B", "--ink-dim": "#84AA9B", "--plum": "#2E6B57",
      } },
    { id: "pub", nm: "미드나잇 펍", cap: "어두운 술집 무드 (다크)",
      bg: ["#232746", "#171A32"], tile: "#2E3354", ac: "#E8B45C", pt: "#F06AAE", ink: "#F2E8D8",
      edge: "#3E4570", vars: {
        "--board-bg0": "#232746", "--board-bg1": "#171A32",
        "--tile": "#2E3354", "--tile-edge": "#3E4570",
        "--hotpink": "#E8B45C", "--hotpink-deep": "#C98F35",
        "--board-ink": "#F2E8D8", "--cream": "#3A3F63", "--corner-edge": "#4E5680",
        "--tile-hi": "#4A4066", "--fx-bg": "#262A48", "--panel": "rgba(30,26,50,.93)",
        "--chip": "#3A3F63", "--point-deep": "#F06AAE",
        "--ink": "#EFE6F5", "--ink-dim": "#A9A3C4", "--plum": "#E3D9F2",
      } },
  ];
  const FONTS = [
    { key: "jua", nm: "주아체 (기본)", stack: '"Jua", "Pretendard Variable", Pretendard, "Malgun Gothic", sans-serif' },
    { key: "jalnan", nm: "잘난체", stack: 'Jalnan, "Jua", "Pretendard Variable", Pretendard, sans-serif' },
    { key: "bhs", nm: "검은고딕", stack: '"Black Han Sans", "Jua", Pretendard, sans-serif' },
    { key: "dohyeon", nm: "도현체", stack: '"Do Hyeon", "Jua", Pretendard, sans-serif' },
    { key: "cafe24", nm: "카페24 써라운드", stack: 'Cafe24Ssurround, "Jua", Pretendard, sans-serif' },
    { key: "pretendard", nm: "프리텐다드", stack: '"Pretendard Variable", Pretendard, "Malgun Gothic", sans-serif' },
  ];
  const SIZES = [["0.9", "작게 90%"], ["1", "보통 100%"], ["1.15", "크게 115%"], ["1.3", "아주 크게 130%"]];
  let themeSel = "cotton", fontSel = "jua", sizeSel = 1;
  const themeOf = id => THEMES.find(t => t.id === id) || THEMES[0];
  const fontOf = key => FONTS.find(f => f.key === key) || FONTS[0];

  // 미리보기(왼쪽 모니터)에 테마 적용 — #stage 스코프 변수 (조작앱 UI에는 영향 없음)
  let pvVars = [];
  function applyBoardPreview(palette, fontStack, fontScale) {
    const st = $("stage").style;
    pvVars.forEach(k => st.removeProperty(k));
    pvVars = [];
    Object.entries(palette || {}).forEach(([k, v]) => {
      if (/^--[\w-]+$/.test(k)) { st.setProperty(k, v); pvVars.push(k); }
    });
    if (fontStack) { st.setProperty("--board-font", fontStack); pvVars.push("--board-font"); }
    st.setProperty("--board-fs", String(fontScale || 1)); pvVars.push("--board-fs");
    const bg0 = (palette || {})["--board-bg0"] || "#F8F0F6";
    const bg1 = (palette || {})["--board-bg1"] || "#F2E8EF";
    $("boardWrap").style.background = `linear-gradient(180deg, ${bg0}, ${bg1})`;
  }

  function renderThemeUI() {
    $("themeGrid").innerHTML = THEMES.map(t =>
      `<div class="thm${t.id === themeSel ? " on" : ""}" data-th="${t.id}">
        <div class="pv" style="background:linear-gradient(160deg,${t.bg[0]},${t.bg[1]})">
          <span class="thchip" style="background:${t.tile}"></span>
          <span class="thchip" style="background:${t.ac}"></span>
          <span class="thchip" style="background:${t.pt}"></span></div>
        <div class="nm">${esc(t.nm)}</div><div class="cap">${esc(t.cap)}</div></div>`).join("");
    $("fontSeg").innerHTML = FONTS.map(f =>
      `<button data-f="${f.key}" class="${f.key === fontSel ? "on" : ""}"
        style="font-family:${f.stack.replace(/"/g, "&quot;")}">${esc(f.nm)}</button>`).join("");
    $("sizeSeg").innerHTML = SIZES.map(([v, nm]) =>
      `<button data-s="${v}" class="${Number(v) === Number(sizeSel) ? "on" : ""}">${nm}</button>`).join("");
    drawThemeLive();
  }
  function drawThemeLive() {
    const t = themeOf(themeSel);
    const stack = fontOf(fontSel).stack;
    const live = $("themeLive");
    live.style.background = `linear-gradient(160deg,${t.bg[0]},${t.bg[1]})`;
    const tile = (lb, sub, cur) => `
      <div class="lt" style="--pv-fs:${sizeSel}; background:${t.tile};
          border-color:${cur ? t.ac : t.edge};
          ${cur ? `box-shadow:0 0 0 3px ${t.ac}44, 0 0 18px ${t.ac}66;` : ""}">
        <span class="lb2" style="font-family:${stack.replace(/"/g, "&quot;")}; color:${t.ink};
          text-shadow:0 0 4px rgba(255,255,255,.6), 0 4px 7px rgba(40,30,60,.35)">${esc(lb)}</span>
        ${sub ? `<span class="sb2" style="color:${t.ink}">${esc(sub)}</span>` : ""}</div>`;
    live.innerHTML = tile("사다리타기") + tile("외국어 금지", "3분") + tile("한잔마셔", "", true) + tile("반잔 적립");
  }
  async function saveTheme() {
    const t = themeOf(themeSel);
    const f = fontOf(fontSel);
    await post("/api/theme", { theme: t.id, theme_name: t.nm, palette: t.vars,
                               font: f.key, font_stack: f.stack, font_scale: Number(sizeSel) });
    // 적용 자체는 서버 브로드캐스트({"type":"theme"})가 모든 화면에 동시 반영
  }
  $("themeGrid").addEventListener("click", e => {
    const el = e.target.closest(".thm");
    if (!el) return;
    themeSel = el.dataset.th;
    renderThemeUI(); saveTheme();
  });
  $("fontSeg").addEventListener("click", e => {
    const b = e.target.closest("button[data-f]");
    if (!b) return;
    fontSel = b.dataset.f;
    renderThemeUI(); saveTheme();
  });
  $("sizeSeg").addEventListener("click", e => {
    const b = e.target.closest("button[data-s]");
    if (!b) return;
    sizeSel = Number(b.dataset.s);
    renderThemeUI(); saveTheme();
  });

  // ══ 게임 규칙 (스트리밍 설정 탭) ══
  function fillRules(c) {
    if (!c) return;
    if (c.dice_base_amount != null) $("cfgBase").value = c.dice_base_amount;
    if (c.multiple_mode) $("cfgMode").value = c.multiple_mode;
    if (c.guard_price != null) $("cfgGuard").value = c.guard_price;
    if (c.command_prefix) $("cfgPrefix").value = c.command_prefix;
    if (c.port != null) $("cfgPort").value = c.port;
    renderTestQuick(c);   // fix29: 테스트 바로가기 버튼도 규칙과 함께 갱신
  }
  // fix29: 테스트 후원 바로가기 = [기준 금액(1굴림)] [방어권 가격] — 게임 규칙 변경 시 자동 반영 (곡천 확정)
  let testAmts = { base: 2000, guard: 10000 };
  function renderTestQuick(c) {
    if (c && c.dice_base_amount != null) testAmts.base = Number(c.dice_base_amount) || 2000;
    if (c && c.guard_price != null) testAmts.guard = Number(c.guard_price) || 10000;
    $("testQuick").innerHTML =
      `<button class="hbtn mini" data-amt="${testAmts.base}">${testAmts.base.toLocaleString()} (1굴림)</button>` +
      `<button class="hbtn mini" data-amt="${testAmts.guard}">${testAmts.guard.toLocaleString()} (방어권)</button>`;
  }
  $("btnRulesSave").addEventListener("click", async () => {
    const res = await post("/api/rules", {
      dice_base_amount: Number($("cfgBase").value),
      multiple_mode: $("cfgMode").value,
      guard_price: Number($("cfgGuard").value),
      command_prefix: $("cfgPrefix").value,
      port: Number($("cfgPort").value),
    });
    if (res.ok) showMsg("게임 규칙 저장 완료" + (res.port_changed ? " — 포트는 재시작 후 적용" : ""));
  });

  // ══ 주사위 설정 탭 — 확률 규칙 (곡천 확정: 100−직접입력합을 균등 체크가 n등분, 직접입력 최대 99) ══
  const DICE_MAX = 60;
  const isNumFace = t => /^\d{1,2}$/.test(String(t).trim());
  let diceRows = [];
  const defaultDice = () => Array.from({ length: 6 }, (_, i) =>
    ({ text: String(i + 1), pct: null, even: true, mode: "move" }));
  function diceCalc() {
    const fixed = diceRows.filter(r => !r.even).reduce((a, r) => a + (Number(r.pct) || 0), 0);
    const evens = diceRows.filter(r => r.even).length;
    const share = evens ? Math.max(0, 100 - fixed) / evens : 0;
    return { fixed, evens, share };
  }
  // 직접 입력 합 한도(균등 있으면 99, 없으면 100) 초과 시 '마지막에 적은 값' 자동 조정 (30,30,40→39)
  function clampFixed(row, v, inputEl) {
    const others = diceRows.filter(x => !x.even && x !== row)
      .reduce((a, x) => a + (Number(x.pct) || 0), 0);
    const evens = diceRows.filter(x => x.even).length;
    const limit = (evens > 0 ? 99 : 100) - others;
    const out = Math.max(0, Math.min(v, Math.max(0, limit)));
    if (out !== v && inputEl) inputEl.value = out;
    return out;
  }
  function drawDice() {
    const { share } = diceCalc();
    $("diceList").innerHTML = "";
    diceRows.forEach((r, i) => {
      const d = document.createElement("div");
      d.className = "drow";
      const num = isNumFace(r.text);
      d.innerHTML = `
        <input class="dtxt" type="text" maxlength="16" value="${esc(r.text)}" placeholder="예: 3 또는 원하는 칸 가기">
        <span class="pctwrap"><input type="number" step="0.01" min="0" max="100"
          value="${r.even ? share.toFixed(2) : (r.pct ?? "")}" ${r.even ? "disabled" : ""}></span>
        <label class="evn${r.even ? " on" : ""}"><input type="checkbox" ${r.even ? "checked" : ""}>균등분배</label>
        <select class="mode-sel" ${num ? "disabled" : ""} title="${num ? "숫자 항목은 그만큼 이동해요" : "글자 항목의 처리 방식"}">
          <option value="move"${r.mode !== "show" ? " selected" : ""}>🚶 이동형 — 칸 선택 열림</option>
          <option value="show"${r.mode === "show" ? " selected" : ""}>🎪 연출형 — 표시만, 완료로 진행</option>
        </select>
        <button class="del" title="이 항목 삭제">✕</button>`;
      d.querySelector(".dtxt").addEventListener("input", e => {
        r.text = e.target.value;
        d.querySelector(".mode-sel").disabled = isNumFace(r.text);
      });
      d.querySelector(".pctwrap input").addEventListener("input", e => {
        let v = e.target.value === "" ? null : Number(e.target.value);
        if (v != null && Number.isFinite(v)) v = clampFixed(r, v, e.target);
        r.pct = v;
        drawDiceSum();
      });
      d.querySelector(".evn input").addEventListener("change", e => {
        r.even = e.target.checked;
        if (!r.even) r.pct = clampFixed(r, r.pct == null ? Number(diceCalc().share.toFixed(2)) : r.pct, null);
        drawDice();
      });
      d.querySelector(".del").addEventListener("click", () => { diceRows.splice(i, 1); drawDice(); });
      $("diceList").appendChild(d);
    });
    drawDiceSum();
  }
  function drawDiceSum() {
    const { fixed, evens, share } = diceCalc();
    const total = fixed + share * evens;
    const el = $("diceSum");
    if (!diceRows.length) { el.className = "sumline bad"; el.textContent = "항목을 1개 이상 추가해 주세요"; }
    else if (fixed > 100.001) { el.className = "sumline bad"; el.textContent = `⚠ 직접 입력 합 ${fixed.toFixed(2)}% — 100%를 넘었어요`; }
    else if (!evens && Math.abs(total - 100) > 0.01) {
      el.className = "sumline bad";
      el.textContent = `합계 ${total.toFixed(2)}% — 100%가 되게 맞춰주세요 (균등분배를 켜면 자동으로 맞아요)`;
    } else {
      el.className = "sumline ok";
      el.textContent = `합계 100% ✓ ${evens ? `(균등분배 ${evens}개 × ${share.toFixed(2)}%)` : ""}`;
    }
    Array.from(document.querySelectorAll("#diceList .drow")).forEach((d, i) => {
      const r = diceRows[i];
      if (r && r.even) d.querySelector(".pctwrap input").value = share.toFixed(2);
    });
  }
  $("btnDiceAdd").addEventListener("click", () => {
    if (diceRows.length >= DICE_MAX) { showMsg(`항목은 최대 ${DICE_MAX}개까지예요.`); return; }
    diceRows.push({ text: "", pct: null, even: true, mode: "move" });
    drawDice();
    $("diceList").scrollTop = $("diceList").scrollHeight;
  });
  $("btnDiceReset").addEventListener("click", () => { diceRows = defaultDice(); drawDice(); });
  $("btnDiceApply").addEventListener("click", async () => {
    if (diceRows.some(r => !String(r.text).trim())) {
      appPopup("⚠️", "결과 칸을 채워주세요", "비어 있는 항목이 있어요.<br>숫자(이동 칸 수)나 글자를 적어 주세요.");
      return;
    }
    const { fixed, evens, share } = diceCalc();
    const total = fixed + share * evens;
    if (Math.abs(total - 100) > 0.01) {
      appPopup("⚠️", "확률을 100%로 채워주세요",
        `지금 합계는 <b>${total.toFixed(2)}%</b>예요.<br>%를 조정하거나, 항목에 <b>균등분배</b>를 켜면 남은 확률이 자동으로 채워져요.`);
      return;
    }
    const res = await post("/api/dice", { faces: diceRows.map(r => ({
      text: String(r.text).trim(), pct: r.even ? null : Number(r.pct) || 0,
      even: !!r.even, mode: r.mode === "show" ? "show" : "move" })) });
    if (res.ok) {
      appPopup("🎲", "주사위 설정 적용!", "합계 100% 확인 — 다음 굴림부터 이 확률로 굴러가요.", true);
    } else if (res.msg) {
      appPopup("⚠️", "저장하지 못했어요", esc(res.msg));
    }
  });

  // ══ 이미지 관리 탭 — 스티커·말 그림 업로드 이미지 한곳 관리 (사용 중 = 삭제 잠금) ══
  async function renderImages() {
    let files = [], usage = {}, memory = null;
    const data = await fetchUserStickers();     // files + sizes 를 한 번에 받아 공유
    if (data) { files = data.files || []; usage = data.usage || {}; memory = data.memory || null; }
    $("imgGrid").innerHTML = files.map(f => {
      const used = usage[f];
      const wh = stickerSizes[f];
      // fix42: 파일 크기가 아니라 '띄웠을 때 쓰는 메모리' — 가로 × 세로 × 4바이트
      const px = wh ? `${wh[0]}×${wh[1]} · ${mbOf(wh[0] * wh[1] * 4)}MB` : "크기 확인 불가";
      const big = wh && wh[0] * wh[1] * 4 > 4 * 1024 * 1024;   // 한 장 4MB 넘으면 눈에 띄게
      return `<div class="imgc${used ? " locked" : ""}">
        ${used ? `<span class="use" title="${esc(used)}에서 사용 중">사용 중</span>` : ""}
        <div class="thumb"><img src="/userstickers/${encodeURIComponent(f)}" alt=""></div>
        <div class="fn" title="${esc(f)}${used ? " — " + esc(used) : ""}">${esc(f)}</div>
        <div class="px${big ? " big" : ""}">${px}</div>
        <button class="rm" data-del="${esc(f)}" title="삭제">✕</button></div>`;
    }).join("") + `<button class="addc2" id="imgAddBtn"><span class="plus">＋</span>이미지 추가</button>`;
    renderMemLine(memory);
  }

  // fix42: 예상 메모리 한 줄 — 오버레이가 '실제로 띄우는' 그림만 합산 (보관만 한 그림은 안 셈)
  function renderMemLine(mem) {
    const lines = ["imgMemLine", "skinMemLine"].map(id => $(id)).filter(Boolean);
    if (!mem) {
      lines.forEach(el => { el.className = "sec-status"; el.innerHTML = '<i class="dot"></i>예상 메모리 확인 불가'; });
      return;
    }
    const mb = mem.total_bytes / 1024 / 1024;
    // 기준: 1080p 화면 한 장 = 약 7.9MB. 120MB(15장) 넘으면 경고, 250MB 넘으면 위험.
    const cls = mb > 250 ? "err" : mb > 120 ? "warn" : "ok";
    const tail = cls === "ok" ? "여유 있어요."
      : cls === "warn" ? "조금 큰 편이에요 — 방송 전에 OBS가 무겁지 않은지 확인해 보세요."
      : "너무 커요. 큰 그림을 줄여서 다시 올리는 걸 권해요.";
    lines.forEach(el => {
      el.className = "sec-status " + cls;
      el.innerHTML = `<i class="dot"></i>지금 오버레이가 쓰는 그림 <b>${mem.count}장</b> ·
        예상 메모리 <b>${mbOf(mem.total_bytes)}MB</b> (1080p 화면 ${(mb / 7.9).toFixed(1)}장 분량) — ${tail}`;
    });
  }

  async function refreshMemLine() {
    try {
      const res = await fetch("/api/imgmemory");
      renderMemLine(await res.json());
    } catch (e) { renderMemLine(null); }
  }
  $("imgGrid").addEventListener("click", async e => {
    if (e.target.closest("#imgAddBtn")) { $("imgFile").click(); return; }
    const del = e.target.closest(".rm");
    if (!del) return;
    const f = del.dataset.del;
    if (!(await appConfirm("🗑", "이미지 삭제", `'<b>${esc(f)}</b>' 그림을 삭제할까요?<br>되돌릴 수 없어요.`))) return;
    const res = await post("/api/stickers/delete", { file: f });
    if (res.ok) { showMsg(`'${f}' 삭제 완료`); renderImages(); }
    else if (res.msg) appPopup("🔒", "삭제할 수 없어요", esc(res.msg));
  });
  $("imgFile").addEventListener("change", async () => {
    const f = $("imgFile").files[0];
    $("imgFile").value = "";
    if (!f) return;
    const res = await uploadImageFile(f);     // fix41: 20MB 검사 + 자동 축소 공용 처리
    if (!res) return;
    showMsg(`'${res.file}' 업로드 완료`);
    renderImages();
  });

  // ══ 인트로 팝업 (fix38 — 김띵띵띵 1주년 크레딧, 시안 v3 확정) ══
  // 경고판(컴퓨터당 최초 1회, [확인했습니다]) → 이후 매 실행 채널판([확인] + 채널 놀러가기).
  // 두 버전은 완전히 같은 플레이트(슬롯 110px 고정) — 교체 위화감 0. 버튼으로만 닫힘.
  let introShown = false;   // 페이지 로드당 1회 (WS 재연결 시 재표시 방지)
  function showIntro(seen) {
    if (introShown) return;
    introShown = true;
    const slot = seen
      ? `<button class="intro-ch" id="introCh"><span class="cvr">▶</span>김띵띵띵 치지직 채널 놀러가기</button>`
      : `<div class="intro-warn">⚠ 무단 재배포 · 무단 수정 · 2차 배포 · 판매를 금지합니다<br>
          <span class="w2">공식 배포처: github.com/gokcheon/board-overlay<br>
          네이버/치지직 공식과 무관한 팬메이드이며, 사용에 따른 책임은 사용자에게 있습니다</span></div>`;
    const v = document.createElement("div");
    v.className = "intro-veil";
    v.innerHTML = `<div class="intro-box">
      <div class="intro-dice">🎲🎂</div>
      <div class="intro-title">보드게임 오버레이</div>
      <div class="intro-anniv">치지직 스트리머 <b>김띵띵띵</b> 님의<br>방송 1주년을 축하하는 마음을 담아 만든 팬메이드 앱입니다.</div>
      <div class="intro-slot">${slot}</div>
      <button class="intro-btn" id="introOk">${seen ? "확인" : "확인했습니다"}</button>
    </div>`;
    v.querySelector("#introOk").addEventListener("click", async () => {
      if (!seen) await post("/api/intro_ack", {});   // 다음부턴 채널판으로
      v.remove();
    });
    const ch = v.querySelector("#introCh");
    if (ch) ch.addEventListener("click", () => post("/api/open_channel", {}));
    document.body.appendChild(v);   // 베일 클릭으로는 안 닫힘 — 버튼 전용 (곡천 확정)
  }

  // ══ 게임 모드 · 합방 (fix37 — 곡천 확정 스펙) ══
  // 채널 추가 = 말 추가 한 몸. [적용하고 새 회차 시작]만이 모드·구성을 확정한다 (반드시 새 회차).
  let collabInfo = { mode: "solo", my_piece: "", members: [], invites: [] };
  let modeSel = "solo";          // UI에서 고른 모드 (적용 전)
  let lastInviteUrl = "";
  function setCollab(c) {
    if (!c) return;
    collabInfo = c;
    if (!modeTouched) modeSel = c.mode || "solo";
    renderModeUI();
  }
  let modeTouched = false;       // 사용자가 카드를 눌러 바꾼 뒤엔 서버 값으로 안 덮음
  function renderModeUI() {
    const live = collabInfo.mode || "solo";
    ["Solo", "Collab"].forEach(k => {
      const el = $("modeCard" + k);
      const m = k === "Solo" ? "solo" : "collab";
      el.classList.toggle("on", modeSel === m);
      el.classList.toggle("live", live === m && modeSel === m);
    });
    $("paneSolo").hidden = modeSel !== "solo";
    $("paneCollab").hidden = modeSel !== "collab";
    if (modeSel === "collab") renderCollab();
  }
  const CH_COLORS = i => [0, 1, 2, 3, 4, 5].map(c =>
    `<button type="button" class="pl-sw c${c}" data-c="${c}" title="말 색"></button>`).join("");
  function chRow({dot, nm, tag, tagCls, piece, color, face, img, xBtn, dataAttr}) {
    const sw = [0, 1, 2, 3, 4, 5].map(c =>
      `<button type="button" class="pl-sw c${c}${color === c ? " sel" : ""}" data-c="${c}"></button>`).join("");
    img = img || "";
    const imgBtn = img   // fix40: 합방에서도 말 그림 지원 (단일 방송과 같은 선택기 공용)
      ? `<button type="button" class="pl-img ch-img has" title="말 그림 바꾸기">
           <img src="/userstickers/${encodeURIComponent(img)}" alt=""></button>`
      : `<button type="button" class="pl-img ch-img" title="이모지 대신 내 그림 쓰기">🖼</button>`;
    return `<div class="ch-row" ${dataAttr || ""} data-color="${color}" data-img="${esc(img)}">
      <span class="ch-dot ${dot}"></span><span class="ch-nm">${esc(nm)}</span>
      <span class="ch-tag${tagCls ? " " + tagCls : ""}">${esc(tag)}</span>
      <input type="text" class="ch-piece-in" maxlength="8" value="${esc(piece)}" placeholder="말 이름">
      <input type="text" class="ch-face-in" maxlength="4" value="${esc(face)}" placeholder="😊" title="말 위 이모지 (비우면 이름 첫 글자 · 그림이 있으면 그림 우선)">
      ${imgBtn}
      <span class="pl-sws">${sw}</span>${xBtn || ""}</div>`;
  }
  function renderCollab() {
    const my = collabInfo.my_piece || (state && state.players && state.players[0]) || "스트리머";
    const myStyle = (pieceStyles && pieceStyles[0]) || {};
    let html = chRow({dot: "ok", nm: "내 채널", tag: "항상 연결", piece: my,
                      color: Number(myStyle.color) || 0, face: myStyle.face || "",
                      img: myStyle.img || "", dataAttr: 'data-my="1"'});
    html += collabInfo.members.map((m, i) => chRow({
      dot: m.status === "connected" ? "ok" : (m.status === "error" || m.status === "auth_required" ? "err" : "ok"),
      nm: m.name || "채널", tag: m.status === "connected" ? "수신 중" : "연결됨",
      piece: m.piece || "", color: Number(m.color) || 0, face: m.face || "",
      img: m.img || "",     // fix40: 말 그림
      xBtn: `<button class="ch-x" data-cid="${esc(m.cid)}">해제</button>`,
      dataAttr: `data-cid="${esc(m.cid)}"`,
    })).join("");
    html += collabInfo.invites.map(inv =>
      `<div class="ch-row" data-invite="${esc(inv.id)}">
        <span class="ch-dot wait"></span><span class="ch-nm">${esc(inv.piece)}</span>
        <span class="ch-tag wait">코드 대기</span>
        <span class="dimtext small" style="flex:1">초대 링크 전달 → 로그인 후 주소를 아래 칸에 붙여넣기</span>
        <button class="ch-x" data-copyinv="${esc(inv.id)}">링크 복사</button>
        <button class="ch-x" data-cancelinv="${esc(inv.id)}">취소</button></div>`).join("");
    $("collabList").innerHTML = html;
    $("collabPasteRow").hidden = collabInfo.invites.length === 0;
    const total = 1 + collabInfo.members.length + collabInfo.invites.length;
    $("btnCollabInvite").disabled = total >= 6;
  }
  $("modeCardSolo").addEventListener("click", () => { modeSel = "solo"; modeTouched = true; renderModeUI(); });
  $("modeCardCollab").addEventListener("click", () => { modeSel = "collab"; modeTouched = true; renderModeUI(); });
  $("collabList").addEventListener("click", async e => {
    const ib = e.target.closest(".ch-img");   // fix40: 합방 말 그림 고르기
    if (ib) { openFacePickRow(ib.closest(".ch-row")); return; }
    const sw = e.target.closest(".pl-sw");
    if (sw) {
      const row = sw.closest(".ch-row");
      row.dataset.color = sw.dataset.c;
      row.querySelectorAll(".pl-sw").forEach(x => x.classList.toggle("sel", x === sw));
      return;
    }
    const cp = e.target.closest("[data-copyinv]");
    if (cp) {
      try { await navigator.clipboard.writeText(lastInviteUrl); showMsg("초대 링크 복사됨 — 멤버에게 보내주세요!"); }
      catch (err) { appPopup("🔗", "초대 링크", `<span style="word-break:break-all">${esc(lastInviteUrl)}</span>`); }
      return;
    }
    const cc = e.target.closest("[data-cancelinv]");
    if (cc) { await post("/api/collab/cancel", { invite_id: cc.dataset.cancelinv }); return; }
    const x = e.target.closest(".ch-x[data-cid]");
    if (x) {
      const cid = x.dataset.cid;
      const m = collabInfo.members.find(mm => mm.cid === cid);
      if (await appConfirm("🔌", "채널 해제", `'<b>${esc(m ? m.name : "")}</b>' 채널 연결을 해제할까요?<br>멤버는 다시 로그인해야 재연결돼요.`)) {
        await post("/api/collab/remove", { cid });
      }
    }
  });
  $("btnCollabInvite").addEventListener("click", async () => {
    const piece = $("collabPieceName").value.trim();
    if (!piece) { showMsg("말 이름을 먼저 적어 주세요 (예: 단풍이)"); return; }
    const res = await post("/api/collab/invite", { piece });
    if (!res.ok) return;
    lastInviteUrl = res.auth_url;
    $("collabPieceName").value = "";
    try { await navigator.clipboard.writeText(res.auth_url); showMsg("초대 링크 복사됨 — 멤버에게 보내주세요!"); }
    catch (err) { appPopup("🔗", "초대 링크가 만들어졌어요", `아래 주소를 멤버에게 보내주세요:<br><span style="word-break:break-all">${esc(res.auth_url)}</span>`); }
  });
  $("btnCollabRedeem").addEventListener("click", async () => {
    const text = $("collabPaste").value.trim();
    if (!text) { showMsg("멤버에게 받은 주소를 붙여넣어 주세요."); return; }
    const res = await post("/api/collab/redeem", { text });
    if (res.ok) { $("collabPaste").value = ""; showMsg(`'${res.member.name}' 채널 연결 완료!`); }
  });
  function collabApplyPayload() {
    const rows = [...document.querySelectorAll("#collabList .ch-row")];
    const myRow = rows.find(r => r.dataset.my);
    const pieces = rows.filter(r => r.dataset.cid).map(r => ({
      cid: r.dataset.cid,
      piece: r.querySelector(".ch-piece-in").value.trim(),
      color: Number(r.dataset.color) || 0,
      face: r.querySelector(".ch-face-in").value.trim().slice(0, 4),
      img: r.dataset.img || "",     // fix40: 말 그림
    }));
    return { my_piece: myRow ? myRow.querySelector(".ch-piece-in").value.trim() : "",
             my_color: myRow ? Number(myRow.dataset.color) || 0 : 0,
             my_face: myRow ? myRow.querySelector(".ch-face-in").value.trim().slice(0, 4) : "",
             my_img: myRow ? myRow.dataset.img || "" : "",     // fix40
             pieces };
  }
  $("btnModeApply").addEventListener("click", async () => {
    if (modeSel === "collab" && collabInfo.members.length < 1) {
      appPopup("🎊", "합방 채널이 아직 없어요", "합방 모드는 연결된 채널이 1개 이상 필요해요.<br>[＋ 채널 추가]로 멤버를 초대해 주세요.");
      return;
    }
    const running = state && state.running;
    const ok = await appConfirm("🔄", "적용하고 새 회차 시작",
      (running ? "지금 진행 중인 판이 <b>초기화</b>되고 " : "") +
      `<b>${modeSel === "collab" ? "🎊 합방" : "🎤 단일 방송"}</b> 모드로 새 회차가 시작돼요.<br>계속할까요?`);
    if (!ok) return;
    const body = { mode: modeSel };
    if (modeSel === "solo") {
      const list = currentList();
      body.solo = { names: list.map(pp => pp.name),
                    styles: list.map(pp => ({ color: pp.color, face: pp.face, img: pp.img || "" })) };
    } else {
      body.collab = collabApplyPayload();
    }
    const res = await post("/api/mode", body);
    if (res.ok) { modeTouched = false; showMsg("적용 완료 — 새 회차 시작!"); }
  });

  // ══ 오버레이 설정 (fix30) — 통합 소스 파츠 온오프 + 배경 (즉시 저장·전 화면 반영) ══
  // ※ 새 파츠 추가 시 OBS_PARTS·PART_BOX(기존 5곳 세트) + 이 목록 + overlay.css off- 규칙까지 세트!
  const PART_TOGGLES = [
    ["board", "보드판", "칸 + 말 (게임판 전체)"],
    ["scoreboard", "주사위 전광판", "닉네임·주사위·칸 효과 카드"],
    ["timers", "벌칙 타이머", "전광판식 타이머 칩들"],
    ["status", "상태바", "잔·안주 적립과 말 상태"],
    ["toast", "방어권 알림", "방어권 선물/사용 말풍선"],
    ["minigame", "미니게임 창", "사다리·야바위 화면"],
  ];
  let ovParts = {};        // {key: bool} — 값 없으면 표시로 간주
  let ovBg = false;
  const partOn = k => ovParts[k] !== false;
  function renderOverlayCfg() {
    $("partToggles").innerHTML = PART_TOGGLES.map(([k, nm, cap]) =>
      `<div class="tglrow">
        <input type="checkbox" class="tgl" id="tglPart_${k}" data-part="${k}" ${partOn(k) ? "checked" : ""}>
        <label for="tglPart_${k}" class="tgl-nm">${nm}
          <span class="tgl-cap"> — ${cap}${partOn(k) ? "" : " · 통합에서 숨김 (파츠 소스로 따로 추가!)"}</span></label>
      </div>`).join("");
    $("tglBg").checked = ovBg;
    $("tglBgState").textContent = ovBg ? "— 테마 배경이 같이 나가요" : "— 지금은 투명 (내 배경 위에 얹기)";
  }
  function setOverlayCfg(parts, bg) {
    if (parts) ovParts = { ...parts };
    if (typeof bg === "boolean") ovBg = bg;
    renderOverlayCfg();
  }
  async function saveOverlayCfg() {
    await post("/api/overlay_cfg", { parts: ovParts, bg: ovBg });
    // 반영 자체는 서버 브로드캐스트({"type":"overlay_cfg"})가 오버레이·다른 창까지 동시 처리
  }
  $("partToggles").addEventListener("change", e => {
    const t = e.target.closest(".tgl[data-part]");
    if (!t) return;
    ovParts[t.dataset.part] = t.checked;
    renderOverlayCfg();
    saveOverlayCfg();
  });
  $("tglBg").addEventListener("change", () => {
    ovBg = $("tglBg").checked;
    renderOverlayCfg();
    saveOverlayCfg();
  });

  // ══ 자동 업데이트 (fix28) — 새 버전 알림 칩 + 원클릭 설치 ══
  let appInfo = { fix: null, label: "", frozen: false };
  let updInfo = null;    // check_update 결과 (available일 때만 채움)
  function setAppInfo(a) {
    if (!a) return;
    appInfo = a;
    $("appVer").textContent = a.label || ("fix" + a.fix);
  }
  function setUpdate(u) {
    if (!u || !u.available) return;
    updInfo = u;
    $("updChip").hidden = false;
    $("updChip").textContent = `🔔 새 버전 fix${u.fix}`;
  }
  function updatePopup() {
    if (!updInfo) return;
    const from = appInfo.label || "지금 버전";
    if (appInfo.frozen && updInfo.url) {
      appConfirm("🔄", `새 버전 fix${updInfo.fix} 업데이트`,
        `${esc(from)} → <b>fix${updInfo.fix}</b><br>
         [확인]을 누르면 설치 파일을 받아서 설치 창이 떠요.<br>
         앱은 잠깐 꺼졌다가 새 버전으로 다시 켜면 돼요. 설정·연동은 그대로 유지!`)
        .then(async okd => {
          if (!okd) return;
          showMsg("설치 파일 다운로드 중… 잠시만요");
          const res = await post("/api/update/run", {});
          if (res.ok) appPopup("⬇️", "다운로드 시작!",
            "곧 설치 창이 떠요 — <b>다음</b>만 눌러 주세요.<br>앱과 이 창은 자동으로 꺼져요.", true);
        });
    } else {
      const pageLine = updInfo.page ? `<br><br>받는 곳:<br><b>${esc(updInfo.page)}</b>` : "";
      appPopup("🔄", `새 버전 fix${updInfo.fix}이 나왔어요`,
        `폴더판(zip)은 새 zip을 받아서 폴더만 바꿔주면 돼요.<br>
         설정·보드·연동 데이터는 따로 보관돼서 그대로 유지돼요.${pageLine}`);
    }
  }
  $("updChip").addEventListener("click", updatePopup);
  $("btnUpdCheck").addEventListener("click", async () => {
    showMsg("새 버전을 확인하는 중…");
    const res = await post("/api/update/check", {});
    if (!res.ok) return;
    if (res.available) { setUpdate(res); updatePopup(); }
    else if (!res.configured) appPopup("🔧", "업데이트 저장소가 아직 없어요",
      "동봉된 <b>자동업데이트_안내.txt</b>를 따라 GitHub 저장소를 만들고<br>" +
      "<b>업데이트주소.txt</b>에 주소를 적으면 그때부터 작동해요.<br>(방송에 꼭 필요한 건 아니에요!)");
    else appPopup("✅", "최신 버전이에요", `지금 <b>${esc(res.label || "현재 버전")}</b>이 가장 새 버전! 그대로 쓰시면 돼요.`, true);
  });
  $("btnSettings").addEventListener("click", openSettings);
  $("btnSettingsClose").addEventListener("click", closeSettings);
  $("settingsVeil").addEventListener("click", closeSettings);
  $("connChzzk").addEventListener("click", openSettings);  // 치지직 뱃지를 눌러도 설정으로
  $("connChzzk").addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") openSettings(); });

  addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!$("obsPop").hidden) $("obsPop").hidden = true;
    else if (!$("stkPicker").hidden) closePicker();
    else if (!$("facePick").hidden) closeFacePick();
    else if (!$("editor").hidden) closeEditor();
    else if (!$("settings").hidden) closeSettings();
  });

  // ---------------- 치지직 연동 ----------------
  function renderChzzk(c) {
    const badge = $("connChzzk");
    const line = $("chzzkStatusLine");
    badge.classList.add("pill");
    if (c.redirect_uri) $("redirectUri").textContent = c.redirect_uri;

    let badgeCls = "", badgeText = "치지직 —", lineCls = "", lineText = "";
    if (c.status === "connected") {
      badgeCls = "ok"; badgeText = `치지직${c.channel_name ? " · " + c.channel_name : ""}`; lineCls = "ok";
      lineText = `연결됨${c.channel_name ? " — " + c.channel_name + " 채널" : ""} · 후원과 채팅 명령을 받고 있어요.`;
    } else if (c.status === "connecting") {
      badgeCls = "warn"; badgeText = "치지직 연결 중…"; lineCls = "warn"; lineText = "치지직에 연결하는 중이에요…";
    } else if (c.status === "auth_required") {
      badgeCls = "err"; badgeText = "치지직 로그인 필요"; lineCls = "err";
      lineText = "로그인이 만료됐어요. [저장하고 연결]을 눌러 다시 로그인해 주세요.";
    } else if (c.status === "error") {
      badgeCls = "err"; badgeText = "치지직 재시도 중"; lineCls = "err";
      lineText = "연결 오류 — 자동으로 다시 시도하고 있어요. 인터넷 연결을 확인해 주세요.";
    } else if (c.has_credentials) {
      badgeCls = "warn"; badgeText = "치지직 로그인 대기"; lineCls = "warn";
      lineText = "앱 정보는 저장됐어요. [저장하고 연결]을 누르면 치지직 로그인 창이 열려요.";
    } else {
      badgeText = "치지직 미연동";
      lineText = "아직 연동 전이라 테스트 모드로 동작 중이에요. 아래 준비물 안내를 따라 주세요.";
    }
    badge.className = "pill " + badgeCls;
    badge.innerHTML = '<i class="dot"></i><span>' + esc(badgeText) + "</span>";
    badge.title = "누르면 설정이 열려요";
    line.className = "sec-status " + lineCls;
    line.innerHTML = '<i class="dot"></i>' + esc(lineText);
    if (c.has_credentials) $("chzzkId").placeholder = "Client ID (저장됨)";
    $("btnChzzkLogout").disabled = !(c.has_tokens || c.status === "connected");
  }

  $("btnChzzkConnect").addEventListener("click", async () => {
    const res = await post("/api/chzzk/connect", {
      client_id: $("chzzkId").value.trim(),
      client_secret: $("chzzkSecret").value.trim(),
    });
    if (res.ok && res.mode === "login") {
      showMsg("브라우저에 열린 치지직 로그인 창에서 로그인을 완료해 주세요.");
      $("chzzkSecret").value = "";
    } else if (res.ok) {
      showMsg("저장된 로그인 정보로 다시 연결하고 있어요.");
    }
  });

  $("btnChzzkLogout").addEventListener("click", async () => {
    if (!(await appConfirm("🔌", "치지직 연결 해제", "치지직 연결을 해제할까요?<br>다시 쓰려면 재로그인이 필요해요."))) return;
    const res = await post("/api/chzzk/logout", {});
    if (res.ok) showMsg("치지직 연결을 해제했어요.");
  });

  // ---------------- 벌칙 타이머 — 작은 칩 + 호버 팝오버 (fix15) ----------------
  // 평소엔 상태 줄의 ⏱ 칩(개수·가장 급한 시간)만 보여 왼쪽 열 높이가 절대 안 변함(미리보기 16:9 고정).
  // 커서를 올리면 전체 목록 팝오버, 타이머가 끝나면 말풍선으로 알림 (곡천 요청).
  const fmtLeft = left => `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const toastedTimers = new Set();   // 같은 타이머로 말풍선이 두 번 뜨지 않게
  function showTimerToast(html) {
    const box = $("timerToasts");
    const el = document.createElement("div");
    el.className = "tmr-toast";
    el.innerHTML = "⏱ " + html;
    box.appendChild(el);
    while (box.children.length > 3) box.removeChild(box.firstChild);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 450); }, 7000);
  }
  function renderTimers() {
    const timers = (state && state.timers) || [];
    const now = Date.now() / 1000;
    const players = (state && state.players) || [];
    const many = players.length > 1;

    // ① 방금 끝난 타이머 → 말풍선 (서버가 목록에서 지우기 전, 클라 표시 상태에서 감지)
    timers.forEach(tm => {
      if (tm.ends_at > now || now - tm.ends_at > 120) return;   // 진행 중이거나 너무 오래된 것 제외
      const key = `${tm.label}|${tm.piece || 0}|${tm.ends_at}`;
      if (toastedTimers.has(key)) return;
      toastedTimers.add(key);
      const name = esc(players[tm.piece || 0] || "?");
      const who = many ? `『${name}』 ` : "";
      showTimerToast(`${who}<b>${esc(tm.label)}</b> 타이머 끝!`);
      addLog(`⏱ ${many ? name + " — " : ""}${tm.label} 타이머 종료`);
    });

    // ② 상태 줄 칩 — 개수 + 가장 급한 남은 시간
    const act = timers.filter(tm => tm.ends_at > now)
      .sort((a, b) => a.ends_at - b.ends_at);           // 임박한 순서
    $("timerWrap").hidden = act.length === 0;            // 없으면 칩 자체를 숨김
    const box = $("timerList");
    if (act.length === 0) { box.innerHTML = ""; return; }
    const left0 = Math.max(0, Math.floor(act[0].ends_at - now));
    $("stTimerCount").textContent = act.length;
    $("stTimerNext").textContent = fmtLeft(left0);
    $("stTimerChip").classList.toggle("urgent", left0 <= 30);

    // ③ 팝오버 목록 (fix14의 칩 목록 그대로)
    box.innerHTML = act.map(tm => {
      const left = Math.max(0, Math.floor(tm.ends_at - now));
      const pi = tm.piece || 0;
      const who = many
        ? `${pdot(pi, "tp", esc(pface(pi, players[pi])))}<em>${esc(players[pi] || "?")}</em>`
        : "";
      return `<span class="tt${left <= 30 ? " urgent" : ""}">${who}` +
        `<span class="tl">${esc(tm.label)}</span><b>${fmtLeft(left)}</b></span>`;
    }).join("");
  }
  setInterval(renderTimers, 1000);

  // ---------------- REALTIME 로그 ----------------
  let logEmpty = true;
  function addLog(msg) {
    const list = $("log");
    if (logEmpty) { list.innerHTML = ""; logEmpty = false; }
    const t = new Date();
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    const ss = String(t.getSeconds()).padStart(2, "0");
    const li = document.createElement("li");
    li.innerHTML = `<time>${hh}:${mm}:${ss}</time><span>${esc(msg)}</span>`;
    list.appendChild(li);
    while (list.children.length > 80) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }

  // ---------------- 진행 인원 설정 (1~6명 스테퍼 + 말 색·이모지 — fix16) ----------------
  const PL_MAX = 6;
  let plList = [{ name: "스트리머", color: 0, face: "" }];
  function currentList() {
    return Array.from(document.querySelectorAll("#plNames .pl-row")).map((row, i) => ({
      name: row.querySelector(".pl-name").value.trim() || `말${i + 1}`,
      color: Number(row.dataset.color) || 0,
      face: row.querySelector(".pl-face").value.trim().slice(0, 4),
      img: row.dataset.img || "",
    }));
  }
  function renderPlayersCfg(players, styles) {
    if (players && players.length) {
      plList = players.slice(0, PL_MAX).map((n, i) => {
        const s = (styles || pieceStyles || [])[i] || {};
        return { name: n, color: Number.isInteger(Number(s.color)) ? Number(s.color) : i % 6,
                 face: s.face || "", img: s.img || "" };
      });
    }
    drawPlayers();
  }
  function drawPlayers() {
    $("plCountLabel").textContent = `${plList.length}명`;
    $("plMinus").disabled = plList.length <= 1;
    $("plPlus").disabled = plList.length >= PL_MAX;
    $("plNames").innerHTML = plList.map((p, i) => {
      const sw = [0, 1, 2, 3, 4, 5].map(c =>
        `<button type="button" class="pl-sw c${c}${p.color === c ? " sel" : ""}" data-c="${c}" title="말 색"></button>`
      ).join("");
      const imgBtn = p.img
        ? `<button type="button" class="pl-img has" data-i="${i}" title="말 그림 바꾸기">
             <img src="/userstickers/${encodeURIComponent(p.img)}" alt=""></button>`
        : `<button type="button" class="pl-img" data-i="${i}" title="이모지 대신 내 그림 쓰기">🖼</button>`;
      return `<div class="pl-row" data-color="${p.color}" data-img="${esc(p.img)}">
        <input type="text" class="pl-name" maxlength="8" value="${esc(p.name)}" placeholder="말 ${i + 1} 이름">
        <input type="text" class="pl-face" maxlength="4" value="${esc(p.face)}" placeholder="😊" title="말 위에 표시할 이모지나 글자 (비우면 이름 첫 글자 · 그림이 있으면 그림 우선)">
        ${imgBtn}
        <span class="pl-sws">${sw}</span>
      </div>`;
    }).join("");
  }
  $("plNames").addEventListener("click", e => {
    const ib = e.target.closest(".pl-img");
    if (ib) { openFacePick(Number(ib.dataset.i)); return; }
    const b = e.target.closest(".pl-sw");
    if (!b) return;
    const row = b.closest(".pl-row");
    row.dataset.color = b.dataset.c;
    row.querySelectorAll(".pl-sw").forEach(x => x.classList.toggle("sel", x === b));
  });

  // ── 말 그림 선택기 (fix17) — 업로드 그림 라이브러리 공용(/api/stickers)
  //    fix40: 합방 채널 행에서도 같은 선택기를 공용 (facePickRow = 합방 행 대상)
  let facePickIdx = null;
  let facePickRow = null;
  async function fillFaceGrid() {
    await fetchUserStickers();
    $("facePickGrid").innerHTML = userStickers.length
      ? userStickers.map(f =>
          `<span class="stk-cell mine"><button type="button" class="pickface" data-img="${esc(f)}" title="${esc(f)}">
            <img src="/userstickers/${encodeURIComponent(f)}" alt=""></button></span>`).join("")
      : '<span class="dimtext small">아직 올린 그림이 없어요 — [＋ 그림 올리기]로 시작!</span>';
  }
  async function openFacePick(i) {
    facePickIdx = i; facePickRow = null;
    plList = currentList();                 // 입력 중이던 값 보존
    $("facePickTitle").textContent = `말 그림 고르기 — ${plList[i] ? plList[i].name : ""}`;
    await fillFaceGrid();
    $("facePick").hidden = false;
  }
  async function openFacePickRow(row) {     // fix40: 합방 행용
    facePickRow = row; facePickIdx = null;
    const nm = row.querySelector(".ch-piece-in").value.trim()
      || row.querySelector(".ch-nm").textContent;
    $("facePickTitle").textContent = `말 그림 고르기 — ${nm}`;
    await fillFaceGrid();
    $("facePick").hidden = false;
  }
  function setPickedImg(img) {              // fix40: 대상(단일 목록/합방 행)에 그림 반영
    if (facePickRow) {
      facePickRow.dataset.img = img || "";
      const btn = facePickRow.querySelector(".ch-img");
      if (btn) {
        btn.classList.toggle("has", !!img);
        btn.innerHTML = img
          ? `<img src="/userstickers/${encodeURIComponent(img)}" alt="">` : "🖼";
      }
      closeFacePick();
      return;
    }
    if (facePickIdx === null) return;
    plList[facePickIdx].img = img || "";
    drawPlayers(); closeFacePick();
  }
  function closeFacePick() { $("facePick").hidden = true; facePickIdx = null; facePickRow = null; }
  $("facePickClose").addEventListener("click", closeFacePick);
  $("facePickGrid").addEventListener("click", e => {
    const b = e.target.closest(".pickface");
    if (!b || (facePickIdx === null && !facePickRow)) return;
    setPickedImg(b.dataset.img);
  });
  $("btnFaceClear").addEventListener("click", () => {
    if (facePickIdx === null && !facePickRow) return;
    setPickedImg("");
  });
  $("btnFaceUpload").addEventListener("click", () => $("faceFile").click());
  $("faceFile").addEventListener("change", async () => {
    const f = $("faceFile").files[0];
    $("faceFile").value = "";
    if (!f) return;
    const res = await uploadImageFile(f);     // fix41: 20MB 검사 + 자동 축소 공용 처리
    if (!res) return;
    if (facePickIdx === null && !facePickRow) return;   // fix40: 합방 행도 지원
    const forCollab = !!facePickRow;
    setPickedImg(res.file);
    showMsg(`'${res.file}' 업로드 완료 — ${forCollab ? "[적용하고 새 회차 시작]" : "[저장]"}을 눌러야 적용돼요.`);
  });
  $("plMinus").addEventListener("click", () => {
    if (plList.length <= 1) return;
    plList = currentList().slice(0, -1);
    drawPlayers();
  });
  $("plPlus").addEventListener("click", () => {
    if (plList.length >= PL_MAX) return;
    plList = currentList();
    plList.push({ name: plList.length === 1 ? "게스트" : `말${plList.length + 1}`,
                  color: plList.length % 6, face: "" });
    drawPlayers();
  });
  $("btnPlayersSave").addEventListener("click", async () => {
    plList = currentList();
    const res = await post("/api/players", {
      names: plList.map(p => p.name),
      styles: plList.map(p => ({ color: p.color, face: p.face, img: p.img || "" })),
    });
    if (res.ok) showMsg(`진행 인원 저장 — 색·이모지는 바로, 이름·인원은 다음 회차부터`);
  });

  // ---------------- OBS 소스 목록 (fix11: 부품별 투명 소스) ----------------
  const OBS_PARTS = [
    { key: "",           nm: "전체 합본",   sz: "1920×1080" },
    { key: "board",      nm: "보드만",      sz: "1920×1080" },
    { key: "scoreboard", nm: "전광판(주사위)", sz: "560×760" },
    { key: "timers",     nm: "벌칙 타이머", sz: "420×420" },
    { key: "status",     nm: "상태바",      sz: "1200×90" },
    { key: "toast",      nm: "방어권 알림", sz: "760×120" },
    { key: "minigame",   nm: "미니게임 창", sz: "880×560" },
  ];
  let overlayBase = "";
  function renderNet(net) {
    if (!net || !net.overlay_url) return;
    overlayBase = net.overlay_url;
    $("obsUrl").textContent = overlayBase;
    $("obsList").innerHTML = OBS_PARTS.map(pt => {
      const url = pt.key ? `${overlayBase}?part=${pt.key}` : overlayBase;
      return `<button class="obs-row" data-url="${esc(url)}">
        <span class="nm">${pt.nm}</span><span class="sz">${pt.sz}</span><code>${esc(url)}</code>
      </button>`;
    }).join("");
  }
  $("obsChip").addEventListener("click", e => {
    e.stopPropagation();
    $("obsPop").hidden = !$("obsPop").hidden;
  });
  $("obsList").addEventListener("click", async e => {
    const row = e.target.closest(".obs-row");
    if (!row) return;
    try {
      await navigator.clipboard.writeText(row.dataset.url);
      const sz = row.querySelector(".sz").textContent;
      showMsg(`주소 복사됨 — OBS 브라우저 소스 크기 ${sz} 권장 (다른 크기여도 비율 맞춰 자동 조절 — fix25)`);
    } catch (err) {
      showMsg("복사가 막혀 있어요. 주소를 드래그해서 복사해 주세요.");
    }
    $("obsPop").hidden = true;
  });
  addEventListener("click", e => {
    if (!$("obsPop").hidden && !e.target.closest(".obs-wrap")) $("obsPop").hidden = true;
  });

  // ---------------- WebSocket ----------------
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => setPill("connWs", "ok", "서버 연결됨");
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "init") {
        board = msg.board;
        state = msg.state;
        lastQueueJson = "";
        applyTileSkin((msg.config || {}).tile_skin);   // fix42: renderBoard 전에 방식 확정
        renderBoard(); fit();
        renderChzzk(msg.chzzk || {});
        renderNet(msg.net || {});
        pieceStyles = (msg.config || {}).piece_style || [];
        mgGame = msg.minigame || null; renderMg();
        renderPlayersCfg((msg.config || {}).players, pieceStyles);
        // fix27: 테마·규칙·주사위 설정 반영
        const c = msg.config || {};
        themeSel = c.board_theme || "cotton";
        fontSel = c.board_font || "jua";
        sizeSel = Number(c.board_font_scale) || 1;
        renderThemeUI();
        applyBoardPreview(c.palette, c.board_font_stack, sizeSel);
        fillRules(c);
        setAppInfo(msg.app);       // fix28: 현재 버전 표시
        setUpdate(msg.update);     // 기동 시 이미 발견된 새 버전
        setOverlayCfg(c.overlay_parts, !!c.overlay_bg);   // fix30: 오버레이 설정
        setCollab(msg.collab);                            // fix37: 게임 모드·합방
        showIntro(!!(msg.app && msg.app.intro_seen));     // fix38: 1주년 인트로 (로드당 1회)
        diceRows = (c.dice_faces && c.dice_faces.length)
          ? c.dice_faces.map(f => ({ ...f })) : defaultDice();
        drawDice();
        render();
      } else if (msg.type === "state") {
        state = msg.state; render();
      } else if (msg.type === "board") {
        board = msg.board; renderBoard();
        if (state) render();
      } else if (msg.type === "chzzk") {
        renderChzzk(msg);
      } else if (msg.type === "notice") {
        showMsg(msg.msg);
        addLog(msg.msg);
      } else if (msg.type === "log") {
        addLog(msg.msg);
      } else if (msg.type === "minigame") {
        mgGame = msg.game; renderMg();
      } else if (msg.type === "players_cfg") {
        if (msg.styles) pieceStyles = msg.styles;
        renderPlayersCfg(msg.players, msg.styles);
        if (state) render();   // 말 색·이모지 즉시 반영
      } else if (msg.type === "theme") {
        // fix27: 테마 저장 브로드캐스트 — 미리보기에도 동시 적용
        applyBoardPreview(msg.palette, msg.font_stack, msg.font_scale);
      } else if (msg.type === "dice_cfg") {
        diceRows = (msg.faces || []).map(f => ({ ...f }));
        if (!diceRows.length) diceRows = defaultDice();
        drawDice();
      } else if (msg.type === "rules_cfg") {
        fillRules(msg.config || {});
      } else if (msg.type === "update") {
        setAppInfo(msg.app);
        setUpdate(msg.update);     // fix28: 새 버전 발견 브로드캐스트
      } else if (msg.type === "overlay_cfg") {
        setOverlayCfg(msg.parts, msg.bg);   // fix30: 다른 창에서 바꿔도 동기화
      } else if (msg.type === "tileskin") {
        applyTileSkin(msg.tile_skin);       // fix42: 다른 창에서 바꿔도 동기화
        renderBoard();
      } else if (msg.type === "collab_cfg") {
        setCollab(msg);                     // fix37: 합방 구성·연결 상태 동기화
      }
    };
    ws.onclose = () => {
      setPill("connWs", "warn", "서버 재연결 중…");
      setTimeout(connect, 1500);
    };
  }
  connect();

  // ---------------- 진행 버튼 ----------------
  $("btnStart").addEventListener("click", async () => {
    if (state && state.running &&
        !(await appConfirm("🎲", "새 회차 시작", "새 회차를 시작할까요?<br>현재 진행 상태가 초기화돼요."))) return;
    cmd("start");
  });
  $("btnPause").addEventListener("click", () => cmd("pause"));
  $("btnNext").addEventListener("click", () => cmd("next"));
  $("btnAdd").addEventListener("click", () => cmd("add"));
  $("btnUndo").addEventListener("click", () => cmd("undo"));
  $("btnDefend").addEventListener("click", () => cmd("defend"));
  $("guardAssign").addEventListener("click", e => {
    const b = e.target.closest(".ga-btn");
    if (!b) return;
    cmd("assign_guard", { gift_id: b.dataset.gift, piece: Number(b.dataset.piece) });
  });
  $("btnTimer").addEventListener("click", () => cmd("timer"));

  // ---------------- 테스트 후원 ----------------
  async function sendTest(amount) {
    const nick = $("testNick").value.trim() || "테스트";
    const res = await post("/api/test/donation", { nick, amount });
    if (res.ok) {
      if (res.kind === "guard") showMsg(`${nick} → 방어권 +1`);
      else if (res.kind === "rolls") showMsg(`${nick} → ${res.rolls}굴림 큐 적재`);
      else if (res.kind === "ignored") showMsg("기준 금액 미만 — 로그만 기록");
    }
  }
  $("btnTestSend").addEventListener("click", () => sendTest(Number($("testAmount").value)));
  // fix29: 바로가기 버튼이 동적 생성되므로 위임 방식으로 (규칙 변경 시 재바인딩 불필요)
  renderTestQuick(null);
  $("testQuick").addEventListener("click", e => {
    const btn = e.target.closest(".hbtn.mini[data-amt]");
    if (btn) sendTest(Number(btn.dataset.amt));
  });
})();
