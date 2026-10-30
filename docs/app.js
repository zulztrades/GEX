// Renders data.json + status.json. No libraries.
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const KEYS = ["ES", "NQ"];
const C = { call: "#1d9bf0", put: "#8f3dff", price: "#ffffff", flip: "#b2b2be", grid: "#1f1f27", axis: "#7b7b89", text: "#b2b2be" };
let DATA = null;

const fmt = (x) => x == null ? "—" : Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pts = (x) => Number(Math.abs(x)).toLocaleString("en-US", { maximumFractionDigits: 2 });
const dist = (lvl, price) => {
  if (lvl == null) return "";
  const d = lvl - price;
  if (Math.abs(d) < 0.125) return "at price";
  return `${pts(d)} pts ${d > 0 ? "above" : "below"}`;
};
const regimeWord = (r) => r === "positive" ? "POSITIVE GAMMA" : "NEGATIVE GAMMA";
const etNow = () => new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
const timeET = (iso) => new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET";
const dateLong = (d) => new Date(d + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const el = (tag, attrs = {}, parent) => {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
};
const warnIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.5"/></svg>';

async function load() {
  const t = Date.now();
  const [data, status] = await Promise.all([
    fetch(`data.json?t=${t}`).then((r) => r.ok ? r.json() : null).catch(() => null),
    fetch(`status.json?t=${t}`).then((r) => r.ok ? r.json() : null).catch(() => null),
  ]);
  if (!data) {
    $("date").textContent = "No data yet";
    $("updated").textContent = "The first update hasn't run. Check back after 8:45 AM ET.";
    return;
  }
  DATA = data;
  render(data, status || {});
}

function pageWarnings(d, status) {
  const w = [...d.warnings];
  if (status.ok === false) {
    w.unshift(`The last update failed at ${timeET(status.last_attempt_et)}. Showing the last good data from ${timeET(d.updated_et)} on ${dateLong(d.date)}.`);
  }
  const now = etNow();
  const mins = now.getHours() * 60 + now.getMinutes();
  const ageMin = (Date.now() - new Date(d.updated_et).getTime()) / 60000;
  const weekday = now.getDay() > 0 && now.getDay() < 6;
  if (weekday && mins >= 9 * 60 + 15 && mins <= 16 * 60 + 15 && ageMin > 75) {
    w.push(`Data is ${Math.round(ageMin / 60 * 10) / 10} hours old. Updates may be delayed or the market is closed today.`);
  }
  for (const k of KEYS) {
    if (d.instruments[k]?.oi_status === "unknown" && d.mode !== "manual") {
      w.push(`${k}: couldn't confirm open interest is fresh (no prior day to compare yet).`);
    }
  }
  return w;
}

function render(d, status) {
  $("date").textContent = dateLong(d.date);
  $("updated").textContent = `Last updated ${timeET(d.updated_et)}`;
  $("regimes").innerHTML = KEYS.map((k) => {
    const i = d.instruments[k];
    return `<div class="badge ${i.regime === "positive" ? "pos" : "neg"}"><div class="sym">${k} · ${i.contract}</div><div class="val">${regimeWord(i.regime)}</div></div>`;
  }).join("");

  const warns = pageWarnings(d, status);
  $("alerts").innerHTML = warns.map((w) => `<div class="alert" role="status">${warnIcon}<span>${w}</span></div>`).join("");

  $("cards").innerHTML = KEYS.map((k) => card(k, d.instruments[k])).join("");
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copyLevels(b)));

  drawAll();
  summary(d, warns);
}

