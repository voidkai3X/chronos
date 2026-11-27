(() => {
const $ = id => document.getElementById(id);
let markets = [], side = "long", mode = "similar", chosen = null, acIdx = -1, lastResult = null, lastRequest = null;
const forceScan = new URLSearchParams(location.search).get("force_scan") === "1";

const fmt = (x, d) => x == null ? "-" : Number(x).toLocaleString("en-US", {maximumFractionDigits: d ?? 6});
const px = x => { if (x == null) return "-"; const a = Math.abs(x); return fmt(x, a >= 1000 ? 2 : a >= 1 ? 4 : 6); };
const pct = x => x == null ? "n/a" : (x * 100).toFixed(1) + "%";
const sgnR = x => x == null ? "-" : (x > 0 ? "+" : "") + Number(x).toFixed(2) + "R";
const pad = n => String(n).padStart(2, "0");
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function wat(ms) { if (!ms) return "-"; const d = new Date(ms + 3600e3);
  return `${pad(d.getUTCDate())} ${MON[d.getUTCMonth()]} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }

// ---------- markets / autocomplete
fetch("/api/markets").then(r => r.json()).then(j => { markets = j.markets || []; applyQuery(); });

function search(q) {
  q = q.trim().toUpperCase();
  if (!q) return markets.slice(0, 60);
  return markets.filter(m => (m.symbol + " " + m.coin + " " + m.market_id).toUpperCase().includes(q))
    .sort((a, b) => (b.symbol.toUpperCase().startsWith(q) - a.symbol.toUpperCase().startsWith(q)) || a.symbol.localeCompare(b.symbol))
    .slice(0, 60);
}
function renderAc() {
  const list = $("ac-list"), items = search($("market").value);
  list.innerHTML = items.map((m, i) => `<li data-id="${m.market_id}" class="${i === acIdx ? "act" : ""}">
    <span>${m.symbol}</span><span class="p">${m.provider} · ${m.asset_class}</span></li>`).join("") ||
    `<li class="p">No MFP market matches</li>`;
  list.hidden = false; $("market").setAttribute("aria-expanded", "true");
}
function pick(id) {
  chosen = markets.find(m => m.market_id === id) || null;
  if (!chosen) return;
  $("market").value = chosen.symbol;
  $("mkt-id").textContent = chosen.market_id;
  $("ac-list").hidden = true; $("market").setAttribute("aria-expanded", "false"); acIdx = -1;
  loadPrice();
}
$("market").addEventListener("input", () => { chosen = null; $("mkt-id").textContent = ""; setPrice("", ""); acIdx = -1; renderAc(); });
$("market").addEventListener("focus", renderAc);
$("market").addEventListener("keydown", e => {
  const lis = [...$("ac-list").querySelectorAll("li[data-id]")];
  if (e.key === "ArrowDown") { acIdx = Math.min(acIdx + 1, lis.length - 1); renderAc(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { acIdx = Math.max(acIdx - 1, 0); renderAc(); e.preventDefault(); }
  else if (e.key === "Enter" && !$("ac-list").hidden && lis.length) { pick(lis[Math.max(acIdx, 0)].dataset.id); e.preventDefault(); }
  else if (e.key === "Escape") $("ac-list").hidden = true;
});
$("ac-list").addEventListener("mousedown", e => { const li = e.target.closest("li[data-id]"); if (li) { pick(li.dataset.id); e.preventDefault(); } });
document.addEventListener("click", e => { if (!e.target.closest(".ac")) $("ac-list").hidden = true; });

let refPrice = null;
function setPrice(txt, cls) { const el = $("price"); el.textContent = txt; el.className = "price" + (cls ? " " + cls : ""); }
function loadPrice() {
  if (!chosen) return;
  const id = chosen.market_id;
  setPrice("loading price...", "loading");
  fetch("/api/price?market=" + encodeURIComponent(id)).then(r => r.json()).then(j => {
    if (!chosen || chosen.market_id !== id) return;
    if (j.error) { setPrice("price unavailable", "bad"); refPrice = null; return; }
    refPrice = j.price;
    setPrice(fmt(j.price) + " · last 15m close", "");
    updateRR();
  }).catch(() => { setPrice("price unavailable", "bad"); });
}

// ---------- side + live R:R
$("side").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  side = b.dataset.side;
  [...$("side").children].forEach(x => x.classList.toggle("on", x === b));
  updateRR();
});
function syncMode() {
  $("pattern-opts").hidden = mode !== "pattern";
  $("style-opts").hidden = mode !== "pattern";
  if ($("similar-opts")) $("similar-opts").hidden = mode !== "similar";
  if ($("insights-row")) $("insights-row").hidden = mode === "similar";
  $("expiry").disabled = mode === "pattern";
  $("exp-note").textContent = mode === "pattern" ? "· not used in pattern mode" : "";
}
$("mode").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  mode = b.dataset.mode;
  [...$("mode").children].forEach(x => x.classList.toggle("on", x === b));
  syncMode();
});
syncMode();
function levelError() {
  const e = parseFloat($("entry").value), t = parseFloat($("tp").value), s = parseFloat($("sl").value);
  if ([e, t, s].some(isNaN)) return null;
  if (side === "long" && !(s < e && e < t)) return "Long needs SL below entry and TP above entry.";
  if (side === "short" && !(t < e && e < s)) return "Short needs TP below entry and SL above entry.";
  return "";
}
function updateRR() {
  const e = parseFloat($("entry").value), t = parseFloat($("tp").value), s = parseFloat($("sl").value);
  const le = levelError();
  $("lvl-err").hidden = !le; $("lvl-err").textContent = le || "";
  if ([e, t, s].some(isNaN) || le) { $("rr").textContent = "-"; $("rr-note").textContent = ""; return; }
  const rr = Math.abs(t - e) / Math.abs(e - s);
  $("rr").textContent = "1:" + rr.toFixed(2);
  let note = `risk ${(Math.abs(e - s) / e * 100).toFixed(2)}%, reward ${(Math.abs(t - e) / e * 100).toFixed(2)}%`;
  if (refPrice) {
    const dist = (e - refPrice) / refPrice * 100;
    const kind = Math.abs(dist) < 1e-9 ? "market" : ((side === "long") === (e < refPrice) ? "limit" : "stop");
    note += `, ${kind} entry ${dist >= 0 ? "+" : ""}${dist.toFixed(2)}% from price`;
  }
  $("rr-note").textContent = note;
}
["entry", "tp", "sl"].forEach(id => $(id).addEventListener("input", updateRR));
$("days").addEventListener("input", () => $("days-v").textContent = $("days").value);

// ---------- screenshot
let shotFile = null, shotUrl = null, setupPattern = "";
function showShotErr(msg) { const el = $("shot-err"); if (!el) return; el.hidden = !msg; el.textContent = msg || ""; }
function syncPicks() {
  const box = $("shot-picks"); if (!box) return;
  const v = ($("shot-pattern").value || "").trim();
  box.querySelectorAll("button[data-pattern]").forEach(b => b.classList.toggle("on", b.dataset.pattern === v));
}
function markUnsure(id, on) { const el = $(id); if (el) el.classList.toggle("unsure", !!on); }
function clearUnsure() { ["market", "entry", "tp", "sl", "similar-tf"].forEach(id => markUnsure(id, false)); }
function sniffImageMagic(buf) {
  const b = new Uint8Array(buf);
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return "image/png";
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (b.length >= 12 && b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 &&
      b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) return "image/webp";
  if (b.length >= 6 && b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return "image/gif";
  return null;
}
async function acceptShotFile(file) {
  if (!file) return { ok: false, error: "Choose a chart screenshot first." };
  if (file.size > 5 * 1024 * 1024) return { ok: false, error: "That image is too large. Use a PNG, JPG or WebP under 5 MB." };
  const type = (file.type || "").toLowerCase();
  if (type.startsWith("image/")) return { ok: true };
  try {
    const buf = await file.slice(0, 16).arrayBuffer();
    if (sniffImageMagic(buf)) return { ok: true };
  } catch (e) { /* fall through to a visible error */ }
  if (/\.(png|jpe?g|webp|gif|heic|heif|bmp|avif)$/i.test(file.name || "")) return { ok: true };
  return { ok: false, error: "Please upload a chart screenshot (PNG, JPG or WebP)." };
}
let shotSetTok = 0;
async function setShotFile(file) {
  if (!file) return clearShotFile();
  const tok = ++shotSetTok;
  const res = await acceptShotFile(file);
  if (tok !== shotSetTok) return;
  if (!res.ok) {
    showShotErr(res.error);
    toast(res.error, "Couldn't use that file.");
    return;
  }
  shotFile = file;
  if (shotUrl) URL.revokeObjectURL(shotUrl);
  shotUrl = URL.createObjectURL(file);
  $("shot-thumb").src = shotUrl;
  $("shot-name").textContent = file.name || "chart";
  const kb = file.size / 1024;
  $("shot-size").textContent = kb >= 1024 ? (kb / 1024).toFixed(2) + " MB" : Math.max(1, Math.round(kb)) + " KB";
  $("shot-preview").hidden = false;
  $("shot-drop-txt").hidden = true;
  const chooseTxt = document.querySelector(".shot-choose-txt");
  if (chooseTxt) chooseTxt.textContent = "Replace file";
  showShotErr("");
}
function clearShotFile() {
  shotFile = null;
  if (shotUrl) { URL.revokeObjectURL(shotUrl); shotUrl = null; }
  if ($("shot-file")) $("shot-file").value = "";
  if ($("shot-thumb")) $("shot-thumb").removeAttribute("src");
  if ($("shot-preview")) $("shot-preview").hidden = true;
  if ($("shot-drop-txt")) $("shot-drop-txt").hidden = false;
  const chooseTxt = document.querySelector(".shot-choose-txt");
  if (chooseTxt) chooseTxt.textContent = "Choose file";
}
function onShotInputEvent() {
  const inp = $("shot-file");
  const f = inp && inp.files && inp.files[0];
  if (f) setShotFile(f);
  // Empty FileList (cancelled picker or a second open) must not wipe a preview already shown.
}
function syncShotFromInput() {
  const inp = $("shot-file");
  const f = inp && inp.files && inp.files[0];
  if (f) setShotFile(f);
}
function applyShot(j) {
  clearUnsure();
  const low = new Set(j.low_confidence || []);
  const missing = new Set(j.missing || []);
  if (j.pattern) $("shot-pattern").value = j.pattern;
  syncPicks();
  setupPattern = ($("shot-pattern").value || "").trim();
  if (j.market && j.market.market_id) {
    const hit = markets.find(m => m.market_id === j.market.market_id);
    if (hit) pick(j.market.market_id);
    else { chosen = null; $("market").value = j.market.symbol || j.symbol_raw || ""; $("mkt-id").textContent = j.market.market_id; }
    markUnsure("market", low.has("market"));
  } else if (j.symbol_raw) {
    chosen = null; $("market").value = j.symbol_raw; $("mkt-id").textContent = "";
    markUnsure("market", true);
  } else {
    chosen = null; $("market").value = ""; $("mkt-id").textContent = "";
    if (missing.has("market")) markUnsure("market", true);
  }
  if (j.side === "long" || j.side === "short") {
    const b = $("side").querySelector("[data-side=" + j.side + "]");
    if (b) b.click();
  }
  $("entry").value = ""; $("tp").value = ""; $("sl").value = "";
  if (j.entry != null) $("entry").value = j.entry;
  if (j.tp != null) $("tp").value = j.tp;
  if (j.sl != null) $("sl").value = j.sl;
  markUnsure("entry", low.has("entry") || missing.has("entry"));
  markUnsure("tp", low.has("take profit") || missing.has("take profit"));
  markUnsure("sl", low.has("stop loss") || missing.has("stop loss"));
  if (j.similar_tf && $("similar-tf")) {
    $("similar-tf").value = j.similar_tf;
    markUnsure("similar-tf", low.has("timeframe"));
  }
  updateRR();
  const box = $("shot-review");
  const bits = [j.review || "Review these fields before you run the test. CHRONOS does not start until you press Run test."];
  if (j.notes) bits.push(j.notes);
  box.textContent = bits.join(" ");
  box.hidden = false;
  box.className = "shot-review" + ((j.missing || []).length || (j.low_confidence || []).length ? "" : " ok");
}
async function readShot() {
  showShotErr("");
  if (!shotFile) return showShotErr("Choose a chart screenshot first.");
  const btn = $("shot-run");
  btn.disabled = true;
  btn.querySelector(".spin").hidden = false;
  btn.querySelector(".txt").textContent = "Reading chart...";
  try {
    const fd = new FormData();
    fd.append("image", shotFile, shotFile.name || "chart.png");
    const pat = ($("shot-pattern").value || "").trim();
    if (pat) fd.append("pattern", pat);
    const r = await fetch("/api/screenshot", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    applyShot(j);
  } catch (err) { showShotErr(err.message); toast(err.message); }
  finally {
    btn.disabled = false;
    btn.querySelector(".spin").hidden = true;
    btn.querySelector(".txt").textContent = "Read screenshot";
  }
}
function applyShotEnabled(j) {
  if (!j || typeof j.enabled !== "boolean") return;
  if ($("shot-on")) $("shot-on").hidden = !j.enabled;
  if ($("shot-off")) $("shot-off").hidden = !!j.enabled;
}
let shotStatusAt = 0;
function refreshShotStatus(force) {
  const now = Date.now();
  if (!force && now - shotStatusAt < 3000) return;
  shotStatusAt = now;
  fetch("/api/screenshot").then(r => r.json()).then(applyShotEnabled).catch(() => {});
}
if ($("shot-panel")) {
  refreshShotStatus(true);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refreshShotStatus(); syncShotFromInput(); } });
  window.addEventListener("focus", () => { refreshShotStatus(); syncShotFromInput(); });
  $("shot-panel").addEventListener("focusin", () => refreshShotStatus());
  $("shot-picks").addEventListener("click", e => {
    const b = e.target.closest("button[data-pattern]"); if (!b) return;
    const cur = ($("shot-pattern").value || "").trim();
    $("shot-pattern").value = cur === b.dataset.pattern ? "" : b.dataset.pattern;
    syncPicks();
  });
  $("shot-pattern").addEventListener("input", () => { syncPicks(); setupPattern = $("shot-pattern").value.trim(); });
  $("shot-drop").addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("shot-file").focus(); $("shot-file").click(); }
  });
  ["change", "input"].forEach(ev => $("shot-file").addEventListener(ev, onShotInputEvent));
  $("shot-clear").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); clearShotFile(); });
  ["dragenter", "dragover"].forEach(ev => $("shot-drop").addEventListener(ev, e => { e.preventDefault(); $("shot-drop").classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => $("shot-drop").addEventListener(ev, e => { e.preventDefault(); $("shot-drop").classList.remove("drag"); }));
  $("shot-drop").addEventListener("drop", e => { const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (f) setShotFile(f); });
  $("shot-run").addEventListener("click", () => readShot());
  ["market", "entry", "tp", "sl", "similar-tf"].forEach(id => $(id) && $(id).addEventListener("input", () => markUnsure(id, false)));
}

// ---------- run
$("f").addEventListener("submit", e => { e.preventDefault(); run(); });
let hadResult = false;
function setBusy(b) {
  $("run").disabled = b; $("run").querySelector(".spin").hidden = !b;
  $("run").querySelector(".txt").textContent = b ? "Running backtest..." : "Run test";
  $("skel").hidden = !b;
  if (b) { $("empty").hidden = true; $("res").hidden = true; }
  else if (!hadResult) $("empty").hidden = false; else $("res").hidden = false;
  if (b && window.matchMedia("(max-width: 980px)").matches) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}
function toast(msg, title) {
  const t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "alert");
  const head = title === "" ? "" : `<b>${esc(title == null ? "Test failed." : title)}</b> `;
  t.innerHTML = `<span>${head}${esc(msg)}</span><button type="button" aria-label="Dismiss">×</button>`;
  t.querySelector("button").onclick = () => t.remove();
  $("toasts").appendChild(t); setTimeout(() => t.remove(), 9000);
}
function showErr(msg) { $("form-err").hidden = !msg; $("form-err").textContent = msg || ""; }
async function run() {
  showErr("");
  const mk = chosen ? chosen.market_id : $("market").value.trim();
  if (!mk) return showErr("Pick a market.");
  const le = levelError();
  if (le === null) return showErr("Enter entry, TP and SL.");
  if (le) return showErr(le);
  const body = { market: mk, side, entry: +$("entry").value, tp: +$("tp").value, sl: +$("sl").value,
    days: +$("days").value, expiry: $("expiry").value, trend_filter: $("trend").checked, mode,
    insights: mode === "similar" ? false : $("insights").checked };
  const pat = (($("shot-pattern") && $("shot-pattern").value) || setupPattern || "").trim();
  if (pat) body.pattern = pat;
  if (mode === "pattern") { body.zone_tf = $("zone-tf").value; body.entry_style = $("entry-style").value; if ($("sl-buf").value !== "") body.sl_buffer_atr = +$("sl-buf").value; }
  if (mode === "similar") {
    body.similar_tf = $("similar-tf").value;
    body.similar_bars = +$("similar-bars").value || 24;
  }
  lastRequest = body;
  setBusy(true);
  try {
    const r = await fetch("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    render(j);
  } catch (err) { showErr(err.message); toast(err.message); }
  finally { setBusy(false); }
}

// ---------- render
function badgeFor(v) {
  v = (v || "").toLowerCase();
  if (v.startsWith("too few")) return ["few", "Too few samples"];
  if (v.startsWith("no edge")) return ["noedge", "No edge"];
  if (v.startsWith("possible edge")) return ["maybe", "Possible edge, not proven"];
  if (v.startsWith("edge")) return ["edge", "Edge on this sample"];
  return ["few", v];
}
function card(k, v, d, cls) { return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`; }
function render(o) {
  lastResult = o;
  const g = o.setup, s = o.result, m = o.market, p = o.params;
  hadResult = true; $("empty").hidden = true; $("skel").hidden = true; $("res").hidden = false;
  const patLabel = (o.pattern || ($("shot-pattern") && $("shot-pattern").value) || setupPattern || "").trim();
  $("r-title").textContent = (patLabel ? patLabel + "  ·  " : "") + `${g.side.toUpperCase()} ${m.market_id}  ·  entry ${fmt(g.entry)}  TP ${fmt(g.tp)}  SL ${fmt(g.sl)}  ·  1:${g.rr.toFixed(2)}`;
  const pat = p.mode === "pattern", sim = p.mode === "similar";
  const windowTxt = `${o.data.days} days tested on ${o.data.interval} candles, ${wat(o.data.from_ms)} to ${wat(o.data.to_ms)} WAT${o.data.days_requested && o.data.days < o.data.days_requested ? ` (asked for ${o.data.days_requested}; that is all the history that exists)` : ""}. `;
  let how;
  if (sim) {
    const qt = o.query && o.query.t ? wat(o.query.t) : "n/a";
    how = `SIMILAR mode. ${windowTxt}Fingerprinted ${p.similar_bars || 24} x ${p.similar_tf || "1h"} bars ending ${qt} WAT (setup moment). ` +
      `Replayed the ATR-scaled entry, stop and single target on the closest past windows. Expiry ${p.expiry}, time stop ${p.max_hold}`;
  } else if (pat) {
    how = `PATTERN mode. ${windowTxt}Price at test ${fmt(g.ref)}. ` + (p.entry_style && p.entry_style !== "limit"
      ? `Entry style ${p.entry_style}: ${p.placement}. Stop ${p.sl_buffer_atr.toFixed(2)} ATR beyond the move's extreme, target ${g.rr.toFixed(2)}R. Time stop ${p.max_hold}`
      : `Replayed as a limit at the near edge of each fresh ${g.side === "short" ? "supply" : "demand"} area on ${p.zone_tf} candles, ` +
      `stop ${p.sl_buffer_atr.toFixed(2)} ATR beyond the far edge (${p.sl_buffer_source.how}), target ${g.rr.toFixed(2)}R. Time stop ${p.max_hold}`);
  } else {
    how = `${windowTxt}Price at test ${fmt(g.ref)}, 4h ATR ${fmt(g.atr, 4)}. Replayed as entry ${g.entry_atr >= 0 ? "+" : ""}${g.entry_atr.toFixed(2)} ATR (${g.entry_type}), ` +
      `stop ${g.risk_atr.toFixed(2)} ATR, target ${g.reward_atr.toFixed(2)} ATR. Expiry ${p.expiry}, time stop ${p.max_hold}`;
  }
  $("r-sub").textContent = how + (p.trend_filter ? ", trend filter on." : ".");
  const [cls, label] = badgeFor(s.verdict);
  $("badge").className = "badge " + cls; $("badge").textContent = label;
  $("verdict-card").className = "verdict-card " + cls;
  $("verdict").textContent = "Engine verdict: " + s.verdict + ".";
  const warns = [];
  if (!pat && !sim && Math.abs(g.entry_atr) > 3) warns.push(`Entry is ${Math.abs(g.entry_atr).toFixed(1)} ATR from the current price, so most replays will not fill.`);
  if (s.shallow_sweep_pct_of_losses && s.sl >= 5 && s.shallow_sweep_pct_of_losses >= 0.2) warns.push("Stop looks too tight: a good share of losers wicked through the stop before the move reached TP.");
  if (p.trend_filter) warns.push(`Trend filter skipped ${o.skipped_by_trend} eligible 4h closes. 4h trend now: ${o.trend_now || "n/a"}.`);
  $("warn").hidden = !warns.length; $("warn").innerHTML = warns.join("<br>");
  const wrCls = s.win_rate == null ? "" : (s.win_rate > s.breakeven_win_rate ? "pos" : "neg");
  const zcard = pat && o.zones ? [card("Patterns found", o.zones.zones_found,
    `${p.zone_tf} ${g.side === "short" ? "supply" : "demand"} areas · touched ${o.zones.zones_touched} · broken ${o.zones.zones_invalidated} · traded ${o.zones.zones_traded}`)] : [];
  const scard = sim && o.similar_matches ? [card("Similar windows", o.similar_matches.filter(x => !x.skipped).length,
    `up to ${p.similar_k || 30} non-overlapping matches on ${p.similar_tf || "1h"}`)] : [];
  $("cards").innerHTML = [...zcard, ...scard,
    card("Orders", s.placed, `filled ${s.fills} · unfilled ${s.unfilled}${s.open_at_end ? ` · open ${s.open_at_end}` : ""}`),
    card("Closed trades", s.closed, `TP ${s.tp} · SL ${s.sl}${s.time_exits ? ` · time ${s.time_exits}` : ""}`),
    card("Win rate", pct(s.win_rate), `break-even ${pct(s.breakeven_win_rate)} after fees (${pct(s.breakeven_win_rate_gross)} before)`, wrCls),
    card("Avg R per trade", sgnR(s.avg_r_net), `gross ${sgnR(s.avg_r_gross)} · per order ${sgnR(s.expectancy_r_per_order)}`, s.avg_r_net > 0 ? "pos" : s.avg_r_net < 0 ? "neg" : ""),
    card("Total R", sgnR(s.total_r_net), `t-stat ${s.t_stat}`, s.total_r_net > 0 ? "pos" : s.total_r_net < 0 ? "neg" : ""),
    card("Max losing streak", s.max_losing_streak, `halves ${s.first_half_avg_r ?? "n/a"} / ${s.second_half_avg_r ?? "n/a"} R avg`),
    card("Max drawdown", (s.max_drawdown_r || 0).toFixed(2) + "R", "peak to trough, in R", s.max_drawdown_r < 0 ? "neg" : ""),
    card("Stop-sweep check", `${s.sl_then_tp} / ${s.sl}`, `stop-outs that still reached TP within ${p.sweep_window}; ${s.shallow_sweeps} only poked ${p.sweep_atr} ATR or less past SL`),
  ].join("");
  $("meta").textContent = `Fees: ${o.fees.commission_pct_per_fill}% per fill + ${o.fees.swap_pct_per_day}%/day swap (${o.fees.asset_class}, MFP standard). ` +
    `Tie rule: ${p.tie_rule}. Placement: ${p.placement}. Generated ${o.generated.replace("T", " ").replace("Z", " UTC")}` + (o.elapsed_s != null ? `, ran in ${o.elapsed_s}s.` : ".");
  renderProvenance(o.data);
  renderInsights(o.insights);
  renderSimilarMatches(o);
  renderTrades(o.trades || [], p.mode);
  drawChart(o.trades || []);
  setupCompare(o);
  setupScan(o);
}
function renderProvenance(d) {
  const el = $("provenance");
  if (!el) return;
  const pv = d && d.provenance;
  if (!pv || !pv.segments) { el.hidden = true; return; }
  const day = ms => new Date(ms).toISOString().slice(0, 10);
  const segs = pv.segments.map(s => `${s.label} (${s.source.split(":").slice(1).join(":")}) ${day(s.from_ms)} to ${day(s.to_ms)}`).join(" + ");
  const chk = (pv.overlap_checks || []).map(c => c.median_abs_pct_diff == null ? `${c.source}: no overlap to check`
    : `${c.source} vs newer data: ${c.median_abs_pct_diff.toFixed(3)}% median close diff` +
      (c.status === "basis-adjusted" ? `, adjusted x${c.ratio}` : c.status.startsWith("rejected") ? ", rejected (not used)" : "")).join("; ");
  const g = pv.gaps || {};
  el.textContent = `Data: ${segs}. ${d.days} days tested, ${pv.days_covered} days loaded incl. warm-up (UTC dates). ` +
    `Gaps: ${g.gaps ?? 0}${g.missing_bars ? ` (${g.missing_bars} missing bars)` : ""}${g.weekend_closures ? `, ${g.weekend_closures} weekend closures` : ""}. ` +
    `Overlap: ${chk || (pv.backfill_needed ? "no backup used" : "not needed, MFP covered the whole window")}.`;
  el.hidden = false;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function renderInsights(ins) {
  $("ins").hidden = !ins;
  if (!ins) return;
  $("ins-list").innerHTML = ins.findings.map(f => `<li class="${f.level === "warn" ? "w" : "i"}">${esc(f.text)}</li>`).join("");
  $("ins-note").textContent = ins.comparison_note;
  $("ins-tbl").querySelector("tbody").innerHTML = ins.comparison.map(r => {
    const ev = r.eligible ? `<span class="tag tp">15+ trades, above BE</span>`
      : r.closed < ins.min_closed ? `<span class="tag unf">too few (&lt;${ins.min_closed})</span>` : `<span class="tag sl">below BE / no edge</span>`;
    return `<tr><td>${esc(r.variant)}</td><td>${esc(r.scope)}</td><td class="r">${r.sl_buffer_atr.toFixed(2)} ATR</td><td class="r">${r.closed}</td>` +
      `<td class="r">${r.tp}/${r.sl}/${r.time_exits}</td><td class="r">${pct(r.win_rate)}</td><td class="r">${pct(r.breakeven_win_rate)}</td>` +
      `<td class="r ${r.avg_r_net > 0 ? "pos" : r.avg_r_net < 0 ? "neg" : ""}">${sgnR(r.avg_r_net)}</td><td class="r">${sgnR(r.total_r_net)}</td>` +
      `<td class="r">${r.t_stat == null ? "n/a" : r.t_stat.toFixed(1)}</td><td>${ev}</td></tr>`;
  }).join("");
  $("ins-best").textContent = ins.best.text;
  const fx = ins.fixes.length ? ins.fixes : [{ fix: "No specific fix: nothing in this result tripped a rule.", evidence: [] }];
  $("ins-fixes").innerHTML = fx.map(f => `<li>${esc(f.fix)}${f.evidence.length ? `<ul>${f.evidence.map(e => `<li class="ev">Tested: ${esc(e)}</li>`).join("")}</ul>` : ""}</li>`).join("");
  $("ins-disc").textContent = ins.disclaimer;
}
function renderTrades(tr, md) {
  const mid = $("trades-head");
  if (mid) {
    const col = md === "pattern" ? "Near / far edge" : md === "similar" ? "Score" : "Context";
    mid.innerHTML = `<th>#</th><th>Placed (WAT)</th><th>${col}</th><th>Filled (WAT)</th><th>Entry</th><th>Exit (WAT)</th><th>Exit price</th><th>Result</th><th class="r">R</th>`;
  }
  const rows = tr.map((t, i) => {
    const st = t.status, tag = st === "tp" ? "tp" : st === "sl" ? "sl" : st === "time" ? "time" : "unf";
    const lbl = { tp: "TP", sl: "SL", time: "Time exit", unfilled: "Unfilled", open_at_end: "Open", pending_at_end: "Pending" }[st] || st;
    const sweep = st === "sl" && t.sl_then_tp ? ` <span class="muted" title="Price still reached TP after the stop-out">swept</span>` : "";
    const r = t.r_net == null ? "" : `<span class="${t.r_net > 0 ? "pos" : "neg"}">${sgnR(t.r_net)}</span>`;
    let ctx = "-";
    if (md === "similar" && t.similar_score != null) ctx = Number(t.similar_score).toFixed(1);
    else if (t.zone_proximal != null) ctx = `${px(t.zone_proximal)} / ${px(t.zone_distal)}`;
    return `<tr><td>${i + 1}</td><td>${wat(t.placed)}</td><td>${ctx}</td><td>${t.filled ? wat(t.filled) : "-"}</td><td>${px(t.fill_px ?? t.entry)}</td>
      <td>${t.exit ? wat(t.exit) : "-"}</td><td>${px(t.exit_px)}</td><td><span class="tag ${tag}">${lbl}</span>${sweep}</td><td class="r">${r}</td></tr>`;
  });
  $("trades").querySelector("tbody").innerHTML = rows.join("") || `<tr><td colspan="9" class="muted">No orders were placed in the window.</td></tr>`;
  $("t-count").textContent = `${tr.length} orders, times in WAT (UTC+1)`;
}
function drawChart(tr) {
  const cv = $("chart"), dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = 260; cv.width = W * dpr; cv.height = H * dpr;
  const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  const closed = tr.filter(t => ["tp", "sl", "time"].includes(t.status));
  const pts = [{ x: 0, y: 0 }]; let eq = 0;
  closed.forEach((t, i) => { eq += t.r_net; pts.push({ x: i + 1, y: eq, t }); });
  const L = 46, R = 14, T = 12, B = 28, w = W - L - R, h = H - T - B;
  c.font = "11px ui-monospace,Menlo,monospace"; c.fillStyle = "#8fa596";
  if (closed.length === 0) { c.fillText("No closed trades to chart.", L, H / 2); return; }
  let lo = Math.min(0, ...pts.map(p => p.y)), hi = Math.max(0, ...pts.map(p => p.y));
  if (hi - lo < 1) { hi += .5; lo -= .5; }
  const padY = (hi - lo) * .08; lo -= padY; hi += padY;
  const X = x => L + (x / Math.max(1, pts.length - 1)) * w, Y = y => T + (hi - y) / (hi - lo) * h;
  const stepR = niceStep((hi - lo) / 5);
  c.strokeStyle = "#0d2414"; c.lineWidth = 1;
  for (let v = Math.ceil(lo / stepR) * stepR; v <= hi; v += stepR) {
    c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(W - R, Y(v)); c.stroke();
    c.fillText((v > 0 ? "+" : "") + v.toFixed(stepR < 1 ? 1 : 0) + "R", 4, Y(v) + 4);
  }
  c.strokeStyle = "#2a5532"; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(L, Y(0)); c.lineTo(W - R, Y(0)); c.stroke(); c.setLineDash([]);
  const col = "#4dff1a";
  const top = Math.min(...pts.map(p => Y(p.y))), grd = c.createLinearGradient(0, top, 0, Y(0));
  grd.addColorStop(0, "rgba(77,255,26,.28)"); grd.addColorStop(1, "rgba(77,255,26,0)");
  const grdN = c.createLinearGradient(0, Y(0), 0, T + h); grdN.addColorStop(0, "rgba(77,255,26,0)"); grdN.addColorStop(1, "rgba(77,255,26,.14)");
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.y)) : c.moveTo(X(p.x), Y(p.y)));
  c.lineTo(X(pts.length - 1), Y(0)); c.lineTo(X(0), Y(0)); c.closePath();
  c.save(); c.clip(); c.fillStyle = grd; c.fillRect(L, T, w, Y(0) - T); c.fillStyle = grdN; c.fillRect(L, Y(0), w, T + h - Y(0)); c.restore();
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.y)) : c.moveTo(X(p.x), Y(p.y)));
  c.strokeStyle = col; c.lineWidth = 2.2; c.lineJoin = "round"; c.shadowColor = "rgba(77,255,26,.7)"; c.shadowBlur = 10; c.stroke(); c.shadowBlur = 0;
  pts.slice(1).forEach(p => { c.beginPath(); c.arc(X(p.x), Y(p.y), 3, 0, 7);
    c.fillStyle = p.t.status === "tp" ? "#3ccf91" : p.t.status === "sl" ? "#f2687a" : "#39e61a"; c.fill();
    c.strokeStyle = "#010a03"; c.lineWidth = 1.2; c.stroke(); });
  c.fillStyle = "#8fa596";
  c.fillText("trade 1", L, H - 8);
  const endLbl = `trade ${closed.length} · ${wat(closed[closed.length - 1].exit)} WAT`;
  c.fillText(endLbl, W - R - c.measureText(endLbl).width, H - 8);
}
function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p; return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p; }
window.addEventListener("resize", () => { if (lastResult) drawChart(lastResult.trades || []); });

