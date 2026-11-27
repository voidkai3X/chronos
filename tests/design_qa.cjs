#!/usr/bin/env node
/* Design QA for the v2 interface on a running CHRONOS (tests/mock_server.py locally, RATE_HEAVY_PER_MIN=100).
   Usage: NODE_PATH=... node tests/design_qa.cjs <base_url> [screenshot_dir]
   For every width in 360 / 390 / 430 / 768 / 1280 / 1440 it walks every view (form, advanced settings, loading,
   each results tab, trade replay, compare, library, copy trading, account menu, sign-in page) and checks:
     - no page-level horizontal overflow (documentElement.scrollWidth <= viewport width)
     - on touch widths (<= 768) every visible control is at least 44 x 44 CSS px (inline text links excepted)
   Uses real BTC data and a throwaway account that is deleted at the end. With a screenshot_dir it also saves
   ui2-desktop-*.png (1440) and ui2-mobile-*.png (390). */
const { chromium } = require("playwright");
const assert = require("assert");
const path = require("path");
const fs = require("fs");

const base = (process.argv[2] || "http://127.0.0.1:8093").replace(/\/$/, "");
const shots = process.argv[3];
const WIDTHS = [360, 390, 430, 768, 1280, 1440];
const EMAIL = `qa-${Date.now()}@example.com`, PW = "design qa password long";
const problems = [];
const ok = m => console.log("ok ", m);

