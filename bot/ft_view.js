// ft_view.js — หน้า FT Tomorn (delta neutral) ในเว็บ Tomorn · ใช้ตัวช่วย ($, el, fmt, sign, pct, dt, css, niceTicks, showBubble) จาก dashboard.html
let FT = null;

const FT_HTML = `
  <div class="banner crit" id="ft-banner-halt"></div>
  <div class="banner warn" id="ft-banner-stale"></div>
  <section class="tiles">
    <div class="tile"><div class="ico">💰</div><div class="label">มูลค่าพอร์ต FT</div><div class="value" id="ft-equity">—</div><div class="sub" id="ft-equity-sub">—</div></div>
    <div class="tile"><div class="ico">💸</div><div class="label">ค่า funding ที่เก็บได้</div><div class="value" id="ft-funding">—</div><div class="sub" id="ft-funding-sub">—</div></div>
    <div class="tile"><div class="ico">🧾</div><div class="label">ค่าธรรมเนียมที่จ่าย</div><div class="value" id="ft-fees">—</div><div class="sub">เปิด/ปิด/ปรับสมดุล ทั้งสองขา</div></div>
    <div class="tile"><div class="ico">📡</div><div class="label">funding รอบนี้</div><div class="value" id="ft-rate">—</div><div class="sub" id="ft-rate-sub">—</div></div>
    <div class="tile"><div class="ico">🪙</div><div class="label" id="ft-px-label">ราคา BTC</div><div class="value" id="ft-px">—</div><div class="sub" id="ft-px-sub">—</div></div>
  </section>
  <div class="grid">
    <div class="card">
      <h2><span class="em">⚖️</span>สองขาหักล้างกัน</h2>
      <div class="desc" id="ft-legs-desc">ถือเหรียญใน Spot + Short ขนาดเท่ากัน → ราคาขึ้นหรือลง สองขาหักล้างกัน เหลือแต่ค่า funding</div>
      <div id="ft-legs"></div>
    </div>
    <div class="side">
      <div class="card">
        <h2><span class="em">🧭</span>บอทตัดสินใจยังไง</h2>
        <div class="desc" id="ft-plan-desc">—</div>
        <div id="ft-plan"></div>
      </div>
      <div class="card">
        <h2><span class="em">🛡️</span>ความปลอดภัย</h2>
        <div class="desc">margin ขา Short เทียบกับตอนเปิด · ต่ำกว่า 50% หรือเกิน 180% บอทปรับสมดุลเอง</div>
        <div id="ft-safety"></div>
      </div>
    </div>
    <div class="card full">
      <h2><span class="em">🌱</span>พอร์ต FT โตขึ้นยังไงบ้าง</h2>
      <div class="desc">บันทึกทุกชั่วโมง · เส้นประ = ทุนเริ่มต้น</div>
      <div class="chart" id="ft-equity-chart"><div class="tip"></div></div>
    </div>
    <div class="card full">
      <h2><span class="em">💰</span>ค่า funding แต่ละรอบ</h2>
      <div class="desc">ได้ทุก 8 ชม. (07:00 · 15:00 · 23:00 น.) · แท่งเขียว = ได้รับ · แท่งแดง = จ่าย</div>
      <div class="chart" id="ft-funding-chart"><div class="tip"></div></div>
    </div>
    <div class="card full">
      <h2><span class="em">📒</span>สมุดบันทึกของบอท FT</h2>
      <div class="desc">ล่าสุดอยู่บน</div>
      <div id="ft-events"></div>
    </div>
  </div>`;

async function loadFT() {
  const box = $("#view-ft");
  if (!box.dataset.ready) { box.innerHTML = FT_HTML; box.dataset.ready = "1"; ftObserve(); }
  try {
    const r = await fetch("/api/ft", { cache: "no-store" }); FT = await r.json();
    if (FT.error) throw new Error(FT.error);
    renderFT();
  } catch (e) {
    $("#status-chip").innerHTML = `<span class="dot off"></span><span>ติดต่อเว็บไม่ได้ — dashboard.py ยังเปิดอยู่ไหม?</span>`;
  }
}