// ---------- similar matches + live scan
function renderSimilarMatches(o) {
  const panel = $("sim-panel");
  if (!panel) return;
  const list = (o.similar_matches || []).filter(m => !m.skipped);
  if ((o.params || {}).mode !== "similar") { panel.hidden = true; return; }
  panel.hidden = false;
  list.sort((a, b) => (a.distance - b.distance) || (a.t - b.t));
  $("sim-count").textContent = `${list.length} matches, times in WAT (UTC+1)`;
  const head = `<div class="sim-tr sim-hd" role="row"><span>When (WAT)</span><span>Score</span><span>Dist</span><span>Result</span><span class="r">R</span></div>`;
  const rows = list.slice(0, 30).map(m => {
    const st = m.status || "-";
    const tag = st === "tp" ? "tp" : st === "sl" ? "sl" : st === "time" ? "time" : "unf";
    return `<div class="sim-tr" role="row"><span>${wat(m.t)}</span><span>${Number(m.score).toFixed(1)}</span>` +
      `<span>${Number(m.distance).toFixed(3)}</span><span><span class="tag ${tag}">${esc(st)}</span></span>` +
      `<span class="r ${m.r_net > 0 ? "pos" : m.r_net < 0 ? "neg" : ""}">${m.r_net == null ? "-" : sgnR(m.r_net)}</span></div>`;
  }).join("") || `<p class="muted">No similar past windows were found in the test range.</p>`;
  $("sim-table").innerHTML = list.length ? head + rows : rows;
  $("sim-cards").innerHTML = list.slice(0, 20).map(m =>
    `<div class="sim-card"><div class="sim-when">${wat(m.t)} WAT</div>` +
    `<div class="sim-sc">score ${Number(m.score).toFixed(1)}</div>` +
    `<div class="muted">${esc(m.status || "-")} · ${m.r_net == null ? "-" : sgnR(m.r_net)}</div></div>`
  ).join("");
}

