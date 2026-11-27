#!/usr/bin/env node
/* Blueprint phase 2 browser checks + screenshots: navigation (desktop + phone More sheet), Risk calculator,
   Blind Replay (real candles; checks every replay response never holds more candles than the cursor allows),
   Trading journal (form, list, calendar, reports, insights). Uses a throwaway account that is deleted at the end.
   Usage: node tests/bp_shots.cjs <base_url> [screenshot_dir] */
const { chromium, devices } = require("playwright");
const assert = require("assert");
const path = require("path");
const crypto = require("crypto");

(async () => {
  const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
  const dir = process.argv[3];
  const shot = async (page, name, full) => { if (dir) await page.screenshot({ path: path.join(dir, name), fullPage: !!full }); };
  const step = m => console.log("ok ", m);
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  const desk = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await desk.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const apiErrors = [];
  const watchApi = pg => pg.on("response", r => { if (/\/api\//.test(r.url()) && r.status() >= 400) apiErrors.push(`${r.status()} ${r.request().method()} ${r.url().replace(base, "")}`); });
  watchApi(page);
  const email = `bp-${crypto.randomBytes(5).toString("hex")}@example.com`, pw = "bp-" + crypto.randomBytes(12).toString("hex");
  let created = false;
  try {
    // ---------------- calculator (no account needed)
    await page.goto(base + "/calculator", { waitUntil: "networkidle" });
    assert(await page.isVisible('.nav [data-nav="calc"].on'), "calculator nav item is current");
    await page.fill("#c-acct", "10000"); await page.fill("#c-riskpct", "1");
    await page.fill("#c-entry", "100"); await page.fill("#c-sl", "99"); await page.fill("#c-tp", "103");
    await page.uncheck("#c-fees");
    await page.waitForFunction(() => /100\s+coins/.test(document.getElementById("c-sum").textContent));
    const sum = await page.textContent("#c-sum");
    assert(/LONG/.test(sum) && /100\.00/.test(sum) && /300\.00/.test(sum), "calc summary: " + sum);
    await page.check("#c-fees");
    await page.click('#c-type [data-t="forex"]');
    await page.fill("#c-sym", "EURUSD"); await page.fill("#c-entry", "1.0850"); await page.fill("#c-sl", "1.0830"); await page.fill("#c-tp", "1.0900");
    await page.waitForFunction(() => /lots/.test(document.getElementById("c-sum").textContent));
    assert(/\b20 pips/.test(await page.textContent("#c-out")), "forex pips shown");
    await shot(page, "bp-calc-desktop.png");
    step("risk calculator (crypto + forex)");

    // ---------------- account
    await page.goto(base + "/signup?next=/replay", { waitUntil: "networkidle" });
    await page.fill("#email", email); await page.fill("#password", pw);
    await Promise.all([page.waitForURL(/\/replay$/), page.click("#auth-submit")]);
    created = true;
    await page.waitForSelector("#rp-form:not([hidden])");
    step("signed up, back on /replay");

    // ---------------- replay
    const replayResp = [];
    page.on("response", async r => {
      if (!/\/api\/practice\/(sessions|active)/.test(r.url()) || r.request().method() === "OPTIONS") return;
      try { const j = await r.json(); if (j.session) replayResp.push(j.session); } catch (_) {}
    });
    await page.fill("#rp-market", "BTC");
    await page.click("#rp-start");
    await page.waitForSelector("#rp-live:not([hidden]), #rp-err:not([hidden])", { timeout: 240000 });
    if (await page.isVisible("#rp-err")) throw new Error("replay start failed: " + await page.textContent("#rp-err"));
    await page.waitForTimeout(600);
    let s = replayResp[replayResp.length - 1];
    assert(s && s.status === "active" && s.candles.length === s.cursor + 1, "first payload holds only revealed candles");
    assert(s.dates_hidden && s.sources.length === 0, "dates hidden, sources withheld");
    step(`session started: ${s.market_id} ${s.tf}, ${s.visible} candles visible, ${s.remaining} hidden`);
    const last = s.candles[s.cursor][4];
    await page.fill("#rp-sl", String(+(last * 0.99).toPrecision(8)));
    await page.fill("#rp-tp", String(+(last * 1.02).toPrecision(8)));
    await page.click("#rp-place");
    await page.waitForSelector("#rp-cancel:not([hidden])");
    await page.click("#rp-next");
    await page.waitForFunction(() => !document.getElementById("rp-close").hidden || !document.getElementById("rp-place").hidden, null, { timeout: 30000 });
    for (let i = 0; i < 6; i++) { if (await page.isVisible("#rp-close")) { await page.click("#rp-next"); await page.waitForTimeout(250); } }
    if (await page.isVisible("#rp-close")) await page.click("#rp-close");
    await page.waitForSelector("#rp-place:not([hidden])");
    await page.click("#rp-skip"); await page.waitForTimeout(300);
    await page.selectOption("#rp-speed", "125");
    await page.click("#rp-play"); await page.waitForTimeout(1500); await page.click("#rp-play");
    await page.waitForTimeout(500);
    for (const x of replayResp.filter(x => x.status === "active")) assert.strictEqual(x.candles.length, x.cursor + 1, "a replay payload had more candles than revealed");
    assert(replayResp.every(x => x.status !== "active" || x.candles.every(c => c[0] < s.candles[0][0] + 400 * 86400000)), "times stay shifted while active");
    const rows = await page.$$eval("#rp-trades tr", t => t.length);
    assert(rows >= 1);
    await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(200);
    await shot(page, "bp-replay-desktop.png");
    await page.locator("#rp-history").scrollIntoViewIfNeeded();
    await page.waitForFunction(() => /practice trades/i.test(document.getElementById("rp-agg").textContent), null, { timeout: 15000 }).catch(async e => { console.log("AGG:", await page.textContent("#rp-agg")); await page.screenshot({ path: "/tmp/agg.png", fullPage: true }); throw e; });
    await shot(page, "bp-replay-stats-desktop.png", true);
    step(`replay: order, fill, close, skip, play; ${replayResp.length} payloads checked for future candles`);

    // ---------------- journal
    await page.goto(base + "/journal", { waitUntil: "networkidle" });
    await page.waitForSelector("#jn-form:not([hidden])");
    const now = Date.now();
    const seed = [];  // a handful of example entries for this throwaway test account (local test database only)
    const mk = (d, h, side, e, sl, tp, x, sess, er, moved, strat) => ({ market: d % 3 ? "BTCUSDT" : "ETHUSDT", side, entry: e, sl, tp, exit_price: x, size: 1,
      opened_at: new Date(now - d * 864e5 - h * 36e5).toISOString(), closed_at: new Date(now - d * 864e5 - h * 36e5 + 2 * 36e5).toISOString(), session: sess, exit_reason: er, sl_moved: moved, strategy_name: strat });
    for (let i = 0; i < 14; i++) seed.push(mk(i + 1, 2, i % 2 ? "short" : "long", 100, i % 2 ? 101 : 99, i % 2 ? 97 : 103, i % 3 === 0 ? (i % 2 ? 101 : 99) : (i % 2 ? 98.6 : 101.4), i % 2 ? "London" : "New York", i % 3 === 0 ? "sl" : "manual", i % 4 === 0, ""));
    const ok = await page.evaluate(async rows => {
      const me = await (await fetch("/api/auth/me")).json();
      let n = 0;
      for (const b of rows) { const r = await fetch("/api/journal/trades", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify(b) }); if (r.ok) n++; }
      return n;
    }, seed);
    assert.strictEqual(ok, seed.length);
    await page.fill("#j-market", "XAUUSD");
    await page.fill("#j-entry", "2650"); await page.fill("#j-sl", "2640"); await page.fill("#j-tp", "2680"); await page.fill("#j-exit", "2672"); await page.fill("#j-size", "1");
    await page.fill("#j-notes", "Waited for the London sweep, took partials early.");
    await page.waitForFunction(() => /planned R:R 1:3\.00/.test(document.getElementById("j-preview").textContent));
    await page.click("#j-save");
    await page.waitForFunction(() => /15 of 15/.test(document.getElementById("jn-count").textContent), null, { timeout: 15000 });
    await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(200);
    await shot(page, "bp-journal-desktop.png");
    step("journal: form entry + list");
    await page.click('#jn-tabs [data-v="calendar"]'); await page.waitForSelector(".cal-day.has");
    await shot(page, "bp-journal-calendar-desktop.png");
    await page.click('#jn-tabs [data-v="insights"]'); await page.waitForSelector("#ins-body .obs li");
    const ins = await page.textContent("#ins-body");
    assert(/Based on:/.test(ins), "observations show their counts");
    await shot(page, "bp-journal-insights-desktop.png", true);
    await page.click('#jn-tabs [data-v="reports"]'); await page.click('#rep-period [data-p="month"]'); await page.waitForTimeout(500);
    await shot(page, "bp-journal-report-desktop.png", true);
    step("journal: calendar, insights (with counts), monthly report");

    // ---------------- desktop nav on the backtester + Phase 1 additions on a real backtest
    await page.goto(base + "/", { waitUntil: "networkidle" });
    await page.fill("#market", "BTC");
    await page.waitForSelector("#ac-list li[data-id]");
    await page.click("#ac-list li[data-id]");
    await page.waitForFunction(() => /last 15m close/.test(document.getElementById("price").textContent), null, { timeout: 60000 });
    const p0 = await page.evaluate(() => parseFloat(document.getElementById("price").textContent.replace(/,/g, "")));
    await page.fill("#entry", String(Math.round(p0 * 0.997))); await page.fill("#sl", String(Math.round(p0 * 0.987))); await page.fill("#tp", String(Math.round(p0 * 1.017)));
    await page.click("#adv-cfg summary");
    await page.fill("#acct-size", "10000"); await page.fill("#risk-pct", "0.5");
    await page.click("#run");
    await page.waitForSelector("#res:not([hidden])", { timeout: 240000 });
    const ks = await page.$$eval("#cards .card .k", els => els.map(e => e.textContent));
    for (const k of ["Recovery factor", "Max concurrent positions", "Position size (your levels)", "Net P/L in money"]) assert(ks.includes(k), "card " + k);
    assert(await page.isVisible("#sizing-row"), "sizing row shown");
    const rp = await page.textContent("#repro-txt");
    assert(/data fingerprint [0-9a-f]{16}/.test(rp) && /engine [0-9a-f]{7,}/.test(rp), "reproducibility line: " + rp);
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#cfg-dl")]);
    const fs = require("fs"); const cfg = JSON.parse(fs.readFileSync(await dl.path(), "utf8"));
    assert(cfg.data.fingerprint && cfg.engine_version && cfg.request && cfg.request.entry, "config file has fingerprint, engine version and request");
    await page.fill("#risk-pct", "1");
    await page.waitForFunction(() => /1% of/.test(document.getElementById("sizing-row").textContent));
    await page.locator("#sizing-row").scrollIntoViewIfNeeded();
    await shot(page, "bp-backtest-sizing-desktop.png");
    step("backtest: recovery factor, max concurrent, sizing row, reproducibility line and config download");
    await page.goto(base + "/", { waitUntil: "networkidle" });
    for (const k of ["bt", "cmp", "lib", "rp", "jn", "calc", "ct"]) assert(await page.isVisible(`.nav [data-nav="${k}"]`), "desktop nav item " + k);
    assert(!(await page.isVisible("#nav-more")), "More is phone-only");
    await shot(page, "bp-nav-desktop.png");
    step("desktop navigation");

    // ---------------- phone
    const state = await desk.storageState();
    const phone = await browser.newContext({ ...devices["Pixel 7"], storageState: state });
    const m = await phone.newPage();
    m.on("pageerror", e => errors.push(e.message)); watchApi(m);
    await m.goto(base + "/replay", { waitUntil: "networkidle" });
    await m.waitForSelector("#rp-live:not([hidden])");
    for (const k of ["bt", "rp", "jn", "lib", "more"]) assert(await m.isVisible(`.nav [data-nav="${k}"]`), "bottom bar item " + k);
    for (const k of ["cmp", "calc", "ct"]) assert(!(await m.isVisible(`.nav [data-nav="${k}"]`)), "moved to More: " + k);
    const ow = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert(ow <= 0, "no horizontal overflow on replay: " + ow);
    await m.evaluate(() => { const el = document.getElementById("rp-live"); window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 64); }); await m.waitForTimeout(300);
    await shot(m, "bp-replay-mobile.png");
    await m.locator("#rp-ticket-panel").scrollIntoViewIfNeeded();
    await shot(m, "bp-replay-ticket-mobile.png");
    await m.click("#nav-more"); await m.waitForSelector("#more-sheet:not([hidden])");
    await shot(m, "bp-more-mobile.png");
    await m.click('#more-sheet [data-more="calc"]'); await m.waitForURL(/\/calculator$/);
    assert(await m.evaluate(() => document.getElementById("nav-more").classList.contains("on")), "More marked current on the calculator");
    await m.fill("#c-acct", "5000"); await m.fill("#c-riskpct", "0.5"); await m.click('#c-type [data-t="crypto"]');
    await m.fill("#c-entry", "64000"); await m.fill("#c-sl", "63200"); await m.fill("#c-tp", "66400");
    await m.waitForTimeout(200);
    await m.evaluate(() => { const el = document.getElementById("c-sum"); window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 120); }); await m.waitForTimeout(200);
    await shot(m, "bp-calc-mobile.png");
    await m.goto(base + "/journal", { waitUntil: "networkidle" });
    await m.waitForFunction(() => /of 15/.test(document.getElementById("jn-count").textContent));
    const ow2 = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert(ow2 <= 0, "no horizontal overflow on journal: " + ow2);
    await m.locator(".jtabs").scrollIntoViewIfNeeded();
    await shot(m, "bp-journal-mobile.png");
    await m.click('#jn-tabs [data-v="calendar"]'); await m.waitForSelector(".cal-day.has");
    await m.locator(".jtabs").scrollIntoViewIfNeeded();
    await shot(m, "bp-journal-calendar-mobile.png");
    // touch targets on the bottom bar and sheet
    const small = await m.$$eval(".nav a, .nav button", els => els.filter(e => e.offsetParent && (e.getBoundingClientRect().height < 44 || e.getBoundingClientRect().width < 44)).length);
    assert.strictEqual(small, 0, "bottom bar tap targets >= 44px");
    await m.goto(base + "/?view=library", { waitUntil: "networkidle" });
    await m.click("#nav-more"); await m.click('#more-sheet [data-more="cmp"]');
    await m.waitForFunction(() => document.body.classList.contains("view-cmp"));
    step("phone: bottom bar, More sheet, no overflow, 44px targets, compare via More");
    await phone.close();
    assert.deepStrictEqual(errors, [], "page errors: " + errors.join(" | "));
    assert.deepStrictEqual(apiErrors, [], "API errors: " + apiErrors.join(" | "));
    console.log("ALL_OK");
  } finally {
    if (created) {
      const r = await page.evaluate(async pw => {
        const me = await (await fetch("/api/auth/me")).json();
        const x = await fetch("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify({ password: pw, confirm: true }) });
        return x.status;
      }, pw).catch(e => String(e));
      console.log("cleanup: account delete ->", r);
    }
    await browser.close();
  }
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
