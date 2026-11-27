// CHRONOS trading journal page: entries, filters, calendar, weekly/monthly reports and evidence-gated observations.
(() => {
"use strict";
const $ = id => document.getElementById(id);
const P = window.CHRONOS_PAGE;
const TZ = -new Date().getTimezoneOffset();
let side = "long", editing = null, items = [], view = "trades", calMonth = null, repPeriod = "week", repDate = new Date();
const nf = (v, d) => v == null || !isFinite(v) ? "-" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d });
const px = v => { if (v == null) return "-"; const a = Math.abs(v); return Number(v).toLocaleString("en-US", { maximumFractionDigits: a >= 1000 ? 2 : a >= 1 ? 4 : 6 }); };
const sgnR = v => v == null ? "-" : (v > 0 ? "+" : "") + Number(v).toFixed(2) + "R";
const cls = v => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const localInput = iso => { if (!iso) return ""; const d = new Date(iso); return `${ymd(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
const fromInput = v => v ? new Date(v).toISOString() : null;
const when = iso => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function card(k, v, d, c) { return `<div class="card"><div class="k">${k}</div><div class="v ${c || ""}">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`; }

// ---------------------------------------------------------------- form
$("j-side").addEventListener("click", e => {
  const b = e.target.closest("button[data-s]"); if (!b) return;
  side = b.dataset.s;
  [...$("j-side").querySelectorAll("button")].forEach(x => { const on = x === b; x.classList.toggle("on", on); x.classList.remove("long", "short"); x.setAttribute("aria-pressed", String(on)); });
  b.classList.add(side); preview();
});
function body() {
  const v = id => $(id).value.trim();
  const sid = v("j-strategy");
  return { market: v("j-market"), side, entry: v("j-entry"), exit_price: v("j-exit"), sl: v("j-sl"), tp: v("j-tp"), size: v("j-size"), fees: v("j-fees"),
    pnl: v("j-pnl"), opened_at: fromInput(v("j-open")), closed_at: fromInput(v("j-close")), strategy_id: sid && /^\d+$/.test(sid) ? +sid : null,
    session: v("j-session"), exit_reason: v("j-exitr"), sl_moved: $("j-moved").checked, tags: v("j-tags"), chart_url: v("j-chart"), notes: $("j-notes").value };
}
function preview() {
  const b = body(), e = +b.entry, sl = +b.sl, tp = +b.tp, x = +b.exit_price;
  const parts = [];
  if (e > 0 && sl > 0 && e !== sl) {
    const risk = Math.abs(e - sl), sg = side === "long" ? 1 : -1;
    if (tp > 0) parts.push(`planned R:R 1:${(Math.abs(tp - e) / risk).toFixed(2)}`);
    if (x > 0) parts.push(`result ${sgnR(sg * (x - e) / risk)}`);
    if (+b.size > 0) parts.push(`risk ${nf(risk * +b.size)}`);
  }
  $("j-preview").textContent = parts.length ? parts.join(" · ") : "";
}
$("jn-form").addEventListener("input", preview);
function resetForm() {
  editing = null; $("jn-form").reset();
  $("j-side").querySelector('[data-s="long"]').click();
  $("j-open").value = localInput(new Date().toISOString());
  $("jn-form-h").textContent = "Add a trade"; $("j-save").querySelector("span").textContent = "Save trade";
  $("j-delete").hidden = true; $("jn-new").hidden = true; $("j-err").hidden = true; preview();
}
$("jn-new").addEventListener("click", resetForm);
function edit(t) {
  editing = t.id;
  const set = (id, v) => { $(id).value = v == null ? "" : v; };
  set("j-market", t.market); $("j-side").querySelector(`[data-s="${t.side}"]`).click();
  set("j-entry", t.entry); set("j-exit", t.exit_price); set("j-sl", t.sl); set("j-tp", t.tp); set("j-size", t.size); set("j-fees", t.fees); set("j-pnl", t.pnl);
  set("j-open", localInput(t.opened_at)); set("j-close", localInput(t.closed_at));
  set("j-strategy", t.strategy_id || ""); set("j-session", t.session); set("j-exitr", t.exit_reason); $("j-moved").checked = !!t.sl_moved;
  set("j-tags", t.tags); set("j-chart", t.chart_url); set("j-notes", t.notes);
  $("jn-form-h").textContent = "Edit trade"; $("j-save").querySelector("span").textContent = "Save changes";
  $("j-delete").hidden = false; $("jn-new").hidden = false; preview();
  $("jn-form").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}
$("jn-form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = $("j-err"); err.hidden = true;
  const b = body();
  if (!b.market || !b.entry || !b.opened_at) { err.textContent = "Instrument, entry price and open time are required."; err.hidden = false; return; }
  const btn = $("j-save"); btn.disabled = true;
  try {
    await P.api(editing ? `/api/journal/trades/${editing}` : "/api/journal/trades", { method: editing ? "PUT" : "POST", body: b });
    P.toast(editing ? "Trade updated." : "Trade saved.", true);
    resetForm(); await refresh();
  } catch (x) { err.textContent = x.message; err.hidden = false; }
  finally { btn.disabled = false; }
});
$("j-delete").addEventListener("click", async () => {
  if (!editing || !confirm("Delete this journal entry? This cannot be undone.")) return;
  try { await P.api(`/api/journal/trades/${editing}`, { method: "DELETE" }); P.toast("Entry deleted.", true); resetForm(); refresh(); }
  catch (x) { P.toast(x.message); }
});

// ---------------------------------------------------------------- trades view
function filters() {
  const q = new URLSearchParams();
  [["q", "f-q"], ["market", "f-market"], ["strategy", "f-strategy"], ["outcome", "f-outcome"], ["session", "f-session"], ["side", "f-side"], ["from", "f-from"], ["to", "f-to"]]
    .forEach(([k, id]) => { const v = $(id).value.trim(); if (v) q.set(k, v); });
  return q;
}
function summaryCards(s) {
  return [card("Trades", s.trades, `${s.wins} wins · ${s.losses} losses`), card("Win rate", s.win_rate == null ? "-" : Math.round(s.win_rate * 100) + "%", `${s.closed} closed`),
    card("Net R", sgnR(s.net_r), s.with_r < s.closed ? `${s.with_r} trades have a stop for R` : (s.avg_r == null ? "" : `avg ${sgnR(s.avg_r)}`), cls(s.net_r)),
    card("Profit factor", s.profit_factor == null ? "-" : s.profit_factor.toFixed(2), "in R"), card("P/L", s.pnl == null ? "-" : nf(s.pnl), "sum of recorded P/L", cls(s.pnl))].join("");
}
let fTimer = null;
async function refresh() {
  try {
    const j = await P.api("/api/journal/trades?" + filters());
    items = j.trades;
    const fill = (id, vals, all) => { const cur = $(id).value; $(id).innerHTML = `<option value="">${all}</option>` + vals.map(v => `<option${v === cur ? " selected" : ""}>${P.esc(v)}</option>`).join(""); };
    fill("f-market", j.markets, "All markets"); fill("f-strategy", j.strategies, "All strategies");
    $("jn-sum").innerHTML = summaryCards(j.summary);
    $("jn-count").textContent = `${j.shown} of ${j.total} entries${j.total >= j.limit ? " (journal full)" : ""}`;
    $("jn-rows").innerHTML = items.length ? items.map(t => `<tr data-id="${t.id}" tabindex="0" title="Edit"><td>${when(t.opened_at)}</td><td>${P.esc(t.market)}</td><td class="side-${t.side}">${t.side}</td>` +
      `<td>${px(t.entry)}</td><td>${px(t.exit_price)}</td><td class="${cls(t.r_multiple)}">${sgnR(t.r_multiple)}</td><td>${t.planned_rr == null ? "-" : "1:" + t.planned_rr}</td>` +
      `<td class="${cls(t.pnl)}">${t.pnl == null ? "-" : nf(t.pnl)}</td><td>${P.esc(t.strategy_name || "-")}</td><td>${P.esc(t.session)}</td></tr>`).join("")
      : `<tr><td colspan="10" class="muted">${j.total ? "No entries match these filters." : "No trades yet. Add your first one on the left."}</td></tr>`;
    if (view !== "trades") showView(view);
  } catch (e) { if (e.status !== 401) P.toast(e.message); }
}
$("jn-rows").addEventListener("click", e => { const r = e.target.closest("tr[data-id]"); if (r) edit(items.find(t => t.id === +r.dataset.id)); });
$("jn-rows").addEventListener("keydown", e => { if (e.key === "Enter") { const r = e.target.closest("tr[data-id]"); if (r) edit(items.find(t => t.id === +r.dataset.id)); } });
$("jn-filters").addEventListener("input", () => { clearTimeout(fTimer); fTimer = setTimeout(refresh, 220); });
$("jn-filters").addEventListener("change", () => { clearTimeout(fTimer); refresh(); });
$("jn-csv").addEventListener("click", () => { location.href = "/api/journal/export.csv?" + filters(); });

// ---------------------------------------------------------------- views
$("jn-tabs").addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (b) showView(b.dataset.v); });
function showView(v) {
  view = v;
  [...$("jn-tabs").querySelectorAll("button")].forEach(b => { const on = b.dataset.v === v; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
  document.querySelectorAll(".jv").forEach(p => { p.hidden = p.dataset.v !== v; });
  if (v === "calendar") loadCal(); else if (v === "reports") loadReport(); else if (v === "insights") loadInsights();
}
async function loadCal() {
  if (!calMonth) { const d = new Date(); calMonth = new Date(d.getFullYear(), d.getMonth(), 1); }
  const key = `${calMonth.getFullYear()}-${String(calMonth.getMonth() + 1).padStart(2, "0")}`;
  $("cal-title").textContent = calMonth.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  try {
    const j = await P.api(`/api/journal/calendar?month=${key}&tz=${TZ}`);
    const first = (calMonth.getDay() + 6) % 7, days = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 0).getDate();
    let h = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(d => `<div class="cal-dow">${d}</div>`).join("") + "<div></div>".repeat(first);
    for (let d = 1; d <= days; d++) {
      const k = `${key}-${String(d).padStart(2, "0")}`, b = j.days[k];
      h += `<div class="cal-day${b ? " has " + (b.net_r > 0 ? "up" : b.net_r < 0 ? "dn" : "") : ""}" role="gridcell" data-day="${k}" aria-label="${k}${b ? `: ${b.trades} trades, ${sgnR(b.net_r)}` : ""}">` +
        `<span class="cd-n">${d}</span>${b ? `<span class="cd-r">${b.with_r ? sgnR(b.net_r) : (b.pnl == null ? "" : nf(b.pnl, 0))}</span><span class="cd-t">${b.trades} trade${b.trades === 1 ? "" : "s"}</span>` : ""}</div>`;
    }
    $("cal").innerHTML = h;
    const s = j.summary;
    $("cal-sum").textContent = s.trades ? `${s.trades} trades this month · ${sgnR(s.net_r)} · win rate ${s.win_rate == null ? "-" : Math.round(s.win_rate * 100) + "%"}. Tap a day to filter the trade list.` : "No trades this month.";
  } catch (e) { $("cal").innerHTML = `<p class="muted">${P.esc(e.message)}</p>`; }
}
$("cal").addEventListener("click", e => { const d = e.target.closest(".cal-day.has"); if (!d) return; $("f-from").value = d.dataset.day; $("f-to").value = d.dataset.day; showView("trades"); refresh(); });
$("cal-prev").addEventListener("click", () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() - 1, 1); loadCal(); });
$("cal-next").addEventListener("click", () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 1); loadCal(); });

$("rep-period").addEventListener("click", e => {
  const b = e.target.closest("button[data-p]"); if (!b) return;
  repPeriod = b.dataset.p;
  [...$("rep-period").querySelectorAll("button")].forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
  loadReport();
});
$("rep-prev").addEventListener("click", () => { repDate = repPeriod === "week" ? new Date(repDate.getTime() - 7 * 864e5) : new Date(repDate.getFullYear(), repDate.getMonth() - 1, 1); loadReport(); });
$("rep-next").addEventListener("click", () => { repDate = repPeriod === "week" ? new Date(repDate.getTime() + 7 * 864e5) : new Date(repDate.getFullYear(), repDate.getMonth() + 1, 1); loadReport(); });
function groupTable(title, g) {
  const rows = Object.entries(g).sort((a, b) => b[1].trades - a[1].trades);
  if (!rows.length) return "";
  return `<div class="sec-h">${title}</div><div class="tbl-wrap"><table><thead><tr><th></th><th>Trades</th><th>Win rate</th><th>Net R</th><th>Avg R</th><th>P/L</th></tr></thead><tbody>` +
    rows.map(([k, b]) => `<tr><td>${P.esc(k)}</td><td>${b.trades}</td><td>${b.win_rate == null ? "-" : Math.round(b.win_rate * 100) + "%"}</td><td class="${cls(b.net_r)}">${sgnR(b.net_r)}</td><td>${b.avg_r == null ? "-" : sgnR(b.avg_r)}</td><td>${b.pnl == null ? "-" : nf(b.pnl)}</td></tr>`).join("") + "</tbody></table></div>";
}
function obsHtml(list) {
  return list.length ? `<ul class="obs">${list.map(o => `<li>${P.esc(o.text)}<span class="muted small"> Based on: ${Object.entries(o.counts).map(([k, v]) => `${P.esc(k.replace(/_/g, " "))} ${v}`).join(", ")}.</span></li>`).join("")}</ul>` : "";
}
async function loadReport() {
  try {
    const j = await P.api(`/api/journal/report?period=${repPeriod}&date=${ymd(repDate)}&tz=${TZ}`);
    const r = j.report, s = r.summary, p = r.previous;
    $("rep-title").textContent = repPeriod === "week" ? `Week of ${r.start}` : new Date(r.start + "T12:00").toLocaleDateString("en-GB", { month: "long", year: "numeric" });
    if (!s.trades) { $("rep-body").innerHTML = `<p class="muted">No trades in this ${repPeriod}.</p>`; return; }
    const delta = p.trades ? `previous ${repPeriod}: ${p.trades} trades, ${sgnR(p.net_r)}` : `no trades the previous ${repPeriod}`;
    $("rep-body").innerHTML = `<div class="kpis2 br-stats">${summaryCards(s)}</div><p class="muted small">Compared with the ${delta}.</p>` +
      groupTable("By strategy", r.by_strategy) + groupTable("By session", r.by_session) + groupTable("By market", r.by_market) +
      (r.observations.length ? `<div class="sec-h">Observations this ${repPeriod}</div>${obsHtml(r.observations)}` : "");
  } catch (e) { $("rep-body").innerHTML = `<p class="muted">${P.esc(e.message)}</p>`; }
}
async function loadInsights() {
  try {
    const j = await P.api("/api/journal/analysis?" + filters());
    const a = j.analysis;
    const scope = filters().toString() ? " (filtered: same filters as the trade list)" : "";
    $("ins-body").innerHTML = `<p class="muted small">${a.closed_trades} closed trades analysed${scope}. ${P.esc(a.note)}</p>` +
      (a.items.length ? obsHtml(a.items) : `<p>No observations yet. Each check needs a minimum number of your own trades before it says anything.</p>`) +
      (a.not_enough_data.length ? `<div class="sec-h">Waiting for more data</div><ul class="obs wait">${a.not_enough_data.map(n => `<li>${P.esc(n.check)}: ${n.have} of ${n.need} needed</li>`).join("")}</ul>` : "") +
      groupTable("By strategy", j.by_strategy) + groupTable("By session", j.by_session);
  } catch (e) { $("ins-body").innerHTML = `<p class="muted">${P.esc(e.message)}</p>`; }
}

async function loadStrategies() {
  try {
    const j = await P.api("/api/library/strategies");
    $("j-strategy").innerHTML = '<option value="">None</option>' + (j.strategies || []).map(s => `<option value="${s.id}">${P.esc(s.name)}</option>`).join("");
  } catch (_) {}
}
document.addEventListener("chronos:auth", e => {
  const u = e.detail && e.detail.user;
  $("jn-gate").hidden = !!u; $("jn-form").hidden = !u;
  document.querySelector(".page-journal .results").hidden = !u;
  if (u) { resetForm(); loadStrategies(); refresh(); }
});
})();