function qualifies(o) {
  const v = ((o && o.result && o.result.verdict) || "").toLowerCase();
  return v.startsWith("possible edge") || v.startsWith("edge ");
}
function gateLabel(o) {
  const v = ((o && o.result && o.result.verdict) || "").toLowerCase();
  if (v.startsWith("too few")) return "needs a passing edge first (too few samples)";
  if (v.startsWith("no edge")) return "needs a passing edge first (no edge)";
  if (!v) return "needs a passing edge first";
  return "needs a passing edge first";
}
function setupScan(o, keepOut) {
  const panel = $("scan-panel");
  if (!panel) return;
  panel.hidden = false;
  if (!keepOut) {
    $("scan-out").hidden = true;
    $("scan-err").hidden = true;
    $("scan-skel").hidden = true;
    $("scan-banner").hidden = true;
  }
  const ok = qualifies(o);
  const btn = $("scan-run");
  const canForce = forceScan && !ok;
  btn.disabled = !(ok || canForce);
  $("scan-gate").hidden = ok;
  $("scan-gate").textContent = ok ? "" : gateLabel(o);
  $("scan-lead").textContent = ok
    ? "This backtest passed. Scan live markets whose current price action looks like this setup. One take-profit and a stop at the same ATR geometry."
    : "Live scan stays off until a backtest shows a passing edge (possible edge or edge on this sample)." +
      (canForce ? " URL force_scan=1 is on: you can run a labelled forced scan for review." : "");
}

