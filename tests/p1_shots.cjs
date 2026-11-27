#!/usr/bin/env node
/* Phase 1 UI check + screenshots on a running CHRONOS (use tests/mock_server.py locally so the screenshot reader is mocked).
   Usage: NODE_PATH=... node tests/p1_shots.cjs <base_url> <out_dir>
   Runs a real BTC backtest through the form, checks the dashboard, trade table, CSV, and the screenshot review panel. */
const { chromium } = require("playwright");
const assert = require("assert");
const path = require("path");
const fs = require("fs");

const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
const out = process.argv[3] || "web/screenshots";
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const ok = m => console.log("ok ", m);
const tab = (page, name) => page.click(`#res-tabs [data-tab="${name}"]`); // v2 results are organised in tabs

async function fillSetup(page) {
  await page.fill("#market", "BTC");
  await page.waitForSelector("#ac-list li[data-id]");
  await page.click("#ac-list li[data-id]");
  await page.waitForFunction(() => /last 15m close/.test(document.getElementById("price").textContent), null, { timeout: 60000 });
  const p0 = await page.evaluate(() => parseFloat(document.getElementById("price").textContent.replace(/,/g, "")));
  await page.fill("#entry", String(Math.round(p0 * 0.997)));
  await page.fill("#sl", String(Math.round(p0 * 0.987)));
  await page.fill("#tp", String(Math.round(p0 * 1.017)));
}

async function runAndCheck(page) {
  await page.click("#run");
  await page.waitForSelector("#res:not([hidden])", { timeout: 240000 });
  const cards = await page.$$eval("#cards .card .k", els => els.map(e => e.textContent));
  for (const k of ["Setups identified", "Trades executed", "Wins / losses", "Win rate", "Net P/L", "Profit factor", "Max drawdown",
    "Expectancy", "Avg R:R achieved", "Avg win / avg loss", "Longest streaks", "Avg time in trade"]) assert(cards.includes(k), "card " + k);
  const gvn = await page.$$eval("#gvn tbody tr", r => r.length);
  assert(gvn >= 10, "theoretical vs after-costs table");
  assert((await page.$$("#why-list li")).length >= 1, "why list");
  const ths = await page.$$eval("#trades-head th", e => e.map(x => x.textContent));
  assert(ths.some(t => t.startsWith("SL")) && ths.some(t => t.startsWith("R theor")) && ths.some(t => t.startsWith("Duration")), "trade columns");
  ok("dashboard, comparison table, why list and trade columns render");
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    // ---------------- desktop 1280
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1, acceptDownloads: true });
    const page = await ctx.newPage();
    page.on("pageerror", e => { throw e; });
    await page.goto(base + "/", { waitUntil: "networkidle" });
    assert(await page.isVisible("#try-example"), "example button in empty state");
    await page.screenshot({ path: path.join(out, "p1-desktop-form.png") });
    ok("desktop empty state");
    await fillSetup(page);
    await runAndCheck(page);
    // sort by net R descending
    await tab(page, "trades");
    await page.click('#trades-head th[data-k="r_net"]'); await page.click('#trades-head th[data-k="r_net"]');
    const rs = await page.$$eval("#trades tbody tr td:nth-child(12)", e => e.map(x => parseFloat(x.textContent)).filter(v => !isNaN(v)));
    for (let i = 1; i < rs.length; i++) assert(rs[i - 1] >= rs[i], "sorted desc");
    ok("trade table sorts");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#t-csv")]);
    const csv = fs.readFileSync(await dl.path(), "utf8").trim().split("\n");
    assert(csv[0].startsWith("n,market,side,status,exit_reason,signal_utc"), "csv header");
    assert(csv.length > 1, "csv rows");
    ok(`CSV export (${csv.length - 1} rows)`);
    await page.click('#trades-head th[data-k="i"]');
    await page.evaluate(() => document.getElementById("res").scrollIntoView());
    await page.screenshot({ path: path.join(out, "p1-desktop-results.png"), fullPage: false });
    const res = await page.$("#results");
    await res.screenshot({ path: path.join(out, "p1-desktop-results-full.png") });
    ok("desktop results screenshots");

    // ---------------- screenshot review (mocked vision)
    await page.goto(base + "/", { waitUntil: "networkidle" });
    await page.setInputFiles("#shot-file", { name: "chart.png", mimeType: "image/png", buffer: PNG });
    await page.click("#shot-run");
    await page.waitForSelector("#shot-review:not([hidden]) table.sr", { timeout: 60000 });
    assert(await page.$eval("#sl", e => e.value === ""), "very-low-confidence SL left blank");
    assert(await page.$eval("#tp", e => e.classList.contains("unsure")), "low-confidence TP highlighted");
    await page.click("#run");
    assert(await page.isVisible("#form-err"), "run blocked until highlighted fields are checked");
    await page.click(".use-sug");
    await page.click("#shot-ok");
    assert(!(await page.$eval("#tp", e => e.classList.contains("unsure"))), "confirm clears highlight");
    await (await page.$("#shot-panel")).screenshot({ path: path.join(out, "p1-desktop-screenshot-review.png") });
    ok("screenshot review: blank, highlight, suggestion and confirm work");
    await ctx.close();

    // ---------------- mobile 390
    const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
      userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36" });
    const mp = await m.newPage();
    mp.on("pageerror", e => { throw e; });
    await mp.goto(base + "/", { waitUntil: "networkidle" });
    await mp.screenshot({ path: path.join(out, "p1-mobile-top.png") });
    await (await mp.$(".form-panel")).screenshot({ path: path.join(out, "p1-mobile-form.png") });
    await mp.evaluate(() => document.getElementById("empty").scrollIntoView());
    await (await mp.$("#empty")).screenshot({ path: path.join(out, "p1-mobile-empty.png") });
    await fillSetup(mp);
    await runAndCheck(mp);
    const wide = await mp.evaluate(() => document.documentElement.scrollWidth);
    assert(wide <= 392, "no horizontal page scroll on mobile (" + wide + ")");
    await (await mp.$("#results")).screenshot({ path: path.join(out, "p1-mobile-results.png") });
    // sticky nav to Copy trading still reachable after scrolling
    await mp.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 2));
    assert(await mp.isVisible("#tab-ct"), "Copy trading tab visible while scrolled");
    ok("mobile form + results, no sideways scroll, nav reachable");
    await m.close();
    console.log("ALL_OK");
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