function card(k, i) {
  const row = (cls, name, v) => `<div class="lv-row"><span class="lv-name"><i class="dot ${cls}"></i>${name}</span><span class="lv-val"><b>${fmt(v)}</b><span>${dist(v, i.price)}</span></span></div>`;
  const flipRow = i.flip != null ? row("flip", "Zero-gamma flip", i.flip)
    : `<div class="lv-row"><span class="lv-name"><i class="dot flip"></i>Zero-gamma flip</span><span class="lv-val"><b>none near</b><span>gamma ${i.flip_side === "above" ? "positive" : "negative"} across ±4%</span></span></div>`;
  return `<article class="card">
    <div class="card-h"><h2>${k}</h2><span class="chip">${i.contract}</span></div>
    <div class="price">${fmt(i.price)}</div><div class="price-l">Current price</div>
    <div class="lv">${[
      { v: i.call_wall, html: row("call", "Call wall", i.call_wall) },
      { v: i.flip ?? (i.call_wall + i.put_wall) / 2, html: flipRow },
      { v: i.put_wall, html: row("put", "Put wall", i.put_wall) },
    ].sort((a, b) => b.v - a.v).map((r) => r.html).join("")}</div>
    <p class="sec">Secondary: ${i.secondary.map((s) => `<b>${fmt(s)}</b>`).join(" · ")}</p>
    <p class="meta">Net GEX ${i.net_gex_bn > 0 ? "+" : "−"}$${Math.abs(i.net_gex_bn).toFixed(1)}B per 1% · from ${i.index.name} options</p>
    <button class="btn" data-copy="${k}">Copy levels</button>
  </article>`;
}

function levelText(k) {
  const i = DATA.instruments[k];
  return [
    `${i.contract} GEX levels · ${dateLong(DATA.date)}`,
    `Regime: ${i.regime} gamma`,
    `Call wall: ${fmt(i.call_wall)}`,
    `Zero-gamma flip: ${i.flip != null ? fmt(i.flip) : "none within 4%"}`,
    `Put wall: ${fmt(i.put_wall)}`,
    `Secondary: ${i.secondary.map(fmt).join(", ")}`,
  ].join("\n");
}

async function copyLevels(btn) {
  const text = levelText(btn.dataset.copy);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  const old = btn.textContent;
  btn.textContent = "Copied";
  setTimeout(() => (btn.textContent = old), 1600);
}

// ---------- charts ----------

function niceStep(range, target) {
  const raw = range / target;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * p) return m * p;
  return 10 * p;
}

function drawAll() {
  if (!DATA) return;
  $("ladders").innerHTML = "";
  $("bars").innerHTML = "";
  for (const k of KEYS) {
    ladder(k, DATA.instruments[k]);
    bars(k, DATA.instruments[k]);
  }
}

function box(parentId, title) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.innerHTML = `<h3>${title}</h3>`;
  $(parentId).appendChild(wrap);
  return wrap;
}

function ladder(k, i) {
  const wrap = box("ladders", `${k} · ${i.contract}`);
  const W = Math.max(wrap.clientWidth - 20, 260), H = 400, padT = 18, padB = 18, axisW = 66;
  const main = [
    { v: i.call_wall, name: "Call wall", color: C.call },
    { v: i.flip, name: "Flip", color: C.flip, dash: "5 4" },
    { v: i.price, name: "Price", color: C.price, strong: true },
    { v: i.put_wall, name: "Put wall", color: C.put },
  ].filter((m) => m.v != null);
  const vals = main.map((m) => m.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.14, i.price * 0.002);
  lo -= pad; hi += pad;
  const y = (v) => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `${k} level ladder` });

  const step = niceStep(hi - lo, 7);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    el("line", { x1: axisW, x2: W, y1: y(v), y2: y(v), stroke: C.grid }, svg);
    el("text", { x: axisW - 8, y: y(v) + 4, "text-anchor": "end", fill: C.axis, "font-size": 11, "font-family": "IBM Plex Mono" }, svg).textContent = v.toLocaleString("en-US");
  }
  el("line", { x1: axisW, x2: axisW, y1: padT - 8, y2: H - padB + 8, stroke: C.axis, "stroke-width": 1 }, svg);

  for (const s of i.secondary) {
    if (s < lo || s > hi) continue;
    el("line", { x1: axisW, x2: W * 0.55, y1: y(s), y2: y(s), stroke: "#4a4a5a", "stroke-width": 1, "stroke-dasharray": "2 3" }, svg);
  }

  // place labels, pushing apart any that would overlap
  const items = main.map((m) => ({ ...m, ly: y(m.v) })).sort((a, b) => a.ly - b.ly);
  for (let n = 1; n < items.length; n++) if (items[n].ly - items[n - 1].ly < 24) items[n].ly = items[n - 1].ly + 24;
  const over = items.length ? items[items.length - 1].ly - (H - 12) : 0;
  if (over > 0) items.forEach((m) => (m.ly -= over));

  const lineEnd = W * 0.42, labelX = W * 0.45;
  for (const m of items) {
    const yy = y(m.v);
    el("line", { x1: axisW, x2: lineEnd, y1: yy, y2: yy, stroke: m.color, "stroke-width": m.strong ? 3 : 2, "stroke-dasharray": m.dash || "" }, svg);
    el("path", { d: `M${lineEnd} ${yy} L${labelX - 4} ${m.ly}`, stroke: m.color, fill: "none", "stroke-width": 1, opacity: 0.6 }, svg);
    el("circle", { cx: axisW, cy: yy, r: 4, fill: m.color }, svg);
    const t = el("text", { x: labelX, y: m.ly + 4, fill: "#ececf2", "font-size": 12.5, "font-family": "IBM Plex Mono" }, svg);
    el("tspan", { fill: m.color === C.flip ? "#ececf2" : m.color, "font-weight": 600 }, t).textContent = m.name + " ";
    el("tspan", {}, t).textContent = fmt(m.v);
  }
  wrap.appendChild(svg);
  if (i.flip == null) wrap.insertAdjacentHTML("beforeend", `<p class="cnote">No zero-gamma flip within ±4% of price.</p>`);
}

