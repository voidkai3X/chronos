// CHRONOS Blind Replay page. The server holds the candles; this page only ever draws what the server has revealed.
(() => {
"use strict";
const $ = id => document.getElementById(id);
const P = window.CHRONOS_PAGE, LWC = window.LightweightCharts;
const WAT = 3600;
const COL = { text: "#78857d", grid: "rgba(170,255,170,.045)", line: "rgba(170,255,170,.12)", accent: "#6dff3c", pos: "#4ade80", neg: "#e0646e", warn: "#d6b25f" };
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
let mode = "practice", tf = "1h", side = "long", kind = "market";
let S = null, chart = null, series = null, lines = [], markers = null, playing = null, busy = false, strategies = [];
const nf = (v, d) => v == null || !isFinite(v) ? "-" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d });
const px = v => { if (v == null) return "-"; const a = Math.abs(v); return Number(v).toLocaleString("en-US", { maximumFractionDigits: a >= 1000 ? 2 : a >= 1 ? 4 : 6 }); };
const sgnR = v => v == null ? "-" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "R";
const toSec = ms => Math.floor(ms / 1000) + WAT;

function toggle(id, attr, cb) {
  $(id).addEventListener("click", e => {
    const b = e.target.closest(`button[data-${attr}]`); if (!b || b.disabled) return;
    [...$(id).querySelectorAll("button")].forEach(x => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", String(on)); });
    cb(b.dataset[attr], b);
  });
}
const HELP = {
  practice: "A random window of real candles on the market you choose.",
  strategy: "Practise one of your saved strategies: its market, timeframe and rules are loaded, and its stop and R:R prefill your orders.",
  challenge: "Fixed risk rules on a random 1h window. Trade carefully: the session ends when you pass, fail or use your 10 trades.",
};
toggle("rp-mode", "m", m => {
  mode = m;
  $("rp-mode-help").textContent = HELP[m];
  $("rp-strat-wrap").hidden = m !== "strategy";
  $("rp-rules").hidden = m !== "challenge";
  $("rp-market").closest("form").querySelector('label[for="rp-market"]').textContent = m === "strategy" ? "Market (from the strategy, or another one it lists)" : "Market";
  if (m === "challenge") { $("rp-risk").value = Math.min(+$("rp-risk").value || 1, 1); $("rp-risk").max = 1; } else $("rp-risk").max = 5;
  if (m === "strategy") { loadStrategies(); fillFromStrategy(); }
});
toggle("rp-tf", "tf", v => { tf = v; });
toggle("rp-side", "s", (v, b) => { side = v; [...$("rp-side").querySelectorAll("button")].forEach(x => x.classList.remove("long", "short")); b.classList.add(v); prefill(); sizeTxt(); });
toggle("rp-kind", "k", v => { kind = v; $("rp-entry").disabled = v === "market"; if (v === "market") $("rp-entry").value = ""; $("rp-entry").placeholder = v === "market" ? "next open" : "limit price"; sizeTxt(); });
$("rp-entry").disabled = true;

// markets list for the input
let mTimer = null;
$("rp-market").addEventListener("input", () => {
  clearTimeout(mTimer);
  const q = $("rp-market").value.trim(); if (q.length < 1) return;
  mTimer = setTimeout(async () => {
    try {
      const j = await (await fetch("/api/markets?q=" + encodeURIComponent(q))).json();
      $("rp-markets").innerHTML = (j.markets || []).slice(0, 12).map(m => `<option value="${P.esc(m.market_id)}">${P.esc(m.symbol || "")}</option>`).join("");
    } catch (_) {}
  }, 180);
});

async function loadStrategies() {
  if (strategies.length) return;
  try {
    const j = await P.api("/api/library/strategies");
    strategies = j.strategies || [];
    $("rp-strat").innerHTML = strategies.length ? strategies.map(s => `<option value="${s.id}">${P.esc(s.name)}</option>`).join("")
      : '<option value="">No saved strategies yet: save one in My strategies</option>';
    fillFromStrategy();
  } catch (e) { $("rp-strat").innerHTML = `<option value="">${P.esc(e.message)}</option>`; }
}
function curStrategy() { const id = +$("rp-strat").value; return strategies.find(s => s.id === id); }
function fillFromStrategy() {
  const s = curStrategy(); if (!s) return;
  const sp = s.spec || s;
  if (sp.markets && sp.markets[0]) $("rp-market").value = sp.markets[0];
  const t = ["15m", "1h", "4h"].includes(sp.tf) ? sp.tf : null;
  if (t) { const b = $("rp-tf").querySelector(`[data-tf="${t}"]`); if (b) b.click(); }
}
$("rp-strat").addEventListener("change", fillFromStrategy);

// ---------------------------------------------------------------- chart
function makeChart() {
  if (chart) { chart.remove(); chart = null; }
  if (!LWC) { $("rp-chart").textContent = "Chart library failed to load."; return; }
  chart = LWC.createChart($("rp-chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: COL.text, fontSize: 11, fontFamily: "'Geist Mono', ui-monospace, Menlo, monospace", attributionLogo: true },
    grid: { vertLines: { color: COL.grid }, horzLines: { color: COL.grid } },
    rightPriceScale: { borderColor: COL.line }, timeScale: { borderColor: COL.line, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: 0, vertLine: { color: "rgba(234,240,235,.25)", labelBackgroundColor: "#10241a" }, horzLine: { color: "rgba(234,240,235,.25)", labelBackgroundColor: "#10241a" } },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true },
  });
  series = chart.addSeries(LWC.CandlestickSeries, { upColor: COL.pos, downColor: COL.neg, borderVisible: false, wickUpColor: COL.pos, wickDownColor: COL.neg, priceLineVisible: false });
  markers = LWC.createSeriesMarkers ? LWC.createSeriesMarkers(series, []) : null;
}
let drawnFor = null, drawnLen = 0;
function draw() {
  if (!S || !series) return;
  const data = S.candles.map(c => ({ time: toSec(c[0]), open: c[1], high: c[2], low: c[3], close: c[4] }));
  if (drawnFor === S.id && data.length === drawnLen + 1) series.update(data[data.length - 1]);
  else if (!(drawnFor === S.id && data.length === drawnLen)) series.setData(data);
  const first = drawnFor !== S.id;
  drawnFor = S.id; drawnLen = data.length;
  if (first) chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - 90), to: data.length + 6 });
  else {
    const r = chart.timeScale().getVisibleLogicalRange();
    if (r && data.length - 1 > r.to - 4) chart.timeScale().scrollToRealTime();
  }
  lines.forEach(l => series.removePriceLine(l)); lines = [];
  const act = S.trades.find(t => t.status === "pending" || t.status === "open");
  if (act) {
    const add = (price, color, title, style) => lines.push(series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    add(act.fill_price || act.entry, "#a9b4ac", act.status === "open" ? "entry" : (act.kind === "limit" ? "limit" : "entry (next open)"), act.status === "open" ? 0 : 2);
    add(act.sl, COL.neg, "SL", 2); add(act.tp, COL.pos, "TP", 2);
  }
  if (markers) {
    const byIdx = i => S.candles[i] ? toSec(S.candles[i][0]) : null;
    const ms = [];
    S.trades.forEach(t => {
      if (t.fill_idx != null && byIdx(t.fill_idx)) ms.push({ time: byIdx(t.fill_idx), position: t.side === "long" ? "belowBar" : "aboveBar", shape: t.side === "long" ? "arrowUp" : "arrowDown", color: "#a9b4ac", text: "in" });
      if (t.status === "closed" && t.exit_idx != null && byIdx(t.exit_idx)) ms.push({ time: byIdx(t.exit_idx), position: t.side === "long" ? "aboveBar" : "belowBar", shape: "circle", color: t.r_net > 0 ? COL.pos : COL.neg, text: sgnR(t.r_net) });
      if (t.status === "skipped" && byIdx(t.placed_idx)) ms.push({ time: byIdx(t.placed_idx), position: "aboveBar", shape: "square", color: "rgba(169,180,172,.45)", text: "" });
    });
    ms.sort((a, b) => a.time - b.time);
    markers.setMarkers(ms);
  }
}

