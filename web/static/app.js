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
  return loadPrice();
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
  if (!chosen) return Promise.resolve(null);
  const id = chosen.market_id;
  setPrice("loading price...", "loading");
  return fetch("/api/price?market=" + encodeURIComponent(id)).then(r => r.json()).then(j => {
    if (!chosen || chosen.market_id !== id) return null;
    if (j.error) { setPrice("price unavailable", "bad"); refPrice = null; return null; }
    refPrice = j.price;
    setPrice(fmt(j.price) + " · last 15m close", "");
    updateRR();
    return j.price;
  }).catch(() => { setPrice("price unavailable", "bad"); return null; });
}

// ---------- side + live R:R
$("side").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  side = b.dataset.side;
  if (e.isTrusted) $("side").classList.remove("unsure");
  [...$("side").children].forEach(x => x.classList.toggle("on", x === b));
  updateRR();
});
const MODE_HELP = {
  similar: "Finds past moments whose price action looked like your chart, then replays your trade there.",
  "every-close": "Places your trade shape at every close of the setup timeframe (4h by default, one at a time). Tests the distances, not the reason for the trade.",
  pattern: "Only trades retests of supply or demand zones found by fixed rules. Optional; not how most ICT or SMC setups are tested.",
};
const TF_DEFAULT = { similar: "1h", "every-close": "4h", pattern: "4h" };
let tfTouched = false;
function syncMode() {
  if ($("mode-help")) $("mode-help").textContent = MODE_HELP[mode] || "";
  if (!tfTouched && $("similar-tf")) $("similar-tf").value = TF_DEFAULT[mode] || "1h";
  if ($("overlap-row")) { $("overlap-row").hidden = mode === "pattern"; if (mode === "pattern") $("overlap").checked = false; }
  $("pattern-opts").hidden = mode !== "pattern";
  $("style-opts").hidden = mode !== "pattern";
  if ($("similar-opts")) $("similar-opts").hidden = mode !== "similar";
  if ($("insights-row")) $("insights-row").hidden = mode === "similar";
  $("expiry").disabled = mode === "pattern";
  $("exp-note").textContent = mode === "pattern" ? "· not used in pattern mode" : "";
}
if ($("similar-tf")) $("similar-tf").addEventListener("change", () => { tfTouched = true; });
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
const SHOT_FIELD_IDS = { symbol: "market", entry: "entry", take_profit: "tp", stop_loss: "sl", timeframe: "similar-tf" };
const LEVEL_TXT = { high: "High", medium: "Medium", low: "Low", "very low": "Very low", none: "-" };
const ACTION_TXT = { filled: "Filled in", check: "Filled in, check it", suggested: "Not filled in: too unclear", "not found": "Not found", unmatched: "No matching market" };
function applyShot(j) {
  clearUnsure();
  $("side").classList.remove("unsure");
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
  $("side").classList.toggle("unsure", low.has("side") || missing.has("side"));
  $("entry").value = ""; $("tp").value = ""; $("sl").value = "";
  if (j.entry != null) $("entry").value = j.entry;
  if (j.tp != null) $("tp").value = j.tp;
  if (j.sl != null) $("sl").value = j.sl;
  markUnsure("entry", low.has("entry") || missing.has("entry"));
  markUnsure("tp", low.has("take profit") || missing.has("take profit"));
  markUnsure("sl", low.has("stop loss") || missing.has("stop loss"));
  if (j.similar_tf && $("similar-tf")) {
    if (mode !== "similar") $("mode").querySelector('[data-mode="similar"]').click();
    $("similar-tf").value = j.similar_tf; tfTouched = true;
    markUnsure("similar-tf", low.has("timeframe"));
  }
  updateRR();
  const box = $("shot-review");
  const fields = j.fields || [];
  const sug = j.suggested || {};
  const rows = fields.map(f => {
    const val = f.value == null ? "-" : (typeof f.value === "number" ? px(f.value) : esc(f.value));
    const conf = f.confidence == null ? "" : ` <span class="muted">${Math.round(f.confidence * 100)}%</span>`;
    let act = esc(ACTION_TXT[f.action] || f.action);
    const sk = { take_profit: "tp", stop_loss: "sl" }[f.key] || f.key;
    if (f.action === "suggested" && ["entry", "take_profit", "stop_loss"].includes(f.key) && sug[sk] != null)
      act += ` <button type="button" class="use-sug" data-field="${SHOT_FIELD_IDS[f.key]}" data-val="${sug[sk]}">Use ${px(sug[sk])}</button>`;
    const cf = f.confidence == null ? "" : `<span class="cf">${LEVEL_TXT[f.level] || f.level}${conf}</span> · `;
    return `<tr class="lv-${(f.level || "none").replace(" ", "-")} ac-${f.action.replace(" ", "-")}"><td>${esc(f.label)}</td><td class="v">${val}</td><td>${cf}${act}</td></tr>`;
  }).join("");
  const needs = (j.missing || []).length || (j.low_confidence || []).length || Object.keys(sug).length;
  box.innerHTML = `<p>${esc(j.review || "Review these fields before you run the test.")}</p>` +
    (rows ? `<div class="tbl-wrap sr-wrap"><table class="sr"><thead><tr><th>Field</th><th>Read as</th><th>Confidence · what CHRONOS did</th></tr></thead><tbody>${rows}</tbody></table></div>` : "") +
    (j.notes ? `<p class="muted small">Note from the reader: ${esc(j.notes)}</p>` : "") +
    (needs ? `<div class="sr-actions"><button type="button" class="btn-ghost sm" id="shot-ok">These look right</button><span class="muted small">Highlighted fields must be edited or confirmed before the test runs.</span></div>` : "");
  box.hidden = false;
  box.className = "shot-review" + (needs ? "" : " ok");
}
function shotReviewClick(e) {
  const u = e.target.closest(".use-sug");
  if (u) { const el = $(u.dataset.field); if (el) { el.value = u.dataset.val; markUnsure(u.dataset.field, false); updateRR(); u.disabled = true; u.textContent = "Used"; } return; }
  if (e.target.id === "shot-ok") {
    ["market", "entry", "tp", "sl", "similar-tf"].forEach(id => markUnsure(id, false));
    $("side").classList.remove("unsure");
    e.target.disabled = true; e.target.textContent = "Confirmed";
    showErr("");
  }
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
  $("shot-review").addEventListener("click", shotReviewClick);
  ["market", "entry", "tp", "sl", "similar-tf"].forEach(id => $(id) && $(id).addEventListener("input", () => markUnsure(id, false)));
}