let scanBusy = false;
$("scan-run").addEventListener("click", async () => {
  if (scanBusy || !lastRequest || !lastResult) return;
  const ok = qualifies(lastResult);
  if (!ok && !forceScan) return;
  scanBusy = true;
  $("scan-run").disabled = true;
  $("scan-run").querySelector(".spin").hidden = false;
  $("scan-run").querySelector(".txt").textContent = "Scanning...";
  $("scan-skel").hidden = false;
  $("scan-err").hidden = true;
  $("scan-out").hidden = true;
  const body = Object.assign({}, lastRequest, {
    result: lastResult.result, query: lastResult.query || null, force: !ok && forceScan, scope: "family", insights: false
  });
  try {
    const r = await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    if (j.gated) {
      $("scan-gate").hidden = false;
      $("scan-gate").textContent = j.reason || "needs a passing edge first";
    } else {
      renderScan(j, !ok && forceScan);
    }
  } catch (err) {
    $("scan-err").hidden = false;
    $("scan-err").textContent = err.message;
  } finally {
    scanBusy = false;
    $("scan-skel").hidden = true;
    $("scan-run").querySelector(".spin").hidden = true;
    $("scan-run").querySelector(".txt").textContent = "Scan live markets";
    setupScan(lastResult, true);
  }
});

