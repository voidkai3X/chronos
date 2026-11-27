#!/usr/bin/env node
/* Phase 2 UI check + screenshots on a running CHRONOS (tests/mock_server.py locally).
   Usage: NODE_PATH=... node tests/p2_shots.cjs <base_url> <out_dir>
   Start the mock with RATE_HEAVY_PER_MIN=100: this script runs more tests per minute than the public limit allows.
   Real BTC data: timeframe sweep, market conditions, trade replay (stored candles, integrity check),
   and strategy comparison, at desktop 1280 and mobile 390. */
const { chromium } = require("playwright");
const assert = require("assert");
const path = require("path");
const fs = require("fs");

// phones keep Compare and Copy trading in the More sheet; desktop shows them in the top bar
async function navClick(page, k) {
  const sel = k === "ct" ? "#tab-ct" : `#view-seg [data-view="${k}"]`;
  if (await page.isVisible(sel)) return page.click(sel);
  await page.click("#nav-more");
  await page.click(`#more-sheet [data-more="${k}"]`);
}

const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
const out = process.argv[3] || "web/screenshots";
const ok = m => console.log("ok ", m);

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

const tab = (page, name) => page.click(`#res-tabs [data-tab="${name}"]`); // v2 results are organised in tabs
async function openAdv(page) { if (!(await page.$eval("#adv-cfg", d => d.open))) await page.click("#adv-cfg > summary"); }
async function checkConditions(page) {
  await tab(page, "analysis");
  await page.waitForSelector("#cond-panel:not([hidden])");
  await page.waitForTimeout(900); // let the existing panel fade-in finish before screenshots
  const dims = await page.$$eval("#cond-grid .cond-dim", e => e.length);
  assert.strictEqual(dims, 3, "three condition dimensions");
  assert((await page.textContent("#cond-head")).length > 20, "condition summary");
  assert(/Right now/.test(await page.textContent("#cond-now")), "current conditions line");
  ok("market conditions panel");
}

async function checkReplay(page, shot) {
  await tab(page, "trades");
  await page.click("#trades .rp-open");
  await page.waitForSelector("#rp:not([hidden])");
  await page.waitForFunction(() => /match the ones used in the test|differ/.test(document.getElementById("rp-src").textContent), null, { timeout: 60000 });
  assert(/match the ones used in the test/.test(await page.textContent("#rp-src")), "replay candles match the test's candles");
  assert(/Order resting|Before the signal|In the trade/i.test(await page.textContent("#rp-status")), "starts at the signal");
  const s0 = +(await page.inputValue("#rp-scrub"));
  await page.selectOption("#rp-speed", "16");
  await page.click("#rp-play");
  await page.waitForTimeout(900);
  await page.click("#rp-play");
  const s1 = +(await page.inputValue("#rp-scrub"));
  assert(s1 > s0, "play advances candle by candle");
  await page.click("#rp-back");
  assert.strictEqual(+(await page.inputValue("#rp-scrub")), s1 - 1, "step back");
  await page.click("#rp-end");
  const st = await page.textContent("#rp-status");
  assert(/Target hit|Stop hit|Time stop/.test(st) && /after/.test(st), "exit shows reason and duration: " + st);
  const info = await page.textContent("#rp-info");
  assert(/Time in trade/.test(info) && /Exit reason/.test(info), "trade facts");
  if (shot) await page.screenshot({ path: shot });
  await page.keyboard.press("Escape");
  await page.waitForSelector("#rp", { state: "hidden" });
  ok("trade replay: stored candles, integrity, play/pause/step/scrub, exit and duration");
}