// ---------- run
$("f").addEventListener("submit", e => { e.preventDefault(); run(); });
let hadResult = false;
let busyTimer = null, busyT0 = 0;
const BUSY_STEPS = [[0, "Loading price history for this market..."], [4, "Finding past setups and replaying each trade candle by candle..."],
  [15, "Still working. The first run on a market downloads its history, which can take up to a minute."],
  [60, "Taking longer than usual. The price data source may be slow; CHRONOS keeps retrying for a while."]];
function tickBusy() {
  const s = Math.floor((Date.now() - busyT0) / 1000);
  let txt = BUSY_STEPS[0][1]; BUSY_STEPS.forEach(([t, m]) => { if (s >= t) txt = m; });
  $("skel-txt").textContent = txt; $("skel-time").textContent = s + "s";
}
function setBusy(b) {
  $("run").disabled = b; $("run").querySelector(".spin").hidden = !b;
  const cmpView = document.body.classList.contains("view-cmp");
  $("run").querySelector(".txt").textContent = b ? (cmpView ? "Running strategies..." : "Running backtest...") : (cmpView ? "Compare strategies" : "Run test");
  $("skel").hidden = !b;
  if (b && $("strat-res")) $("strat-res").hidden = true;
  clearInterval(busyTimer); busyTimer = null;
  if (b) { busyT0 = Date.now(); tickBusy(); busyTimer = setInterval(tickBusy, 1000); $("res-err").hidden = true; }
  if (b) { $("empty").hidden = true; $("res").hidden = true; }
  else if (window.P2 && window.P2.showingStrategies()) $("empty").hidden = true;
  else if (!hadResult) $("empty").hidden = false; else $("res").hidden = false;
  if (b && window.matchMedia("(max-width: 980px)").matches) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}
