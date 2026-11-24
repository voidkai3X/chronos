(() => {
const $ = id => document.getElementById(id);
let markets = [], side = "long", mode = "every-close", chosen = null, acIdx = -1, lastResult = null;

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
  list.hidden = false;
}
function pick(id) {
  chosen = markets.find(m => m.market_id === id) || null;
  if (!chosen) return;
  $("market").value = chosen.symbol;
  $("mkt-id").textContent = chosen.market_id;
  $("ac-list").hidden = true; acIdx = -1;
  loadPrice();
}
$("market").addEventListener("input", () => { chosen = null; $("mkt-id").textContent = ""; $("price").textContent = ""; acIdx = -1; renderAc(); });
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
function loadPrice() {
  if (!chosen) return;
  const id = chosen.market_id;
  $("price").textContent = "loading price...";
  fetch("/api/price?market=" + encodeURIComponent(id)).then(r => r.json()).then(j => {
    if (!chosen || chosen.market_id !== id) return;
    if (j.error) { $("price").textContent = "price unavailable"; refPrice = null; return; }
    refPrice = j.price;
    $("price").textContent = "Price " + fmt(j.price) + " (last 15m close)";
    updateRR();
  }).catch(() => { $("price").textContent = "price unavailable"; });
}

// ---------- side + live R:R
$("side").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  side = b.dataset.side;
  [...$("side").children].forEach(x => x.classList.toggle("on", x === b));
  updateRR();
});
$("mode").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  mode = b.dataset.mode;
  [...$("mode").children].forEach(x => x.classList.toggle("on", x === b));
  $("pattern-opts").hidden = mode !== "pattern";
  $("expiry").disabled = mode === "pattern";
  $("exp-note").textContent = mode === "pattern" ? "(not used in pattern mode)" : "";
});
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

