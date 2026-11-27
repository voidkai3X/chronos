// CHRONOS accounts on the main page: header buttons, CSRF header on POSTs, Backtester / Copy trading tabs.
(() => {
const $ = id => document.getElementById(id);
const state = window.CHRONOS_AUTH = { user: null, csrf: null, ready: false };

// Add the CSRF token to every same-origin POST (needed when REQUIRE_LOGIN is on, harmless otherwise).
const origFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  init = init || {};
  const method = (init.method || (input && input.method) || "GET").toUpperCase();
  const url = typeof input === "string" ? input : (input && input.url) || "";
  const sameOrigin = url.startsWith("/") || url.startsWith(location.origin);
  if (method !== "GET" && sameOrigin && state.csrf) {
    const h = new Headers(init.headers || {});
    if (!h.has("X-CSRF-Token")) h.set("X-CSRF-Token", state.csrf);
    init = Object.assign({}, init, { headers: h, credentials: "same-origin" });
  }
  return origFetch(input, init);
};

function renderAcct() {
  const u = state.user;
  $("acct-signin").hidden = !!u;
  $("acct-signup").hidden = !!u;
  $("acct-email").hidden = !u;
  $("acct-signout").hidden = !u;
  $("acct-email").textContent = u ? u.email : "";
  $("acct-email").title = u ? u.email : "";
}

async function loadMe() {
  try {
    const r = await origFetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
    const j = await r.json();
    state.user = j.user || null;
    state.csrf = j.csrf || null;
    state.requireLogin = !!j.require_login;
  } catch (_) { /* offline: stay signed out */ }
  state.ready = true;
  renderAcct();
  renderCopy();
}

$("acct-signout").addEventListener("click", async () => {
  const btn = $("acct-signout");
  btn.disabled = true;
  try {
    const r = await window.fetch("/api/auth/signout", { method: "POST" });
    const j = await r.json().catch(() => ({}));
    state.user = null;
    if (j.csrf) state.csrf = j.csrf;
  } finally { btn.disabled = false; renderAcct(); renderCopy(); }
});

// ---------- tabs
function currentTab() {
  return (location.pathname === "/copy-trading" || location.hash === "#copy-trading") ? "ct" : "bt";
}
function showTab(t) {
  const ct = t === "ct";
  $("view-bt").hidden = ct;
  $("view-ct").hidden = !ct;
  document.querySelector(".hero-in").hidden = ct;
  $("tab-bt").classList.toggle("on", !ct);
  $("tab-ct").classList.toggle("on", ct);
  $("tab-bt").setAttribute("aria-selected", String(!ct));
  $("tab-ct").setAttribute("aria-selected", String(ct));
  document.title = ct ? "Copy trading · CHRONOS" : "CHRONOS · Backtest your setup on real market history";
  if (ct) renderCopy();
}
function go(e, t, path) {
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
  e.preventDefault();
  if (location.pathname !== path || location.hash) history.pushState({ tab: t }, "", path);
  showTab(t);
  window.scrollTo(0, 0);
}
$("tab-bt").addEventListener("click", e => go(e, "bt", "/"));
$("tab-ct").addEventListener("click", e => go(e, "ct", "/copy-trading"));
window.addEventListener("popstate", () => showTab(currentTab()));

let copyLoadedFor = null;
async function renderCopy() {
  if (!state.ready) return;
  const u = state.user;
  $("ct-locked").hidden = !!u;
  $("ct-open").hidden = !u;
  if (!u || $("view-ct").hidden || copyLoadedFor === u.id) return;
  try {
    const r = await origFetch("/api/copy-trading", { credentials: "same-origin", cache: "no-store" });
    const j = await r.json();
    if (r.ok && j.message) { $("ct-msg").textContent = j.message; copyLoadedFor = u.id; }
    else if (r.status === 401) { state.user = null; renderAcct(); renderCopy(); }
    else if (j.error) $("ct-msg").textContent = j.error;
  } catch (_) {}
}

showTab(currentTab());
loadMe();
})();
