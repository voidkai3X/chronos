/* CHRONOS Phase 2: market conditions, timeframe sweep, strategy comparison and trade replay.
   Reads results produced by the engine; never invents data. Canvas drawing only, no CSS animation. */
(() => {
"use strict";
const C = window.CHRONOS;
if (!C) return;
const { $, fmt, px, pct, sgnR, wat, esc, durTxt, srcLabel, sizeCanvas, niceStep } = C;
const pfTxt = r => r.profit_factor != null ? Number(r.profit_factor).toFixed(2) : (r.profit_factor_infinite ? "no losses" : "n/a");
const cls = v => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const TF_LBL = { "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "Daily" };
const SAMPLE_LBL = { ok: "", small: "small sample", too_few: "too few" };

// ---------------------------------------------------------------- results: market conditions + timeframe sweep
function renderConditions(o) {
  const panel = $("cond-panel"), c = o.conditions;
  if (!panel) return;
  panel.hidden = !c;
  if (!c) return;
  const sm = c.summary || {};
  $("cond-head").className = "cond-head st-" + (sm.status || "");
  $("cond-head").textContent = sm.headline || "";
  $("cond-lines").innerHTML = (sm.lines || []).map(l => `<li class="info"><span>${esc(l)}</span></li>`).join("");
  $("cond-grid").innerHTML = (c.dimensions || []).map(d => `
    <div class="cond-dim">
      <div class="cond-t">${esc(d.title)}${d.unknown ? ` <span class="muted small">· ${d.unknown} trades untagged (not enough history yet)</span>` : ""}</div>
      <table class="cond-tbl"><thead><tr><th>Condition</th><th class="r">Trades</th><th class="r">Win rate</th><th class="r">Expectancy</th><th class="r">PF</th><th class="r">Net R</th></tr></thead>
      <tbody>${d.rows.map(r => `<tr class="smp-${r.sample}"><td>${esc(r.label)}${SAMPLE_LBL[r.sample] ? ` <span class="tag-s">${SAMPLE_LBL[r.sample]}</span>` : ""}</td>
        <td class="r">${r.trades}</td><td class="r">${r.trades ? pct(r.win_rate) : "-"}</td>
        <td class="r ${r.trades ? cls(r.expectancy_r) : ""}">${r.trades ? sgnR(r.expectancy_r) : "-"}</td>
        <td class="r">${r.trades ? pfTxt(r) : "-"}</td><td class="r ${r.trades ? cls(r.total_r) : ""}">${r.trades ? sgnR(r.total_r) : "-"}</td></tr>`).join("")}</tbody></table>
    </div>`).join("");
  const n = c.now || {};
  const nowBits = [n.trend, n.vol ? n.vol + " volatility" : null, n.bias].filter(Boolean);
  $("cond-now").textContent = nowBits.length
    ? `Right now (last closed 4h candle, ${wat(n.asof)} WAT): ${nowBits.join(", ")}. Groups under ${c.min_group} trades are small samples; under ${c.too_few} are not judged.`
    : "";
  const df = c.definitions || {};
  $("cond-def").innerHTML = [["Trend vs range", df.trend], ["Volatility", df.vol], ["Direction", df.bias], ["Timing", df.timing]]
    .filter(x => x[1]).map(([k, v]) => `<li><b>${k}:</b> ${esc(v)}</li>`).join("");
}
function renderSweep(o) {
  const panel = $("tfs-panel"), sw = o.tf_sweep;
  if (!panel) return;
  panel.hidden = !sw;
  if (!sw) return;
  const best = sw.rows.filter(r => !r.error && r.closed >= 15).sort((a, b) => b.expectancy_r - a.expectancy_r)[0];
  $("tfs").querySelector("tbody").innerHTML = sw.rows.map(r => {
    if (r.error) return `<tr><td>${TF_LBL[r.tf] || r.tf}</td><td>${r.interval}</td><td colspan="7" class="muted">Not run</td><td class="muted small">${esc(r.error)}</td></tr>`;
    const note = [r.current ? "your run" : "", r.closed < 15 ? "too few trades" : "", r.dq && r.dq !== "ok" ? "data: " + r.dq : "",
      best && best.tf === r.tf ? "highest expectancy (15+ trades)" : ""].filter(Boolean).join(" · ");
    return `<tr class="${r.current ? "cur" : ""}"><td><b>${TF_LBL[r.tf] || r.tf}</b></td><td>${r.interval}</td><td class="r">${r.setups}</td><td class="r">${r.closed}</td>
      <td class="r">${pct(r.win_rate)}</td><td class="r ${cls(r.expectancy_r)}">${sgnR(r.expectancy_r)}</td><td class="r">${pfTxt(r)}</td>
      <td class="r ${cls(r.total_r)}">${sgnR(r.total_r)}</td><td class="r">${Number(r.max_drawdown_r || 0).toFixed(2)}R</td><td class="muted small">${note}</td></tr>`;
  }).join("");
  $("tfs-note").textContent = sw.note + " Looking at six timeframes and keeping the best one is a form of curve-fitting: confirm it on a later window.";
}
document.addEventListener("chronos:result", e => { renderConditions(e.detail); renderSweep(e.detail); });

// ---------------------------------------------------------------- view switch: test one setup | compare strategies
const LS_KEY = "chronos.strategies.v1";
let types = null, named = null, limits = { min: 2, max: 6 }, stratRows = [], showingStrat = false, scResult = null;
const TYPE_ORDER = [["Your levels", ["similar", "every_close"]], ["Rule-based", ["breakout", "ema_cross", "liquidity_sweep", "fvg"]],
  ["Zone rules (approximations)", ["zone_retest", "zone_sweep", "zone_bos"]]];
const PARAM_LBL = { fast: "Fast EMA", slow: "Slow EMA", stop_atr: "Stop (ATR)", lookback: "Lookback candles", min_gap_atr: "Min gap (ATR)", valid_bars: "Order valid (candles)" };
function setView(v) {
  const cmp = v === "cmp", lib = v === "lib";
  document.body.classList.toggle("view-cmp", cmp);
  document.body.classList.toggle("view-lib", lib);
  [...$("view-seg").children].forEach(b => b.classList.toggle("on", b.dataset.view === v));
  $("view-help").hidden = !cmp;
  $("strat-block").hidden = !cmp;
  ["tfs-row", "overlap-row", "insights-row"].forEach(id => { if ($(id)) $(id).classList.toggle("cmp-hide", cmp); });
  document.querySelectorAll(".mode-block, #similar-opts, #pattern-opts, #style-opts").forEach(el => el.classList.toggle("cmp-hide", cmp));
  $("run").querySelector(".txt").textContent = cmp ? "Compare strategies" : "Run test";
  if (cmp) loadTypes();
  // show whichever result belongs to this view
  if (!$("skel").hidden) return;
  const hasTest = !!C.getResult();
  $("strat-res").hidden = !(cmp && scResult);
  $("res").hidden = !(!cmp && !lib && hasTest);
  $("empty").hidden = lib || (cmp ? !!scResult : hasTest);
  if ($("lib-res")) $("lib-res").hidden = !lib;
  showingStrat = cmp && !!scResult;
  document.dispatchEvent(new CustomEvent("chronos:view", { detail: v }));
}
$("view-seg").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setView(b.dataset.view); });

async function loadTypes() {
  if (types) return;
  try {
    const r = await fetch("/api/strategy-types"); const j = await r.json();
    types = j.types; named = j.named; limits = { min: j.min, max: j.max };
  } catch (e) { $("strat-limit").textContent = "Could not load the strategy list. Check your connection."; return; }
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { saved = null; }
  stratRows = Array.isArray(saved) && saved.length >= 2 ? saved.filter(s => types[s.type]).slice(0, limits.max) : [
    { name: "My levels", type: "similar", params: {} }, { name: "", type: "breakout", params: {} }, { name: "", type: "ema_cross", params: {} }];
  $("strat-named").innerHTML = Object.entries(named).map(([k, v]) => `<li><b>${esc(k)}:</b> ${esc(types[v.type].label)} (${esc(v.how)})</li>`).join("");
  drawStratRows();
}
function save() { try { localStorage.setItem(LS_KEY, JSON.stringify(stratRows)); } catch (e) { /* private mode */ } }
function drawStratRows() {
  const opts = TYPE_ORDER.map(([g, ks]) => `<optgroup label="${g}">${ks.filter(k => types[k]).map(k => `<option value="${k}">${esc(types[k].label)}</option>`).join("")}</optgroup>`).join("");
  $("strat-list").innerHTML = stratRows.map((s, i) => {
    const t = types[s.type], ps = t.params || {};
    const kind = t.kind === "rule" ? "Rule-based: CHRONOS detects it" : t.kind === "approx" ? "Approximation: closest CHRONOS rule" : "Your own levels";
    return `<div class="st-row" data-i="${i}">
      <div class="st-top"><span class="st-n">${i + 1}</span>
        <input class="st-name" type="text" maxlength="48" placeholder="${esc(t.label)}" value="${esc(s.name || "")}" aria-label="Name for strategy ${i + 1}">
        <button type="button" class="btn-ghost sm st-del" ${stratRows.length <= limits.min ? "disabled" : ""} aria-label="Remove strategy ${i + 1}">Remove</button></div>
      <div class="sel"><select class="st-type" aria-label="Rule for strategy ${i + 1}">${opts}</select></div>
      ${Object.keys(ps).length ? `<div class="st-params">${Object.entries(ps).map(([k, dv]) => `<label><span>${PARAM_LBL[k] || k}</span>
        <input type="number" step="any" data-k="${k}" value="${s.params && s.params[k] != null ? s.params[k] : dv}" inputmode="decimal"></label>`).join("")}</div>` : ""}
      <p class="help st-note"><span class="st-kind k-${t.kind}">${kind}</span> ${esc(t.note)}</p>
    </div>`;
  }).join("");
  [...$("strat-list").querySelectorAll(".st-row")].forEach((row, i) => { row.querySelector(".st-type").value = stratRows[i].type; });
  $("strat-add").disabled = stratRows.length >= limits.max;
  $("strat-limit").textContent = `${stratRows.length} of ${limits.max} strategies. Every one runs on the same market, window, timeframe, fees and slippage.`;
}
$("strat-list").addEventListener("input", e => {
  const row = e.target.closest(".st-row"); if (!row) return;
  const s = stratRows[+row.dataset.i];
  if (e.target.classList.contains("st-name")) s.name = e.target.value;
  if (e.target.dataset.k) { s.params = s.params || {}; s.params[e.target.dataset.k] = e.target.value === "" ? undefined : +e.target.value; }
  save();
});
$("strat-list").addEventListener("change", e => {
  const row = e.target.closest(".st-row"); if (!row) return;
  if (e.target.classList.contains("st-type")) { const s = stratRows[+row.dataset.i]; s.type = e.target.value; s.params = {}; save(); drawStratRows(); }
});
$("strat-list").addEventListener("click", e => {
  const b = e.target.closest(".st-del"); if (!b) return;
  stratRows.splice(+b.closest(".st-row").dataset.i, 1); save(); drawStratRows();
});
$("strat-add").addEventListener("click", () => {
  if (stratRows.length >= limits.max) return;
  const used = new Set(stratRows.map(s => s.type));
  const next = ["breakout", "ema_cross", "liquidity_sweep", "fvg", "zone_retest", "every_close", "zone_sweep", "zone_bos", "similar"].find(k => !used.has(k)) || "breakout";
  stratRows.push({ name: "", type: next, params: {} }); save(); drawStratRows();
});

async function runStrategies() {
  C.showErr("");
  const mk = C.getMarket();
  if (!mk) return C.showErr("Pick a market.");
  const le = C.levelError();
  if (le === null) return C.showErr("Enter entry, TP and SL: they set the side and the R:R every strategy uses.");
  if (le) return C.showErr(le);
  if (!types) await loadTypes();
  if (stratRows.length < limits.min) return C.showErr(`Add at least ${limits.min} strategies.`);
  const slip = $("slip").value === "" ? 0 : +$("slip").value;
  const body = { market: mk, side: C.getSide(), entry: +$("entry").value, tp: +$("tp").value, sl: +$("sl").value,
    days: +$("days").value, expiry: $("expiry").value, tf: $("similar-tf").value, htf_confirm: $("htf").value,
    slippage: slip, slippage_unit: $("slip-unit").value,
    strategies: stratRows.map(s => ({ name: (s.name || "").trim() || types[s.type].label, type: s.type,
      params: Object.fromEntries(Object.entries(s.params || {}).filter(([, v]) => v != null && v !== "" && !isNaN(v))) })) };
  C.setBusy(true);
  $("skel-txt").textContent = `Running ${body.strategies.length} strategies on the same data...`;
  try {
    let r;
    try { r = await fetch("/api/strategies", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
    catch (e) { throw Object.assign(new Error("Could not reach CHRONOS. Check your connection and try again."), { status: 0 }); }
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw Object.assign(new Error(j.error || ("HTTP " + r.status)), { status: r.status });
    scResult = j; showingStrat = true;
    C.setBusy(false);
    renderStrategies(j);
  } catch (err) { C.setBusy(false); showingStrat = false; C.showResErr(err.message, err.status); C.toast(err.message); }
}

// ---------------------------------------------------------------- strategy comparison results
const SC_COLORS = ["#5fd63a", "#9fe7b5", "#e3c35a", "#7fb8ff", "#ff8a7a", "#c9d6cc"];
const SC_DASH = [[], [6, 4], [], [2, 3], [8, 3, 2, 3], [4, 4]];
let scSort = { key: null, dir: -1 };
const SC_COLS = [
  { k: "name", t: "Strategy", v: r => r.name, f: (r, i) => `<span class="sw" style="background:${SC_COLORS[i % 6]}"></span><b>${esc(r.name)}</b><div class="muted small">${esc(r.label)}${r.kind === "approx" ? " · approximation" : r.kind === "rule" ? " · rule" : " · your levels"}</div>` },
  { k: "edge_score", t: "Edge Score", v: r => r.edge_score ?? -1, f: r => r.edge_score == null ? "-" : `<b>${r.edge_score}</b><div class="muted small">${esc(r.edge_label || "")}</div>`, r: 1 },
  { k: "closed", t: "Trades", v: r => r.closed ?? -1, f: r => `${r.closed}<div class="muted small">${r.setups} setups</div>`, r: 1 },
  { k: "win_rate", t: "Win rate", v: r => r.win_rate ?? -1, f: r => pct(r.win_rate), r: 1 },
  { k: "expectancy_r", t: "Expectancy", v: r => r.expectancy_r ?? -99, f: r => `<b class="${cls(r.expectancy_r)}">${sgnR(r.expectancy_r)}</b><div class="muted small">theor. ${sgnR(r.expectancy_r_gross)}</div>`, r: 1 },
  { k: "profit_factor", t: "PF", v: r => r.profit_factor ?? (r.profit_factor_infinite ? 1e9 : -1), f: r => pfTxt(r), r: 1 },
  { k: "total_r", t: "Net R", v: r => r.total_r ?? -1e9, f: r => `<span class="${cls(r.total_r)}">${sgnR(r.total_r)}</span><div class="muted small">theor. ${sgnR(r.total_r_gross)}</div>`, r: 1 },
  { k: "max_drawdown_r", t: "Max DD", v: r => r.max_drawdown_r ?? -1e9, f: r => Number(r.max_drawdown_r || 0).toFixed(2) + "R", r: 1 },
  { k: "avg_win_r", t: "Avg win / loss", v: r => r.avg_win_r ?? -1, f: r => `${r.avg_win_r != null ? sgnR(r.avg_win_r) : "-"} / ${r.avg_loss_r != null ? sgnR(r.avg_loss_r) : "-"}`, r: 1 },
  { k: "longest_loss_streak", t: "Loss streak", v: r => r.longest_loss_streak ?? -1, f: r => r.longest_loss_streak ?? "-", r: 1 },
  { k: "avg_hours", t: "Avg time", v: r => r.avg_hours ?? -1, f: r => durTxt(r.avg_hours), r: 1 },
  { k: "flags", t: "Notes", v: r => (r.flags || []).length, f: r => `<span class="muted small">${esc([...(r.flags || []), r.dq && r.dq !== "ok" ? "data " + r.dq : ""].filter(Boolean).join("; ") || "-")}</span>` },
];
function renderStrategies(j) {
  $("empty").hidden = true; $("res").hidden = true; $("strat-res").hidden = false; $("res-err").hidden = true;
  const c = j.conditions;
  $("sc-title").textContent = `${c.side.toUpperCase()} ${String(c.market_id || c.market).split("|").pop()}  ·  ${j.rows.length} strategies  ·  ${TF_LBL[c.setup_tf] || c.setup_tf} setups  ·  target 1:${Number(c.rr).toFixed(2)}`;
  $("sc-sub").textContent = `Identical conditions: ${c.days} days (${wat(c.from_ms)} to ${wat(c.to_ms)} WAT), fills on ${c.interval} candles, ` +
    `fees ${c.fees.commission_pct_per_fill}% per fill + ${c.fees.swap_pct_per_day}%/day swap, slippage ${c.slippage}, expiry ${c.expiry}, time stop ${c.max_hold}, ` +
    `one trade at a time` + (c.htf_confirm !== "off" ? `, ${c.htf_confirm} trend confirmation` : "") + ".";
  $("sc-prov").textContent = c.provenance_text || "";
  $("sc-prov").hidden = !c.provenance_text;
  $("sc-lead").className = "dq warn"; $("sc-lead").textContent = j.lead || ""; $("sc-lead").hidden = !j.lead;
  drawScTable();
  drawScChart();
  $("sc-notes").innerHTML = (j.notes || []).map(n => `<li class="info"><span>${esc(n)}</span></li>`).join("") +
    j.rows.filter(r => r.error).map(r => `<li class="warn"><span>${esc(r.name)} did not run: ${esc(r.error)}</span></li>`).join("");
  $("sc-how").innerHTML = j.rows.map(r => `<li><b>${esc(r.name)}:</b> ${esc(r.placement || r.note || "")}${r.conditions ? ` <span class="muted">Conditions: ${esc(r.conditions)}</span>` : ""}</li>`).join("");
  if (window.matchMedia("(max-width: 980px)").matches) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}
function drawScTable() {
  const j = scResult; if (!j) return;
  let rows = j.rows.map((r, i) => ({ r, i })).filter(x => !x.r.error);
  if (scSort.key) { const c = SC_COLS.find(x => x.k === scSort.key); rows.sort((a, b) => { const x = c.v(a.r), y = c.v(b.r); return (x < y ? -1 : x > y ? 1 : a.i - b.i) * scSort.dir; }); }
  $("sc-head").innerHTML = SC_COLS.map(c => { const on = scSort.key === c.k;
    return `<th class="${c.r ? "r " : ""}sortable${on ? " on" : ""}" data-k="${c.k}" tabindex="0">${c.t}${on ? (scSort.dir > 0 ? " ▲" : " ▼") : ""}</th>`; }).join("");
  $("sc-tbl").querySelector("tbody").innerHTML = rows.map(({ r, i }) => `<tr class="${r.closed < 15 ? "few" : ""}">${SC_COLS.map(c => `<td class="${c.r ? "r" : ""}">${c.f(r, i)}</td>`).join("")}</tr>`).join("");
}
$("sc-tbl").addEventListener("click", e => { const th = e.target.closest("th[data-k]"); if (!th) return;
  scSort = scSort.key === th.dataset.k ? { key: th.dataset.k, dir: -scSort.dir } : { key: th.dataset.k, dir: -1 }; drawScTable(); });
function drawScChart() {
  const j = scResult; if (!j) return;
  const cv = $("sc-chart"), H = 280, [c, W] = sizeCanvas(cv, H);
  const series = j.rows.map((r, i) => ({ r, i })).filter(x => !x.r.error && x.r.equity && x.r.equity.length);
  const L = 50, R = 12, T = 12, B = 26, w = W - L - R, h = H - T - B;
  c.font = "11px ui-monospace,Menlo,monospace"; c.fillStyle = "#8fa596";
  if (!series.length) { c.fillText("No closed trades to chart.", L, H / 2); $("sc-legend").innerHTML = ""; return; }
  const t0 = j.conditions.from_ms, t1 = j.conditions.to_ms;
  let lo = 0, hi = 0; series.forEach(s => s.r.equity.forEach(p => { lo = Math.min(lo, p[1]); hi = Math.max(hi, p[1]); }));
  if (hi - lo < 1) { hi += .5; lo -= .5; }
  const pad = (hi - lo) * .08; lo -= pad; hi += pad;
  const X = t => L + (t - t0) / Math.max(1, t1 - t0) * w, Y = v => T + (hi - v) / (hi - lo) * h;
  const st = niceStep((hi - lo) / 5);
  c.strokeStyle = "#0d2414"; c.lineWidth = 1;
  for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) { c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(W - R, Y(v)); c.stroke(); c.fillText((v > 0 ? "+" : "") + v.toFixed(st < 1 ? 1 : 0) + "R", 4, Y(v) + 4); }
  c.strokeStyle = "#2a5532"; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(L, Y(0)); c.lineTo(W - R, Y(0)); c.stroke(); c.setLineDash([]);
  series.forEach(({ r, i }) => {
    c.beginPath(); c.moveTo(X(t0), Y(0));
    r.equity.forEach(p => { c.lineTo(X(p[0]), Y(p[1])); });
    c.strokeStyle = SC_COLORS[i % 6]; c.lineWidth = 2; c.setLineDash(SC_DASH[i % 6]); c.stroke(); c.setLineDash([]);
  });
  c.fillStyle = "#8fa596"; c.fillText(wat(t0).split(" ").slice(0, 2).join(" "), L, H - 6);
  const e = wat(t1).split(" ").slice(0, 2).join(" "); c.fillText(e, W - R - c.measureText(e).width, H - 6);
  $("sc-legend").innerHTML = series.map(({ r, i }) => `<span><i class="lg" style="background:${SC_COLORS[i % 6]}"></i>${esc(r.name)} ${sgnR(r.total_r)}</span>`).join("");
}

// ---------------------------------------------------------------- trade replay
const RP = { trade: null, o: null, bars: [], head: 0, timer: null, idx: {}, step: 0, lastFocus: null };
function openReplay(o, i) {
  const t = o && o.trades && o.trades[i];
  if (!t || !t.candle_window) return;
  RP.o = o; RP.trade = t; RP.bars = []; RP.head = 0; stopPlay();
  RP.lastFocus = document.activeElement;
  const g = o.setup, st = t.status;
  $("rp-title").textContent = `Trade ${i + 1} · ${g.side.toUpperCase()} ${o.market.coin || o.market.market_id} · ${C.STATUS_LBL[st] || st} ${sgnR(t.r_net)}`;
  $("rp-status").textContent = ""; $("rp-info").innerHTML = ""; $("rp-src").textContent = "";
  $("rp").hidden = false; document.body.classList.add("rp-open-body");
  $("rp-load").hidden = false;
  $("rp-close").focus();
  const cw = t.candle_window;
  const q = new URLSearchParams({ market: t.market || o.market.market_id, interval: cw.interval, from: cw.from_ms, to: cw.to_ms });
  if (cw.load_from_ms) { q.set("load_from", cw.load_from_ms); q.set("load_to", cw.load_to_ms); }
  fetch("/api/candles?" + q).then(r => r.json().then(j => ({ r, j }))).then(({ r, j }) => {
    if (RP.trade !== t) return;
    $("rp-load").hidden = true;
    if (!r.ok || j.error) { $("rp-status").innerHTML = `<span class="neg">${esc(j.error || "Could not load the candles.")}</span>`; return; }
    if (!j.candles.length) { $("rp-status").textContent = "No stored candles exist for this window, so there is nothing to replay."; return; }
    RP.bars = j.candles; RP.step = { "5m": 3e5, "15m": 9e5, "30m": 18e5, "1h": 36e5 }[j.interval] || 9e5;
    const at = ms => { if (ms == null) return -1; let k = RP.bars.findIndex(b => b[0] >= ms); return k < 0 ? RP.bars.length - 1 : k; };
    RP.idx = { signal: at(t.placed), fill: at(t.filled), exit: at(t.exit_bar != null ? t.exit_bar : (t.exit ? t.exit - RP.step : null)) };
    RP.head = Math.max(0, RP.idx.signal);
    $("rp-scrub").max = RP.bars.length - 1; $("rp-scrub").value = RP.head;
    // integrity: the candles that decided the trade must be the ones the test used
    const by = {}; RP.bars.forEach(b => { by[b[0]] = b; });
    const chk = t.bar_check || {}, keys = Object.keys(chk);
    const same = (a, b) => a && b && a.every((v, k) => Math.abs(v - b[k]) <= Math.abs(v) * 1e-9);
    const okAll = keys.length && keys.every(k => same(chk[k], by[chk[k][0]]));
    const src = (j.sources || []).map(s => `${s.label || srcLabel(s.source)} (${s.source.split(":").slice(1).join(":")})`).join(", ");
    $("rp-src").innerHTML = `${RP.bars.length} stored ${j.interval} candles from ${esc(src || srcLabel(t.source))}, ${wat(RP.bars[0][0])} to ${wat(RP.bars[RP.bars.length - 1][0] + RP.step)} WAT. ` +
      (!keys.length ? "No fill or exit candle stored for this trade, so the candles were not cross-checked."
        : okAll ? `<span class="pos">Fill and exit candles match the ones used in the test.</span>`
          : `<span class="neg">The stored candles differ from the ones used in the test (the source data has changed since). Rerun the test before relying on this replay.</span>`);
    renderInfo(); drawReplay();
  }).catch(() => { $("rp-load").hidden = true; $("rp-status").innerHTML = `<span class="neg">Market data is unavailable right now. Try again in a minute.</span>`; });
}
function closeReplay() {
  stopPlay(); $("rp").hidden = true; document.body.classList.remove("rp-open-body"); RP.trade = null;
  if (RP.lastFocus && RP.lastFocus.focus) RP.lastFocus.focus();
}
function hm(ms) { if (ms == null || ms < 0) return "-"; const m = Math.round(ms / 6e4); if (m < 60) return m + "m"; const h = Math.floor(m / 60); if (h < 48) return `${h}h ${m % 60}m`; return `${Math.floor(h / 24)}d ${h % 24}h`; }
function renderInfo() {
  const t = RP.trade, g = RP.o.setup;
  const rows = [["Signal", wat(t.placed) + " WAT"], ["Order", `${t.entry_type || g.entry_type || ""} at ${px(t.entry)}`.trim()],
    ["Filled", t.filled ? `${wat(t.filled)} WAT at ${px(t.fill_px)}${t.fill_px_theoretical != null && t.fill_px_theoretical !== t.fill_px ? ` (theoretical ${px(t.fill_px_theoretical)})` : ""}` : "-"],
    ["Stop / target", `${px(t.sl)} / ${px(t.tp)}`],
    ["Exit", t.exit ? `${wat(t.exit)} WAT at ${px(t.exit_px)}` : "-"], ["Exit reason", C.EXIT_LBL[t.exit_reason] || t.exit_reason || "-"],
    ["Waited for fill", t.filled ? hm(t.filled - t.placed) : "-"], ["Time in trade", t.exit && t.filled ? hm(t.exit - t.filled) : "-"],
    ["Result", `${sgnR(t.r_net)} after costs · ${sgnR(t.r_gross)} theoretical`]];
  if (t.conditions && t.conditions.trend) rows.push(["Conditions at signal", [t.conditions.trend, t.conditions.vol ? t.conditions.vol + " vol" : "", t.conditions.bias].filter(Boolean).join(", ")]);
  const notes = [];
  if (t.same_bar_tp_sl) notes.push("Stop and target were both inside the exit candle; the order inside a candle is unknown, so it counts as a loss.");
  if (t.tp_touched_on_fill_bar) notes.push("The target was touched on the fill candle; that does not count, because the order inside the candle is unknown.");
  if (t.exit_reason === "sl_gap_open") notes.push("Price gapped through the stop, so the exit is at the gap's open price.");
  if (t.gap_bars_in_trade) notes.push(`${t.gap_bars_in_trade} candles are missing from the data while this trade was open.`);
  $("rp-info").innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join("") + notes.map(n => `<p class="rp-note">${esc(n)}</p>`).join("");
}
function statusAt(k) {
  const t = RP.trade, b = RP.bars[k], sgn = RP.o.setup.side === "short" ? -1 : 1;
  const { signal, fill, exit } = RP.idx, when = wat(b[0]);
  if (k < signal) return `Before the signal (${when} WAT): the candles CHRONOS could see when it decided.`;
  if (fill < 0 || k < fill) return `Order resting at ${px(t.entry)} since ${wat(t.placed)} WAT. Waiting ${hm(b[0] + RP.step - t.placed)}${fill < 0 ? "; it never filled" : ""}.`;
  if (exit >= 0 && k >= exit) {
    const head = t.status === "tp" ? "Target hit" : t.status === "sl" ? "Stop hit" : "Time stop";
    return `${head} at ${wat(t.exit)} WAT after ${hm(t.exit - t.filled)} in the trade: ${sgnR(t.r_net)} after costs.` + (k > exit ? " Candles after the exit are context only." : "");
  }
  const open = (b[4] - (t.fill_px_theoretical ?? t.fill_px)) * sgn / Math.abs(t.entry - t.sl);
  return `In the trade for ${hm(b[0] + RP.step - t.filled)}. Close ${px(b[4])}, open result ${sgnR(open)} before costs.`;
}
function drawReplay() {
  if (!RP.bars.length) return;
  const cv = $("rp-cv"), H = window.matchMedia("(max-width: 640px)").matches ? 260 : 320, [c, W] = sizeCanvas(cv, H);
  const t = RP.trade, k = RP.head, VIEW = Math.max(40, Math.min(160, Math.floor(W / 6)));
  const from = Math.max(0, Math.min(k - VIEW + 12, RP.bars.length - VIEW)), to = Math.min(RP.bars.length - 1, Math.max(k, from + VIEW - 1));
  const vis = RP.bars.slice(from, k + 1);
  c.font = "11px ui-monospace,Menlo,monospace";
  const L = 8, R = Math.ceil(Math.max(...["TP " + px(t.tp), "SL " + px(t.sl), "Entry"].map(x => c.measureText(x).width))) + 10, T = 10, B = 22, w = W - L - R, h = H - T - B, n = to - from + 1, bw = w / n;
  let lo = Math.min(t.sl, t.tp, t.entry), hi = Math.max(t.sl, t.tp, t.entry);
  vis.forEach(b => { lo = Math.min(lo, b[3]); hi = Math.max(hi, b[2]); });
  const pad = (hi - lo) * .06 || hi * .001; lo -= pad; hi += pad;
  const X = i => L + (i - from) * bw + bw / 2, Y = v => T + (hi - v) / (hi - lo) * h;
  c.font = "11px ui-monospace,Menlo,monospace";
  const { signal, fill, exit } = RP.idx;
  // trade span shading
  if (fill >= 0 && k >= fill) { const x0 = X(fill) - bw / 2, x1 = X(Math.min(k, exit >= 0 ? exit : k)) + bw / 2; c.fillStyle = "rgba(95,214,58,.06)"; c.fillRect(x0, T, Math.max(0, x1 - x0), h); }
  // levels
  const lvl = (v, col, lbl, dash) => { c.strokeStyle = col; c.setLineDash(dash); c.lineWidth = 1.2; c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(W - R, Y(v)); c.stroke(); c.setLineDash([]);
    c.fillStyle = col; c.fillText(lbl, W - R + 4, Y(v) + 4); };
  lvl(t.tp, "#3ddc84", "TP " + px(t.tp), [6, 4]); lvl(t.sl, "#ff4d5e", "SL " + px(t.sl), [6, 4]); lvl(t.entry, "#c9d6cc", "Entry", [2, 3]);
  // signal line
  if (signal >= from && signal <= k) { c.strokeStyle = "rgba(201,214,204,.5)"; c.setLineDash([3, 3]); c.beginPath(); c.moveTo(X(signal) - bw / 2, T); c.lineTo(X(signal) - bw / 2, T + h); c.stroke(); c.setLineDash([]);
    c.fillStyle = "#8fa596"; c.fillText("signal", X(signal) - bw / 2 + 3, T + 10); }
  // candles up to the play head only
  vis.forEach((b, j) => { const i = from + j, x = X(i), up = b[4] >= b[1];
    c.strokeStyle = up ? "#4cc22c" : "#e0505e"; c.fillStyle = up ? "#4cc22c" : "#e0505e"; c.lineWidth = 1;
    c.beginPath(); c.moveTo(x, Y(b[2])); c.lineTo(x, Y(b[3])); c.stroke();
    const yo = Y(b[1]), yc = Y(b[4]); c.fillRect(x - Math.max(1, bw * .35), Math.min(yo, yc), Math.max(2, bw * .7), Math.max(1, Math.abs(yc - yo))); });
  // fill and exit markers
  const sgn = RP.o.setup.side === "short" ? -1 : 1;
  if (fill >= 0 && k >= fill && fill >= from) { const x = X(fill), y = Y(t.fill_px_theoretical ?? t.fill_px); c.fillStyle = "#e8f5ea";
    c.beginPath(); c.moveTo(x, y + sgn * 0); c.lineTo(x - 6, y + sgn * 10); c.lineTo(x + 6, y + sgn * 10); c.closePath(); c.fill(); }
  if (exit >= 0 && k >= exit && exit >= from) { const x = X(exit), y = Y(t.exit_px_theoretical ?? t.exit_px); c.fillStyle = t.status === "tp" ? "#3ddc84" : t.status === "sl" ? "#ff4d5e" : "#e3c35a";
    c.beginPath(); c.arc(x, y, 5, 0, 7); c.fill(); c.strokeStyle = "#010a03"; c.lineWidth = 1.5; c.stroke(); }
  c.fillStyle = "#8fa596"; const tl = wat(RP.bars[k][0]) + " WAT"; c.fillText(tl, L, H - 6);
  $("rp-status").textContent = statusAt(k);
  $("rp-scrub").value = k;
  $("rp-play").textContent = RP.timer ? "Pause" : (k >= RP.bars.length - 1 ? "Replay" : "Play");
}
function stopPlay() { clearInterval(RP.timer); RP.timer = null; if ($("rp-play")) $("rp-play").textContent = "Play"; }
function play() {
  if (RP.timer) { stopPlay(); drawReplay(); return; }
  if (RP.head >= RP.bars.length - 1) RP.head = Math.max(0, RP.idx.signal);
  const sp = +$("rp-speed").value || 4;
  RP.timer = setInterval(() => {
    if (RP.head >= RP.bars.length - 1) { stopPlay(); drawReplay(); return; }
    RP.head++; drawReplay();
  }, Math.max(30, 400 / sp));
  drawReplay();
}
function go(k) { stopPlay(); RP.head = Math.max(0, Math.min(RP.bars.length - 1, k)); drawReplay(); }
$("rp-play").addEventListener("click", play);
$("rp-back").addEventListener("click", () => go(RP.head - 1));
$("rp-fwd").addEventListener("click", () => go(RP.head + 1));
$("rp-start").addEventListener("click", () => go(RP.idx.signal));
$("rp-end").addEventListener("click", () => go(RP.idx.exit >= 0 ? RP.idx.exit : RP.bars.length - 1));
$("rp-scrub").addEventListener("input", e => go(+e.target.value));
$("rp-speed").addEventListener("change", () => { if (RP.timer) { stopPlay(); play(); } });
$("rp-close").addEventListener("click", closeReplay);
$("rp").addEventListener("click", e => { if (e.target === $("rp")) closeReplay(); });
document.addEventListener("keydown", e => {
  if ($("rp").hidden) return;
  if (e.key === "Escape") { e.preventDefault(); closeReplay(); }
  else if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  else if (e.key === " ") { e.preventDefault(); play(); }
  else if (e.key === "ArrowRight") { e.preventDefault(); go(RP.head + 1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); go(RP.head - 1); }
});
let rzT = null;
window.addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => { if (!$("rp").hidden) drawReplay(); if (scResult && !$("strat-res").hidden) drawScChart(); }, 120); });

window.P2 = { runStrategies, openReplay, setView, showingStrategies: () => showingStrat && document.body.classList.contains("view-cmp"),
  // strategy library (p3.js): read the device-saved rows, or load saved strategies into the comparison
  deviceRows: () => { try { const v = JSON.parse(localStorage.getItem(LS_KEY) || "null"); return Array.isArray(v) ? v : []; } catch (e) { return []; } },
  setRows: async rows => { await loadTypes(); stratRows = rows.filter(r => types[r.type]).slice(0, limits.max); save(); drawStratRows(); } };
{ const qv = new URLSearchParams(location.search).get("view");
  if (qv === "compare") setView("cmp"); else if (qv === "library") setView("lib"); }
})();
