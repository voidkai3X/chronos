// CHRONOS shared navigation: the phone "More" sheet on every page, and on the Replay / Journal / Calculator pages
// the account header (sign-in state, CSRF header on writes, account menu, sign out, delete account).
(() => {
"use strict";
const $ = id => document.getElementById(id);
const page = document.body.dataset.page || "";   // "" on the backtester page (account.js + ui.js handle it there)

// ---------------------------------------------------------------- More sheet (phones)
const more = $("nav-more"), sheet = $("more-sheet");
function closeSheet() { if (sheet && !sheet.hidden) { sheet.hidden = true; more.setAttribute("aria-expanded", "false"); } }
if (more && sheet) {
  more.addEventListener("click", e => {
    e.stopPropagation();
    const open = sheet.hidden;
    sheet.hidden = !open; more.setAttribute("aria-expanded", String(open));
    if (open) { const f = sheet.querySelector("a,button"); if (f) f.focus({ preventScroll: true }); }
  });
  document.addEventListener("click", e => { if (!sheet.hidden && !sheet.contains(e.target)) closeSheet(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !sheet.hidden) { closeSheet(); more.focus(); } });
  window.addEventListener("resize", () => { if (window.innerWidth > 980) closeSheet(); });
  sheet.addEventListener("click", e => {
    const it = e.target.closest("[data-more]"); if (!it) return;
    closeSheet();
    if (page) return;  // plain links on the other pages
    const k = it.dataset.more;
    if (k === "res" && $("nav-res")) { e.preventDefault(); $("nav-res").click(); }
    else if (k === "cmp") { const b = document.querySelector('#view-seg [data-view="cmp"]'); if (b) { e.preventDefault(); b.click(); window.scrollTo(0, 0); } }
    else if (k === "ct" && $("tab-ct")) { e.preventDefault(); $("tab-ct").click(); }
  });
}
// the More button is "on" when the current section lives inside it
function syncMoreOn() {
  if (!more) return;
  const inMore = page === "calc" || (!page && (document.body.classList.contains("tab-ct") || document.body.classList.contains("view-cmp")));
  more.classList.toggle("on", inMore);
}
syncMoreOn();
new MutationObserver(syncMoreOn).observe(document.body, { attributes: true, attributeFilter: ["class"] });

if (!page) return;

// ---------------------------------------------------------------- account header on the other pages
const cur = document.querySelector(`.nav [data-nav="${page}"]`);
if (cur) { cur.classList.add("on"); cur.setAttribute("aria-current", "page"); }
const state = window.CHRONOS_AUTH = { user: null, csrf: null, ready: false };
const origFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  init = init || {};
  const method = (init.method || (input && input.method) || "GET").toUpperCase();
  const url = typeof input === "string" ? input : (input && input.url) || "";
  if (method !== "GET" && (url.startsWith("/") || url.startsWith(location.origin)) && state.csrf) {
    const h = new Headers(init.headers || {});
    if (!h.has("X-CSRF-Token")) h.set("X-CSRF-Token", state.csrf);
    init = Object.assign({}, init, { headers: h, credentials: "same-origin" });
  }
  return origFetch(input, init);
};
const mBtn = $("acct-menu-btn"), menu = $("acct-menu");
function render() {
  const u = state.user;
  $("acct-signin").hidden = !!u; $("acct-signup").hidden = !!u;
  $("acct-signout").hidden = !u;
  if ($("acct-email")) { $("acct-email").hidden = !u; $("acct-email").textContent = u ? u.email : ""; $("acct-email").title = u ? u.email : ""; }
  if (mBtn) mBtn.hidden = !u;
  if ($("acct-av")) $("acct-av").textContent = u && u.email ? u.email.trim().charAt(0).toUpperCase() : "";
  if (!u) closeMenu(); else fillMenu();
  document.dispatchEvent(new CustomEvent("chronos:auth", { detail: { user: u } }));
}
async function loadMe() {
  try {
    const r = await origFetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
    const j = await r.json();
    state.user = j.user || null; state.csrf = j.csrf || null;
  } catch (_) {}
  // page scripts after nav.js listen for chronos:auth; wait until they have all run
  if (document.readyState === "loading") await new Promise(r => document.addEventListener("DOMContentLoaded", r, { once: true }));
  state.ready = true;
  render();
}
// sign-in links come back to this page
["acct-signin", "acct-signup"].forEach(id => { const a = $(id); if (a) a.href = a.getAttribute("href") + "?next=" + encodeURIComponent(location.pathname); });
function closeMenu() { if (menu && !menu.hidden) { menu.hidden = true; mBtn.setAttribute("aria-expanded", "false"); } }
const dateTxt = s => { if (!s) return ""; const d = new Date(s); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Africa/Lagos" }); };
function fillMenu() {
  const u = state.user; if (!u || !$("am-email")) return;
  $("am-email").textContent = u.email || "";
  const plan = u.is_admin ? "Admin" : (u.plan || "free") + " plan";
  $("am-plan").textContent = plan.charAt(0).toUpperCase() + plan.slice(1);
  const rows = [];
  if (u.free_until) rows.push([new Date(u.free_until) < new Date() ? "Free access ended" : "Free access until", dateTxt(u.free_until)]);
  if (u.created_at) rows.push(["Member since", dateTxt(u.created_at)]);
  $("am-dl").innerHTML = rows.filter(r => r[1]).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  $("am-dl").hidden = !rows.length;
}
if (mBtn && menu) {
  mBtn.addEventListener("click", e => { e.stopPropagation(); const open = menu.hidden; menu.hidden = !open; mBtn.setAttribute("aria-expanded", String(open)); if (open) fillMenu(); });
  document.addEventListener("click", e => { if (!menu.hidden && !menu.contains(e.target) && e.target !== mBtn) closeMenu(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !menu.hidden) { closeMenu(); mBtn.focus(); } });
  menu.addEventListener("click", e => {
    const b = e.target.closest("[data-go]"); if (!b) return;
    location.href = b.dataset.go === "ct" ? "/copy-trading" : "/?view=library";
  });
}
$("acct-signout").addEventListener("click", async () => {
  const btn = $("acct-signout"); btn.disabled = true;
  try {
    const r = await window.fetch("/api/auth/signout", { method: "POST" });
    const j = await r.json().catch(() => ({}));
    state.user = null; if (j.csrf) state.csrf = j.csrf;
  } finally { btn.disabled = false; render(); }
});
const delGo = $("am-del-go");
if (delGo) delGo.addEventListener("click", async () => {
  const pw = $("am-del-pw").value, err = $("am-del-err");
  err.hidden = true;
  if (!pw) { err.textContent = "Enter your password to confirm."; err.hidden = false; return; }
  delGo.disabled = true;
  try {
    const r = await window.fetch("/api/auth/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw, confirm: true }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = j.error || "Could not delete the account."; err.hidden = false; return; }
    location.href = "/";
  } catch (_) { err.textContent = "Network error. Nothing was deleted."; err.hidden = false; }
  finally { delGo.disabled = false; $("am-del-pw").value = ""; }
});

// small helpers shared by the page scripts
window.CHRONOS_PAGE = {
  toast(msg, info) {
    const box = $("toasts"); if (!box) return;
    const t = document.createElement("div"); t.className = "toast" + (info ? " info" : ""); t.setAttribute("role", info ? "status" : "alert");
    const s = document.createElement("span"); s.textContent = msg; t.appendChild(s);
    const x = document.createElement("button"); x.type = "button"; x.setAttribute("aria-label", "Dismiss"); x.textContent = "×"; x.onclick = () => t.remove(); t.appendChild(x);
    box.appendChild(t); setTimeout(() => t.remove(), 6000);
  },
  async api(path, opts) {
    opts = opts || {};
    await ready;  // the CSRF token arrives with /api/auth/me
    const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let r;
    try { r = await window.fetch(path, init); } catch (_) { throw Object.assign(new Error("Could not reach CHRONOS. Check your connection."), { status: 0 }); }
    const j = await r.json().catch(() => ({ error: "Server returned an invalid response (" + r.status + ")" }));
    if (!r.ok || j.error) {
      if (r.status === 401) { state.user = null; render(); }
      throw Object.assign(new Error(j.error || "HTTP " + r.status), { status: r.status });
    }
    return j;
  },
  esc: s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
};
const ready = loadMe();
})();
