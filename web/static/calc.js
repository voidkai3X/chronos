// CHRONOS trade risk calculator. Pure maths (exported for tests) + the page wiring. Runs in the browser only;
// nothing is sent anywhere except an optional last-price lookup.
(function (root) {
"use strict";
// contract assumptions per instrument type (shown on the page; brokers and venues can differ)
const SPECS = {
  crypto: { label: "Crypto perpetual", unit: "coin", lot: 1, note: "Linear USD(T)-margined perpetual: 1 unit = 1 coin. P/L = units × price move." },
  forex:  { label: "Forex", unit: "unit", lot: 100000, note: "1 standard lot = 100,000 units of the base currency. Pip = 0.0001 (0.01 for JPY pairs)." },
  xau:    { label: "Gold (XAU)", unit: "oz", lot: 100, note: "1 lot = 100 troy ounces, quoted in USD." },
  xag:    { label: "Silver (XAG)", unit: "oz", lot: 5000, note: "1 lot = 5,000 troy ounces, quoted in USD." },
  index:  { label: "Index", unit: "unit", lot: 1, note: "1 unit = 1 × index points in the quote currency (CFD/perp style)." },
  stock:  { label: "Stock", unit: "share", lot: 1, note: "1 unit = 1 share (or 1 perp unit) in the quote currency." },
};
// MFP standard commission per fill, % of notional (same table as the backtester)
const FEE_PCT = { crypto: 0.03, forex: 0.0025, xau: 0.005, xag: 0.005, index: 0.005, stock: 0.005 };

function pipSize(symbol) { return /JPY/i.test(symbol || "") ? 0.01 : 0.0001; }

/** Quote-currency-to-account (USD) rate for an FX pair: 1 when quoted in USD, 1/price when USD is the base,
 *  otherwise the user's own rate is required (null when missing). */
function fxQuoteToUsd(symbol, price, manual) {
  const s = String(symbol || "").toUpperCase().replace(/[^A-Z]/g, "");
  if (s.length >= 6) {
    const base = s.slice(0, 3), quote = s.slice(3, 6);
    if (quote === "USD") return 1;
    if (base === "USD" && price > 0) return 1 / price;
  }
  return manual > 0 ? manual : null;
}

/** Core sizing. in: {type, symbol, account, riskPct | riskAmount, entry, sl, tp, quoteToUsd, includeFees}
 *  out: numbers, or {error} when inputs are not usable. */
function calc(i) {
  const t = SPECS[i.type] ? i.type : "crypto";
  const entry = +i.entry, sl = +i.sl, tp = i.tp === "" || i.tp == null ? null : +i.tp;
  if (!(entry > 0) || !(sl > 0)) return { error: "Enter an entry and a stop-loss above 0." };
  if (entry === sl) return { error: "The stop-loss must differ from the entry." };
  const side = sl < entry ? "long" : "short";
  if (tp != null && (tp <= 0 || (side === "long" ? tp <= entry : tp >= entry)))
    return { error: side === "long" ? "For a long (stop below entry) the target must be above the entry." : "For a short (stop above entry) the target must be below the entry." };
  const account = +i.account;
  let risk = i.riskAmount != null && i.riskAmount !== "" ? +i.riskAmount : (account > 0 && +i.riskPct > 0 ? account * +i.riskPct / 100 : NaN);
  if (!(risk > 0)) return { error: "Enter your account size and risk %, or a fixed amount to risk." };
  let q2u = 1;
  if (t === "forex") {
    q2u = fxQuoteToUsd(i.symbol, entry, +i.quoteToUsd);
    if (q2u == null) return { error: "This pair is not quoted in USD. Enter the quote-currency to USD rate." };
  } else if (+i.quoteToUsd > 0) q2u = +i.quoteToUsd;
  const dist = Math.abs(entry - sl);
  const lossPerUnit = dist * q2u;                       // account currency per unit
  const feePct = i.includeFees === false ? 0 : (i.feePct != null ? +i.feePct : FEE_PCT[t]);
  const exitAtSl = sl;
  const feePerUnit = feePct / 100 * (entry + exitAtSl) * q2u;
  const units = risk / (lossPerUnit + feePerUnit);      // fees are counted inside the risk budget
  const spec = SPECS[t];
  const notional = units * entry * q2u;
  const out = {
    side, type: t, riskAmount: risk, riskPct: account > 0 ? risk / account * 100 : null,
    slDistance: dist, slPct: dist / entry * 100, units, lots: spec.lot > 1 ? units / spec.lot : null,
    notional, leverage: account > 0 ? notional / account : null, fees: units * feePerUnit, feePct,
    potentialLoss: units * (lossPerUnit + feePerUnit), quoteToUsd: q2u,
  };
  if (t === "forex") {
    const ps = pipSize(i.symbol);
    out.pipSize = ps; out.slPips = dist / ps; out.pipValuePerLot = spec.lot * ps * q2u;
  }
  if (tp != null) {
    const gain = Math.abs(tp - entry) * q2u;
    const feeTp = feePct / 100 * (entry + tp) * q2u;
    out.potentialProfit = units * (gain - feeTp);
    out.rr = Math.abs(tp - entry) / dist;
    out.rrNet = out.potentialProfit / out.potentialLoss;
    if (t === "forex") out.tpPips = Math.abs(tp - entry) / out.pipSize;
  }
  return out;
}

const api = { SPECS, FEE_PCT, calc, pipSize, fxQuoteToUsd };
if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
root.CHRONOS_CALC = api;

// ---------------------------------------------------------------- page
const $ = id => document.getElementById(id);
if (!$("calc-form")) return;
let type = "crypto";
const nf = (v, d) => v == null || !isFinite(v) ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d == null ? 2 : d, minimumFractionDigits: d == null ? 2 : 0 });
const sig = v => v == null || !isFinite(v) ? "-" : (Math.abs(v) >= 1000 ? nf(v, 2) : Number(v.toPrecision(5)).toLocaleString("en-US", { maximumFractionDigits: 8 }));
function read() {
  const v = id => $(id).value.trim();
  return { type, symbol: v("c-sym"), account: v("c-acct"), riskPct: v("c-riskpct"), riskAmount: $("c-mode").value === "amount" ? v("c-riskamt") : null,
    entry: v("c-entry"), sl: v("c-sl"), tp: v("c-tp"), quoteToUsd: v("c-q2u"), includeFees: $("c-fees").checked };
}
function card(k, v, d, cls) { return `<div class="card"><div class="k">${k}</div><div class="v ${cls || ""}">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`; }
function update() {
  const i = read(), spec = SPECS[type];
  $("c-spec").textContent = spec.note;
  $("c-pct-wrap").hidden = $("c-mode").value === "amount";
  $("c-amt-wrap").hidden = $("c-mode").value !== "amount";
  const fx = type === "forex";
  const q = fx ? fxQuoteToUsd(i.symbol, +i.entry, 0) : 1;
  $("c-q2u-wrap").hidden = fx ? q != null : !["index", "stock"].includes(type);
  $("c-q2u-lbl").textContent = fx ? "Quote currency to USD rate" : "Quote currency to USD rate (leave empty if quoted in USD)";
  try { localStorage.setItem("chronos.calc", JSON.stringify(Object.assign({}, i, { mode: $("c-mode").value }))); } catch (_) {}
  const r = calc(i);
  const out = $("c-out"), err = $("c-err");
  if (r.error) { err.textContent = r.error; err.hidden = !(i.entry && i.sl); out.innerHTML = ""; $("c-sum").textContent = "Enter your levels to see the position size."; return; }
  err.hidden = true;
  const unit = spec.unit + (r.units === 1 ? "" : "s");
  $("c-sum").innerHTML = `<b class="${r.side === "long" ? "side-long" : "side-short"}">${r.side.toUpperCase()}</b> ${sig(r.units)} ${unit}${r.lots != null ? ` (${nf(r.lots, 2)} lots)` : ""} risks <b>${nf(r.potentialLoss)}</b>` +
    (r.potentialProfit != null ? ` to make <b class="pos">${nf(r.potentialProfit)}</b>` : "") + ".";
  out.innerHTML = [
    card("Position size", `${sig(r.units)} ${unit}`, r.lots != null ? `${nf(r.lots, 2)} lots of ${nf(spec.lot, 0)} ${spec.unit}s` : spec.label),
    card("Risk", nf(r.riskAmount), r.riskPct != null ? `${nf(r.riskPct, 2)}% of the account` : "fixed amount"),
    card("Stop distance", sig(r.slDistance), `${nf(r.slPct, 3)}% of entry${r.slPips != null ? ` · ${nf(r.slPips, 1)} pips` : ""}`),
    card("Potential loss", "-" + nf(r.potentialLoss), r.feePct ? `includes about ${nf(r.fees)} fees (${r.feePct}% per fill)` : "fees not included", "neg"),
    card("Potential profit", r.potentialProfit != null ? "+" + nf(r.potentialProfit) : "-", r.potentialProfit != null ? (r.feePct ? "after fees at the target" : "before fees") : "add a take-profit", r.potentialProfit != null ? "pos" : ""),
    card("Risk : reward", r.rr != null ? `1 : ${nf(r.rr, 2)}` : "-", r.rrNet != null ? `1 : ${nf(r.rrNet, 2)} after fees` : "add a take-profit"),
    card("Position value", nf(r.notional), r.leverage != null ? `${nf(r.leverage, 2)}× your account (check your margin limits)` : ""),
    ...(r.pipValuePerLot != null ? [card("Pip value", nf(r.pipValuePerLot), `per standard lot, in USD${r.quoteToUsd !== 1 ? ` (rate ${sig(r.quoteToUsd)})` : ""}`)] : []),
  ].join("");
}
$("c-type").addEventListener("click", e => {
  const b = e.target.closest("button[data-t]"); if (!b) return;
  type = b.dataset.t;
  [...$("c-type").querySelectorAll("button")].forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
  update();
});
$("calc-form").addEventListener("input", update);
$("calc-form").addEventListener("change", update);
$("calc-form").addEventListener("submit", e => e.preventDefault());
$("c-price").addEventListener("click", async () => {
  const sym = $("c-sym").value.trim(); if (!sym) { $("c-sym").focus(); return; }
  const btn = $("c-price"); btn.disabled = true;
  try {
    const m = await (await fetch("/api/markets?q=" + encodeURIComponent(sym))).json();
    const hit = (m.markets || m || [])[0];
    if (!hit) throw new Error("No MFP market matches " + sym);
    const p = await (await fetch("/api/price?market=" + encodeURIComponent(hit.market_id))).json();
    if (p.error || p.price == null) throw new Error(p.error || "No price available");
    $("c-entry").value = p.price;
    $("c-price-note").textContent = `Last closed 15m price for ${hit.market_id}: ${p.price}. Prices move; use your real entry.`;
    update();
  } catch (e) { $("c-price-note").textContent = e.message; }
  finally { btn.disabled = false; }
});
try {
  const s = JSON.parse(localStorage.getItem("chronos.calc") || "null");
  if (s) {
    ["sym", "acct", "riskpct", "entry", "sl", "tp", "q2u"].forEach(k => { const v = s[{ sym: "symbol", acct: "account", riskpct: "riskPct", q2u: "quoteToUsd" }[k] || k]; if (v != null && v !== "") $("c-" + k).value = v; });
    if (s.riskAmount) $("c-riskamt").value = s.riskAmount;
    if (s.mode) $("c-mode").value = s.mode;
    if (s.includeFees === false) $("c-fees").checked = false;
    if (SPECS[s.type]) { const b = $("c-type").querySelector(`[data-t="${s.type}"]`); if (b) b.click(); }
  }
} catch (_) {}
update();
})(typeof window !== "undefined" ? window : globalThis);