const ftHover = {};
function ftObserve() {
  const seen = new WeakMap();
  const ro = new ResizeObserver(es => es.forEach(en => {
    const w = Math.round(en.contentRect.width);
    if (!FT || VIEW !== "ft" || !w || seen.get(en.target) === w) return;
    seen.set(en.target, w); en.target.id === "ft-equity-chart" ? ftEquity() : ftFunding();
  }));
  ["ft-equity-chart", "ft-funding-chart"].forEach(id => {
    const b = document.getElementById(id); ro.observe(b);
    b.addEventListener("pointerenter", () => ftHover[id] = true);
    b.addEventListener("pointerleave", () => ftHover[id] = false);
  });
}

const qty = v => (+v).toFixed(8).replace(/\.?0+$/, "");
const until = ms => { if (ms <= 0) return "อีกไม่กี่วินาที"; const m = Math.floor(ms / 60000); return `อีก ${Math.floor(m / 60)} ชม. ${m % 60} นาที`; };

function renderFT() {
  const F = FT, S = F.state, L = F.live || {}, C = F.config;
  $("#mode-chip").innerHTML = `โหมด <b>Demo · delta neutral</b>`;
  $("#status-chip").innerHTML = `<span class="dot ${F.alive ? "on" : "off"}"></span><span>${F.alive ? "บอท FT ทำงานอยู่" : "บอท FT ไม่ได้ทำงาน"}</span>`;
  $("#updated").innerHTML = L.mark ? `<span class="live-dot"></span>ราคาตลาด ${new Date(F.now).toLocaleTimeString("th-TH", { timeZone: TZ })} · Binance`
                                   : `<span class="live-dot off"></span>ดึงราคาไม่ได้ ใช้ข้อมูลที่บอทเก็บไว้`;
  const stale = $("#ft-banner-stale"), halt = $("#ft-banner-halt");
  if (!S) {
    stale.classList.add("show");
    stale.innerHTML = `🐣 <b>ยังไม่มีข้อมูลบอท FT</b> — เปิดบอทด้วย <code>cd ~/Desktop/Tomorn/projects/ft-tomorn/bot</code> แล้ว <code>caffeinate -i python3 ft_runner.py</code>`;
    ftMascot(); return;
  }
  stale.classList.toggle("show", !F.alive);
  stale.innerHTML = `⚠️ <b>บอท FT ไม่ได้อัปเดต${F.last_write ? " " + ago(F.now - F.last_write) : ""}</b> — เปิดด้วย <code>caffeinate -i python3 ft_runner.py</code> ในโฟลเดอร์ ft-tomorn/bot`;
  halt.classList.toggle("show", !!S.halted);
  halt.innerHTML = `🛑 <b>บอท FT หยุดตัวเอง:</b> ${S.halt_reason || ""} — ตรวจแล้วปลดล็อกด้วย <code>python3 ft_runner.py --reset-halt</code>`;

  const eq = F.equity, ret = (eq / S.start - 1) * 100;
  $("#ft-equity").textContent = fmt(eq);
  $("#ft-equity-sub").innerHTML = `USDT · <span class="${ret >= 0 ? "pos-text" : "neg-text"}">${pct(ret, 3)}</span> จากทุน ${fmt(S.start, 0)}`;
  const nPay = (S.funding_log || []).length;
  $("#ft-funding").innerHTML = `<span class="${S.funding_total >= 0 ? "pos-text" : "neg-text"}">${sign(S.funding_total, 4)}</span>`;
  $("#ft-funding-sub").textContent = nPay ? `USDT · ${nPay} รอบ` : "USDT · รอบแรกจะเข้าเร็ว ๆ นี้";
  $("#ft-fees").textContent = fmt(S.fees_total);
  if (L.rate != null) {
    const apr = L.rate * 3 * 365 * 100;
    $("#ft-rate").innerHTML = `<span class="${L.rate >= 0 ? "pos-text" : "neg-text"}">${(L.rate * 100).toFixed(4)}%</span>`;
    $("#ft-rate-sub").textContent = `≈ ${apr >= 0 ? "+" : ""}${apr.toFixed(1)}%/ปี · รอบถัดไป${until(L.next - F.now)}`;
  }
  $("#ft-px-label").textContent = `ราคา ${F.symbol.replace("USDT", "")}`;
  $("#ft-px").textContent = fmt(L.mark);
  $("#ft-px-sub").textContent = L.bid ? `Spot ${fmt(L.bid)} · ส่วนต่าง ${pct((L.mark / L.bid - 1) * 100, 3)}` : "—";

  // ---- สองขา ----
  const p = S.pos, legs = $("#ft-legs");
  if (p && F.legs) {
    const g = F.legs, rows = [["🟢 ขา Spot (ถือเหรียญ)", g.spot_pnl], ["🔴 ขา Short (Futures)", g.short_pnl],
      ["= รวมสองขา", g.spot_pnl + g.short_pnl], ["💸 funding ไม้นี้", p.funding], ["🧾 ค่าธรรมเนียมไม้นี้", -g.fees]];
    const mx = Math.max(1, ...rows.map(r => Math.abs(r[1])));
    $("#ft-legs-desc").textContent = `${p.sym} · เปิดเมื่อ ${dt(p.open_time)} · Spot ${qty(p.q)} ${p.base} + Short ${qty(p.fq)} @ ${fmt(p.fut_entry)}`;
    legs.innerHTML = rows.map(([lab, v], i) => `
      <div class="leg${i === 2 ? " leg-sum" : ""}"><span class="leg-l">${lab}</span>
        <div class="leg-track"><div class="leg-bar ${v >= 0 ? "pos" : "neg"}" style="width:${Math.abs(v) / mx * 50}%;${v >= 0 ? "left:50%" : `right:50%`}"></div></div>
        <b class="${v >= 0 ? "pos-text" : "neg-text"}">${sign(v)}</b></div>`).join("") +
      `<div class="leg-note">ราคาขยับจากตอนเปิด ${pct((L.mark / p.fut_entry - 1) * 100)} — สองแท่งแรกยาวพอ ๆ กันแต่คนละทิศ คือ hedge ทำงานอยู่ 🐼</div>`;
  } else {
    $("#ft-legs-desc").textContent = "ยังไม่ถือ — รอ funding 7 วันล่าสุดดีกว่าเกณฑ์ก่อน";
    legs.innerHTML = `<div class="empty">บอทถือเงินสดไว้ จะเปิดสองขาเมื่อ funding คุ้ม (ตัดสินใจหลังรอบ funding ทุก 8 ชม.)</div>`;
  }

  // ---- เกณฑ์ตัดสินใจ ----
  const a7 = (S.apr || {})[F.symbol], lo = -10, hi = 30, pos = v => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
  $("#ft-plan-desc").textContent = `กลยุทธ์ ${C.strategy} · เปิดเมื่อ funding 7 วัน > ${C.enter}%/ปี · ปิดเมื่อ < ${C.exit}%/ปี · เลเวอเรจ ${C.leverage}x`;
  $("#ft-plan").innerHTML = a7 == null ? `<div class="empty">รอข้อมูล funding 7 วัน…</div>` : `
    <div class="gauge"><div class="gz" style="left:0;width:${pos(C.exit)}%;background:rgba(255,107,129,.35)"></div>
      <div class="gz" style="left:${pos(C.exit)}%;width:${pos(C.enter) - pos(C.exit)}%;background:rgba(255,214,102,.28)"></div>
      <div class="gz" style="left:${pos(C.enter)}%;right:0;background:rgba(47,211,154,.35)"></div>
      <div class="gmark" style="left:${pos(C.exit)}%"><span>ออก ${C.exit}%</span></div>
      <div class="gmark" style="left:${pos(C.enter)}%"><span>เข้า ${C.enter}%</span></div>
      <div class="now" style="left:${pos(a7)}%">🐼</div></div>
    <dl class="kv" style="margin-top:30px"><dt>funding 7 วันล่าสุด</dt><dd><b class="${a7 >= 0 ? "pos-text" : "neg-text"}">${a7 >= 0 ? "+" : ""}${a7.toFixed(2)}%/ปี</b></dd>
    <dt>ตอนนี้บอท</dt><dd>${p ? "ถือ " + p.sym : "ถือเงินสด"}</dd></dl>`;

  // ---- ความปลอดภัย ----
  const mr = F.margin_ratio;
  $("#ft-safety").innerHTML = p ? `
    <div class="gate-row"><div class="top"><span>margin ขา Short</span><b>${(mr * 100).toFixed(0)}%</b></div>
      <div class="bar" style="position:relative"><div style="width:${Math.min(100, mr / 2 * 100)}%;background:${mr < 0.6 || mr > 1.7 ? "var(--warning)" : "var(--good)"}"></div></div></div>
    <div class="check"><span class="ic">${F.legs_match ? "✅" : "⚠️"}</span>สองขาขนาดเท่ากัน (Spot ${qty(p.q)} / Short ${qty(p.fq)})</div>
    <div class="check"><span class="ic">${S.halted ? "🛑" : "✅"}</span>${S.halted ? "บอทหยุดตัวเองอยู่" : "ไม่มีความผิดปกติ"}</div>`
    : `<div class="check"><span class="ic">${S.halted ? "🛑" : "✅"}</span>${S.halted ? "บอทหยุดตัวเองอยู่" : "ไม่มีไม้ค้าง ไม่มีความเสี่ยง"}</div>`;

  // ---- บันทึก ----
  const ev = (S.events || []).slice().reverse();
  $("#ft-events").innerHTML = ev.length ? `<div class="evlist">${ev.map(([t, txt]) => `<div class="ev"><span class="tag">${dt(t)}</span><span>${txt.replace(/\n/g, "<br>")}</span></div>`).join("")}</div>`
    : `<div class="empty">ยังไม่มีบันทึก</div>`;

  $("#footer").innerHTML = `FT Tomorn ใช้เงินปลอมของ Binance Demo Trading · ทุนของบอท $${fmt(S.start, 0)} · เลเวอเรจ ${C.leverage}x ·
    ค่าธรรมเนียม Spot ${(C.spot_fee * 100).toFixed(2)}% + Futures ${(C.fut_fee * 100).toFixed(2)}%<br>
    backtest 5 ปี: BTC delta neutral ≈ +3–4%/ปี (12 เดือนล่าสุด ~1%) · ผลในอดีตไม่รับประกันผลในอนาคต`;
  if (!ftHover["ft-equity-chart"]) ftEquity();
  if (!ftHover["ft-funding-chart"]) ftFunding();
  ftMascot();
}