// ---------------------------------------------------------------- render
function card(k, v, d, cls) { return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`; }
const REASON = { tp: "target", sl: "stop", gap_sl: "gap through stop", manual: "closed by you", end: "session end", cancelled: "cancelled" };
function render(s, events) {
  S = s;
  const live = !!s;
  $("rp-empty").hidden = live; $("rp-live").hidden = !live;
  if (!live) return;
  if (!series || drawnFor !== s.id) { makeChart(); drawnFor = null; }
  const ended = s.status !== "active";
  const st = s.stats;
  $("rp-title").textContent = `${s.market_id} · ${s.tf} · ${s.mode.charAt(0).toUpperCase() + s.mode.slice(1)}${s.settings.strategy ? " · " + s.settings.strategy.name : ""}`;
  $("rp-sub").textContent = ended ? `Session ${s.status}${s.result ? ": challenge " + s.result : ""}. The full window and real dates are now shown.`
    : (s.dates_hidden ? "Real dates hidden (times shown in WAT, shifted by whole weeks)." : "Times in WAT (UTC+1).");
  $("rp-balance").textContent = nf(s.balance);
  $("rp-ret").textContent = (st.return_pct > 0 ? "+" : "") + nf(st.return_pct) + "%";
  $("rp-ret").className = "small " + (st.return_pct > 0 ? "pos" : st.return_pct < 0 ? "neg" : "muted");
  $("rp-remaining").textContent = ended ? `${s.candles.length} candles in this window.` : `${s.remaining} hidden candles left in this window.`;
  const last = s.candles[s.cursor] || s.candles[s.candles.length - 1];
  $("rp-last").textContent = last ? `last close ${px(last[4])}` : "";
  ["rp-next", "rp-next5", "rp-play", "rp-skip", "rp-finish", "rp-place"].forEach(id => { $(id).disabled = ended; });
  const act = s.trades.find(t => t.status === "pending" || t.status === "open");
  $("rp-cancel").hidden = !(act && act.status === "pending");
  $("rp-close").hidden = !(act && act.status === "open");
  $("rp-place").hidden = !!act;
  $("rp-skip").disabled = ended || !!act;
  const ch = s.settings.challenge;
  $("rp-trisk").max = ch ? ch.max_risk_pct : 5;
  if (!$("rp-trisk").value) $("rp-trisk").value = s.settings.risk_pct;
  const sb = $("rp-strategy-box"), sg = s.settings.strategy;
  sb.hidden = !sg && !ch;
  if (sg) sb.innerHTML = `<b>${P.esc(sg.name)}</b> · ${P.esc(sg.side || "")} · stop ${sg.stop_pct != null ? P.esc(sg.stop_pct) + "%" : "-"} · R:R ${sg.rr != null ? "1:" + P.esc(sg.rr) : "-"}` +
    (sg.entry_conditions ? `<p><span class="muted">Entry:</span> ${P.esc(sg.entry_conditions)}</p>` : "") + (sg.exit_conditions ? `<p><span class="muted">Exit:</span> ${P.esc(sg.exit_conditions)}</p>` : "") +
    `<p class="muted small">Check each condition yourself before you enter. CHRONOS does not mark setups for you here.</p>`;
  else if (ch) sb.innerHTML = `<b>Challenge</b> · max ${ch.max_risk_pct}% risk per trade · pass +${ch.pass_profit_pct}% · fail -${ch.fail_drawdown_pct}% · ${st.closed}/${ch.max_trades} trades used`;
  $("rp-stats").innerHTML = [
    card("Trades", st.closed, `${st.wins} wins · ${st.losses} losses`),
    card("Win rate", st.win_rate == null ? "-" : Math.round(st.win_rate * 100) + "%", "closed trades"),
    card("Net R", sgnR(st.net_r), st.avg_r == null ? "" : `avg ${sgnR(st.avg_r)} per trade`, st.net_r > 0 ? "pos" : st.net_r < 0 ? "neg" : ""),
    card("Profit factor", st.profit_factor == null ? "-" : st.profit_factor.toFixed(2), "gross win ÷ gross loss"),
    card("Max drawdown", sgnR(st.max_drawdown_r), "within the session"),
    card("Skipped", st.skipped, "candles you chose not to trade"),
  ].join("");
  const rows = s.trades.filter(t => t.kind !== "skip");
  $("rp-trades").innerHTML = rows.length ? rows.map((t, i) => `<tr><td>${i + 1}</td><td class="side-${t.side}">${t.side}</td><td>${px(t.fill_price || t.entry)}</td><td>${px(t.sl)}</td><td>${px(t.tp)}</td>` +
    `<td>${px(t.exit_price)}</td><td>${t.status === "closed" ? REASON[t.exit_reason] || t.exit_reason : t.status}${t.note ? ` <span class="muted" title="${P.esc(t.note)}">ⓘ</span>` : ""}</td>` +
    `<td class="${t.r_net > 0 ? "pos" : t.r_net < 0 ? "neg" : ""}">${t.r_net == null ? "-" : sgnR(t.r_net)}</td><td>${t.pnl == null ? "-" : nf(t.pnl)}</td></tr>`).join("")
    : `<tr><td colspan="9" class="muted">No trades yet.</td></tr>`;
  $("rp-sources").textContent = s.sources && s.sources.length ? "Data: " + s.sources.map(x => x.label || x.source).filter((v, i, a) => a.indexOf(v) === i).join(", ") + "." : (ended ? "" : "Data sources are shown when the session ends (they would reveal the dates).");
  draw();
  sizeTxt();
  (events || []).forEach(ev => {
    const t = s.trades.find(x => x.id === ev.trade_id); if (!t) return;
    if (ev.status === "open") P.toast(`Order filled at ${px(t.fill_price)}.`, true);
    else if (ev.status === "closed") P.toast(`Trade closed: ${REASON[t.exit_reason] || t.exit_reason}, ${sgnR(t.r_net)}${t.pnl != null ? ` (${nf(t.pnl)})` : ""}.`, true);
    else if (ev.status === "cancelled") P.toast(t.note || "Order cancelled.", true);
  });
  if (ended) { stopPlay(); loadAgg(); }
}

function sizeTxt() {
  if (!S) return;
  const last = S.candles[S.cursor] ? S.candles[S.cursor][4] : null;
  const e = kind === "limit" ? +$("rp-entry").value : last, sl = +$("rp-sl").value, r = +$("rp-trisk").value || S.settings.risk_pct;
  if (!(e > 0) || !(sl > 0) || e === sl) { $("rp-size").textContent = "-"; return; }
  const amt = S.balance * r / 100, u = amt / Math.abs(e - sl);
  const tp = +$("rp-tp").value;
  $("rp-size").textContent = `${Number(u.toPrecision(4))} units · risk ${nf(amt)}${tp > 0 ? ` · 1:${(Math.abs(tp - e) / Math.abs(e - sl)).toFixed(2)}` : ""}`;
}
["rp-entry", "rp-sl", "rp-tp", "rp-trisk"].forEach(id => $(id).addEventListener("input", sizeTxt));
function prefill() {  // strategy mode: stop % and R:R from the saved strategy
  if (!S || !S.settings.strategy) return;
  const sg = S.settings.strategy, last = S.candles[S.cursor][4];
  if (sg.stop_pct && sg.rr && !$("rp-sl").value) {
    const d = last * sg.stop_pct / 100, sgn = side === "long" ? 1 : -1;
    $("rp-sl").value = +(last - sgn * d).toPrecision(8); $("rp-tp").value = +(last + sgn * d * sg.rr).toPrecision(8);
  }
}

// ---------------------------------------------------------------- actions
async function act(path, body, quiet) {
  if (!S || busy) return null;
  busy = true;
  try {
    const j = await P.api(`/api/practice/sessions/${S.id}/${path}`, { method: "POST", body: body || {} });
    render(j.session, j.events);
    return j;
  } catch (e) { if (!quiet) P.toast(e.message); stopPlay(); return null; }
  finally { busy = false; }
}
$("rp-next").addEventListener("click", () => act("advance", { n: 1 }));
$("rp-next5").addEventListener("click", () => act("advance", { n: 5 }));
$("rp-skip").addEventListener("click", () => act("skip"));
$("rp-cancel").addEventListener("click", () => act("cancel"));
$("rp-close").addEventListener("click", () => act("close"));
$("rp-finish").addEventListener("click", () => { if (confirm("End this session? Open trades close at the last price and the full window is revealed.")) act("finish"); });
function stopPlay() { if (playing) { clearTimeout(playing); playing = null; } $("rp-play").textContent = "Play"; $("rp-play").setAttribute("aria-pressed", "false"); }
async function tick() {
  const j = await act("advance", { n: 1 }, false);
  if (!j || !playing) return;
  if ((j.events || []).some(ev => ev.status === "closed" || ev.status === "open") || j.session.status !== "active") { stopPlay(); return; }
  playing = setTimeout(tick, +$("rp-speed").value);
}
$("rp-play").addEventListener("click", () => {
  if (playing) { stopPlay(); return; }
  $("rp-play").textContent = "Pause"; $("rp-play").setAttribute("aria-pressed", "true");
  playing = setTimeout(tick, 0);
});
$("rp-ticket").addEventListener("submit", async e => {
  e.preventDefault();
  $("rp-terr").hidden = true;
  const body = { side, kind, sl: $("rp-sl").value, tp: $("rp-tp").value, risk_pct: $("rp-trisk").value || undefined };
  if (kind === "limit") body.entry = $("rp-entry").value;
  if (!body.sl || !body.tp) { $("rp-terr").textContent = "Set a stop-loss and a take-profit."; $("rp-terr").hidden = false; return; }
  stopPlay();
  try {
    const j = await P.api(`/api/practice/sessions/${S.id}/orders`, { method: "POST", body });
    render(j.session);
    P.toast(kind === "market" ? "Market order placed: it fills at the next candle's open." : "Limit order placed.", true);
  } catch (err) { $("rp-terr").textContent = err.message; $("rp-terr").hidden = false; }
});
$("rp-form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = $("rp-err"); err.hidden = true;
  const body = { mode, tf, market: $("rp-market").value.trim(), risk_pct: $("rp-risk").value, hide_dates: $("rp-hide").checked };
  if (mode === "strategy") { body.strategy_id = $("rp-strat").value; if (!body.strategy_id) { err.textContent = "Choose a saved strategy."; err.hidden = false; return; } }
  else if (!body.market) { err.textContent = "Choose a market."; err.hidden = false; return; }
  if (S && S.status === "active" && !confirm("Start a new session? The current one will end and its open trade closes at the last price.")) return;
  const btn = $("rp-start"); btn.disabled = true; btn.querySelector("span").textContent = "Loading real candles…";
  stopPlay();
  try {
    const j = await P.api("/api/practice/sessions", { method: "POST", body });
    ["rp-entry", "rp-sl", "rp-tp", "rp-trisk"].forEach(id => { $(id).value = ""; });
    drawnFor = null;
    render(j.session);
    prefill();
    if (window.innerWidth <= 980) $("rp-live").scrollIntoView({ behavior: reduce() ? "auto" : "smooth" });
  } catch (x) { err.textContent = x.message; err.hidden = false; }
  finally { btn.disabled = false; btn.querySelector("span").textContent = "Start session"; }
});
// keyboard: N = next candle, Space = play/pause (not while typing)
document.addEventListener("keydown", e => {
  if (!S || S.status !== "active" || e.target.closest("input,select,textarea,button") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "n" || e.key === "N" || e.key === "ArrowRight") { e.preventDefault(); act("advance", { n: 1 }); }
  else if (e.key === " ") { e.preventDefault(); $("rp-play").click(); }
});

// ---------------------------------------------------------------- practice statistics
async function loadAgg() {
  const box = $("rp-agg");
  try {
    const j = await P.api("/api/practice/stats?tz=" + (-new Date().getTimezoneOffset()));
    const a = j.stats;
    if (!a.total.trades && !a.skipped) { box.innerHTML = '<p class="muted">No practice trades yet. Finished trades appear here by day and week.</p>'; return; }
    const max = Math.max(1, ...a.daily.map(d => Math.abs(d.net_r)));
    const bars = a.daily.map(d => {
      const h = Math.round(Math.abs(d.net_r) / max * 100);
      return `<div class="db" title="${d.date}: ${d.trades} trades, ${sgnR(d.net_r)}"><i class="${d.net_r >= 0 ? "up" : "dn"}" style="height:${d.trades ? Math.max(4, h) : 0}%"></i><span>${d.date.slice(8)}</span></div>`;
    }).join("");
    const wk = a.weekly.slice().reverse().filter(w => w.trades).map(w => `<tr><td>${w.week_start}</td><td>${w.trades}</td><td>${w.win_rate == null ? "-" : Math.round(w.win_rate * 100) + "%"}</td><td class="${w.net_r > 0 ? "pos" : w.net_r < 0 ? "neg" : ""}">${sgnR(w.net_r)}</td><td>${w.avg_r == null ? "-" : sgnR(w.avg_r)}</td></tr>`).join("");
    box.innerHTML = `<div class="kpis2 br-stats">${card("All practice trades", a.total.trades, `${a.total.wins} wins · ${a.total.losses} losses`)}` +
      card("Win rate", a.total.win_rate == null ? "-" : Math.round(a.total.win_rate * 100) + "%", "") +
      card("Net R", sgnR(a.total.net_r), a.total.avg_r == null ? "" : `avg ${sgnR(a.total.avg_r)}`, a.total.net_r > 0 ? "pos" : a.total.net_r < 0 ? "neg" : "") +
      card("Challenges", `${a.challenges.passed}/${a.challenges.attempted}`, `passed · ${a.challenges.failed} failed`) + `</div>` +
      `<div class="sec-h">Last 14 days (net R per day)</div><div class="daybars" role="img" aria-label="Net R per day for the last 14 days">${bars}</div>` +
      (wk ? `<div class="sec-h">By week</div><div class="tbl-wrap"><table><thead><tr><th>Week of</th><th>Trades</th><th>Win rate</th><th>Net R</th><th>Avg R</th></tr></thead><tbody>${wk}</tbody></table></div>` : "") +
      `<p class="muted small">By mode: practice ${a.by_mode.practice.trades}, strategy ${a.by_mode.strategy.trades}, challenge ${a.by_mode.challenge.trades} trades. ${a.skipped} skipped setups. ${a.total.trades < 30 ? "Fewer than 30 trades: too few to judge your skill." : ""}</p>`;
  } catch (e) { box.innerHTML = `<p class="muted">${P.esc(e.status === 401 ? "Sign in to see your practice statistics." : e.message)}</p>`; }
}

document.addEventListener("chronos:auth", async e => {
  const u = e.detail && e.detail.user;
  $("rp-gate").hidden = !!u; $("rp-form").hidden = !u;
  if (!u) { render(null); $("rp-agg").innerHTML = '<p class="muted">Sign in to see your practice statistics.</p>'; return; }
  try { const j = await P.api("/api/practice/active"); if (j.session) render(j.session); } catch (_) {}
  loadAgg();
  if (mode === "strategy") loadStrategies();
});
})();
