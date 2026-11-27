// CHRONOS v2 interface glue: navigation, account menu, result tabs, and TradingView Lightweight Charts
// (equity + drawdown, trade replay) with zoom / pan / reset. Every chart is drawn from the test's own data only.
(() => {
"use strict";
const $ = id => document.getElementById(id);
const LWC = window.LightweightCharts;
const WAT = 3600; // chart times are shown in WAT (UTC+1): shift UTC seconds by one hour
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COL = {
  text: "#78857d", grid: "rgba(170,255,170,.045)", line: "rgba(170,255,170,.12)",
  accent: "#6dff3c", pos: "#4ade80", neg: "#e0646e", warn: "#d6b25f", soft: "#a9b4ac", bg: "#050c08",
};
const coarse = () => window.matchMedia("(pointer: coarse)").matches;

// ---------------------------------------------------------------- charts
function baseOpts(extra) {
  return Object.assign({
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: COL.text, fontSize: 11,
      fontFamily: "'Geist Mono', ui-monospace, Menlo, monospace", attributionLogo: true,
      panes: { separatorColor: COL.line, separatorHoverColor: "rgba(109,255,60,.15)", enableResize: false } },
    grid: { vertLines: { color: COL.grid }, horzLines: { color: COL.grid } },
    rightPriceScale: { borderColor: COL.line },
    timeScale: { borderColor: COL.line, timeVisible: true, secondsVisible: false, rightOffset: 4, fixLeftEdge: false },
    crosshair: { mode: 0, vertLine: { color: "rgba(234,240,235,.25)", labelBackgroundColor: "#10241a" },
      horzLine: { color: "rgba(234,240,235,.25)", labelBackgroundColor: "#10241a" } },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: { time: true, price: false } },
    kineticScroll: { touch: true, mouse: false },
  }, extra || {});
}
const toSec = ms => Math.floor(ms / 1000) + WAT;
function increasing(points) { // Lightweight Charts needs strictly increasing times
  let last = -Infinity;
  return points.map(p => { let t = p.time; if (t <= last) t = last + 1; last = t; return Object.assign({}, p, { time: t }); });
}
const charts = {}; // id -> { chart, ... }
function zoom(id, how) {
  const c = charts[id]; if (!c) return;
  const ts = c.chart.timeScale();
  if (how === "fit") { c.reset ? c.reset() : ts.fitContent(); return; }
  const r = ts.getVisibleLogicalRange(); if (!r) return;
  const mid = (r.from + r.to) / 2, half = (r.to - r.from) / 2 * (how === "in" ? 0.6 : 1.6);
  ts.setVisibleLogicalRange({ from: mid - Math.max(2, half), to: mid + Math.max(2, half) });
}
document.addEventListener("click", e => {
  const b = e.target.closest(".chart-tools .ctl"); if (!b) return;
  zoom(b.closest(".chart-tools").dataset.for, b.dataset.z);
});