// ---- กราฟพอร์ต ----
function ftEquity() {
  const box = $("#ft-equity-chart"), tip = box.querySelector(".tip");
  box.querySelectorAll("svg, .empty").forEach(s => s.remove());
  const S = FT && FT.state; if (!S) return;
  const pts = (S.curve || []).concat([[FT.now, FT.equity]]), W = box.clientWidth, H = 200, m = { l: 6, r: 70, t: 12, b: 26 };
  if (!W) return;
  const t0 = pts[0][0], t1 = Math.max(pts[pts.length - 1][0], t0 + 3600000);
  let lo = Math.min(S.start, ...pts.map(p => p[1])), hi = Math.max(S.start, ...pts.map(p => p[1]));
  const pad = Math.max((hi - lo) * 0.2, S.start * 0.002); lo -= pad; hi += pad;
  const x = t => m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r), y = v => m.t + (hi - v) / (hi - lo) * (H - m.t - m.b);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }); box.insertBefore(svg, tip);
  niceTicks(lo, hi, 4).forEach(v => {
    el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: css("--grid") }, svg);
    el("text", { x: W - m.r + 8, y: y(v) + 4, fill: css("--text-muted"), "font-size": 11 }, svg).textContent = fmt(v, 1);
  });
  el("text", { x: m.l, y: H - 8, fill: css("--text-muted"), "font-size": 11 }, svg).textContent = dt(t0);
  el("text", { x: W - m.r, y: H - 8, fill: css("--text-muted"), "font-size": 11, "text-anchor": "end" }, svg).textContent = "ตอนนี้";
  el("line", { x1: m.l, x2: W - m.r, y1: y(S.start), y2: y(S.start), stroke: css("--text-muted"), "stroke-dasharray": "5 4" }, svg);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const g = el("linearGradient", { id: "fteq", x1: 0, x2: 0, y1: 0, y2: 1 }, el("defs", {}, svg));
  el("stop", { offset: "0%", "stop-color": css("--mint"), "stop-opacity": .45 }, g);
  el("stop", { offset: "100%", "stop-color": css("--mint"), "stop-opacity": 0 }, g);
  el("path", { d: d + ` L${x(pts[pts.length - 1][0])},${H - m.b} L${x(t0)},${H - m.b} Z`, fill: "url(#fteq)" }, svg);
  el("path", { d, fill: "none", stroke: css("--good"), "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const last = pts[pts.length - 1];
  el("circle", { cx: x(last[0]), cy: y(last[1]), r: 5, fill: css("--good"), stroke: css("--surface-1"), "stroke-width": 2 }, svg);
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", ev => {
    const px = ev.clientX - svg.getBoundingClientRect().left;
    let k = 0; pts.forEach((p, i) => { if (Math.abs(x(p[0]) - px) < Math.abs(x(pts[k][0]) - px)) k = i; });
    tip.innerHTML = `<div class="t">${k === pts.length - 1 ? "ตอนนี้" : dt(pts[k][0])}</div><div class="row"><span>พอร์ต</span><b>${fmt(pts[k][1])} USDT</b></div>`;
    tip.style.display = "block"; let left = x(pts[k][0]) + 12; if (left + tip.offsetWidth > W) left = x(pts[k][0]) - tip.offsetWidth - 12;
    tip.style.left = left + "px"; tip.style.top = Math.max(0, y(pts[k][1]) - 50) + "px";
  });
  hit.addEventListener("pointerleave", () => tip.style.display = "none");
}