// ---------- run
$("f").addEventListener("submit", e => { e.preventDefault(); run(); });
function setBusy(b) {
  $("run").disabled = b; $("run").querySelector(".spin").hidden = !b;
  $("run").querySelector(".txt").textContent = b ? "Running backtest..." : "Run test";
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
    days: +$("days").value, expiry: $("expiry").value, trend_filter: $("trend").checked, mode };
  if (mode === "pattern") { body.zone_tf = $("zone-tf").value; if ($("sl-buf").value !== "") body.sl_buffer_atr = +$("sl-buf").value; }
  setBusy(true);
  try {
    const r = await fetch("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    render(j);
  } catch (err) { showErr(err.message); }
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
  $("empty").hidden = true; $("res").hidden = false;
  $("r-title").textContent = `${g.side.toUpperCase()} ${m.market_id}  ·  entry ${fmt(g.entry)}  TP ${fmt(g.tp)}  SL ${fmt(g.sl)}  ·  1:${g.rr.toFixed(2)}`;
  const pat = p.mode === "pattern";
  $("r-sub").textContent = `${pat ? "PATTERN mode. " : ""}${o.data.days} days of ${o.data.interval} MFP candles, ${wat(o.data.from_ms)} to ${wat(o.data.to_ms)} WAT. ` +
    (pat ? `Price at test ${fmt(g.ref)}. Replayed as a limit at the near edge of each fresh ${g.side === "short" ? "supply" : "demand"} zone on ${p.zone_tf} candles, ` +
      `stop ${p.sl_buffer_atr.toFixed(2)} ATR beyond the far edge (${p.sl_buffer_source.how}), target ${g.rr.toFixed(2)}R. Time stop ${p.max_hold}`
    : `Price at test ${fmt(g.ref)}, 4h ATR ${fmt(g.atr, 4)}. Replayed as entry ${g.entry_atr >= 0 ? "+" : ""}${g.entry_atr.toFixed(2)} ATR (${g.entry_type}), ` +
      `stop ${g.risk_atr.toFixed(2)} ATR, target ${g.reward_atr.toFixed(2)} ATR. Expiry ${p.expiry}, time stop ${p.max_hold}`) + (p.trend_filter ? ", trend filter on." : ".");
  const [cls, label] = badgeFor(s.verdict);
  $("badge").className = "badge " + cls; $("badge").textContent = label;
  $("verdict").textContent = "Engine verdict: " + s.verdict + ".";
  const warns = [];
  if (!pat && Math.abs(g.entry_atr) > 3) warns.push(`Entry is ${Math.abs(g.entry_atr).toFixed(1)} ATR from the current price, so most replays will not fill.`);
  if (s.shallow_sweep_pct_of_losses && s.sl >= 5 && s.shallow_sweep_pct_of_losses >= 0.2) warns.push("Stop looks too tight: a good share of losers wicked through the stop before the move reached TP.");
  if (p.trend_filter) warns.push(`Trend filter skipped ${o.skipped_by_trend} eligible 4h closes. 4h trend now: ${o.trend_now || "n/a"}.`);
  $("warn").hidden = !warns.length; $("warn").innerHTML = warns.join("<br>");
  const wrCls = s.win_rate == null ? "" : (s.win_rate > s.breakeven_win_rate ? "pos" : "neg");
  const zcard = pat && o.zones ? [card("Zones found", o.zones.zones_found,
    `${p.zone_tf} ${g.side === "short" ? "supply" : "demand"} zones · touched ${o.zones.zones_touched} · broken ${o.zones.zones_invalidated} · traded ${o.zones.zones_traded}`)] : [];
  $("cards").innerHTML = [...zcard,
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
  renderTrades(o.trades || []);
  drawChart(o.trades || []);
}
function renderTrades(tr) {
  const rows = tr.map((t, i) => {
    const st = t.status, tag = st === "tp" ? "tp" : st === "sl" ? "sl" : st === "time" ? "time" : "unf";
    const lbl = { tp: "TP", sl: "SL", time: "Time exit", unfilled: "Unfilled", open_at_end: "Open", pending_at_end: "Pending" }[st] || st;
    const sweep = st === "sl" && t.sl_then_tp ? ` <span class="muted" title="Price still reached TP after the stop-out">swept</span>` : "";
    const r = t.r_net == null ? "" : `<span class="${t.r_net > 0 ? "pos" : "neg"}">${sgnR(t.r_net)}</span>`;
    const zn = t.zone_proximal != null ? `${px(t.zone_proximal)} / ${px(t.zone_distal)}` : "-";
    return `<tr><td>${i + 1}</td><td>${wat(t.placed)}</td><td>${zn}</td><td>${t.filled ? wat(t.filled) : "-"}</td><td>${px(t.fill_px ?? t.entry)}</td>
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
  c.font = "11px ui-monospace,Menlo,monospace"; c.fillStyle = "#7c879b";
  if (closed.length === 0) { c.fillText("No closed trades to chart.", L, H / 2); return; }
  let lo = Math.min(0, ...pts.map(p => p.y)), hi = Math.max(0, ...pts.map(p => p.y));
  if (hi - lo < 1) { hi += .5; lo -= .5; }
  const padY = (hi - lo) * .08; lo -= padY; hi += padY;
  const X = x => L + (x / Math.max(1, pts.length - 1)) * w, Y = y => T + (hi - y) / (hi - lo) * h;
  const stepR = niceStep((hi - lo) / 5);
  c.strokeStyle = "#1d2433"; c.lineWidth = 1;
  for (let v = Math.ceil(lo / stepR) * stepR; v <= hi; v += stepR) {
    c.beginPath(); c.moveTo(L, Y(v)); c.lineTo(W - R, Y(v)); c.stroke();
    c.fillText((v > 0 ? "+" : "") + v.toFixed(stepR < 1 ? 1 : 0) + "R", 4, Y(v) + 4);
  }
  c.strokeStyle = "#3a4458"; c.setLineDash([4, 4]); c.beginPath(); c.moveTo(L, Y(0)); c.lineTo(W - R, Y(0)); c.stroke(); c.setLineDash([]);
  const last = pts[pts.length - 1].y, col = last >= 0 ? "#22c08a" : "#ef5a5a";
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.y)) : c.moveTo(X(p.x), Y(p.y)));
  c.lineTo(X(pts.length - 1), Y(0)); c.lineTo(X(0), Y(0)); c.closePath();
  c.fillStyle = last >= 0 ? "rgba(34,192,138,.10)" : "rgba(239,90,90,.10)"; c.fill();
  c.beginPath(); pts.forEach((p, i) => i ? c.lineTo(X(p.x), Y(p.y)) : c.moveTo(X(p.x), Y(p.y)));
  c.strokeStyle = col; c.lineWidth = 2; c.stroke();
  pts.slice(1).forEach(p => { c.beginPath(); c.arc(X(p.x), Y(p.y), 3, 0, 7);
    c.fillStyle = p.t.status === "tp" ? "#22c08a" : p.t.status === "sl" ? "#ef5a5a" : "#4c8dff"; c.fill(); });
  c.fillStyle = "#7c879b";
  c.fillText("trade 1", L, H - 8);
  const endLbl = `trade ${closed.length} · ${wat(closed[closed.length - 1].exit)} WAT`;
  c.fillText(endLbl, W - R - c.measureText(endLbl).width, H - 8);
}
function niceStep(x) { const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p; return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * p; }
window.addEventListener("resize", () => { if (lastResult) drawChart(lastResult.trades || []); });

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
  if (q.get("mode") === "pattern") $("mode").querySelector("[data-mode=pattern]").click();
  if (q.get("zone_tf")) $("zone-tf").value = q.get("zone_tf");
  updateRR();
  if (q.get("run") === "1") run();
}
})();