function renderScan(j, forced) {
  $("scan-out").hidden = false;
  $("scan-banner").hidden = !forced && !j.forced;
  const rows = j.rows || [];
  $("scan-meta").textContent = `${j.generated || ""} · looked at ${j.considered ?? "?"} markets, fingerprinted ${j.scanned ?? rows.length}. Showing the ${rows.length} closest.`;
  const head = `<div class="scan-tr scan-hd" role="row"><span>Side</span><span>Pair</span><span>Limit</span><span>TP</span><span>SL</span><span>Score</span><span>From price</span></div>`;
  $("scan-table").innerHTML = rows.length ? head + rows.map(r =>
    `<div class="scan-tr" role="row"><span class="side-${r.side}">${esc((r.side || "").toUpperCase())}</span>` +
    `<span>${esc(r.market_id)}</span><span>${px(r.entry)}</span><span>${px(r.tp)}</span><span>${px(r.sl)}</span>` +
    `<span>${Number(r.score).toFixed(1)}</span>` +
    `<span>${r.dist_pct ?? "-"}% / ${r.dist_atr ?? "-"} ATR${r.through_entry ? " · through entry" : ""}</span></div>`
  ).join("") : `<p class="muted">No live matches.</p>`;
  $("scan-cards").innerHTML = rows.map(r =>
    `<div class="scan-card"><div class="scan-side side-${r.side}">${esc((r.side || "").toUpperCase())} ${esc(r.symbol || r.market_id)}</div>` +
    `<div>Limit ${px(r.entry)}</div><div>TP ${px(r.tp)}</div><div>SL ${px(r.sl)}</div>` +
    `<div class="scan-sc">score ${Number(r.score).toFixed(1)}</div>` +
    `<div class="muted">${r.dist_pct ?? "-"}% from price${r.through_entry ? " · already through entry" : ""}</div></div>`
  ).join("");
}