function bars(k, i) {
  const wrap = box("bars", `${k} · ${i.contract}`);
  const rows = [...i.strikes].sort((a, b) => b.k - a.k);
  if (!rows.length) return;
  const W = Math.max(wrap.clientWidth - 20, 260), rowH = Math.max(7, Math.min(14, 520 / rows.length));
  const padT = 8, padB = 22, axisW = 66;
  const H = padT + rows.length * rowH + padB;
  const hi = rows[0].k, lo = rows[rows.length - 1].k;
  const y = (v) => padT + (hi - v) / (hi - lo || 1) * (rows.length - 1) * rowH + rowH / 2;
  const maxAbs = Math.max(...rows.map((r) => Math.max(r.c, -r.p)), 1e-9);
  const mid = axisW + (W - axisW) / 2, half = (W - axisW) / 2 - 6;
  const xv = (v) => mid + v / maxAbs * half;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": `${k} GEX by strike` });

  const labelEvery = Math.ceil(16 / rowH);
  rows.forEach((r, n) => {
    if (n % labelEvery === 0) {
      el("text", { x: axisW - 8, y: y(r.k) + 4, "text-anchor": "end", fill: C.axis, "font-size": 11, "font-family": "IBM Plex Mono" }, svg).textContent = fmt(r.k);
    }
    const bh = Math.max(rowH - 2, 2);
    if (r.c > 0) el("rect", { x: mid, y: y(r.k) - bh / 2, width: Math.max(xv(r.c) - mid, 0.5), height: bh, rx: Math.min(2, bh / 2), fill: C.call }, svg);
    if (r.p < 0) el("rect", { x: xv(r.p), y: y(r.k) - bh / 2, width: Math.max(mid - xv(r.p), 0.5), height: bh, rx: Math.min(2, bh / 2), fill: C.put }, svg);
  });
  el("line", { x1: mid, x2: mid, y1: padT, y2: H - padB + 4, stroke: C.axis }, svg);
  el("text", { x: axisW + 4, y: H - 6, fill: C.axis, "font-size": 10.5, "font-family": "IBM Plex Mono" }, svg).textContent = "← puts";
  el("text", { x: W - 4, y: H - 6, "text-anchor": "end", fill: C.axis, "font-size": 10.5, "font-family": "IBM Plex Mono" }, svg).textContent = "calls →";

  const mark = (v, color, label, dash) => {
    if (v == null || v < lo - 1e-9 || v > hi + 1e-9) return false;
    el("line", { x1: axisW, x2: W, y1: y(v), y2: y(v), stroke: color, "stroke-width": 1.5, "stroke-dasharray": dash || "" }, svg);
    const tx = el("text", { x: W - 4, y: y(v) - 4, "text-anchor": "end", fill: color, "font-size": 11, "font-family": "IBM Plex Mono", "font-weight": 600, "paint-order": "stroke", stroke: "#0b0b0f", "stroke-width": 3 }, svg);
    tx.textContent = `${label} ${fmt(v)}`;
    return true;
  };
  mark(i.price, C.price, "Price");
  const flipShown = mark(i.flip, C.flip, "Flip", "5 4");

  // hover / tap tooltip
  const hit = el("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent" }, svg);
  const tip = $("tip");
  const show = (ev) => {
    const box = svg.getBoundingClientRect();
    const py = (ev.clientY - box.top) * (H / box.height);
    const n = Math.min(rows.length - 1, Math.max(0, Math.round((py - padT - rowH / 2) / rowH)));
    const r = rows[n];
    tip.innerHTML = `<b>${fmt(r.k)}</b> <span style="color:#7b7b89">(${i.index.name} ${r.s})</span><br><span style="color:${C.call}">Calls</span> +$${(r.c * 1000).toFixed(0)}M<br><span style="color:#a876ff">Puts</span> −$${(-r.p * 1000).toFixed(0)}M`;
    tip.hidden = false;
    const tx = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${Math.max(8, tx)}px`;
    tip.style.top = `${ev.clientY + 14}px`;
  };
  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", () => (tip.hidden = true));
  wrap.appendChild(svg);
  if (i.flip != null && !flipShown) {
    wrap.insertAdjacentHTML("beforeend", `<p class="cnote">Flip ${fmt(i.flip)} is ${i.flip > hi ? "above" : "below"} this zoom range.</p>`);
  }
}

// ---------- summary ----------

function summary(d, warns) {
  const es = d.instruments.ES, nq = d.instruments.NQ;
  const meaning = (k, i) => i.regime === "positive"
    ? `<b>${k}</b> is in positive gamma: dealers hedge against moves, so price tends to get pulled back and range.`
    : `<b>${k}</b> is in negative gamma: dealer hedging adds to moves, so moves tend to extend and ranges widen.`;
  const lvls = (k, i) => `${i.contract}: call wall ${fmt(i.call_wall)}, flip ${i.flip != null ? fmt(i.flip) : "none near"}, put wall ${fmt(i.put_wall)}, secondary ${i.secondary.map(fmt).join(" / ")}`;

  let model;
  if (es.regime === nq.regime) {
    model = es.regime === "positive"
      ? "Context favors <b>Inversions</b>: with both in positive gamma, fades of ±2 STD back to VWAP have a better backdrop. Still wait for your location and execution."
      : "Context favors <b>Continuation</b>: with both in negative gamma, moves tend to extend, so be careful fading ±2 STD. Still wait for your location and execution.";
  } else {
    const pos = es.regime === "positive" ? "ES" : "NQ", neg = pos === "ES" ? "NQ" : "ES";
    model = `Mixed context: <b>${pos}</b> leans Inversions (fading ±2 STD back to VWAP), <b>${neg}</b> leans Continuation (moves tend to extend). Weigh each instrument on its own.`;
  }

  const flipLine = (k, i) => {
    if (i.flip == null) return `${k} has no flip within 4%, so the regime is unlikely to change today.`;
    const d = i.price - i.flip;
    const where = d >= 0 ? "above" : "below";
    const change = d >= 0
      ? `a break below it shifts ${k} into negative gamma, where moves tend to extend`
      : `a move back above it shifts ${k} into positive gamma, where moves tend to slow and revert`;
    return `${k} is <b>${pts(d)} pts ${where}</b> the flip at ${fmt(i.flip)}; ${change}.`;
  };

  const items = [
    meaning("ES", es),
    meaning("NQ", nq),
    `Levels to mark in Sierra Chart:<span class="lvls">${lvls("ES", es)}</span><span class="lvls">${lvls("NQ", nq)}</span>`,
    model,
    `${flipLine("ES", es)} ${flipLine("NQ", nq)}`,
    warns.length ? `Warnings: ${warns.join(" ")}` : "No data warnings today.",
  ];
  $("summary").innerHTML = items.map((t) => `<li>${t}</li>`).join("");
}

let lastW = window.innerWidth;
window.addEventListener("resize", () => {
  if (Math.abs(window.innerWidth - lastW) > 30) { lastW = window.innerWidth; drawAll(); }
});
document.addEventListener("pointerdown", (e) => { if (e.target.tagName !== "rect") $("tip").hidden = true; });
load();
setInterval(load, 5 * 60 * 1000);