let eqFor = null;
function equity(el, o) {
  if (!LWC || !el || el.tagName === "CANVAS") return false;
  const eqs = (o.stats && o.stats.equity) || [];
  if (eqFor === o && charts.chart) return true;
  eqFor = o;
  if (charts.chart) { charts.chart.chart.remove(); delete charts.chart; }
  el.innerHTML = "";
  const tools = document.querySelector('.chart-tools[data-for="chart"]');
  if (!eqs.length) {
    el.innerHTML = '<div class="lwc-empty">No closed trades in this test, so there is no equity curve to draw.</div>';
    if (tools) tools.hidden = true;
    return true;
  }
  if (tools) tools.hidden = false;
  const chart = LWC.createChart(el, baseOpts({ localization: { priceFormatter: v => (v > 0 ? "+" : "") + v.toFixed(2) + "R" } }));
  const t0 = o.data && o.data.from_ms && o.data.from_ms < eqs[0].t ? o.data.from_ms : eqs[0].t - 1000;
  const raw = [{ t: t0, n: 0, g: 0, s: null }].concat(eqs);
  const net = increasing(raw.map(p => ({ time: toSec(p.t), value: p.n })));
  const gross = net.map((p, i) => ({ time: p.time, value: raw[i].g }));
  let peak = 0; const dd = net.map(p => { peak = Math.max(peak, p.value); return { time: p.time, value: +(p.value - peak).toFixed(4) }; });
  const fmt = { type: "custom", formatter: v => (v > 0 ? "+" : "") + v.toFixed(2) + "R", minMove: 0.01 };
  const gS = chart.addSeries(LWC.LineSeries, { color: "rgba(169,180,172,.55)", lineWidth: 1, lineStyle: 2, priceLineVisible: false,
    lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: fmt, title: "" });
  const nS = chart.addSeries(LWC.AreaSeries, { lineColor: COL.accent, lineWidth: 2, topColor: "rgba(109,255,60,.16)", bottomColor: "rgba(109,255,60,0)",
    priceLineVisible: false, priceFormat: fmt, crosshairMarkerBackgroundColor: COL.accent, crosshairMarkerBorderColor: COL.bg });
  gS.setData(gross); nS.setData(net);
  nS.createPriceLine({ price: 0, color: "rgba(170,255,170,.22)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
  if (eqs.length <= 200 && LWC.createSeriesMarkers) {
    LWC.createSeriesMarkers(nS, net.slice(1).map((p, i) => ({ time: p.time, position: "inBar", shape: "circle", size: 0.4,
      color: eqs[i].s === "tp" ? COL.pos : eqs[i].s === "sl" ? COL.neg : COL.warn })));
  }
  const dS = chart.addSeries(LWC.AreaSeries, { lineColor: "rgba(224,100,110,.85)", lineWidth: 1, topColor: "rgba(224,100,110,.04)",
    bottomColor: "rgba(224,100,110,.28)", invertFilledArea: false, priceLineVisible: false, priceFormat: fmt }, 1);
  dS.setData(dd);
  try { const panes = chart.panes(); if (panes[1]) { panes[0].setStretchFactor(3); panes[1].setStretchFactor(1); } } catch (_) {}
  chart.timeScale().fitContent();
  charts.chart = { chart, reset: () => chart.timeScale().fitContent() };
  return true;
}

// trade replay: candles revealed up to the play head (never past it), entry / TP / SL lines, signal / fill / exit markers
const RPC = { chart: null, series: null, markers: null, trade: null, shown: -1, lines: [] };
function replay(el, RP) {
  if (!LWC || !el || el.tagName === "CANVAS" || !RP.bars.length) return false;
  const t = RP.trade, k = RP.head;
  const dec = (() => { // price precision from the stored candles themselves (2 to 6 decimals)
    let d = 0; RP.bars.slice(0, 80).forEach(b => b.slice(1, 5).forEach(v => { const s = String(v), i = s.indexOf("."); if (i >= 0) d = Math.max(d, s.length - i - 1); }));
    return Math.min(6, Math.max(2, d)); })();
  if (!RPC.chart) {
    RPC.chart = LWC.createChart(el, baseOpts({ rightPriceScale: { borderColor: COL.line, scaleMargins: { top: .12, bottom: .1 } } }));
    charts["rp-cv"] = { chart: RPC.chart, reset: () => frame(RP, true) };
  }
  if (RPC.trade !== t) {
    if (RPC.series) RPC.chart.removeSeries(RPC.series);
    RPC.series = RPC.chart.addSeries(LWC.CandlestickSeries, {
      upColor: COL.pos, downColor: COL.neg, borderUpColor: COL.pos, borderDownColor: COL.neg, wickUpColor: "rgba(74,222,128,.8)",
      wickDownColor: "rgba(224,100,110,.8)", priceLineVisible: false, lastValueVisible: true,
      priceFormat: { type: "price", precision: dec, minMove: Math.pow(10, -dec) } });
    const lvl = (price, color, title, style) => RPC.series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    lvl(t.tp, COL.pos, "TP", 2); lvl(t.sl, COL.neg, "SL", 2); lvl(t.entry, COL.soft, "Entry", 1);
    RPC.markers = LWC.createSeriesMarkers ? LWC.createSeriesMarkers(RPC.series, []) : null;
    RPC.data = RP.bars.map(b => ({ time: toSec(b[0]), open: b[1], high: b[2], low: b[3], close: b[4] }));
    RPC.data = increasing(RPC.data);
    RPC.trade = t; RPC.shown = -1;
  }
  if (k === RPC.shown + 1 && RPC.shown >= 0) RPC.series.update(RPC.data[k]);
  else RPC.series.setData(RPC.data.slice(0, k + 1));
  const first = RPC.shown < 0; RPC.shown = k;
  const { signal, fill, exit } = RP.idx, long = RP.o.setup.side !== "short", m = [];
  if (signal >= 0 && k >= signal) m.push({ time: RPC.data[signal].time, position: "aboveBar", shape: "arrowDown", color: COL.soft, text: "Signal", size: 0.8 });
  if (fill >= 0 && k >= fill) m.push({ time: RPC.data[fill].time, position: long ? "belowBar" : "aboveBar", shape: long ? "arrowUp" : "arrowDown", color: "#eaf0eb", text: "Fill", size: 0.9 });
  if (exit >= 0 && k >= exit) m.push({ time: RPC.data[exit].time, position: "inBar", shape: "circle",
    color: t.status === "tp" ? COL.pos : t.status === "sl" ? COL.neg : COL.warn, text: t.status === "tp" ? "TP" : t.status === "sl" ? "SL" : "Exit", size: 1 });
  m.sort((a, b) => a.time - b.time);
  if (RPC.markers) RPC.markers.setMarkers(m);
  frame(RP, first);
  return true;
}
function frame(RP, force) {
  const ts = RPC.chart.timeScale(), k = RP.head, w = el => (el && el.clientWidth) || 600;
  const VIEW = Math.max(40, Math.min(140, Math.floor(w($("rp-cv")) / 7)));
  const want = { from: k - VIEW + 12, to: k + 12 };
  const r = ts.getVisibleLogicalRange();
  if (force || !r || k > r.to - 3 || k < r.from + 1) ts.setVisibleLogicalRange(want);
}
window.CHRONOS_UI = { equity, replay, zoom };

// ---------------------------------------------------------------- navigation
const seg = $("view-seg");
if (seg) seg.addEventListener("click", e => {
  const b = e.target.closest("button[data-view]"); if (!b) return;
  if (b.dataset.view !== "test" && window.CHRONOS_NAV && window.CHRONOS_NAV.current() === "ct") window.CHRONOS_NAV.show("bt");
  closeMenu();
}, true);
const navRes = $("nav-res");
if (navRes) navRes.addEventListener("click", () => {
  if (window.CHRONOS_NAV && window.CHRONOS_NAV.current() === "ct") window.CHRONOS_NAV.show("bt");
  const target = ["res", "lib-res", "strat-res", "skel", "res-err"].map($).find(x => x && !x.hidden) || $("results");
  const top = target.getBoundingClientRect().top + window.scrollY - ($("topbar") ? $("topbar").offsetHeight : 0) - 8;
  window.scrollTo({ top: Math.max(0, top), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
});

// the form panel's title follows the view (Backtester / Compare / Library)
const setupH = $("setup-h");
function syncTitle() {
  if (!setupH) return;
  const b = document.body.classList;
  setupH.textContent = b.contains("view-lib") ? "Strategy library" : b.contains("view-cmp") ? "Compare strategies" : "Your setup";
}
new MutationObserver(syncTitle).observe(document.body, { attributes: true, attributeFilter: ["class"] });
syncTitle();

// ---------------------------------------------------------------- result tabs
const tabs = $("res-tabs");
function showPane(name) {
  if (!tabs) return;
  [...tabs.querySelectorAll("button[data-tab]")].forEach(b => { const on = b.dataset.tab === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
  document.querySelectorAll("#res .rpane").forEach(p => p.classList.toggle("on", p.dataset.pane === name));
  const C = window.CHRONOS;
  if (name === "perf" && window.CHRONOS_LAST && C && C.drawHist) try { C.drawHist(window.CHRONOS_LAST); } catch (_) {}
}
if (tabs) {
  tabs.addEventListener("click", e => {
    const b = e.target.closest("button[data-tab]"); if (!b) return;
    showPane(b.dataset.tab);
    const r = tabs.getBoundingClientRect(), stick = $("topbar") ? $("topbar").offsetHeight : 0;
    if (r.top < stick - 1) window.scrollTo({ top: window.scrollY + r.top - stick, behavior: "auto" });
  });
  tabs.addEventListener("keydown", e => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const bs = [...tabs.querySelectorAll("button[data-tab]")], i = bs.indexOf(document.activeElement); if (i < 0) return;
    const n = bs[(i + (e.key === "ArrowRight" ? 1 : bs.length - 1)) % bs.length]; n.focus(); n.click(); e.preventDefault();
  });
}
document.addEventListener("chronos:result", e => {
  window.CHRONOS_LAST = e.detail;
  showPane("overview");
  const o = e.detail || {}, n = (o.trades || []).filter(t => t.status && t.status !== "unfilled").length;
  if ($("tab-n-trades")) $("tab-n-trades").textContent = n ? String(n) : "";
  syncAdvSum();
});
// a sort / replay link elsewhere can ask for a pane
window.CHRONOS_UI.showPane = showPane;

// ---------------------------------------------------------------- advanced settings summary
function syncAdvSum() {
  const s = $("adv-sum"); if (!s) return;
  const v = id => $(id) ? $(id).value : "";
  const parts = [];
  if (v("htf") && v("htf") !== "off") parts.push("HTF " + v("htf"));
  parts.push("time stop " + (v("max-hold") === "none" ? "off" : v("max-hold")));
  if ($("slip")) parts.push("slip " + v("slip") + " " + (v("slip-unit") || ""));
  if ($("tf-sweep") && $("tf-sweep").checked) parts.push("all TFs");
  if ($("overlap") && $("overlap").checked) parts.push("overlap");
  if ($("insights") && $("insights").checked) parts.push("insights");
  s.textContent = parts.join(" · ");
}
const f = $("f");
if (f) { f.addEventListener("change", syncAdvSum); f.addEventListener("input", syncAdvSum); }
syncAdvSum();

// ---------------------------------------------------------------- account menu
const mBtn = $("acct-menu-btn"), menu = $("acct-menu");
function closeMenu() { if (menu && !menu.hidden) { menu.hidden = true; mBtn.setAttribute("aria-expanded", "false"); } }
function openMenu() { menu.hidden = false; mBtn.setAttribute("aria-expanded", "true"); fillMenu(); }
if (mBtn && menu) {
  mBtn.addEventListener("click", e => { e.stopPropagation(); menu.hidden ? openMenu() : closeMenu(); });
  document.addEventListener("click", e => { if (!menu.hidden && !menu.contains(e.target) && e.target !== mBtn) closeMenu(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !menu.hidden) { closeMenu(); mBtn.focus(); } });
  menu.addEventListener("click", e => {
    const b = e.target.closest("[data-go]"); if (!b) return;
    closeMenu();
    if (b.dataset.go === "ct") { const t = $("tab-ct"); if (t) t.click(); }
    else { const v = seg && seg.querySelector(`[data-view="${b.dataset.go}"]`); if (v) v.click(); window.scrollTo(0, 0); }
  });
  $("acct-signout").addEventListener("click", () => setTimeout(closeMenu, 0));
}
const dateTxt = s => { if (!s) return ""; const d = new Date(s); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Africa/Lagos" }); };
function fillMenu() {
  const u = window.CHRONOS_AUTH && window.CHRONOS_AUTH.user; if (!u) return;
  $("am-email").textContent = u.email || "";
  const plan = u.is_admin ? "Admin" : (u.plan || "free") + " plan";
  $("am-plan").textContent = typeof plan === "string" ? plan.charAt(0).toUpperCase() + plan.slice(1) : "";
  const rows = [];
  if (u.free_until) rows.push([new Date(u.free_until) < new Date() ? "Free access ended" : "Free access until", dateTxt(u.free_until)]);
  if (u.created_at || u.created) rows.push(["Member since", dateTxt(u.created_at || u.created)]);
  $("am-dl").innerHTML = rows.filter(r => r[1]).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  $("am-dl").hidden = !rows.length;
}
document.addEventListener("chronos:auth", e => {
  const u = e.detail && e.detail.user;
  if (mBtn) mBtn.hidden = !u;
  if (!u) closeMenu();
  if ($("acct-av")) $("acct-av").textContent = u && u.email ? u.email.trim().charAt(0).toUpperCase() : "";
  if (u) fillMenu();
});
const delGo = $("am-del-go");
if (delGo) delGo.addEventListener("click", async () => {
  const pw = $("am-del-pw").value, err = $("am-del-err");
  err.hidden = true;
  if (!pw) { err.textContent = "Enter your password to confirm."; err.hidden = false; return; }
  delGo.disabled = true;
  try {
    const r = await window.fetch("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw, confirm: true }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = j.error || "Could not delete the account."; err.hidden = false; return; }
    location.href = "/";
  } catch (_) { err.textContent = "Network error. Nothing was deleted."; err.hidden = false; }
  finally { delGo.disabled = false; $("am-del-pw").value = ""; }
});
})();
