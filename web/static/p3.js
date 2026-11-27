// CHRONOS Phase 3: Edge Score panel, advanced analytics, and the personal strategy library.
(() => {
const C = window.CHRONOS, $ = C.$, esc = C.esc, sgnR = C.sgnR, pct = C.pct, wat = C.wat;
const AUTH = () => window.CHRONOS_AUTH || {};
const TF_LBL = { "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "Daily" };
const BAND_SUM = {
  none: "No evidence of an edge in this test.",
  weak: "Weak: some positive signs, but far too uncertain to rely on.",
  promising: "Promising, not proven: positive after costs, but chance can't be ruled out yet.",
  strong: "Strong on this sample: positive after costs, consistent, and unlikely to be chance alone. Past results only.",
};
const r2 = x => x == null ? "-" : Number(x).toFixed(2);

// ---------------------------------------------------------------- Edge Score
function renderEdge(o) {
  const e = o.edge, panel = $("edge-panel");
  panel.hidden = !e;
  if (!e) return;
  $("edge-score").textContent = e.score;
  $("edge-band").className = "edge-band band-" + e.band;
  $("edge-band").textContent = e.label;
  $("edge-sum").textContent = BAND_SUM[e.band] + ` Based on ${e.trades} closed trade${e.trades === 1 ? "" : "s"} after fees, swap and slippage.`;
  $("edge-mark").style.left = Math.max(0, Math.min(100, e.score)) + "%";
  $("edge-caps").hidden = !e.capped;
  $("edge-caps").textContent = e.capped ? `The points add up to ${e.raw}, but the score is capped at ${Math.min(...e.caps.map(c => c.cap))} because ` +
    e.caps.map(c => c.reason).join("; ") + "." : "";
  $("edge-tbl").querySelector("tbody").innerHTML = e.components.map(c => `<tr>
    <td><b>${esc(c.name)}</b><div class="muted small edge-det">${esc(c.detail)}</div></td>
    <td>${esc(c.value)}</td><td class="r">${c.weight}</td>
    <td class="r"><b>${c.points}</b><div class="edge-pbar" aria-hidden="true"><i style="width:${(c.fraction * 100).toFixed(0)}%"></i></div></td></tr>`).join("");
  $("edge-tbl").querySelector("tfoot").innerHTML = `<tr><td colspan="3">Total before caps</td><td class="r">${e.raw}</td></tr>` +
    `<tr><td colspan="3"><b>Edge Score</b>${e.capped ? " (after caps)" : ""}</td><td class="r"><b>${e.score}</b></td></tr>`;
  $("edge-rules").innerHTML = [
    "Each component scores 0 to 1, multiplied by its weight; the weights add up to 100.",
    "Win rate on its own earns nothing: a high win rate with small wins and big losses has a negative average R and scores low.",
    "Caps: under 15 closed trades or an average R of zero or less, at most 15. Under 30 trades, at most 40. If the 90% bootstrap range of average R includes zero, or the edge disappears when costs are doubled, at most 60.",
    "Bands: 0-19 No evidence, 20-44 Weak, 45-69 Promising, 70-100 Strong.",
    "The engine verdict above uses the same score: Promising reads as 'possible edge, not proven', Strong as 'edge on this sample'.",
  ].map(x => `<li>${esc(x)}</li>`).join("");
  $("edge-disc").textContent = e.disclaimer;
}

// ---------------------------------------------------------------- advanced analytics
function renderAdv(o) {
  const a = o.analytics, panel = $("adv-panel");
  panel.hidden = !a || (!a.walk_forward && !a.bootstrap && !a.monte_carlo);
  if (panel.hidden) return;
  const wf = a.walk_forward;
  const wfT = $("adv-wf");
  if (wf) {
    const I = wf.in_sample, O = wf.out_of_sample;
    const span = b => b.from_ms ? `${wat(b.from_ms)} to ${wat(b.to_ms)}` : "";
    wfT.querySelector("thead").innerHTML = `<tr><th>Metric</th><th class="r">Earlier ${Math.round(wf.split * 100)}%<br><span class="muted">${span(I)}</span></th><th class="r">Later ${100 - Math.round(wf.split * 100)}% (out of sample)<br><span class="muted">${span(O)}</span></th></tr>`;
    const row = (k, f) => `<tr><td>${k}</td><td class="r">${f(I)}</td><td class="r">${f(O)}</td></tr>`;
    const cls = x => x > 0 ? "pos" : x < 0 ? "neg" : "";
    wfT.querySelector("tbody").innerHTML = [
      row("Closed trades", b => b.trades), row("Win rate", b => pct(b.win_rate)),
      row("Expectancy", b => `<span class="${cls(b.expectancy_r)}">${sgnR(b.expectancy_r)}</span>`),
      row("Profit factor", b => b.profit_factor_infinite ? "no losses" : r2(b.profit_factor)),
      row("Net R", b => sgnR(b.total_r)), row("Max drawdown", b => r2(b.max_drawdown_r) + "R"), row("t-stat", b => r2(b.t_stat))].join("");
    $("adv-wf-note").textContent = (wf.held_up ? "Both parts were positive after costs. " : wf.faded ? "The earlier trades were positive but the later ones were not: the result did not hold up out of sample. " :
      "The earlier part was not positive after costs. ") + "Nothing was fitted on the earlier part: CHRONOS uses your levels as given, so this shows whether later trades behaved like earlier ones. " + (wf.note || "");
  } else {
    wfT.querySelector("thead").innerHTML = ""; wfT.querySelector("tbody").innerHTML = "";
    $("adv-wf-note").textContent = "Fewer than 10 closed trades: too few to split.";
  }
  const b = a.bootstrap;
  $("adv-boot").innerHTML = b ? `
    <div class="adv-kv"><span>Expectancy, 90% range</span><b>${sgnR(b.expectancy.low)} to ${sgnR(b.expectancy.high)}</b><i class="muted">actual ${sgnR(b.expectancy.actual)}</i></div>
    <div class="adv-kv"><span>Win rate, 90% range</span><b>${pct(b.win_rate.low)} to ${pct(b.win_rate.high)}</b><i class="muted">actual ${pct(b.win_rate.actual)}</i></div>
    <div class="adv-kv"><span>Resamples with a positive average</span><b>${pct(b.prob_avg_above_zero)}</b><i class="muted">of ${b.samples.toLocaleString("en-US")}</i></div>` :
    `<p class="muted small">Needs at least 2 closed trades.</p>`;
  const m = a.monte_carlo;
  $("adv-mc").innerHTML = m ? `
    <div class="adv-kv"><span>In the real order</span><b>${r2(m.actual)}R</b></div>
    <div class="adv-kv"><span>Median of ${m.sims.toLocaleString("en-US")} random orders</span><b>${r2(m.median)}R</b></div>
    <div class="adv-kv"><span>95th percentile</span><b>${r2(m.p95)}R</b><i class="muted">1 in 20 orders was worse</i></div>
    <div class="adv-kv"><span>Worst order found</span><b>${r2(m.worst)}R</b></div>` : `<p class="muted small">Needs at least 2 closed trades.</p>`;
  $("adv-def").innerHTML = [
    "Earlier vs later: the closed trades in time order, split two-thirds / one-third. The later part is out of sample for anyone who chose the setup by looking at the earlier part.",
    "Bootstrap: the real trades are drawn at random with replacement 2,000 times; the 90% range is where the middle 90% of those averages fall. A range that includes zero means chance can't be ruled out.",
    "Monte Carlo drawdown: the same trades shuffled into 1,000 random orders. The real order is only one of many that could have happened; the 95th percentile is a more cautious drawdown to plan for.",
    "All three use only this test's closed trades after costs. Nothing is simulated beyond reordering or resampling those trades.",
  ].map(x => `<li>${esc(x)}</li>`).join("");
}
document.addEventListener("chronos:result", e => {
  renderEdge(e.detail); renderAdv(e.detail);
  const lr = e.detail.library_run;
  if (lr) C.toast(lr.saved ? `Saved to My strategies as run #${lr.id}.` : (lr.error || "This run was not saved."), "");
});

// ---------------------------------------------------------------- strategy library
let types = null, lib = [], libLoaded = false, editing = null, histFor = null, runOverride = null, pick = [];
const TYPE_ORDER = [["Your levels", ["similar", "every_close"]], ["Rule-based", ["breakout", "ema_cross", "liquidity_sweep", "fvg"]],
  ["Zone rules (approximations)", ["zone_retest", "zone_sweep", "zone_bos"]]];
const PARAM_LBL = { fast: "Fast EMA", slow: "Slow EMA", stop_atr: "Stop (ATR)", lookback: "Lookback candles", min_gap_atr: "Min gap (ATR)", valid_bars: "Order valid (candles)" };
const MODE_OF = { similar: ["similar"], every_close: ["every-close"], zone_retest: ["pattern", "limit"], zone_sweep: ["pattern", "sweep"],
  zone_bos: ["pattern", "confirm"] };
const signedIn = () => !!AUTH().user;

async function api(method, url, body) {
  const h = { "X-CSRF-Token": AUTH().csrf || "" };
  if (body !== undefined) h["Content-Type"] = "application/json";
  let r;
  try { r = await fetch(url, { method, headers: h, credentials: "same-origin", cache: "no-store", body: body !== undefined ? JSON.stringify(body) : undefined }); }
  catch (e) { throw new Error("Could not reach CHRONOS. Check your connection and try again."); }
  const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
  if (!r.ok) throw Object.assign(new Error(j.error || ("HTTP " + r.status)), { status: r.status });
  return j;
}
async function loadTypes() {
  if (types) return types;
  const j = await (await fetch("/api/strategy-types")).json();
  types = j.types;
  return types;
}
function typeLabel(t) { return types && types[t] ? types[t].label : t; }
function mktLabel(id) { const m = C.findMarket(id); return m ? m.symbol : String(id).split("|").pop(); }

function libView() { return document.body.classList.contains("view-lib"); }
function renderSide() {
  $("lib-side").hidden = !libView();
  if (!libView()) return;
  const ready = AUTH().ready;
  $("lib-signin").hidden = !ready || signedIn();
  $("lib-actions").hidden = !signedIn();
  const dev = window.P2 ? window.P2.deviceRows() : [];
  $("lib-import").hidden = !signedIn() || !dev.length;
  $("lib-import").textContent = `Move ${dev.length} device-saved comparison strateg${dev.length === 1 ? "y" : "ies"} into My strategies`;
}
async function refresh() {
  renderSide();
  if (!libView()) return;
  if (!AUTH().ready) return;
  if (!signedIn()) {
    $("lib-sub").textContent = ""; $("lib-list").innerHTML = ""; $("lib-cmp").hidden = true; $("lib-hist").hidden = true;
    $("lib-msg").textContent = "Sign in to see your saved strategies. Testing and comparing work without an account.";
    return;
  }
  $("lib-msg").textContent = "Loading...";
  try {
    await loadTypes();
    const j = await api("GET", "/api/library/strategies");
    lib = j.strategies; libLoaded = true;
    $("lib-count").textContent = `${lib.length} of ${j.limit} strategies saved. Up to ${j.run_limit} runs are kept for each.`;
    $("lib-sub").textContent = `${lib.length} saved`;
    drawList();
  } catch (e) { $("lib-msg").textContent = e.message; }
}
function scoreChip(r) {
  const m = r && r.metrics;
  if (!m || m.edge_score == null) return `<span class="lib-chip">not run yet</span>`;
  return `<span class="lib-chip band-${esc(m.edge_band || "none")}">${m.edge_score}/100 ${esc(m.edge_label || "")}</span>`;
}
function drawList() {
  $("lib-msg").textContent = lib.length ? "" : "No saved strategies yet. Use New strategy, or Save as strategy under the test form.";
  pick = pick.filter(id => lib.some(s => s.id === id));
  $("lib-list").innerHTML = lib.map(s => {
    const sp = s.spec, last = s.last_run || {}, m = last.metrics || {};
    const rules = Object.entries(sp.rule_params || {}).map(([k, v]) => `${PARAM_LBL[k] || k} ${v}`).join(", ");
    return `<div class="lib-item" data-id="${s.id}">
      <div class="lib-top">
        <label class="chk mini lib-pick"><input type="checkbox" data-pick="${s.id}" ${pick.includes(s.id) ? "checked" : ""}><span class="box" aria-hidden="true"></span><span class="lib-sr">Select to compare</span></label>
        <div class="lib-name"><b>${esc(s.name)}</b><div class="muted small">${esc(typeLabel(sp.setup_type))}${sp.pattern_label && sp.pattern_label !== typeLabel(sp.setup_type) ? " · " + esc(sp.pattern_label) : ""}${rules ? " · " + esc(rules) : ""}</div></div>
        ${scoreChip(last)}
      </div>
      ${s.description ? `<p class="lib-desc">${esc(s.description)}</p>` : ""}
      <div class="lib-meta">
        <span>${sp.side === "short" ? "Short" : "Long"}</span><span>${sp.markets.map(mktLabel).map(esc).join(", ")}</span>
        <span>${TF_LBL[sp.tf] || sp.tf}${sp.htf_confirm !== "off" ? " + " + (TF_LBL[sp.htf_confirm] || sp.htf_confirm) + " filter" : ""}</span>
        <span>stop ${+(+sp.stop_pct).toFixed(2)}% · 1:${sp.rr}</span><span>expiry ${sp.expiry} · time stop ${sp.max_hold}</span>
        <span>${sp.days}d · slippage ${sp.slippage} ${sp.slippage_unit}</span>
      </div>
      ${last.created_at ? `<p class="muted small lib-last">Last run ${wat(Date.parse(last.created_at))} WAT on ${esc(mktLabel(last.market || ""))}: ${m.closed} trades, expectancy ${sgnR(m.expectancy_r)}, ${s.runs} run${s.runs === 1 ? "" : "s"} saved.</p>` : ""}
      <div class="lib-btns">
        <button type="button" class="btn-ghost sm" data-act="run">Run backtest</button>
        <button type="button" class="btn-ghost sm" data-act="hist" ${s.runs ? "" : "disabled"}>History</button>
        <button type="button" class="btn-ghost sm" data-act="edit">Edit</button>
        <button type="button" class="btn-ghost sm" data-act="dup">Duplicate</button>
        <button type="button" class="btn-ghost sm lib-del" data-act="del">Delete</button>
      </div></div>`;
  }).join("");
  $("lib-cmp").hidden = lib.length < 2;
  syncPick();
}
function syncPick() {
  $("lib-cmp-run").disabled = pick.length < 2 || pick.length > 6;
  $("lib-cmp-run").textContent = pick.length ? `Compare selected (${pick.length})` : "Compare selected";
}
$("lib-list").addEventListener("change", e => {
  const id = +e.target.dataset.pick; if (!id) return;
  if (e.target.checked) { if (pick.length >= 6) { e.target.checked = false; C.toast("Up to 6 strategies can be compared at once.", ""); return; } pick.push(id); }
  else pick = pick.filter(x => x !== id);
  syncPick();
});
$("lib-list").addEventListener("click", async e => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const s = lib.find(x => x.id === +b.closest(".lib-item").dataset.id); if (!s) return;
  const act = b.dataset.act;
  if (act === "edit") return openEditor(s);
  if (act === "run") return runSaved(s);
  if (act === "hist") return showHistory(s);
  b.disabled = true;
  try {
    if (act === "dup") { await api("POST", `/api/library/strategies/${s.id}/duplicate`); C.toast(`Duplicated "${s.name}".`, ""); }
    if (act === "del") {
      if (!window.confirm(`Delete "${s.name}" and its ${s.runs} saved run${s.runs === 1 ? "" : "s"}? This cannot be undone.`)) { b.disabled = false; return; }
      await api("DELETE", `/api/library/strategies/${s.id}`);
      if (histFor === s.id) { $("lib-hist").hidden = true; histFor = null; }
      C.toast(`Deleted "${s.name}".`, "");
    }
    await refresh();
  } catch (err) { C.toast(err.message, "Couldn't do that."); b.disabled = false; }
});

// ---------- levels: saved as distances, turned into prices at the latest close when a strategy runs
function levelsFrom(spec, price) {
  const sg = spec.side === "short" ? -1 : 1;
  const entry = price * (1 + spec.entry_offset_pct / 100);
  const risk = entry * spec.stop_pct / 100;
  const dec = price >= 1000 ? 2 : price >= 1 ? 5 : 8;
  const rnd = x => +x.toFixed(dec);
  return { entry: rnd(entry), sl: rnd(entry - sg * risk), tp: rnd(entry + sg * spec.rr * risk) };
}
function specFromForm() {
  const price = C.getRefPrice(), side = C.getSide();
  const e = parseFloat($("entry").value), t = parseFloat($("tp").value), s = parseFloat($("sl").value);
  const mk = C.getMarket(), le = C.levelError();
  if (!mk) throw new Error("Pick a market first.");
  if (le === null) throw new Error("Enter entry, TP and SL first.");
  if (le) throw new Error(le);
  if (!price) throw new Error("Wait for the current price to load, then try again.");
  const mode = C.getMode();
  const typ = mode === "similar" ? "similar" : mode === "every-close" ? "every_close" :
    ({ limit: "zone_retest", sweep: "zone_sweep", confirm: "zone_bos" })[$("entry-style").value] || "zone_retest";
  return { setup_type: typ, rule_params: {}, pattern_label: (($("shot-pattern") && $("shot-pattern").value) || "").trim().slice(0, 80),
    side, markets: [mk], tf: C.getTf(), htf_confirm: $("htf").value, trend_filter: $("trend").checked,
    entry_offset_pct: +((e / price - 1) * 100).toFixed(4), stop_pct: +(Math.abs(e - s) / e * 100).toFixed(4),
    rr: +(Math.abs(t - e) / Math.abs(e - s)).toFixed(4), expiry: $("expiry").value, max_hold: $("max-hold").value,
    days: +$("days").value, slippage: $("slip").value === "" ? 0 : +$("slip").value, slippage_unit: $("slip-unit").value,
    allow_overlap: mode !== "pattern" && $("overlap").checked, entry_conditions: "", exit_conditions: "" };
}
async function runSaved(s) {
  const sp = s.spec;
  window.P2.setView("test");
  const price = await C.pickMarket(sp.markets[0]);
  if (!price) return C.toast("The current price for " + mktLabel(sp.markets[0]) + " could not be loaded. Try again in a minute.", "Couldn't run.");
  C.setSide(sp.side);
  const mm = MODE_OF[sp.setup_type];
  C.setMode(mm ? mm[0] : "every-close");
  if (mm && mm[1]) $("entry-style").value = mm[1];
  C.setTf(sp.tf);
  const lv = levelsFrom(sp, price);
  $("entry").value = lv.entry; $("sl").value = lv.sl; $("tp").value = lv.tp;
  $("htf").value = sp.htf_confirm; $("trend").checked = sp.trend_filter;
  $("expiry").value = sp.expiry; $("max-hold").value = sp.max_hold;
  $("days").value = sp.days; $("days-v").textContent = sp.days;
  $("slip").value = sp.slippage; $("slip-unit").value = sp.slippage_unit;
  $("overlap").checked = !!sp.allow_overlap; if ($("tf-sweep")) $("tf-sweep").checked = false;
  C.updateRR();
  const t = types && types[sp.setup_type];
  runOverride = { strategy_id: s.id, mode: t && t.kind === "rule" ? "rule" : null, rule: sp.setup_type, rule_params: sp.rule_params,
    pattern_label: sp.pattern_label || s.name, name: s.name };
  $("lib-run-note").textContent = `Running saved strategy "${s.name}" at the latest close (${C.fmt(price)}). The result is saved to its history.`;
  if (sp.markets.length > 1) C.toast(`A saved strategy runs on its first market (${mktLabel(sp.markets[0])}). To test ${sp.markets.slice(1).map(mktLabel).join(" or ")}, move it to the front in Edit.`, "");
  C.run();
}

// ---------- editor
function fillParams(typ, params) {
  const t = types[typ], ps = (t && t.params) || {};
  $("le-params").innerHTML = Object.entries(ps).map(([k, dv]) => `<label><span>${PARAM_LBL[k] || k}</span>
    <input type="number" step="any" data-k="${k}" value="${params && params[k] != null ? params[k] : dv}" inputmode="decimal"></label>`).join("");
  $("le-type-note").innerHTML = t ? `<span class="st-kind k-${t.kind}">${t.kind === "rule" ? "Rule-based: CHRONOS detects it" : t.kind === "approx" ? "Approximation: closest CHRONOS rule" : "Your own levels"}</span> ${esc(t.note)}` : "";
}
async function openEditor(s, preset) {
  try { await loadTypes(); } catch (e) { return C.toast("Could not load the strategy types.", "Couldn't open the editor."); }
  editing = s ? s.id : null;
  const sp = (s && s.spec) || preset || {};
  $("le-title").textContent = s ? "Edit strategy" : "New strategy";
  $("le-type").innerHTML = TYPE_ORDER.map(([g, ks]) => `<optgroup label="${g}">${ks.filter(k => types[k]).map(k => `<option value="${k}">${esc(types[k].label)}</option>`).join("")}</optgroup>`).join("");
  $("le-name").value = s ? s.name : (preset && preset.name) || "";
  $("le-desc").value = s ? s.description : "";
  $("le-type").value = sp.setup_type || "similar";
  $("le-label").value = sp.pattern_label || "";
  $("le-side").value = sp.side || C.getSide();
  $("le-markets").value = (sp.markets || [C.getMarket()].filter(Boolean)).map(mktLabel).join(", ");
  $("le-tf").value = sp.tf || "1h"; $("le-htf").value = sp.htf_confirm || "off";
  $("le-entry").value = sp.entry_offset_pct ?? 0; $("le-stop").value = sp.stop_pct ?? 1; $("le-rr").value = sp.rr ?? 2;
  $("le-expiry").value = sp.expiry || "72h"; $("le-maxhold").value = sp.max_hold || "7d";
  $("le-days").value = sp.days || 90; $("le-slip").value = sp.slippage ?? 1; $("le-slip-unit").value = sp.slippage_unit || "bps";
  $("le-trend").checked = !!sp.trend_filter; $("le-overlap").checked = !!sp.allow_overlap;
  $("le-entry-notes").value = sp.entry_conditions || ""; $("le-exit-notes").value = sp.exit_conditions || "";
  fillParams($("le-type").value, sp.rule_params);
  $("le-err").hidden = true;
  $("le").hidden = false; document.body.classList.add("rp-open-body");
  $("le-name").focus();
}
function closeEditor() { $("le").hidden = true; document.body.classList.remove("rp-open-body"); editing = null; }
$("le-type").addEventListener("change", () => fillParams($("le-type").value, {}));
$("le-close").addEventListener("click", closeEditor);
$("le-cancel").addEventListener("click", closeEditor);
$("le").addEventListener("click", e => { if (e.target === $("le")) closeEditor(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("le").hidden) closeEditor(); });
$("le-form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = m => { $("le-err").hidden = false; $("le-err").textContent = m; };
  const names = $("le-markets").value.split(",").map(x => x.trim()).filter(Boolean);
  if (!names.length || names.length > 5) return err("List 1 to 5 markets.");
  const ids = [];
  for (const n of names) { const m = C.findMarket(n); if (!m) return err(`"${n}" is not an MFP market. Use the symbol, like BTC or EURUSD.`); ids.push(m.market_id); }
  const params = {};
  $("le-params").querySelectorAll("input[data-k]").forEach(i => { if (i.value !== "") params[i.dataset.k] = +i.value; });
  const body = { name: $("le-name").value.trim(), description: $("le-desc").value.trim(), setup_type: $("le-type").value,
    rule_params: params, pattern_label: $("le-label").value.trim(), side: $("le-side").value, markets: ids,
    tf: $("le-tf").value, htf_confirm: $("le-htf").value, trend_filter: $("le-trend").checked,
    entry_offset_pct: $("le-entry").value === "" ? 0 : +$("le-entry").value, stop_pct: +$("le-stop").value, rr: +$("le-rr").value,
    expiry: $("le-expiry").value, max_hold: $("le-maxhold").value, days: +$("le-days").value,
    slippage: $("le-slip").value === "" ? 0 : +$("le-slip").value, slippage_unit: $("le-slip-unit").value,
    allow_overlap: $("le-overlap").checked, entry_conditions: $("le-entry-notes").value.trim(), exit_conditions: $("le-exit-notes").value.trim() };
  if (!body.name) return err("Give the strategy a name.");
  $("le-save").disabled = true;
  try {
    if (editing) await api("PUT", `/api/library/strategies/${editing}`, body);
    else await api("POST", "/api/library/strategies", body);
    C.toast(`Saved "${body.name}" to My strategies.`, "");
    closeEditor();
    if (libView()) await refresh(); else libLoaded = false;
  } catch (x) { err(x.status === 401 ? "Your session ended. Sign in again to save." : x.message); }
  finally { $("le-save").disabled = false; }
});

$("lib-new").addEventListener("click", () => openEditor(null, null));
$("lib-save").addEventListener("click", () => {
  if (!AUTH().ready) return;
  if (!signedIn()) {
    $("lib-run-note").innerHTML = `Saving needs a free account. <a href="/signin?next=%2F">Sign in</a> or <a href="/signup?next=%2F">create one</a>; testing works without it.`;
    return;
  }
  let sp;
  try { sp = specFromForm(); } catch (e) { return C.showErr(e.message); }
  openEditor(null, Object.assign(sp, { name: "" }));
});
$("lib-import").addEventListener("click", async () => {
  const rows = window.P2.deviceRows();
  let base;
  try { base = specFromForm(); } catch (e) { return C.toast(e.message + " The market, side and levels in the test form are used for the imported strategies.", "Fill in the form first."); }
  await loadTypes();
  const items = rows.filter(r => types[r.type]).slice(0, 6).map(r => Object.assign({}, base, {
    name: ((r.name || "").trim() || types[r.type].label).slice(0, 80), setup_type: r.type,
    rule_params: Object.fromEntries(Object.entries(r.params || {}).filter(([, v]) => v != null && v !== "" && !isNaN(v))),
    allow_overlap: r.type.startsWith("zone_") ? false : base.allow_overlap }));
  if (!items.length) return;
  if (!window.confirm(`Save ${items.length} strateg${items.length === 1 ? "y" : "ies"} from this device into My strategies, using ${mktLabel(base.markets[0])}, ${base.side}, stop ${base.stop_pct}% and 1:${base.rr} from the test form?`)) return;
  try { const j = await api("POST", "/api/library/import", { strategies: items }); C.toast(`Moved ${j.imported} into My strategies. The device copy is kept for the comparison view.`, ""); await refresh(); }
  catch (e) { C.toast(e.message, "Couldn't import."); }
});
$("lib-cmp-run").addEventListener("click", async () => {
  const chosen = pick.map(id => lib.find(s => s.id === id)).filter(Boolean);
  if (chosen.length < 2) return;
  const first = chosen[0], sp = first.spec;
  window.P2.setView("test");
  const price = await C.pickMarket(sp.markets[0]);
  if (!price) return C.toast("The current price could not be loaded. Try again in a minute.", "Couldn't compare.");
  C.setSide(sp.side); C.setTf(sp.tf);
  const lv = levelsFrom(sp, price);
  $("entry").value = lv.entry; $("sl").value = lv.sl; $("tp").value = lv.tp;
  $("htf").value = sp.htf_confirm; $("expiry").value = sp.expiry; $("max-hold").value = sp.max_hold;
  $("days").value = sp.days; $("days-v").textContent = sp.days; $("slip").value = sp.slippage; $("slip-unit").value = sp.slippage_unit;
  C.updateRR();
  await window.P2.setRows(chosen.map(s => ({ name: s.name, type: s.spec.setup_type, params: s.spec.rule_params || {} })));
  window.P2.setView("cmp");
  C.toast(`Comparing ${chosen.length} saved strategies on ${mktLabel(sp.markets[0])} with the levels, timeframe and costs of "${first.name}".`, "");
  window.P2.runStrategies();
});

// ---------- run history
async function showHistory(s) {
  histFor = s.id;
  $("lib-hist").hidden = false;
  $("lib-hist-title").textContent = `Run history: ${s.name}`;
  $("lib-hist-sub").textContent = "loading...";
  try {
    const j = await api("GET", `/api/library/strategies/${s.id}/runs`);
    if (histFor !== s.id) return;
    drawHistory(j.runs);
    $("lib-hist").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { $("lib-hist-sub").textContent = e.message; }
}
let lastRuns = [];
function drawHistory(runs) {
  lastRuns = runs;
  $("lib-hist-sub").textContent = `${runs.length} run${runs.length === 1 ? "" : "s"}, newest first`;
  $("lib-runs").querySelector("tbody").innerHTML = runs.map(r => {
    const m = r.metrics || {}, p = r.params || {}, pv = r.provenance || {};
    return `<tr><td>${wat(Date.parse(r.created_at))}</td><td>${esc(mktLabel(r.market || ""))}</td>
      <td>${esc((p.mode || "") + " " + (TF_LBL[p.setup_tf] || p.setup_tf || ""))}${p.htf_confirm && p.htf_confirm !== "off" ? " +" + esc(p.htf_confirm) : ""}</td>
      <td class="r"><b>${m.edge_score ?? "-"}</b> <span class="muted small">${esc(m.edge_label || "")}</span></td>
      <td class="r">${m.closed ?? "-"}</td><td class="r">${pct(m.win_rate)}</td>
      <td class="r ${m.expectancy_r > 0 ? "pos" : m.expectancy_r < 0 ? "neg" : ""}">${sgnR(m.expectancy_r)}</td>
      <td class="r">${m.profit_factor_infinite ? "no losses" : r2(m.profit_factor)}</td><td class="r">${sgnR(m.total_r)}</td>
      <td class="r">${r2(m.max_drawdown_r)}R</td>
      <td class="muted small">${pv.days ?? "-"}d · ${esc((pv.sources || []).join(", "))}</td>
      <td><button type="button" class="btn-ghost sm" data-del-run="${r.id}" aria-label="Delete this run">Delete</button></td></tr>`;
  }).join("");
  drawTrend(runs.slice().reverse());
}
$("lib-runs").addEventListener("click", async e => {
  const b = e.target.closest("[data-del-run]"); if (!b) return;
  if (!window.confirm("Delete this saved run? This cannot be undone.")) return;
  try { await api("DELETE", `/api/library/runs/${b.dataset.delRun}`); const s = lib.find(x => x.id === histFor); await refresh(); if (s) showHistory(s); }
  catch (err) { C.toast(err.message, "Couldn't delete."); }
});
function drawTrend(runs) {
  const cv = $("lib-trend"), H = 170, [c, W] = C.sizeCanvas(cv, H);
  const L = 40, R = 44, T = 12, B = 22, w = W - L - R, h = H - T - B;
  c.font = "11px ui-monospace,Menlo,monospace";
  if (runs.length < 2) { c.fillStyle = "#8fa596"; c.fillText("The trend appears after a second run.", L, H / 2); return; }
  const n = runs.length, X = i => L + (n === 1 ? w / 2 : i * w / (n - 1));
  const sc = runs.map(r => r.metrics.edge_score ?? 0), ex = runs.map(r => r.metrics.expectancy_r ?? 0);
  const Ys = v => T + (100 - v) / 100 * h;
  let lo = Math.min(0, ...ex), hi = Math.max(0, ...ex); if (hi - lo < 0.1) { hi += 0.05; lo -= 0.05; }
  const Ye = v => T + (hi - v) / (hi - lo) * h;
  c.strokeStyle = "rgba(95,214,58,.08)"; c.lineWidth = 1;
  [0, 20, 45, 70, 100].forEach(v => { c.beginPath(); c.moveTo(L, Ys(v)); c.lineTo(W - R, Ys(v)); c.stroke(); c.fillStyle = "#8fa596"; c.fillText(String(v), 6, Ys(v) + 4); });
  c.fillStyle = "#8fa596"; c.fillText(sgnR(hi), W - R + 4, Ye(hi) + 10); c.fillText(sgnR(lo), W - R + 4, Ye(lo) - 2);
  c.strokeStyle = "rgba(191,211,196,.25)"; c.setLineDash([2, 3]); c.beginPath(); c.moveTo(L, Ye(0)); c.lineTo(W - R, Ye(0)); c.stroke();
  c.strokeStyle = "rgba(191,211,196,.6)"; c.setLineDash([6, 4]); c.lineWidth = 1.5; c.beginPath();
  ex.forEach((v, i) => i ? c.lineTo(X(i), Ye(v)) : c.moveTo(X(i), Ye(v))); c.stroke(); c.setLineDash([]);
  c.strokeStyle = "#5fd63a"; c.lineWidth = 2; c.beginPath();
  sc.forEach((v, i) => i ? c.lineTo(X(i), Ys(v)) : c.moveTo(X(i), Ys(v))); c.stroke();
  c.fillStyle = "#5fd63a"; sc.forEach((v, i) => { c.beginPath(); c.arc(X(i), Ys(v), 2.5, 0, 7); c.fill(); });
  c.fillStyle = "#8fa596";
  const d0 = wat(Date.parse(runs[0].created_at)).slice(0, 6), d1 = wat(Date.parse(runs[n - 1].created_at)).slice(0, 6);
  c.fillText(d0, L, H - 6); c.fillText(d1, W - R - c.measureText(d1).width, H - 6);
}
let rzT = null;
window.addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => { if (!$("lib-hist").hidden && lastRuns.length) drawTrend(lastRuns.slice().reverse()); }, 150); });

$("f").addEventListener("input", () => { if (runOverride) { runOverride = null; $("lib-run-note").textContent = ""; } });
document.addEventListener("chronos:view", e => { if (e.detail === "lib") refresh(); else renderSide(); $("lib-save-row").hidden = e.detail === "cmp"; });
document.addEventListener("chronos:auth", () => { libLoaded = false; if (libView()) refresh(); else renderSide(); });
if (libView()) refresh();

window.P3 = { takeRunOverrides: () => { const o = runOverride; runOverride = null; if (!o) $("lib-run-note").textContent = ""; return o; },
  refresh, levelsFrom };
})();
