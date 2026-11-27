#!/usr/bin/env node
/* Pixel-emulation checks for the screenshot file picker. Never clicks "Read screenshot". */
const { chromium, devices } = require("playwright");
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const assert = require("assert");

function pngBytes() {
  function crc32(buf) { return zlib.crc32(buf) >>> 0; }
  function chunk(tag, data) {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(tag), data]);
    const c = Buffer.alloc(4); c.writeUInt32BE(crc32(body));
    return Buffer.concat([len, body, c]);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0); ihdr.writeUInt32BE(1, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = zlib.deflateSync(Buffer.from([0, 0, 0, 0]));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", raw),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const PNG = pngBytes();
const URL = process.argv[2];
if (!URL) { console.error("usage: node shot_upload.cjs <url>"); process.exit(2); }
const screenshotOut = process.argv[3] || "";

async function attachEmptyMime(page, selector, buf, name) {
  const b64 = buf.toString("base64");
  await page.evaluate(async ({ selector, b64, name }) => {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const file = new File([bytes], name, { type: "" });
    const dt = new DataTransfer();
    dt.items.add(file);
    const input = document.querySelector(selector);
    if (!input) throw new Error("input not found: " + selector);
    input.files = dt.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, { selector, b64, name });
}

async function expectPreview(page, label) {
  const preview = page.locator("#shot-preview");
  await preview.waitFor({ state: "visible", timeout: 8000 });
  const hidden = await preview.evaluate((el) => el.hasAttribute("hidden") || getComputedStyle(el).display === "none");
  assert.strictEqual(hidden, false, label + ": preview still hidden");
  const src = await page.locator("#shot-thumb").getAttribute("src");
  assert.ok(src && src.length > 0, label + ": thumb src empty");
  const dropTxt = page.locator("#shot-drop-txt");
  const dropHidden = await dropTxt.evaluate((el) => el.hasAttribute("hidden") || getComputedStyle(el).display === "none");
  assert.ok(dropHidden, label + ": drop hint still visible");
  console.log("ok  " + label);
}

async function clearPreview(page) {
  if (await page.locator("#shot-clear").isVisible()) {
    await page.locator("#shot-clear").click();
  }
  await page.locator("#shot-drop-txt").waitFor({ state: "visible", timeout: 4000 });
}

(async () => {
  const pixel = devices["Pixel 5"] || devices["Pixel 7"];
  const browser = await chromium.launch({
    channel: "chrome",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const context = await browser.newContext({
    ...pixel,
    viewport: pixel.viewport || { width: 393, height: 851 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  await page.goto(URL, { waitUntil: "networkidle" });
  // Panel must be enabled (file picker is inside #shot-on)
  await page.locator("#shot-on").waitFor({ state: "visible", timeout: 15000 });
  assert.ok(await page.locator("#shot-file").count(), "missing #shot-file");
  assert.ok(await page.locator("#shot-choose").count(), "missing Choose file control");

  // 1) Empty MIME, no extension, via input/change events (Android-like File)
  await attachEmptyMime(page, "#shot-file", PNG, "image");
  await expectPreview(page, "empty MIME + name 'image' via input/change");
  const shownName = await page.locator("#shot-name").innerText();
  assert.strictEqual(shownName.trim(), "image");

  // 2) Real click on the drop-zone label, then file chooser
  await clearPreview(page);
  const [chooser1] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.locator("#shot-drop").click(),
  ]);
  await chooser1.setFiles({ name: "image", mimeType: "", buffer: PNG });
  await expectPreview(page, "real click on drop-zone label + empty MIME");

  // 3) Real click on the visible Choose file control (native input overlay)
  await clearPreview(page);
  const [chooser2] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.locator("#shot-choose").click(),
  ]);
  await chooser2.setFiles({ name: "image", mimeType: "", buffer: PNG });
  await expectPreview(page, "real click on Choose file + empty MIME");

  if (screenshotOut) {
    await page.screenshot({ path: screenshotOut, fullPage: false });
    console.log("wrote " + screenshotOut);
  }

  // 4) Rejection is visible, never silent
  await clearPreview(page);
  await page.evaluate(() => {
    const bytes = new Uint8Array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const file = new File([bytes], "file", { type: "" });
    const dt = new DataTransfer();
    dt.items.add(file);
    const input = document.getElementById("shot-file");
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const err = page.locator("#shot-err");
  await err.waitFor({ state: "visible", timeout: 8000 });
  const msg = (await err.innerText()).trim();
  assert.ok(msg.length > 0, "rejected file produced no error");
  const previewHidden = await page.locator("#shot-preview").evaluate((el) => el.hasAttribute("hidden") || getComputedStyle(el).display === "none");
  assert.ok(previewHidden, "rejected file still showed a preview");
  console.log("ok  visible error for non-image with empty MIME: " + JSON.stringify(msg));

  await browser.close();
  console.log("ALL_OK");
})().catch(async (e) => {
  console.error("FAIL", e && e.stack || e);
  process.exit(1);
});
