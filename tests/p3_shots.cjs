#!/usr/bin/env node
/* Phase 3 UI check + screenshots on a running CHRONOS (tests/mock_server.py locally, started with RATE_HEAVY_PER_MIN=100).
   Usage: NODE_PATH=... node tests/p3_shots.cjs <base_url> <out_dir>
   Real BTC data. Edge Score + analytics panels, then the strategy library with a throwaway account
   (signed-out prompt, save from the form, new/edit/duplicate/delete, run twice, history, rule strategy,
   compare saved strategies, import device-saved rows), desktop 1280 and mobile 390. The account is deleted at the end. */
const { chromium } = require("playwright");
const assert = require("assert");
const path = require("path");
const fs = require("fs");

const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
const out = process.argv[3] || "web/screenshots";
const ok = m => console.log("ok ", m);
const EMAIL = `p3-check-${Date.now()}@example.com`, PW = "p3 check password long";
const shot = (loc, name) => loc.screenshot({ path: path.join(out, name) });

async function fillSetup(page, mode = "every-close", tf = "1h") {
  await page.fill("#market", "BTC");
  await page.waitForSelector("#ac-list li[data-id]");
  await page.click("#ac-list li[data-id]");
  await page.waitForFunction(() => /last 15m close/.test(document.getElementById("price").textContent), null, { timeout: 60000 });
  const p0 = await page.evaluate(() => parseFloat(document.getElementById("price").textContent.replace(/,/g, "")));
  await page.fill("#entry", String(Math.round(p0 * 0.997)));
  await page.fill("#sl", String(Math.round(p0 * 0.987)));
  await page.fill("#tp", String(Math.round(p0 * 1.017)));
  await page.click(`#mode [data-mode="${mode}"]`);
  await page.selectOption("#similar-tf", tf);
}
async function waitResult(page) {
  await page.waitForSelector("#res:not([hidden])", { timeout: 300000 });
  await page.waitForSelector("#edge-panel:not([hidden])");
  await page.waitForTimeout(900);
}
async function checkEdge(page) {
  const sc = +(await page.textContent("#edge-score"));
  assert(sc >= 0 && sc <= 100, "score in range");
  assert(/No evidence|Weak|Promising|Strong/.test(await page.textContent("#edge-band")), "band label");
  await page.click("#edge-how summary");
  assert.strictEqual(await page.$$eval("#edge-tbl tbody tr", e => e.length), 8, "eight components");
  assert(/not a prediction/.test(await page.textContent("#edge-disc")), "disclaimer");
  const v = await page.textContent("#verdict");
  if (sc < 45) assert(!/possible edge|edge on this sample/.test(v), "verdict agrees with a low score: " + v);
  assert(await page.isVisible("#adv-panel"), "analytics");
  assert(/90% range/.test(await page.textContent("#adv-boot")), "bootstrap");
  assert(/95th percentile/.test(await page.textContent("#adv-mc")), "monte carlo");
  ok(`Edge Score ${sc} (${await page.textContent("#edge-band")}) with methodology, analytics and verdict "${v.slice(0, 60)}..."`);
  return sc;
}
async function authCall(page, url, body) {
  return page.evaluate(async ([url, body]) => {
    const me = await (await fetch("/api/auth/me", { cache: "no-store" })).json();
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify(body) });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }, [url, body]);
}
async function libCount(page) { return page.$$eval("#lib-list .lib-item", e => e.length); }
async function gotoLib(page) {
  await page.click('#view-seg [data-view="lib"]');
  await page.waitForFunction(() => !document.getElementById("lib-msg").textContent.startsWith("Loading"), null, { timeout: 20000 });
}

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  let page;
  try {
    // ---------------- desktop 1280
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
    page = await ctx.newPage();
    page.on("pageerror", e => { throw e; });
    page.on("dialog", d => d.accept());
    await page.goto(base + "/", { waitUntil: "networkidle" });
    await fillSetup(page);
    await page.click("#run");
    await waitResult(page);
    await checkEdge(page);
    await shot(page.locator("#edge-panel"), "p3-desktop-edge.png");
    await shot(page.locator("#adv-panel"), "p3-desktop-analytics.png");
    // signed out: tools work, saving prompts for sign-in
    await page.click("#lib-save");
    assert(/Sign in/.test(await page.textContent("#lib-run-note")), "signed-out save prompt");
    await gotoLib(page);
    assert(await page.isVisible("#lib-signin"), "library sign-in prompt");
    await page.screenshot({ path: path.join(out, "p3-desktop-library-signedout.png") });
    ok("signed out: results work, saving asks to sign in");
    // throwaway account
    const su = await authCall(page, "/api/auth/signup", { email: EMAIL, password: PW });
    assert.strictEqual(su.status, 201, JSON.stringify(su));
    await page.goto(base + "/", { waitUntil: "networkidle" });
    await fillSetup(page);
    await page.click("#lib-save");
    await page.waitForSelector("#le:not([hidden])");
    assert.strictEqual(await page.inputValue("#le-type"), "every_close", "setup type from the form");
    assert.strictEqual(await page.inputValue("#le-tf"), "1h");
    await page.fill("#le-name", "BTC 1h every close");
    await page.fill("#le-desc", "Long 0.3% under price, 1% stop, 1.7% target.");
    await page.fill("#le-exit-notes", "Fixed TP and SL, out after 7 days.");
    await page.click("#le-save");
    await page.waitForSelector("#le", { state: "hidden" });
    ok("saved the form as a strategy");
    await gotoLib(page);
    assert.strictEqual(await libCount(page), 1);
    await page.click("#lib-new");
    await page.waitForSelector("#le:not([hidden])");
    await page.fill("#le-name", "EMA 20/50 crossover");
    await page.selectOption("#le-type", "ema_cross");
    await page.waitForSelector('#le-params input[data-k="fast"]');
    await page.fill("#le-label", "EMA crossover");
    await page.fill("#le-markets", "BTC, ETH");
    await page.selectOption("#le-tf", "1h");
    await page.fill("#le-stop", "1");
    await page.fill("#le-rr", "2");
    await page.fill("#le-entry-notes", "Fast EMA closes above slow EMA.");
    await shot(page.locator("#le .le-box"), "p3-desktop-editor.png");
    await page.click("#le-save");
    await page.waitForSelector("#le", { state: "hidden" });
    await page.waitForFunction(() => document.querySelectorAll("#lib-list .lib-item").length === 2);
    await page.click('.lib-item:nth-child(2) [data-act="dup"]');
    await page.waitForFunction(() => document.querySelectorAll("#lib-list .lib-item").length === 3);
    const dupName = await page.textContent("#lib-list .lib-item:first-child .lib-name b");
    assert(/\(copy\)$/.test(dupName), "duplicate named copy: " + dupName);
    await page.click('#lib-list .lib-item:first-child [data-act="edit"]');
    await page.waitForSelector("#le:not([hidden])");
    await page.fill("#le-name", "Scratch copy");
    await page.click("#le-save");
    await page.waitForFunction(() => document.querySelector("#lib-list .lib-item:first-child .lib-name b").textContent === "Scratch copy");
    await page.click('#lib-list .lib-item:first-child [data-act="del"]');
    await page.waitForFunction(() => document.querySelectorAll("#lib-list .lib-item").length === 2);
    ok("new, edit, duplicate, delete (with confirm)");
    // run the every-close strategy twice -> two snapshots
    const runSaved = async name => {
      await gotoLib(page);
      const item = page.locator(".lib-item", { hasText: name });
      await item.locator('[data-act="run"]').click();
      await page.waitForFunction(() => { const t = [...document.querySelectorAll(".toast")].map(x => x.textContent).join(" "); return /Saved to My strategies as run/.test(t); }, null, { timeout: 300000 });
      await page.evaluate(() => document.querySelectorAll(".toast").forEach(t => t.remove()));
    };
    await runSaved("BTC 1h every close");
    await runSaved("BTC 1h every close");
    await runSaved("EMA 20/50 crossover");
    assert(/RULE: EMA crossover/.test(await page.textContent("#r-sub")), "rule strategy ran as a rule");
    ok("ran saved strategies; each run saved a snapshot");
    await gotoLib(page);
    const item = page.locator(".lib-item", { hasText: "BTC 1h every close" });
    assert(/\d+\/100/.test(await item.locator(".lib-chip").textContent()), "last score chip");
    await item.locator('[data-act="hist"]').click();
    await page.waitForFunction(() => document.querySelectorAll("#lib-runs tbody tr").length === 2);
    await page.waitForTimeout(500);
    await shot(page.locator("#lib-res .lib-panel"), "p3-desktop-library.png");
    await shot(page.locator("#lib-hist"), "p3-desktop-history.png");
    ok("run history with trend chart");
    // compare the two saved strategies
    await page.click('#lib-list .lib-item:nth-child(1) .lib-pick');
    await page.click('#lib-list .lib-item:nth-child(2) .lib-pick');
    await page.click("#lib-cmp-run");
    await page.waitForSelector("#strat-res:not([hidden])", { timeout: 300000 });
    await page.waitForTimeout(900);
    assert.strictEqual(await page.$$eval("#sc-tbl tbody tr", e => e.length), 2);
    assert((await page.$$eval("#sc-head th", e => e.map(x => x.textContent))).some(t => /Edge Score/.test(t)), "edge column");
    await shot(page.locator("#strat-res"), "p3-desktop-compare-saved.png");
    ok("compared saved strategies under identical conditions");
    // import the device-saved comparison rows
    await page.click('#view-seg [data-view="test"]');
    await fillSetup(page);
    await gotoLib(page);
    const before = await libCount(page);
    assert(await page.isVisible("#lib-import"), "import offered");
    await page.click("#lib-import");
    await page.waitForFunction(n => document.querySelectorAll("#lib-list .lib-item").length > n, before, { timeout: 20000 });
    ok(`imported device-saved strategies (${before} -> ${await libCount(page)})`);
    await ctx.close();

    // ---------------- mobile 390
    const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    page = await m.newPage();
    page.on("pageerror", e => { throw e; });
    page.on("dialog", d => d.accept());
    await page.goto(base + "/", { waitUntil: "networkidle" });
    const si = await authCall(page, "/api/auth/signin", { email: EMAIL, password: PW });
    assert.strictEqual(si.status, 200, JSON.stringify(si));
    await page.goto(base + "/?view=library", { waitUntil: "networkidle" });
    await page.waitForFunction(() => document.querySelectorAll("#lib-list .lib-item").length >= 2, null, { timeout: 20000 });
    await page.locator(".lib-item", { hasText: "BTC 1h every close" }).filter({ has: page.locator('[data-act="hist"]:not([disabled])') }).first().locator('[data-act="hist"]').click();
    await page.waitForFunction(() => document.querySelectorAll("#lib-runs tbody tr").length >= 2);
    await page.waitForTimeout(600);
    let sw = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(sw <= 391, "no sideways scroll in library: " + sw);
    await shot(page.locator("#lib-res .lib-panel"), "p3-mobile-library.png");
    await shot(page.locator("#lib-hist"), "p3-mobile-history.png");
    await page.locator(".lib-item", { hasText: "EMA 20/50" }).first().locator('[data-act="edit"]').click();
    await page.waitForSelector("#le:not([hidden])");
    await page.screenshot({ path: path.join(out, "p3-mobile-editor.png") });
    await page.click("#le-cancel");
    await page.click('#view-seg [data-view="test"]');
    await fillSetup(page, "every-close", "1h");
    await page.click("#run");
    await waitResult(page);
    await checkEdge(page);
    sw = await page.evaluate(() => document.documentElement.scrollWidth);
    assert(sw <= 391, "no sideways scroll on results: " + sw);
    await shot(page.locator("#edge-panel"), "p3-mobile-edge.png");
    await shot(page.locator("#adv-panel"), "p3-mobile-analytics.png");
    ok("mobile: library, history, editor, Edge Score and analytics, no sideways scroll");
    await m.close();
    page = null;
    console.log("ALL_OK");
  } finally {
    try {
      const c = await browser.newContext(); const p = await c.newPage();
      await p.goto(base + "/", { waitUntil: "domcontentloaded" });
      await authCall(p, "/api/auth/signin", { email: EMAIL, password: PW });
      const d = await authCall(p, "/api/auth/delete", { password: PW });
      console.log("cleanup: test account delete status", d.status);
    } catch (e) { console.log("cleanup failed", e.message); }
    await browser.close();
  }
})().catch(e => { console.error("FAIL", e); process.exit(1); });