function toast(msg, title) {
  const t = document.createElement("div"); t.className = title === "" ? "toast info" : "toast"; t.setAttribute("role", title === "" ? "status" : "alert");
  const head = title === "" ? "" : `<b>${esc(title == null ? "Test failed." : title)}</b> `;
  t.innerHTML = `<span>${head}${esc(msg)}</span><button type="button" aria-label="Dismiss">×</button>`;
  t.querySelector("button").onclick = () => t.remove();
  $("toasts").appendChild(t); setTimeout(() => t.remove(), 9000);
}
function showErr(msg) { $("form-err").hidden = !msg; $("form-err").textContent = msg || ""; }
function errKind(msg, status) {
  const m = (msg || "").toLowerCase();
  if (status === 503 || status === 504 || /busy|unavailable|timed out|source/.test(m))
    return ["Market data is unavailable right now", "The price feed (and its backups) did not answer in time. Nothing was calculated, so there is no partial result. Wait a minute and try again; a cached market is usually fast."];
  if (/not enough|history|candles/.test(m))
    return ["Not enough price history", "CHRONOS will not fill in missing candles or guess results. Try a market with longer history or a shorter test window."];
  if (status === 429) return ["Too many tests at once", "Wait a few seconds, then try again."];
  return ["The test could not run", "Check the market and levels, then try again."];
}
function showResErr(msg, status) {
  const [h, hint] = errKind(msg, status);
  $("res-err-h").textContent = h; $("res-err-msg").textContent = msg; $("res-err-hint").textContent = hint;
  $("res-err").hidden = false; $("empty").hidden = true; $("res").hidden = true;
}
async function run() {
  showErr("");
  if (document.body.classList.contains("view-cmp") && window.P2) return window.P2.runStrategies();
  const mk = chosen ? chosen.market_id : $("market").value.trim();
  if (!mk) return showErr("Pick a market.");
  const le = levelError();
  if (le === null) return showErr("Enter entry, TP and SL.");
  if (le) return showErr(le);
  const unsure = ["market", "entry", "tp", "sl"].filter(id => $(id).classList.contains("unsure"));
  if (unsure.length) return showErr("Check the highlighted fields from the screenshot first. Edit them, or press \"These look right\" under the screenshot.");
  const slip = $("slip").value === "" ? 0 : +$("slip").value;
  if (!(slip >= 0) || slip > ($("slip-unit").value === "ticks" ? 1000 : 100)) return showErr("Slippage must be 0 to 100 bps (or 0 to 1000 ticks).");
  const body = { market: mk, side, entry: +$("entry").value, tp: +$("tp").value, sl: +$("sl").value,
    days: +$("days").value, expiry: $("expiry").value, trend_filter: $("trend").checked, mode,
    insights: mode === "similar" ? false : $("insights").checked,
    slippage: slip, slippage_unit: $("slip-unit").value, allow_overlap: mode !== "pattern" && $("overlap").checked,
    max_hold: $("max-hold") ? $("max-hold").value : "7d" };
  const pat = (($("shot-pattern") && $("shot-pattern").value) || setupPattern || "").trim();
  if (pat) body.pattern = pat;
  body.tf = $("similar-tf").value;
  if ($("htf")) body.htf_confirm = $("htf").value;
  if ($("tf-sweep") && $("tf-sweep").checked) body.tf_sweep = true;
  if (mode === "pattern") { body.zone_tf = body.tf; body.entry_style = $("entry-style").value; if ($("sl-buf").value !== "") body.sl_buffer_atr = +$("sl-buf").value; }
  if (mode === "similar") {
    body.similar_tf = $("similar-tf").value;
    body.similar_bars = +$("similar-bars").value || 24;
  }
  const lib = window.P3 && window.P3.takeRunOverrides ? window.P3.takeRunOverrides() : null;
  if (lib) {  // running a saved strategy: its own setup type, and the result snapshot is saved to the library
    body.strategy_id = lib.strategy_id;
    if (lib.mode === "rule") {
      Object.assign(body, { mode: "rule", rule: lib.rule, rule_params: lib.rule_params || {}, insights: false });
      delete body.similar_tf; delete body.similar_bars; delete body.zone_tf; delete body.entry_style;
    }
    if (lib.pattern_label) body.pattern = lib.pattern_label;
  }
  lastRequest = body;
  setBusy(true);
  try {
    let r, j;
    try {
      r = await fetch("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (e) { throw Object.assign(new Error("Could not reach CHRONOS. Check your connection and try again."), { status: 0 }); }
    j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw Object.assign(new Error(j.error || ("HTTP " + r.status)), { status: r.status });
    render(j);
  } catch (err) { setBusy(false); showResErr(err.message, err.status); toast(err.message); return; }
  finally { if (busyTimer) setBusy(false); }
}

// ---------- render
function badgeFor(v) {
  v = (v || "").toLowerCase();
  if (v.startsWith("too few")) return ["few", "Too few samples"];
  if (v.startsWith("no edge")) return ["noedge", "No edge"];
  if (v.startsWith("no reliable edge")) return ["few", "No reliable edge"];
  if (v.startsWith("possible edge")) return ["maybe", "Possible edge, not proven"];
  if (v.startsWith("edge")) return ["edge", "Edge on this sample"];
  return ["few", v];
}
function card(k, v, d, cls, kpi) { return `<div class="card${kpi ? " kpi" : ""}"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`; }
function render(o) {
  lastResult = o;
  const g = o.setup, s = o.result, m = o.market, p = o.params;
  hadResult = true; $("empty").hidden = true; $("skel").hidden = true; $("res").hidden = false;
  if ($("strat-res")) $("strat-res").hidden = true;
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
  } else if (p.mode === "rule") {
    how = `RULE: ${p.rule_label || p.rule}. ${windowTxt}${p.placement}. Expiry ${p.expiry}, time stop ${p.max_hold}`;
  } else {
    const atf = p.setup_tf || "4h";
    how = `EVERY ${atf.toUpperCase()} CLOSE. ${windowTxt}Price at test ${fmt(g.ref)}, ${atf} ATR ${fmt(g.atr, 4)}. Replayed as entry ${g.entry_atr >= 0 ? "+" : ""}${g.entry_atr.toFixed(2)} ATR (${g.entry_type}), ` +
      `stop ${g.risk_atr.toFixed(2)} ATR, target ${g.reward_atr.toFixed(2)} ATR. Expiry ${p.expiry}, time stop ${p.max_hold}`;
  }
  const htfTxt = p.htf_confirm && p.htf_confirm !== "off" && o.htf ? ` Higher-timeframe confirmation on ${p.htf_confirm}: ${o.htf.rejected} of ${o.htf.checked} setups skipped because the ${p.htf_confirm} trend did not agree.` : "";
  $("r-sub").textContent = how + (p.trend_filter ? ", trend filter on." : ".") + htfTxt;
  const [cls, label] = badgeFor(s.verdict);
  $("badge").className = "badge " + cls; $("badge").textContent = label;
  $("verdict-card").className = "verdict-card " + cls;
  $("verdict").textContent = "Engine verdict: " + s.verdict + ".";
  const warns = [];
  if (!pat && !sim && Math.abs(g.entry_atr) > 3) warns.push(`Entry is ${Math.abs(g.entry_atr).toFixed(1)} ATR from the current price, so most replays will not fill.`);
  if (s.shallow_sweep_pct_of_losses && s.sl >= 5 && s.shallow_sweep_pct_of_losses >= 0.2) warns.push("Stop looks too tight: a good share of losers wicked through the stop before the move reached TP.");
  if (p.trend_filter) warns.push(`Trend filter skipped ${o.skipped_by_trend} eligible 4h closes. 4h trend now: ${o.trend_now || "n/a"}.`);
  $("warn").hidden = !warns.length; $("warn").innerHTML = warns.join("<br>");
  const st = o.stats;
  const zcard = pat && o.zones ? [card("Patterns found", o.zones.zones_found,
    `${p.zone_tf} ${g.side === "short" ? "supply" : "demand"} areas · touched ${o.zones.zones_touched} · broken ${o.zones.zones_invalidated} · traded ${o.zones.zones_traded}`)] : [];
  const scard = sim && o.similar_matches ? [card("Similar windows", o.similar_matches.filter(x => !x.skipped).length,
    `up to ${p.similar_k || 30} non-overlapping matches on ${p.similar_tf || "1h"}`)] : [];
  if (st) {
    const n = st.net, gr = st.gross, cl = v => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
    const pfTxt = b => b.profit_factor != null ? Number(b.profit_factor).toFixed(2) : (b.profit_factor_infinite ? "no losses" : "n/a");
    const hrs = h => h == null ? "-" : h >= 48 ? (h / 24).toFixed(1) + "d" : Number(h).toFixed(1) + "h";
    const rr = b => b.avg_rr_achieved == null ? "n/a" : "1:" + Number(b.avg_rr_achieved).toFixed(2);
    // primary KPIs first; every other number stays one tap away under "More numbers"
    const kpis = [
      card("Win rate", pct(n.win_rate), `break-even ${pct(s.breakeven_win_rate)} · theoretical ${pct(gr.win_rate)}`, n.win_rate == null ? "" : (n.win_rate > s.breakeven_win_rate ? "pos" : "neg"), 1),
      card("Trades executed", st.trades_executed, `of ${st.orders_placed} orders · unfilled ${st.unfilled}${st.open_at_end ? ` · still open ${st.open_at_end}` : ""}`, "", 1),
      card("Net P/L", sgnR(n.total_r), `${sgnPct(n.total_pct)} at ${st.risk_pct_per_trade}% risk per trade · theoretical ${sgnR(gr.total_r)}`, cl(n.total_r), 1),
      card("Profit factor", pfTxt(n), `theoretical ${pfTxt(gr)} · above 1 = made money`, n.profit_factor == null ? "" : (n.profit_factor > 1 ? "pos" : "neg"), 1),
      card("Max drawdown", (n.max_drawdown_r || 0).toFixed(2) + "R", `${sgnPct(n.max_drawdown_pct)} at ${st.risk_pct_per_trade}% risk · theoretical ${(gr.max_drawdown_r || 0).toFixed(2)}R`, n.max_drawdown_r < 0 ? "neg" : "", 1),
      card("Expectancy", sgnR(n.expectancy_r), `average result per trade · theoretical ${sgnR(gr.expectancy_r)}`, cl(n.expectancy_r), 1),
      card("Avg R:R achieved", rr(n), `avg win ÷ avg loss · theoretical ${rr(gr)}`, "", 1),
      card("Wins / losses", `${n.wins} / ${n.losses}`, `${st.closed} closed${n.flat ? ` · ${n.flat} flat` : ""} · theoretical ${gr.wins} / ${gr.losses}`, "", 1),
    ];
    const more = [
      card("Setups identified", st.setups_identified, esc(st.setups_basis)),
      card("Avg win / avg loss", `${sgnR(n.avg_win_r)} / ${sgnR(n.avg_loss_r)}`, `theoretical ${sgnR(gr.avg_win_r)} / ${sgnR(gr.avg_loss_r)}`),
      card("Longest streaks", `${n.longest_win_streak}W / ${n.longest_loss_streak}L`, "wins and losses in a row"),
      card("Avg time in trade", hrs(st.duration_h.avg), `winners ${hrs(st.duration_h.avg_win)} · losers ${hrs(st.duration_h.avg_loss)}`),
      ...zcard, ...scard,
      card("Stop-sweep check", `${s.sl_then_tp} / ${s.sl}`, `stop-outs that still reached TP within ${p.sweep_window}`),
    ];
    const wasOpen = !!document.querySelector("#cards details.more-stats[open]");
    $("cards").innerHTML = `<div class="kpis">${kpis.join("")}</div>` +
      `<details class="more-stats"${wasOpen ? " open" : ""}><summary>More numbers <span class="muted">${more.length} more from this test</span></summary><div class="kpis2">${more.join("")}</div></details>`;
    renderGvn(st, o);
    renderWhy(st.why || []);
    renderOutcomes(st);
    renderMonthly(st.monthly || []);
  } else {
    $("cards").innerHTML = '<div class="kpis">' + [...zcard, ...scard,
      card("Orders", s.placed, `filled ${s.fills} · unfilled ${s.unfilled}`),
      card("Closed trades", s.closed, `TP ${s.tp} · SL ${s.sl}`),
      card("Win rate", pct(s.win_rate), `break-even ${pct(s.breakeven_win_rate)}`),
      card("Avg R per trade", sgnR(s.avg_r_net), `gross ${sgnR(s.avg_r_gross)}`),
      card("Total R", sgnR(s.total_r_net), ""),
      card("Max drawdown", (s.max_drawdown_r || 0).toFixed(2) + "R", "")].join("") + "</div>";
  }
  ["gvn-panel", "why-panel"].forEach(id => $(id).hidden = !st);
  renderDQ(o.data_quality);
  const slipTxt = o.costs && o.costs.slippage ? o.costs.slippage.text : "none";
  $("meta").textContent = `Fees: ${o.fees.commission_pct_per_fill}% per fill + ${o.fees.swap_pct_per_day}%/day swap (${o.fees.asset_class}, MFP standard). ` +
    `Slippage: ${slipTxt} (${p.slippage_rule || "market-type fills"}). ` +
    `${p.one_at_a_time === false ? "Overlapping trades allowed." : "One order or trade at a time."} ` +
    (p.htf_rule ? `HTF confirmation: ${p.htf_rule}. ` : "") +
    `Setup timeframe ${p.setup_tf || "-"}, fills on ${p.interval || o.data.interval} candles. ` +
    `Tie rule: ${p.tie_rule}. Look-ahead: ${p.lookahead_rule || "only closed candles are used"}. Placement: ${p.placement}. ` +
    `Generated ${o.generated.replace("T", " ").replace("Z", " UTC")}` + (o.elapsed_s != null ? `, ran in ${o.elapsed_s}s.` : ".");
  renderProvenance(o.data);
  renderInsights(o.insights);
  renderSimilarMatches(o);
  tradeSort = { key: null, dir: 1 };
  renderTrades(o.trades || [], p.mode);
  drawChart(o);
  drawHist(o);
  setupCompare(o);
  setupScan(o);
  document.dispatchEvent(new CustomEvent("chronos:result", { detail: o }));
}
const sgnPct = x => x == null ? "-" : (x > 0 ? "+" : "") + Number(x).toFixed(1) + "%";
function renderDQ(dq) {
  const el = $("dq");
  if (!dq || dq.status === "ok" || !(dq.messages || []).length) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "dq " + (dq.status === "insufficient" ? "bad" : "warn");
  const h = dq.status === "insufficient" ? "Not enough data for a verdict" : "Check the data behind this result";
  el.innerHTML = `<b>${h}</b><ul>${dq.messages.map(m => `<li>${esc(m)}</li>`).join("")}</ul>`;
}
function renderGvn(st, o) {
  const g = st.gross, n = st.net;
  const f2 = x => x == null ? "n/a" : Number(x).toFixed(2);
  const pf = b => b.profit_factor != null ? f2(b.profit_factor) : (b.profit_factor_infinite ? "no losses" : "n/a");
  const rows = [
    ["Closed trades", g.trades, n.trades],
    ["Wins / losses", `${g.wins} / ${g.losses}`, `${n.wins} / ${n.losses}`],
    ["Win rate", pct(g.win_rate), pct(n.win_rate)],
    ["Net result", sgnR(g.total_r), sgnR(n.total_r), true],
    ["Return at " + st.risk_pct_per_trade + "% risk/trade", sgnPct(g.total_pct), sgnPct(n.total_pct), true],
    ["Profit factor", pf(g), pf(n)],
    ["Expectancy per trade", sgnR(g.expectancy_r), sgnR(n.expectancy_r), true],
    ["Average win", sgnR(g.avg_win_r), sgnR(n.avg_win_r)],
    ["Average loss", sgnR(g.avg_loss_r), sgnR(n.avg_loss_r)],
    ["Avg R:R achieved", g.avg_rr_achieved == null ? "n/a" : "1:" + f2(g.avg_rr_achieved), n.avg_rr_achieved == null ? "n/a" : "1:" + f2(n.avg_rr_achieved)],
    ["Max drawdown", f2(g.max_drawdown_r) + "R", f2(n.max_drawdown_r) + "R"],
    ["Longest win / loss streak", `${g.longest_win_streak} / ${g.longest_loss_streak}`, `${n.longest_win_streak} / ${n.longest_loss_streak}`],
    ["t-stat", g.t_stat == null ? "n/a" : f2(g.t_stat), n.t_stat == null ? "n/a" : f2(n.t_stat)],
  ];
  const num = v => parseFloat(String(v).replace(/[^0-9.+-]/g, ""));
  $("gvn").querySelector("tbody").innerHTML = rows.map(([k, a, b, c]) => {
    const ca = c ? (num(a) > 0 ? "pos" : num(a) < 0 ? "neg" : "") : "", cb = c ? (num(b) > 0 ? "pos" : num(b) < 0 ? "neg" : "") : "";
    return `<tr><td>${k}</td><td class="r ${ca}">${a}</td><td class="r ${cb}">${b}</td></tr>`;
  }).join("");
  const c = st.costs;
  $("gvn-note").textContent = `${st.closed} closed trades`;
  $("cost-line").innerHTML = st.closed ? `Costs took <b>${Number(c.total_r).toFixed(2)}R</b> in total: fees and swap ${Number(c.fees_r).toFixed(2)}R, slippage ${Number(c.slippage_r).toFixed(2)}R` +
    ` (${Number(c.per_trade_r).toFixed(3)}R per trade` + (c.share_of_gross_profit != null ? `, ${(c.share_of_gross_profit * 100).toFixed(0)}% of the theoretical profit` : "") + `).` : "";
}
function renderWhy(list) {
  const icon = { good: "+", warn: "!", info: "i" };
  $("why-list").innerHTML = list.length ? list.map(f => `<li class="${f.level}"><span class="wi" aria-hidden="true">${icon[f.level] || "i"}</span><div><b>${esc(f.title)}</b><span>${esc(f.text)}</span></div></li>`).join("")
    : `<li class="info"><span class="wi">i</span><div><b>Nothing stands out</b><span>No rule tripped on these trades.</span></div></li>`;
}
function renderOutcomes(st) {
  const parts = [["tp", "Take profit", st.exits.tp], ["sl", "Stop loss", st.exits.sl], ["time", "Time exit", st.exits.time],
    ["unf", "Unfilled", st.unfilled], ["open", "Still open", st.open_at_end]];
  const tot = parts.reduce((a, x) => a + x[2], 0) || 1;
  $("outbar").innerHTML = parts.filter(x => x[2]).map(([k, l, v]) => `<span class="ob ${k}" style="flex:${v}" title="${l}: ${v}"></span>`).join("") || `<span class="ob unf" style="flex:1"></span>`;
  $("outleg").innerHTML = parts.map(([k, l, v]) => `<span><i class="ob ${k}"></i>${l} <b>${v}</b> <span class="muted">${Math.round(v / tot * 100)}%</span></span>`).join("");
  $("out-note").textContent = `${st.orders_placed} orders`;
}
function renderMonthly(rows) {
  const el = $("monthly");
  if (!rows.length) { el.innerHTML = `<p class="muted small">No closed trades.</p>`; return; }
  const mx = Math.max(...rows.map(r => Math.abs(r.r_net)), 0.5);
  el.innerHTML = rows.map(r => {
    const w = Math.abs(r.r_net) / mx * 50, neg = r.r_net < 0;
    const [y, m] = r.month.split("-");
    return `<div class="mrow"><span class="ml">${MON[+m - 1]} ${y.slice(2)}</span><span class="mbar"><i class="${neg ? "neg" : "pos"}" style="${neg ? `right:50%;width:${w}%` : `left:50%;width:${w}%`}"></i></span><span class="mv ${neg ? "neg" : "pos"}">${sgnR(r.r_net)}</span><span class="mn muted">${r.trades}</span></div>`;
  }).join("");
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
let tradeSort = { key: null, dir: 1 }, tradeRows = [], tradeMode = "similar";
const EXIT_LBL = { tp: "Take profit", sl: "Stop loss", sl_gap_open: "Stop (gap, filled at open)", time_stop: "Time stop" };
const STATUS_LBL = { tp: "TP", sl: "SL", time: "Time exit", unfilled: "Unfilled", open_at_end: "Open", pending_at_end: "Pending" };
function srcLabel(s) { if (!s) return "-"; const k = s.split(":")[0]; return ({ mfp: "MFP", okx: "OKX", bybit: "Bybit", yahoo: "Yahoo", dukascopy: "Dukascopy", hyperliquid: "Hyperliquid" })[k] || k; }
function durTxt(h) { if (h == null) return "-"; if (h < 1) return Math.round(h * 60) + "m"; if (h < 48) return h.toFixed(1) + "h"; return (h / 24).toFixed(1) + "d"; }
const TRADE_COLS = [
  { k: "i", t: "#", v: r => r.i },
  { k: "placed", t: "Signal (WAT)", v: r => r.t.placed || 0, f: r => wat(r.t.placed) },
  { k: "ctx", t: "Context", v: r => r.ctxv, f: r => r.ctx },
  { k: "filled", t: "Entry time", v: r => r.t.filled || 0, f: r => r.t.filled ? wat(r.t.filled) : "-" },
  { k: "entry", t: "Entry", v: r => r.t.fill_px ?? r.t.entry ?? 0, f: r => px(r.t.fill_px ?? r.t.entry), r: 1 },
  { k: "sl", t: "SL", v: r => r.t.sl ?? 0, f: r => px(r.t.sl), r: 1 },
  { k: "tp", t: "TP", v: r => r.t.tp ?? 0, f: r => px(r.t.tp), r: 1 },
  { k: "exit", t: "Exit time", v: r => r.t.exit || 0, f: r => r.t.exit ? wat(r.t.exit) : "-" },
  { k: "exit_px", t: "Exit", v: r => r.t.exit_px ?? 0, f: r => px(r.t.exit_px), r: 1 },
  { k: "status", t: "Result", v: r => r.t.status, f: r => {
      const st = r.t.status, tag = st === "tp" ? "tp" : st === "sl" ? "sl" : st === "time" ? "time" : "unf";
      const extra = (r.t.same_bar_tp_sl ? ` <span class="muted" title="TP and SL both inside one candle: counted as a loss">tie</span>` : "") +
        (r.t.exit_reason === "sl_gap_open" ? ` <span class="muted" title="Price gapped through the stop; exit at the gap open">gap</span>` : "") +
        (r.t.gap_bars_in_trade ? ` <span class="muted" title="${r.t.gap_bars_in_trade} candles missing while this trade was open">data hole</span>` : "") +
        (st === "sl" && r.t.sl_then_tp ? ` <span class="muted" title="Price still reached TP after the stop-out">swept</span>` : "");
      return `<span class="tag ${tag}" title="${esc(EXIT_LBL[r.t.exit_reason] || STATUS_LBL[st] || st)}">${STATUS_LBL[st] || st}</span>${extra}`; } },
  { k: "r_gross", t: "R theor.", v: r => r.t.r_gross ?? -1e9, f: r => r.t.r_gross == null ? "" : `<span class="${r.t.r_gross > 0 ? "pos" : "neg"}">${sgnR(r.t.r_gross)}</span>`, r: 1 },
  { k: "r_net", t: "R net", v: r => r.t.r_net ?? -1e9, f: r => r.t.r_net == null ? "" : `<b class="${r.t.r_net > 0 ? "pos" : "neg"}">${sgnR(r.t.r_net)}</b>`, r: 1 },
  { k: "hours", t: "Duration", v: r => r.t.hours ?? -1, f: r => durTxt(r.t.hours), r: 1 },
  { k: "source", t: "Candles", v: r => r.t.source || "", f: r => `<span class="muted">${srcLabel(r.t.source)} ${r.t.interval || ""}</span>` },
  { k: "replay", t: "Replay", v: r => r.i, f: r => ["tp", "sl", "time"].includes(r.t.status) && r.t.candle_window
      ? `<button type="button" class="btn-ghost sm rp-open" data-i="${r.i - 1}" aria-label="Replay trade ${r.i}">Replay</button>` : "" },
];
function renderTrades(tr, md) {
  tradeMode = md;
  const col = md === "pattern" ? "Zone near / far" : md === "similar" ? "Similarity" : "Ref price";
  tradeRows = tr.map((t, i) => {
    let ctx = "-", ctxv = 0;
    if (md === "similar" && t.similar_score != null) { ctx = Number(t.similar_score).toFixed(1); ctxv = t.similar_score; }
    else if (t.zone_proximal != null) { ctx = `${px(t.zone_proximal)} / ${px(t.zone_distal)}`; ctxv = t.zone_proximal; }
    else if (t.ref != null) { ctx = px(t.ref); ctxv = t.ref; }
    return { i: i + 1, t, ctx, ctxv };
  });
  TRADE_COLS[2].t = col;
  drawTrades();
}
function drawTrades() {
  const closedOnly = $("t-closed") && $("t-closed").checked;
  let rows = tradeRows.filter(r => !closedOnly || ["tp", "sl", "time"].includes(r.t.status));
  if (tradeSort.key) {
    const c = TRADE_COLS.find(x => x.k === tradeSort.key);
    rows = rows.slice().sort((a, b) => { const x = c.v(a), y = c.v(b); return (x < y ? -1 : x > y ? 1 : a.i - b.i) * tradeSort.dir; });
  }
  $("trades-head").innerHTML = TRADE_COLS.map(c => {
    const on = tradeSort.key === c.k, arrow = on ? (tradeSort.dir > 0 ? " ▲" : " ▼") : "";
    return `<th class="${c.r ? "r " : ""}sortable${on ? " on" : ""}" data-k="${c.k}" tabindex="0" aria-sort="${on ? (tradeSort.dir > 0 ? "ascending" : "descending") : "none"}">${c.t}${arrow}</th>`;
  }).join("");
  $("trades").querySelector("tbody").innerHTML = rows.map(r => `<tr>${TRADE_COLS.map(c => `<td class="${c.r ? "r" : ""}">${c.f ? c.f(r) : c.v(r)}</td>`).join("")}</tr>`).join("") ||
    `<tr><td colspan="${TRADE_COLS.length}" class="muted">${closedOnly ? "No closed trades." : "No orders were placed in the window."}</td></tr>`;
  const nClosed = tradeRows.filter(r => ["tp", "sl", "time"].includes(r.t.status)).length;
  $("t-count").textContent = `${tradeRows.length} orders · ${nClosed} closed`;
}
$("trades").addEventListener("click", e => {
  const rb = e.target.closest(".rp-open");
  if (rb) { if (window.P2) window.P2.openReplay(lastResult, +rb.dataset.i); return; }
  const th = e.target.closest("th[data-k]"); if (!th || th.dataset.k === "replay") return;
  const k = th.dataset.k;
  tradeSort = tradeSort.key === k ? { key: k, dir: -tradeSort.dir } : { key: k, dir: 1 };
  drawTrades();
});
$("trades").addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && e.target.matches("th[data-k]")) { e.preventDefault(); e.target.click(); } });
if ($("t-closed")) $("t-closed").addEventListener("change", drawTrades);
function csvCell(v) { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }
function tradesCsv(o) {
  const iso = ms => ms ? new Date(ms).toISOString().replace(".000Z", "Z") : "";
  const cols = ["n", "market", "side", "status", "exit_reason", "signal_utc", "entry_time_utc", "exit_time_utc", "entry_order", "entry_fill",
    "entry_fill_theoretical", "stop_loss", "take_profit", "exit_price", "exit_price_theoretical", "r_theoretical", "r_slippage", "r_fees",
    "r_net", "hours", "same_candle_tp_sl", "gap_candles_in_trade", "candle_interval", "candle_source", "similarity", "zone_near", "zone_far"];
  const side = o.setup.side;
  const lines = [cols.join(",")];
  (o.trades || []).forEach((t, i) => lines.push([i + 1, t.market, side, t.status, t.exit_reason || "", iso(t.placed), iso(t.filled), iso(t.exit),
    t.entry, t.fill_px, t.fill_px_theoretical, t.sl, t.tp, t.exit_px, t.exit_px_theoretical, t.r_gross, t.r_slip, t.r_fee, t.r_net, t.hours,
    t.same_bar_tp_sl ? 1 : 0, t.gap_bars_in_trade || 0, t.interval, t.source, t.similar_score, t.zone_proximal, t.zone_distal].map(csvCell).join(",")));
  return lines.join("\n") + "\n";
}
if ($("t-csv")) $("t-csv").addEventListener("click", () => {
  if (!lastResult) return;
  const o = lastResult, blob = new Blob([tradesCsv(o)], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `chronos-${(o.market.market_id.split("|").pop() || "trades").replace(/[^A-Za-z0-9_-]/g, "")}-${o.setup.side}-${(o.params || {}).mode || "test"}.csv`;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
});
function sizeCanvas(cv, H) {
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth;
  cv.width = W * dpr; cv.height = H * dpr;
  const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  return [c, W];
}
function drawChart(o) {
  // TradingView Lightweight Charts (zoom / pan) when available; the plain canvas below is the fallback
  if (window.CHRONOS_UI && window.CHRONOS_UI.equity && window.CHRONOS_UI.equity($("chart"), o)) return;
  let cv = $("chart");
  if (cv.tagName !== "CANVAS") cv = cv.querySelector("canvas") || cv.appendChild(document.createElement("canvas"));
  const H = 260, [c, W] = sizeCanvas(cv, H);
  const eqs = (o.stats && o.stats.equity) || [];
  const pts = [{ x: 0, n: 0, g: 0 }].concat(eqs.map((e, i) => ({ x: i + 1, n: e.n, g: e.g, s: e.s, t: e.t })));
  const L = 50, R = 14, T = 12, B = 28, w = W - L - R, h = H - T - B;
  c.font = "11px 'Geist Mono',ui-monospace,Menlo,monospace"; c.fillStyle = "#78857d";
  if (eqs.length === 0) { c.fillText("No closed trades to chart.", L, H / 2); return; }
  let lo = Math.min(0, ...pts.map(p => Math.min(p.n, p.g))), hi = Math.max(0, ...pts.map(p => Math.max(p.n, p.g)));
  if (hi - lo < 1) { hi += .5; lo -= .5; }
  const padY = (hi - lo) * .08; lo -= padY; hi += padY;
  const X = x => L + (x / Math.max(1, pts.length - 1)) * w, Y = y => T + (hi - y) / (hi - lo) * h;
  const stepR = niceStep((hi - lo) / 5);
  c.strokeStyle = "rgba(170,255,170,.06)"; c.lineWidth = 1;
  for (let v = Math.ceil(lo / stepR) * stepR; v <= hi; v += stepR) {
    c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(W - R, Y(v)); c.stroke();
    c.fillText((v > 0 ? "+" : "") + v.toFixed(stepR < 1 ? 1 : 0) + "R", 4, Y(v) + 4);
  }
  c.strokeStyle = "rgba(170,255,170,.2)"; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(L, Y(0)); c.lineTo(W - R, Y(0)); c.stroke(); c.setLineDash([]);
  // theoretical (gross): thin dashed, muted
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.g)) : c.moveTo(X(p.x), Y(p.g)));
  c.strokeStyle = "rgba(169,180,172,.55)"; c.lineWidth = 1.4; c.setLineDash([5, 4]); c.stroke(); c.setLineDash([]);
  // after costs (net): filled area + solid line
  const col = "#6dff3c";
  const top = Math.min(...pts.map(p => Y(p.n))), grd = c.createLinearGradient(0, top, 0, Y(0));
  grd.addColorStop(0, "rgba(109,255,60,.14)"); grd.addColorStop(1, "rgba(109,255,60,0)");
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.n)) : c.moveTo(X(p.x), Y(p.n)));
  c.lineTo(X(pts.length - 1), Y(0)); c.lineTo(X(0), Y(0)); c.closePath();
  c.save(); c.clip(); c.fillStyle = grd; c.fillRect(L, T, w, Math.max(0, Y(0) - T)); c.restore();
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.n)) : c.moveTo(X(p.x), Y(p.n)));
  c.strokeStyle = col; c.lineWidth = 2.2; c.lineJoin = "round"; c.stroke();
  if (pts.length <= 160) pts.slice(1).forEach(p => { c.beginPath(); c.arc(X(p.x), Y(p.n), 2.6, 0, 7);
    c.fillStyle = p.s === "tp" ? "#4ade80" : p.s === "sl" ? "#e0646e" : "#d6b25f"; c.fill(); });
  c.fillStyle = "#78857d";
  c.fillText("trade 1", L, H - 8);
  const endLbl = `trade ${eqs.length} · ${wat(eqs[eqs.length - 1].t)} WAT`;
  c.fillText(endLbl, W - R - c.measureText(endLbl).width, H - 8);
}
function drawHist(o) {
  const cv = $("hist"); if (!cv) return;
  const H = 220, [c, W] = sizeCanvas(cv, H);
  const bins = (o.stats && o.stats.r_hist && o.stats.r_hist.net) || [];
  c.font = "11px 'Geist Mono',ui-monospace,Menlo,monospace"; c.fillStyle = "#78857d";
  const tot = bins.reduce((a, b) => a + b.count, 0);
  if (!tot) { c.fillText("No closed trades.", 10, H / 2); return; }
  const L = 8, R = 8, T = 18, B = 34, w = W - L - R, h = H - T - B, n = bins.length, bw = w / n;
  const mx = Math.max(...bins.map(b => b.count));
  bins.forEach((b, i) => {
    const bh = b.count / mx * h, x = L + i * bw + 3, y = T + h - bh;
    const neg = b.hi != null ? b.hi <= 0 : false;
    c.fillStyle = neg ? "rgba(224,100,110,.7)" : "rgba(74,222,128,.75)";
    c.fillRect(x, y, Math.max(2, bw - 6), bh);
    c.fillStyle = "#a9b4ac"; if (b.count) c.fillText(String(b.count), x + Math.max(0, (bw - 6) / 2 - 4), y - 4);
  });
  c.fillStyle = "#78857d";
  bins.forEach((b, i) => {
    const lbl = b.lo == null ? `≤${b.hi}` : b.hi == null ? `>${b.lo}` : `${b.hi}`;
    const x = L + i * bw + bw / 2 - c.measureText(lbl).width / 2;
    c.fillText(lbl, x, H - 18);
  });
  c.fillText("R per trade (bucket upper edge)", L, H - 3);
}
function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p; return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p; }
let rzT = null;
window.addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => { if (lastResult) { drawChart(lastResult); drawHist(lastResult); } }, 120); });

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

