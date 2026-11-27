#!/usr/bin/env node
/* End-to-end account flow on a running CHRONOS (local or live), Pixel 7 emulation.
   Usage: node tests/auth_e2e.cjs <base_url> [screenshot_dir]
   Signs up a throwaway account, checks Copy trading, signs out, signs back in, then deletes the account. */
const { chromium, devices } = require("playwright");
const assert = require("assert");
const path = require("path");
const crypto = require("crypto");

(async () => {
  const base = (process.argv[2] || "http://127.0.0.1:8090").replace(/\/$/, "");
  const shots = process.argv[3];
  const email = `e2e-${crypto.randomBytes(5).toString("hex")}@example.com`;
  const pw = "e2e-" + crypto.randomBytes(12).toString("hex");
  const browser = await chromium.launch({ channel: "chrome", args: ["--no-sandbox", "--disable-gpu"] });
  const ctx = await browser.newContext({ ...devices["Pixel 7"] });
  const page = await ctx.newPage();
  const step = m => console.log("ok ", m);
  let created = false;
  try {
    await page.goto(base + "/copy-trading", { waitUntil: "networkidle", timeout: 120000 });
    assert(await page.isVisible("#ct-locked"), "copy trading should be locked when signed out");
    assert(await page.isVisible("#acct-signin"));
    step("copy trading locked when signed out");

    await page.click("#ct-signup");
    await page.waitForURL(/\/signup\?next=/);
    await page.waitForLoadState("networkidle");
    if (shots) await page.screenshot({ path: path.join(shots, "signup-mobile.png") });
    await page.fill("#email", "bad");
    await page.fill("#password", pw);
    await page.click("#auth-submit");
    assert(await page.isVisible("#auth-err"), "bad email shows an error");
    step("client-side validation");

    await page.fill("#email", email);
    await Promise.all([page.waitForURL(/\/copy-trading$/), page.click("#auth-submit")]);
    created = true;
    await page.waitForSelector("#ct-open:not([hidden])");
    await page.waitForFunction(() => /coming soon/i.test(document.getElementById("ct-msg").textContent));
    assert.strictEqual((await page.textContent("#acct-email")).trim(), email);
    if (shots) await page.screenshot({ path: path.join(shots, "copy-trading-mobile.png") });
    step("signed up and Copy trading open: " + (await page.textContent("#ct-msg")).trim());

    await page.click("#acct-signout");
    await page.waitForSelector("#ct-locked:not([hidden])");
    assert(await page.isVisible("#acct-signin"));
    step("signed out");

    await page.goto(base + "/signin?next=/copy-trading", { waitUntil: "networkidle" });
    await page.fill("#email", email);
    await page.fill("#password", "wrong-password-123");
    await page.click("#auth-submit");
    await page.waitForSelector("#auth-err:not([hidden])");
    assert.match(await page.textContent("#auth-err"), /wrong email or password/i);
    step("wrong password gives generic error");
    await page.fill("#password", pw);
    await Promise.all([page.waitForURL(/\/copy-trading$/), page.click("#auth-submit")]);
    await page.waitForSelector("#ct-open:not([hidden])");
    step("signed back in, Copy trading visible");

    await page.click("#tab-bt");
    assert(await page.isVisible("#view-bt"));
    step("backtester tab still works");
  } finally {
    if (created) {
      const res = await page.evaluate(async pw => {
        const me = await (await fetch("/api/auth/me")).json();
        if (!me.user) {
          return { skipped: "not signed in" };
        }
        const r = await fetch("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": me.csrf }, body: JSON.stringify({ password: pw }) });
        return { status: r.status, body: await r.json() };
      }, pw);
      console.log("cleanup delete:", JSON.stringify(res));
      assert.strictEqual(res.status, 200, "test account must be deleted");
      const me = await page.evaluate(async () => (await fetch("/api/auth/me")).json());
      assert.strictEqual(me.user, null);
      step("test account deleted");
    }
    await browser.close();
  }
  console.log("ALL_OK");
})().catch(e => { console.error(e); process.exit(1); });