async function runCompare(page, n) {
  await navClick(page, "cmp");
  await page.waitForSelector("#strat-list .st-row");
  while ((await page.$$("#strat-list .st-row")).length < n) await page.click("#strat-add");
  await page.click("#run");
  await page.waitForSelector("#strat-res:not([hidden])", { timeout: 300000 });
  await page.waitForTimeout(900);
  const rows = await page.$$eval("#sc-tbl tbody tr", e => e.length);
  assert.strictEqual(rows, n, "one row per strategy");
  assert(/Identical conditions/.test(await page.textContent("#sc-sub")), "conditions stated");
  assert((await page.$$("#sc-legend span")).length >= 1, "equity overlay legend");
  ok(`strategy comparison with ${n} strategies`);
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    // ---------------- desktop 1280
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on("pageerror", e => { throw e; });
    await page.goto(base + "/", { waitUntil: "networkidle" });
    await fillSetup(page);
    await page.selectOption("#similar-tf", "1h");
    await openAdv(page);
    await page.check("#tf-sweep");
    await page.locator("#tf-block").screenshot({ path: path.join(out, "p2-desktop-timeframe-form.png") });
    await page.click("#run");
    await page.waitForSelector("#res:not([hidden])", { timeout: 400000 });
    await tab(page, "analysis");
    await page.waitForSelector("#tfs-panel:not([hidden])");
    const tfs = await page.$$eval("#tfs tbody tr td:first-child", e => e.map(x => x.textContent.trim()));
    assert.deepStrictEqual(tfs, ["5m", "15m", "30m", "1h", "4h", "Daily"], "six timeframes");
    ok("timeframe sweep table");
    await checkConditions(page);
    await page.locator("#cond-panel").screenshot({ path: path.join(out, "p2-desktop-conditions.png") });
    await page.locator("#tfs-panel").screenshot({ path: path.join(out, "p2-desktop-timeframes.png") });
    await checkReplay(page, path.join(out, "p2-desktop-replay.png"));
    // HTF confirmation on the every-close mode, 15m setups
    await page.click('#mode [data-mode="every-close"]');
    await page.selectOption("#similar-tf", "15m");
    await page.selectOption("#htf", "4h");
    await page.uncheck("#tf-sweep");
    await page.click("#run");
    await page.waitForFunction(() => /Higher-timeframe confirmation on 4h/.test(document.getElementById("r-sub").textContent), null, { timeout: 300000 });
    assert(/EVERY 15M CLOSE/.test(await page.textContent("#r-sub")), "every 15m close");
    ok("higher-timeframe confirmation reported");
    await runCompare(page, 5);
    await page.locator("#strat-res").screenshot({ path: path.join(out, "p2-desktop-compare.png") });
    await page.locator("#strat-block").screenshot({ path: path.join(out, "p2-desktop-compare-form.png") });
    await page.click('#view-seg [data-view="test"]');
    assert(await page.isVisible("#res") && !(await page.isVisible("#strat-res")), "views keep their own results");
    ok("view switch");
    await ctx.close();

    // ---------------- mobile 390
    const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    const mp = await m.newPage();
    mp.on("pageerror", e => { throw e; });
    await mp.goto(base + "/", { waitUntil: "networkidle" });
    await fillSetup(mp);
    await mp.click('#mode [data-mode="every-close"]');
    await mp.selectOption("#similar-tf", "4h");
    await mp.locator("#tf-block").screenshot({ path: path.join(out, "p2-mobile-timeframe-form.png") });
    await mp.click("#run");
    await mp.waitForSelector("#res:not([hidden])", { timeout: 300000 });
    await checkConditions(mp);
    await mp.locator("#cond-panel").screenshot({ path: path.join(out, "p2-mobile-conditions.png") });
    let sw = await mp.evaluate(() => document.documentElement.scrollWidth);
    assert(sw <= 391, "no sideways scroll on results: " + sw);
    await tab(mp, "trades");
    await mp.locator("#trades .rp-open").first().scrollIntoViewIfNeeded();
    await checkReplay(mp, path.join(out, "p2-mobile-replay.png"));
    await mp.evaluate(() => window.scrollTo(0, 0));
    await runCompare(mp, 4);
    sw = await mp.evaluate(() => document.documentElement.scrollWidth);
    assert(sw <= 391, "no sideways scroll on comparison: " + sw);
    await mp.locator("#strat-res").screenshot({ path: path.join(out, "p2-mobile-compare.png") });
    await mp.evaluate(() => document.getElementById("view-seg").scrollIntoView());
    await mp.screenshot({ path: path.join(out, "p2-mobile-compare-form.png") });
    ok("mobile: conditions, replay and comparison, no sideways scroll");
    await m.close();
    console.log("ALL_OK");
  } finally { await browser.close(); }
})().catch(e => { console.error("FAIL", e); process.exit(1); });