// ---------- empty-state example and error retry
if ($("res-err-retry")) $("res-err-retry").addEventListener("click", () => run());
if ($("try-example")) $("try-example").addEventListener("click", async () => {
  const btn = $("try-example");
  btn.disabled = true; btn.textContent = "Loading BTC price...";
  try {
    if (!markets.length) { const j = await (await fetch("/api/markets")).json(); markets = j.markets || []; }
    const m = markets.find(x => x.coin === "BTCUSDT") || markets.find(x => /BTC/.test(x.symbol));
    if (!m) throw new Error("BTC is not in the market list right now.");
    pick(m.market_id);
    const pj = await (await fetch("/api/price?market=" + encodeURIComponent(m.market_id))).json();
    if (pj.error || !pj.price) throw new Error(pj.error || "price unavailable");
    const p0 = pj.price, r = v => Math.round(v);
    $("side").querySelector("[data-side=long]").click();
    $("entry").value = r(p0 * 0.997); $("sl").value = r(p0 * 0.987); $("tp").value = r(p0 * 1.017);
    clearUnsure(); updateRR();
    showErr("");
    $("run").scrollIntoView({ behavior: "smooth", block: "center" });
    toast("Example filled in: BTC long, entry 0.3% under price, stop 1% below entry, target 2R. Press Run test.", "");
  } catch (e) { toast(e.message, "Couldn't load the example."); }
  finally { btn.disabled = false; btn.textContent = "Try an example: BTC long at the current price"; }
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
  const qtf = q.get("tf") || q.get("zone_tf") || q.get("similar_tf");
  if (qtf && [...$("similar-tf").options].some(o => o.value === qtf)) { $("similar-tf").value = qtf; tfTouched = true; }
  if (["1h", "4h", "1d"].includes(q.get("htf"))) $("htf").value = q.get("htf");
  if (q.get("tf_sweep") === "1") $("tf-sweep").checked = true;
  if (q.get("similar_bars")) $("similar-bars").value = q.get("similar_bars");
  if (["limit", "sweep", "confirm"].includes(q.get("entry_style"))) $("entry-style").value = q.get("entry_style");
  if (q.get("insights") === "0") $("insights").checked = false;
  if (q.get("insights") === "1") $("insights").checked = true;
  if (q.get("slippage") != null && q.get("slippage") !== "") $("slip").value = q.get("slippage");
  if (["bps", "ticks"].includes(q.get("slippage_unit"))) $("slip-unit").value = q.get("slippage_unit");
  if (q.get("overlap") === "1" && mode !== "pattern") $("overlap").checked = true;
  updateRR();
  if (q.get("run") === "1") run();
}
window.CHRONOS = { $, fmt, px, pct, sgnR, sgnPct, wat, esc, durTxt, srcLabel, sizeCanvas, niceStep, toast, setBusy,
  showErr, showResErr, levelError, card, EXIT_LBL, STATUS_LBL, drawHist, drawChart,
  getMarket: () => (chosen ? chosen.market_id : $("market").value.trim()),
  getSide: () => side, getResult: () => lastResult,
  // used by the strategy library (p3.js) to load a saved strategy into the form
  pickMarket: id => { const m = markets.find(x => x.market_id === id || x.symbol.toUpperCase() === String(id).toUpperCase());
    return m ? pick(m.market_id) : Promise.resolve(null); },
  findMarket: id => markets.find(x => x.market_id === id || x.symbol.toUpperCase() === String(id).toUpperCase()) || null,
  getRefPrice: () => refPrice, getMode: () => mode, getTf: () => $("similar-tf").value,
  setSide: s => $("side").querySelector(`[data-side=${s === "short" ? "short" : "long"}]`).click(),
  setMode: m => { const b = $("mode").querySelector(`[data-mode="${m}"]`); if (b) b.click(); },
  setTf: tf => { $("similar-tf").value = tf; tfTouched = true; },
  updateRR, run };
})();

/* Small live UTC clock in the hero (display only). */
(() => {
  const el = document.getElementById("utc-clock");
  if (!el) return;
  const p = n => String(n).padStart(2, "0");
  const tick = () => { const d = new Date(); el.textContent = `UTC ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`; };
  let t = null;
  const start = () => { if (!t) { tick(); t = setInterval(tick, 1000); } };
  const stop = () => { clearInterval(t); t = null; };
  document.addEventListener("visibilitychange", () => document.hidden ? stop() : start());
  if ("IntersectionObserver" in window) new IntersectionObserver(([e]) => e.isIntersecting && !document.hidden ? start() : stop()).observe(el);
  start();
})();

/* Pause decorative animations when the tab is hidden or the header scrolls away. */
(() => {
  const root = document.documentElement;
  const vis = () => root.classList.toggle("anim-paused", document.hidden);
  document.addEventListener("visibilitychange", vis); vis();
  const hero = document.querySelector(".hero");
  if (hero && "IntersectionObserver" in window)
    new IntersectionObserver(([e]) => hero.classList.toggle("off-screen", !e.isIntersecting)).observe(hero);
})();