async function overflow(page, W, where) {
  const sw = await page.evaluate(() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth));
  if (sw > W) {
    const who = await page.evaluate(W => [...document.querySelectorAll("body *")].filter(e => { const r = e.getBoundingClientRect(); return r.width && r.right > W + 1; })
      .slice(0, 5).map(e => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + "." + String(e.className).split(" ")[0]), W);
    problems.push(`${W}px ${where}: page is ${sw}px wide (${who.join(", ")})`);
  }
}
async function taps(page, W, where) {
  if (W > 768) return;
  const small = await page.evaluate(() => {
    const vis = e => { const r = e.getBoundingClientRect(); if (!r.width || !r.height) return false; const s = getComputedStyle(e);
      return s.visibility !== "hidden" && s.display !== "none" && !e.closest("[hidden]") && e.closest("body") && r.bottom > 0; };
    const inline = e => e.tagName === "A" && e.closest("p, li, .help, .muted, td, .provenance, .foot-attr, .r-sub, .dq, .scan-disc, .ct-panel p");
    const out = [];
    document.querySelectorAll("button, a[href], select, summary, [role=tab], input:not([type=hidden]):not([type=checkbox]):not([type=file]), label.chk, .shot-choose").forEach(e => {
      if (!vis(e) || inline(e) || e.closest(".tv-lightweight-charts") || e.id === "tv-attr-logo") return;
      if (e.tagName === "INPUT" && e.type === "range") { // the thumb is the target: the track box must be tall enough
        if (e.getBoundingClientRect().height < 28) out.push("range#" + e.id + " h" + Math.round(e.getBoundingClientRect().height)); return; }
      const r = e.getBoundingClientRect();
      if (r.height < 43.5 || r.width < 43.5) out.push(`${e.tagName.toLowerCase()}${e.id ? "#" + e.id : ""}.${String(e.className).split(" ")[0]} "${(e.textContent || "").trim().slice(0, 20)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
    });
    return out;
  });
  small.forEach(s => problems.push(`${W}px ${where}: small tap target ${s}`));
}
async function check(page, W, where) { await overflow(page, W, where); await taps(page, W, where); }
const tab = (page, name) => page.click(`#res-tabs [data-tab="${name}"]`);
const snap = async (page, W, name, opts) => {
  if (!shots || (W !== 1440 && W !== 390)) return;
  await page.waitForTimeout(250);
  await page.screenshot(Object.assign({ path: path.join(shots, `ui2-${W === 1440 ? "desktop" : "mobile"}-${name}.png`) }, opts || {}));
};
const top = (page, sel) => page.evaluate(sel => { const e = document.querySelector(sel); const h = document.getElementById("topbar").offsetHeight;
  window.scrollTo(0, e.getBoundingClientRect().top + window.scrollY - h - 8); }, sel);

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

(async () => {
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  let state = null;
  try {
    // one throwaway account for the signed-in views (account menu, library)
    {
      const c = await browser.newContext(); const p = await c.newPage();
      await p.goto(base + "/", { waitUntil: "networkidle" });
      const r = await p.evaluate(async ([email, password]) => {
        const me = await (await fetch("/api/auth/me", { cache: "no-store" })).json();
        const r = await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify({ email, password }) });
        return r.status;
      }, [EMAIL, PW]);
      assert(r === 200 || r === 201, "qa signup " + r);
      state = await c.storageState(); await c.close();
    }
    for (const W of WIDTHS) {
      const mob = W <= 768;
      const ctx = await browser.newContext({ viewport: { width: W, height: mob ? (W < 700 ? 844 : 1024) : 900 }, deviceScaleFactor: W === 390 ? 2 : 1,
        isMobile: mob && W < 700, hasTouch: mob, storageState: state });
      const page = await ctx.newPage();
      const errs = []; page.on("pageerror", e => errs.push(e.message));
      // sign-in page (signed out context)
      const anon = await browser.newContext({ viewport: { width: W, height: 844 }, isMobile: mob && W < 700, hasTouch: mob });
      const ap = await anon.newPage(); await ap.goto(base + "/signin", { waitUntil: "networkidle" }); await check(ap, W, "sign-in page");
      await ap.goto(base + "/", { waitUntil: "networkidle" }); await check(ap, W, "home signed out"); await snap(ap, W, "home-signed-out"); await anon.close();

      await page.goto(base + "/", { waitUntil: "networkidle" });
      await page.waitForSelector("#acct-menu-btn:not([hidden])");
      await check(page, W, "home"); await snap(page, W, "home-form");
      await page.click("#adv-cfg > summary"); await top(page, "#sec-b");
      await check(page, W, "advanced open"); await snap(page, W, "advanced-open");
      await page.click("#adv-cfg > summary");
      await page.click("#acct-menu-btn"); await page.waitForSelector("#acct-menu:not([hidden])"); await page.waitForTimeout(400); // menu open animation
      await page.evaluate(() => window.scrollTo(0, 0));
      await check(page, W, "account menu"); await snap(page, W, "account");
      await page.keyboard.press("Escape");

      await fillSetup(page);
      // hold the real request for a moment so the loading state can be inspected
      await page.route("**/api/test", async r => { await new Promise(f => setTimeout(f, 2500)); await r.continue(); });
      await page.click("#run");
      await page.waitForSelector("#skel:not([hidden])");
      await top(page, "#skel"); await page.waitForTimeout(600);
      await check(page, W, "loading"); await snap(page, W, "loading");
      await page.waitForSelector("#res:not([hidden])", { timeout: 300000 });
      await page.unroute("**/api/test");
      await page.waitForTimeout(600);
      await top(page, "#res");
      await check(page, W, "results top"); await snap(page, W, "results-top");
      await top(page, "#res-tabs"); await check(page, W, "tab overview"); await snap(page, W, "charts");
      for (const t of ["perf", "trades", "analysis", "markets"]) {
        await tab(page, t); await top(page, "#res-tabs"); await page.waitForTimeout(200);
        await check(page, W, "tab " + t);
        if (t === "perf") await snap(page, W, "performance");
        if (t === "trades") await snap(page, W, "trade-table");
        if (t === "analysis") await snap(page, W, "analysis");
      }
      await tab(page, "trades");
      await page.click("#trades .rp-open");
      await page.waitForFunction(() => /match the ones used in the test|differ/.test(document.getElementById("rp-src").textContent), null, { timeout: 60000 });
      await page.click("#rp-end"); await page.waitForTimeout(300);
      await check(page, W, "replay"); await snap(page, W, "replay");
      await page.keyboard.press("Escape"); await page.waitForSelector("#rp", { state: "hidden" });

      await page.click('#view-seg [data-view="cmp"]');
      await page.waitForSelector("#strat-list .st-row");
      await check(page, W, "compare form");
      if (W === 1440 || W === 390) {
        await page.click("#run");
        await page.waitForSelector("#strat-res:not([hidden])", { timeout: 300000 });
        await page.waitForTimeout(600);
        await top(page, "#strat-res"); await check(page, W, "compare results"); await snap(page, W, "compare");
      }
      await page.click('#view-seg [data-view="lib"]');
      await page.waitForFunction(() => !document.getElementById("lib-msg").textContent.startsWith("Loading"), null, { timeout: 20000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      await check(page, W, "library"); await snap(page, W, "library");
      await page.click('#view-seg [data-view="test"]');
      await page.click("#tab-ct"); await page.waitForSelector("#view-ct:not([hidden])");
      await check(page, W, "copy trading"); await snap(page, W, "copy-trading");
      await page.click('#view-seg [data-view="lib"]'); // a view button leaves Copy trading
      assert(await page.isVisible("#view-bt"), "view buttons return from Copy trading");
      if (errs.length) problems.push(`${W}px page errors: ${errs.join(" | ")}`);
      ok(`${W}px walked`);
      await ctx.close();
    }
  } finally {
    const c = await browser.newContext({ storageState: state || undefined }); const p = await c.newPage();
    await p.goto(base + "/", { waitUntil: "networkidle" }).catch(() => {});
    const st = await p.evaluate(async pw => {
      const me = await (await fetch("/api/auth/me", { cache: "no-store" })).json();
      if (!me.user) return "not signed in";
      const r = await fetch("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify({ password: pw }) });
      return r.status;
    }, PW).catch(e => String(e));
    console.log("cleanup: qa account delete", st);
    await browser.close();
  }
  const uniq = [...new Set(problems)];
  uniq.forEach(p => console.log("FAIL", p));
  if (uniq.length) process.exit(1);
  console.log("ALL_OK");
})().catch(e => { console.error(e); process.exit(1); });