// ---- กราฟ funding แต่ละรอบ ----
function ftFunding() {
  const box = $("#ft-funding-chart"), tip = box.querySelector(".tip");
  box.querySelectorAll("svg, .empty").forEach(s => s.remove());
  const S = FT && FT.state; if (!S) return;
  const log = (S.funding_log || []).slice(-90), W = box.clientWidth, H = 170, m = { l: 6, r: 70, t: 12, b: 26 };
  if (!W) return;
  if (!log.length) {
    const nx = FT.live && FT.live.next ? ` · รอบถัดไป ${new Date(FT.live.next).toLocaleTimeString("th-TH", { timeZone: TZ, hour: "2-digit", minute: "2-digit" })} น.` : "";
    box.insertAdjacentHTML("afterbegin", `<div class="empty">ยังไม่มีรอบ funding ที่เก็บได้${nx} 🐼</div>`); return;
  }
  const hi = Math.max(0, ...log.map(f => f[1])), lo = Math.min(0, ...log.map(f => f[1])), span = (hi - lo) || 1;
  const top = hi + span * 0.15, bot = lo - span * (lo < 0 ? 0.15 : 0);
  const y = v => m.t + (top - v) / (top - bot) * (H - m.t - m.b), n = log.length, step = (W - m.l - m.r) / n;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }); box.insertBefore(svg, tip);
  niceTicks(bot, top, 3).forEach(v => {
    el("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: css("--grid") }, svg);
    el("text", { x: W - m.r + 8, y: y(v) + 4, fill: css("--text-muted"), "font-size": 11 }, svg).textContent = "$" + v.toFixed(3);
  });
  const bw = Math.max(3, Math.min(26, step * 0.6));
  log.forEach(([t, v], i) => {
    const cx = m.l + step * (i + 0.5), y0 = y(0), y1 = y(v);
    el("rect", { x: cx - bw / 2, y: Math.min(y0, y1), width: bw, height: Math.max(1.5, Math.abs(y1 - y0)), rx: Math.min(4, bw / 2),
      fill: css(v >= 0 ? "--good" : "--critical") }, svg);
  });
  el("line", { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), stroke: css("--text-muted") }, svg);
  el("text", { x: m.l, y: H - 8, fill: css("--text-muted"), "font-size": 11 }, svg).textContent = dt(log[0][0]);
  el("text", { x: W - m.r, y: H - 8, fill: css("--text-muted"), "font-size": 11, "text-anchor": "end" }, svg).textContent = dt(log[n - 1][0]);
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  hit.addEventListener("pointermove", ev => {
    const i = Math.max(0, Math.min(n - 1, Math.floor((ev.clientX - svg.getBoundingClientRect().left - m.l) / step)));
    const sum = log.slice(0, i + 1).reduce((a, f) => a + f[1], 0);
    tip.innerHTML = `<div class="t">${dt(log[i][0])}</div><div class="row"><span>รอบนี้</span><b class="${log[i][1] >= 0 ? "pos-text" : "neg-text"}">${sign(log[i][1], 4)}</b></div>
      <div class="row"><span>สะสม</span><b>${sign(sum, 4)}</b></div>`;
    tip.style.display = "block"; const cx = m.l + step * (i + 0.5); let left = cx + 12; if (left + tip.offsetWidth > W) left = cx - tip.offsetWidth - 12;
    tip.style.left = left + "px"; tip.style.top = m.t + "px";
  });
  hit.addEventListener("pointerleave", () => tip.style.display = "none");
}

