#!/usr/bin/env node
/* v2 interface smoke test on real data: runs BTC, XAU and EURUSD backtests through the form and checks the
   v2 results (KPIs, Edge Score, tabs, Lightweight Charts equity/drawdown, replay candles).
   Usage: NODE_PATH=... node tests/ui2_markets.cjs <base_url> */
const { chromium } = require("playwright");
const assert = require("assert");
const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
(async () => {
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  try {
    for (const [q, side] of [["BTC", "long"], ["XAU", "short"], ["EURUSD", "long"]]) {
      const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
      const errs = []; page.on("pageerror", e => errs.push(e.message));
      await page.goto(base + "/", { waitUntil: "networkidle" });
      await page.fill("#market", q);
      await page.waitForSelector("#ac-list li[data-id]");
      await page.click("#ac-list li[data-id]");
      await page.waitForFunction(() => /last 15m close/.test(document.getElementById("price").textContent), null, { timeout: 90000 });
      const p0 = await page.evaluate(() => parseFloat(document.getElementById("price").textContent.replace(/[^0-9.]/g, "")));
      const k = side === "long" ? 1 : -1, r = v => String(+v.toPrecision(6));
      if (side === "short") await page.click('#side [data-side="short"]');
      await page.fill("#entry", r(p0 * (1 - k * 0.002))); await page.fill("#sl", r(p0 * (1 - k * 0.009))); await page.fill("#tp", r(p0 * (1 + k * 0.012)));
      await page.click("#run");
      await page.waitForSelector("#res:not([hidden])", { timeout: 400000 });
      await page.waitForTimeout(800);
      const kpis = await page.$$eval("#cards .kpis .card.kpi", e => e.length);
      assert(kpis >= 7, "primary KPI cards " + kpis);
      const closed = await page.evaluate(() => ((window.CHRONOS.getResult().stats || {}).equity || []).length);
      const chartOk = await page.evaluate(() => !!document.querySelector("#chart .tv-lightweight-charts, #chart .lwc-empty"));
      assert(chartOk, "equity chart drawn");
      const edge = (await page.textContent("#edge-score")).trim(), band = (await page.textContent("#edge-band")).trim();
      const title = (await page.textContent("#r-title")).trim(), verdict = (await page.textContent("#verdict")).trim();
      let replay = "no trades to replay";
      if (await page.$("#trades .rp-open")) {
        await page.click('#res-tabs [data-tab="trades"]');
        await page.click("#trades .rp-open");
        await page.waitForFunction(() => /match the ones used in the test|differ|No stored candles/.test(document.getElementById("rp-src").textContent + document.getElementById("rp-status").textContent), null, { timeout: 90000 });
        replay = (await page.evaluate(() => !!document.querySelector("#rp-cv .tv-lightweight-charts"))) ? "replay candles drawn" : "replay without chart";
        await page.keyboard.press("Escape");
      }
      assert(!errs.length, "page errors: " + errs.join(" | "));
      console.log(`ok  ${title} | ${verdict} | Edge ${edge} ${band} | ${closed} closed trades | ${replay}`);
      await page.context().close();
    }
    console.log("ALL_OK");
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