// ---------- compare on other pairs
const CMP_LIMIT = 5;
let cmpSelected = new Set(), cmpSuggested = new Set(), cmpBusy = false;

function setupCompare(o) {
  const panel = $("cmp");
  if (!panel) return;
  // only after a single-market result (a basket run already pooled several markets)
  if (!o || o.per_market || o.compare_rows || !lastRequest) { panel.hidden = true; return; }
  panel.hidden = false;
  $("cmp-out").hidden = true; $("cmp-skel").hidden = true; $("cmp-err").hidden = true;
  $("cmp-q").value = "";
  const srcId = o.market.market_id;
  const sameFam = markets.filter(m => m.market_id !== srcId && m.family && o.market && markets.find(x => x.market_id === srcId)?.family === m.family);
  fetch("/api/compare-suggest?market=" + encodeURIComponent(srcId)).then(r => r.json()).then(j => {
    const ids = (j.suggestions || []).map(s => s.market_id);
    cmpSuggested = new Set(ids);
    cmpSelected = new Set(ids.slice(0, CMP_LIMIT));
    renderCmpList();
  }).catch(() => {
    cmpSuggested = new Set(sameFam.slice(0, CMP_LIMIT).map(m => m.market_id));
    cmpSelected = new Set(cmpSuggested);
    renderCmpList();
  });
  renderCmpList();
}
function cmpMatches(m, q) {
  if (!q) return true;
  return (m.symbol + " " + m.coin + " " + m.market_id + " " + (m.family_label || m.family || "")).toUpperCase().includes(q);
}
function renderCmpList() {
  const srcId = lastResult && lastResult.market && lastResult.market.market_id;
  const q = ($("cmp-q").value || "").trim().toUpperCase();
  const others = markets.filter(m => m.market_id !== srcId);
  others.sort((a, b) => {
    const as = cmpSuggested.has(a.market_id), bs = cmpSuggested.has(b.market_id);
    if (as !== bs) return bs - as;
    const af = a.family === (markets.find(x => x.market_id === srcId) || {}).family;
    const bf = b.family === (markets.find(x => x.market_id === srcId) || {}).family;
    if (af !== bf) return bf - af;
    return (a.symbol || "").localeCompare(b.symbol || "");
  });
  const shown = others.filter(m => cmpMatches(m, q) || cmpSelected.has(m.market_id)).slice(0, 80);
  const full = cmpSelected.size >= CMP_LIMIT;
  $("cmp-list").innerHTML = shown.map(m => {
    const on = cmpSelected.has(m.market_id);
    const dis = !on && full;
    const sug = cmpSuggested.has(m.market_id) ? `<span class="sug">suggested</span>` : "";
    return `<label class="cmp-item ${on ? "is-on" : ""} ${dis ? "is-off" : ""}">
      <input type="checkbox" data-id="${m.market_id}" ${on ? "checked" : ""} ${dis ? "disabled" : ""}>
      <span class="box" aria-hidden="true"></span>
      <span><span class="nm">${esc(m.symbol)}</span>${sug}
        <span class="meta">${esc(m.family_label || m.asset_class || "")} · ${esc(m.market_id)}</span></span>
    </label>`;
  }).join("") || `<div class="muted" style="padding:10px">No markets match that search.</div>`;
  $("cmp-count").textContent = `${cmpSelected.size} / ${CMP_LIMIT} selected`;
  $("cmp-run").disabled = cmpBusy || cmpSelected.size === 0;
}
$("cmp-q").addEventListener("input", renderCmpList);
$("cmp-list").addEventListener("change", e => {
  const inp = e.target.closest("input[data-id]");
  if (!inp) return;
  if (inp.checked) {
    if (cmpSelected.size >= CMP_LIMIT) { inp.checked = false; return; }
    cmpSelected.add(inp.dataset.id);
  } else cmpSelected.delete(inp.dataset.id);
  renderCmpList();
});
function shortVerdict(v) {
  v = v || "";
  if (v.startsWith("too few")) return "too few samples";
  if (v.startsWith("no edge")) return "no edge";
  if (v.startsWith("possible edge")) return "possible edge";
  if (v.startsWith("edge")) return "edge on this sample";
  return v;
}
function cmpWin(r) {
  if (r.win_rate == null) return "n/a";
  return `${pct(r.win_rate)} vs ${pct(r.breakeven_win_rate)}`;
}
function renderCmpResults(o) {
  $("cmp-out").hidden = false;
  $("cmp-summary").textContent = o.compare_summary || "";
  const rows = (o.compare_rows || []).concat(o.compare_pooled ? [o.compare_pooled] : []);
  const head = `<div class="cmp-thead" role="row">
    <span>Pair</span><span class="cmp-r">Closed</span><span class="cmp-r">Win vs BE</span>
    <span class="cmp-r">Avg R</span><span class="cmp-r">Total R</span><span class="cmp-r">TP/SL/time</span>
    <span>Verdict</span><span>Data</span></div>`;
  $("cmp-table").innerHTML = head + rows.map(r => {
    const name = r.role === "pooled" ? "Pooled" : (r.symbol || r.market_id);
    const cls = r.role === "pooled" ? "pooled" : (r.role === "source" ? "source" : "");
    return `<div class="cmp-row ${cls}" role="row">
      <div class="cmp-pair"><span class="pair-name">${esc(name)}</span>${r.role !== "pooled" ? `<span class="pair-id">${esc(r.market_id)}</span>` : ""}</div>
      <div class="cmp-r">${r.closed}</div>
      <div class="cmp-r">${cmpWin(r)}</div>
      <div class="cmp-r ${r.avg_r_net > 0 ? "pos" : r.avg_r_net < 0 ? "neg" : ""}">${sgnR(r.avg_r_net)}</div>
      <div class="cmp-r ${r.total_r_net > 0 ? "pos" : r.total_r_net < 0 ? "neg" : ""}">${sgnR(r.total_r_net)}</div>
      <div class="cmp-r">${r.tp}/${r.sl}/${r.time_exits}</div>
      <div class="cmp-verdict">${esc(shortVerdict(r.verdict))}</div>
      <div class="cmp-data">${esc(r.data_short || "")}</div>
    </div>`;
  }).join("");
  $("cmp-cards").innerHTML = rows.map(r => {
    const name = r.role === "pooled" ? "Pooled" : (r.symbol || r.market_id);
    const role = r.role === "source" ? "source" : r.role === "pooled" ? "pooled" : "";
    return `<div class="cmp-card ${r.role === "pooled" ? "pooled" : ""}">
      <h4>${esc(name)}${role ? `<span class="role">${role}</span>` : ""}</h4>
      ${r.role !== "pooled" ? `<div class="dat">${esc(r.market_id)}</div>` : ""}
      <div class="grid">
        <div><div class="k">Closed</div><div class="v">${r.closed}</div></div>
        <div><div class="k">Win vs BE</div><div class="v">${cmpWin(r)}</div></div>
        <div><div class="k">Avg R</div><div class="v ${r.avg_r_net > 0 ? "pos" : r.avg_r_net < 0 ? "neg" : ""}">${sgnR(r.avg_r_net)}</div></div>
        <div><div class="k">Total R</div><div class="v ${r.total_r_net > 0 ? "pos" : r.total_r_net < 0 ? "neg" : ""}">${sgnR(r.total_r_net)}</div></div>
        <div><div class="k">TP / SL / time</div><div class="v">${r.tp} / ${r.sl} / ${r.time_exits}</div></div>
      </div>
      <div class="ver">${esc(shortVerdict(r.verdict))}</div>
      <div class="dat">${esc(r.data_short || "")}</div>
    </div>`;
  }).join("");
}
$("cmp-run").addEventListener("click", async () => {
  if (cmpBusy || !lastRequest || cmpSelected.size === 0) return;
  if (cmpSelected.size > CMP_LIMIT) return;
  const body = Object.assign({}, lastRequest, { compare: [...cmpSelected], insights: false });
  cmpBusy = true; $("cmp-run").disabled = true;
  $("cmp-run").querySelector(".spin").hidden = false;
  $("cmp-run").querySelector(".txt").textContent = "Running comparison...";
  $("cmp-err").hidden = true; $("cmp-out").hidden = true; $("cmp-skel").hidden = false;
  $("cmp-skel-txt").textContent = `Running the same setup on ${cmpSelected.size} other pair${cmpSelected.size === 1 ? "" : "s"}...`;
  try {
    const r = await fetch("/api/compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    renderCmpResults(j);
  } catch (err) {
    $("cmp-err").hidden = false; $("cmp-err").textContent = err.message;
    toast(err.message);
  } finally {
    cmpBusy = false; $("cmp-skel").hidden = true;
    $("cmp-run").querySelector(".spin").hidden = true;
    $("cmp-run").querySelector(".txt").textContent = "Run on selected pairs";
    renderCmpList();
  }
});

// ---------- prefill from URL (?market=BTC&side=short&entry=..&tp=..&sl=..&run=1)
function applyQuery() {
  const q = new URLSearchParams(location.search);
  if (!q.get("market")) return;
  const mq = q.get("market").toUpperCase();
  const m = markets.find(x => [x.symbol, x.coin, x.market_id].map(s => s.toUpperCase()).includes(mq)) ||
            markets.find(x => x.coin.toUpperCase() === mq + "USDT");
  if (m) pick(m.market_id); else $("market").value = q.get("market");
  if (q.get("side")) $("side").querySelector(`[data-side=${q.get("side") === "short" ? "short" : "long"}]`).click();
  ["entry", "tp", "sl"].forEach(k => q.get(k) && ($(k).value = q.get(k)));
  if (q.get("days")) { $("days").value = q.get("days"); $("days-v").textContent = $("days").value; }
  if (q.get("expiry")) $("expiry").value = q.get("expiry");
  if (q.get("trend") === "1") $("trend").checked = true;
  if (["similar", "every-close", "pattern"].includes(q.get("mode"))) {
    const btn = $("mode").querySelector(`[data-mode="${q.get("mode")}"]`);
    if (btn) btn.click();
  }
  if (q.get("zone_tf")) $("zone-tf").value = q.get("zone_tf");
  if (q.get("similar_tf")) $("similar-tf").value = q.get("similar_tf");
  if (q.get("similar_bars")) $("similar-bars").value = q.get("similar_bars");
  if (["limit", "sweep", "confirm"].includes(q.get("entry_style"))) $("entry-style").value = q.get("entry_style");
  if (q.get("insights") === "0") $("insights").checked = false;
  if (q.get("insights") === "1") $("insights").checked = true;
  updateRR();
  if (q.get("run") === "1") run();
}
})();

/* Small live UTC clock in the hero (display only). */
(() => {
  const el = document.getElementById("utc-clock");
  if (!el) return;
  const p = n => String(n).padStart(2, "0");
  const tick = () => { const d = new Date(); el.textContent = `UTC ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`; };
  tick(); setInterval(tick, 1000);
})();