// ---- น้องโตมรในหน้า FT ----
function ftMascot() {
  const F = FT, S = F && F.state, box = $("#tomorn");
  let mood = "idle", say, small = "";
  const lastPay = S && (S.funding_log || []).slice(-1)[0];
  if (!S) { mood = "sleep"; say = "บอท FT ยังไม่ตื่นเลย 😴"; small = "เปิดด้วย caffeinate -i python3 ft_runner.py ในโฟลเดอร์ ft-tomorn/bot"; }
  else if (!F.alive) { mood = "sleep"; say = "งือ… บอท FT หลับอยู่ 😴"; small = "ปลุกได้ที่ Terminal: cd ~/Desktop/Tomorn/projects/ft-tomorn/bot แล้วพิมพ์ caffeinate -i python3 ft_runner.py"; }
  else if (S.halted) { mood = "sad"; say = "ถอดแว่นพักก่อน 🛑"; small = S.halt_reason || ""; }
  else if (lastPay && F.now - lastPay[0] < 45 * 60000) {
    if (lastPay[1] >= 0) { mood = "happy"; say = `ได้ค่า funding ${sign(lastPay[1], 4)} USDT ชิล ๆ 😎✨`; small = `สะสมแล้ว ${sign(S.funding_total, 4)} USDT`; }
    else { mood = "sad"; say = `รอบนี้ต้องจ่าย funding ${sign(lastPay[1], 4)} 🥺`; small = "ถ้าติดลบนาน บอทจะปิดไม้ให้เองน้า"; }
  } else if (S.pos) {
    say = "โตมรคุมเกม funding อยู่ 😎"; small = F.live && F.live.next ? `รอบถัดไป${until(F.live.next - F.now)} · ราคาขึ้นลงไม่กระทบ เพราะสองขาหักล้างกัน` : "";
  } else { say = "ยังไม่ถึงจังหวะ รอก่อน 😎"; small = `เปิดเมื่อ funding 7 วัน > ${F.config.enter}%/ปี`; }
  box.classList.toggle("sleeping", mood === "sleep");
  box.classList.toggle("sad", mood === "sad");
  box.classList.toggle("happy", mood === "happy");
  if (Date.now() < quipUntil) return;
  const key = "ft" + mood + (lastPay ? lastPay[0] : "") + (S && S.pos ? S.pos.open_time : "");
  const b = $("#bubble"), html = `${say}${small ? `<small>${small}</small>` : ""}`;
  if (key !== lastMood) { lastMood = key; b.innerHTML = html; b.style.animation = "none"; b.offsetHeight; b.style.animation = ""; showBubble(); }
  else if (b.innerHTML !== html) b.innerHTML = html;
}

// ---- สไตล์เฉพาะหน้า FT ----
document.head.insertAdjacentHTML("beforeend", `<style>
  .leg { display: grid; grid-template-columns: 170px 1fr 92px; gap: 12px; align-items: center; padding: 9px 4px; font-size: 14px; }
  .leg b { text-align: right; font-variant-numeric: tabular-nums; }
  .leg-sum { border-top: 2px dashed var(--grid); margin-top: 4px; padding-top: 13px; font-weight: 600; }
  .leg-track { position: relative; height: 16px; background: var(--surface-2); border-radius: 99px; }
  .leg-track::after { content: ""; position: absolute; left: 50%; top: -4px; bottom: -4px; width: 2px; background: var(--text-muted); opacity: .4; }
  .leg-bar { position: absolute; top: 0; bottom: 0; border-radius: 99px; min-width: 3px; }
  .leg-bar.pos { background: linear-gradient(90deg, #9be3c6, var(--good)); } .leg-bar.neg { background: linear-gradient(90deg, var(--critical), #ffb3b3); }
  .leg-note { margin-top: 12px; padding: 10px 14px; background: var(--mint-bg); border-radius: 16px; font-size: 13px; color: var(--text-secondary); }
  .gauge { position: relative; height: 14px; border-radius: 99px; margin: 26px 0 6px; overflow: visible; }
  .gauge .gz { position: absolute; top: 0; bottom: 0; } .gauge .gz:first-child { border-radius: 99px 0 0 99px; } .gauge .gz:nth-child(3) { border-radius: 0 99px 99px 0; }
  .gauge .gmark { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--text-secondary); }
  .gauge .gmark span { position: absolute; top: 20px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--text-muted); white-space: nowrap; }
  .gauge .now { position: absolute; top: -12px; font-size: 22px; transform: translateX(-50%); filter: drop-shadow(0 2px 3px rgba(35,48,79,.2)); }
  .evlist { display: flex; flex-direction: column; gap: 8px; max-height: 360px; overflow-y: auto; }
  .ev { display: flex; gap: 12px; align-items: flex-start; padding: 10px 12px; background: var(--surface-2); border-radius: 16px; font-size: 13.5px; line-height: 1.55; }
  .ev:nth-child(3n+2) { background: var(--pink-bg); } .ev:nth-child(3n+3) { background: var(--mint-bg); }
  @media (max-width: 600px) { .leg { grid-template-columns: 1fr 76px; } .leg-track { grid-column: 1 / -1; grid-row: 2; }
    .tabs { display: flex; } .tabs button { flex: 1; justify-content: center; padding: 8px 10px; } .tabs button small { display: none; } }
</style>`);

// ================= โหมดมืด + น้องโตมรเวอร์ชันเท่ (เฉพาะหน้า FT) =================
const ORIG_SUB = document.querySelector(".logo small").textContent;
function onViewChange(v) {
  const dark = v === "ft";
  document.body.classList.toggle("ft-dark", dark);
  document.querySelector(".logo small").textContent = dark ? "ห้องบัญชาการ FT ของโตมร ⚡" : ORIG_SUB;
  const tc = document.querySelector('meta[name="theme-color"]'); if (tc) tc.content = dark ? "#0a1430" : "#eaf4ff";
}
// แว่นกันแดดของน้องโตมร (วาดเพิ่มใน SVG ตัวเดิม · โชว์เฉพาะโหมดมืด)
document.querySelector("#panda svg").insertAdjacentHTML("beforeend", `
  <defs><linearGradient id="lens" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2a3a66"/><stop offset=".55" stop-color="#0b1022"/><stop offset="1" stop-color="#1b2b55"/></linearGradient></defs>
  <g class="cool cool-glasses">
    <path d="M36 44 Q37 41 41 41 L69 41 Q72 41 71 45 L69 55 Q67 62 58 62 L48 62 Q39 62 37 55 Z" fill="url(#lens)" stroke="#0b0f1f" stroke-width="2.4" stroke-linejoin="round"/>
    <path d="M114 44 Q113 41 109 41 L81 41 Q78 41 79 45 L81 55 Q83 62 92 62 L102 62 Q111 62 113 55 Z" fill="url(#lens)" stroke="#0b0f1f" stroke-width="2.4" stroke-linejoin="round"/>
    <path d="M70 46 Q75 42 80 46" fill="none" stroke="#0b0f1f" stroke-width="3" stroke-linecap="round"/>
    <path d="M37 45 L27 40 M113 45 L123 40" stroke="#0b0f1f" stroke-width="2.6" stroke-linecap="round"/>
    <path d="M44 46 L52 46 M85 46 L93 46" stroke="#9fd0ff" stroke-width="2.2" stroke-linecap="round" opacity=".85"/>
    <path d="M46 51 L49 51 M87 51 L90 51" stroke="#9fd0ff" stroke-width="1.6" stroke-linecap="round" opacity=".5"/>
  </g>
  <g class="cool"><path d="M70 66.5 q5 3.2 10 -1" fill="none" stroke="#1d1d1f" stroke-width="1.9" stroke-linecap="round" class="smirk"/>
    <path d="M131 18 l-6 9 h5 l-3 8 9 -11 h-5 l3 -6z" fill="#ffd25e" stroke="#0b0f1f" stroke-width="1.2" stroke-linejoin="round" class="bolt"/></g>`);
document.querySelector(".logo-mark svg").insertAdjacentHTML("beforeend", `
  <g class="cool" stroke="#0b0f1f" stroke-width="1.6" stroke-linejoin="round">
    <path d="M16 22 L33 22 Q34 22 33.5 25 L32.5 29 Q31 32 26 32 L22 32 Q17.5 32 16.5 28.5 Z" fill="#141c36"/>
    <path d="M54 22 L37 22 Q36 22 36.5 25 L37.5 29 Q39 32 44 32 L48 32 Q52.5 32 53.5 28.5 Z" fill="#141c36"/>
    <path d="M33 24 Q35 22.5 37 24" fill="none"/><path d="M19.5 25 L24 25 M40 25 L44.5 25" stroke="#9fd0ff" stroke-width="1.3"/></g>`);

document.head.insertAdjacentHTML("beforeend", `<style>
  .cool { display: none; }
  body.ft-dark .cool { display: inline; }
  body.ft-dark .panda .mouth-happy { display: none; }
  body.ft-dark .tomorn.sad .smirk { display: none; }
  body.ft-dark .panda .eye-open, body.ft-dark .panda .eye-shine { display: none; }
  body.ft-dark .panda .cool-glasses { transition: transform .4s; transform-box: view-box; }
  body.ft-dark .tomorn.sleeping .cool-glasses { transform: translateY(-21px) rotate(-4deg); }   /* หลับ → ดันแว่นขึ้นหัว */
  body.ft-dark .tomorn.sleeping .bolt { display: none; }
  body.ft-dark .panda .bolt { animation: twinkle 1.6s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }

  body.ft-dark {
    --bg: #0a1430; --surface-1: #13213f; --surface-2: #0e1a35; --grid: #20305a;
    --text-primary: #e8eefc; --text-secondary: #aab7d8; --text-muted: #7484aa;
    --primary: #4da3ff; --series-1: #4da3ff; --mint: #2fd39a;
    --good: #2fd39a; --good-text: #4be3a8; --critical: #ff6b81; --critical-text: #ff8fa0; --warning: #ffc54d;
    --yellow-bg: rgba(255,214,102,.10); --pink-bg: rgba(255,128,170,.10); --mint-bg: rgba(47,211,154,.10); --sky-bg: rgba(77,163,255,.13);
    --bubble: 0 14px 34px rgba(0,0,0,.35), 0 0 0 1px rgba(120,160,255,.07);
    --bubble-hover: 0 18px 44px rgba(0,0,0,.45), 0 0 0 1px rgba(120,160,255,.14);
    color-scheme: dark;
    background: radial-gradient(1100px 600px at 12% -10%, #1a2f6b 0%, transparent 60%),
                radial-gradient(900px 520px at 110% 8%, #2a1a5e 0%, transparent 55%),
                radial-gradient(900px 600px at 50% 120%, #0f3a4a 0%, transparent 60%), #0a1430;
  }
  html:has(body.ft-dark) { background: #0a1430; }
  body.ft-dark .card, body.ft-dark .tile:nth-child(5) { background: var(--surface-1); }
  body.ft-dark .chip, body.ft-dark .tabs, body.ft-dark .logo-mark { background: #13213f; }
  body.ft-dark .chip b { color: var(--text-primary); }
  body.ft-dark .tile .ico { background: rgba(255,255,255,.07); box-shadow: none; }
  body.ft-dark .tile::after { background: rgba(255,255,255,.04); }
  body.ft-dark .tag { background: #1d2d55; color: var(--text-secondary); box-shadow: none; }
  body.ft-dark .tip { background: #1a2a52; box-shadow: 0 12px 30px rgba(0,0,0,.5); }
  body.ft-dark .bar { background: #0e1a35; }
  body.ft-dark .banner.crit { background: rgba(255,107,129,.16); } body.ft-dark .banner.warn { background: rgba(255,197,77,.13); }
  body.ft-dark .tomorn .bubble { background: #16254a; color: var(--text-primary); box-shadow: 0 14px 36px rgba(0,0,0,.45), 0 0 0 1px rgba(77,163,255,.25); }
  body.ft-dark .tomorn .panda { filter: drop-shadow(0 0 16px rgba(77,163,255,.45)) drop-shadow(0 8px 10px rgba(0,0,0,.4)); }
  body.ft-dark .logo h1 { color: #6cb6ff; text-shadow: 0 0 18px rgba(77,163,255,.45); }
  body.ft-dark .doodles svg { opacity: .55; }
  body.ft-dark .gauge .gmark { background: var(--text-secondary); }
  body.ft-dark .leg-track::after { opacity: .6; }
  body.ft-dark .tabs button.on { box-shadow: 0 0 18px rgba(77,163,255,.5); }
  body, .card, .tile, .chip, .tabs { transition: background-color .35s, color .35s; }
</style>`);
