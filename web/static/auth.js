// CHRONOS sign-up / sign-in / forgot-password page.
(() => {
const $ = id => document.getElementById(id);
const mode = location.pathname.replace(/\/+$/, "").split("/").pop() || "signin"; // signin | signup | forgot
const params = new URLSearchParams(location.search);
function safeNext() {
  const n = params.get("next") || "/";
  return (n.startsWith("/") && !n.startsWith("//") && !n.includes("\\")) ? n : "/";
}
const next = safeNext();
const q = next !== "/" ? "?next=" + encodeURIComponent(next) : "";
let csrf = null;

const COPY = {
  signin: { title: "Sign in", sub: "Welcome back. Sign in to your CHRONOS account.", btn: "Sign in",
    links: `<a href="/forgot${q}">Forgot password?</a><span>New here? <a href="/signup${q}">Create an account</a></span>` },
  signup: { title: "Create your account", sub: "Save your place for CHRONOS member tools like Copy trading.", btn: "Create account",
    links: `<span>Already have an account? <a href="/signin${q}">Sign in</a></span>` },
  forgot: { title: "Reset password", sub: "Enter your account email.", btn: "Send reset link",
    links: `<span><a href="/signin${q}">Back to sign in</a></span>` },
};
const c = COPY[mode] || COPY.signin;
document.title = c.title + " · CHRONOS";
$("auth-h").textContent = c.title;
$("auth-sub").textContent = c.sub;
$("auth-submit").querySelector(".txt").textContent = c.btn;
$("auth-links").innerHTML = c.links;
if (mode === "signup") {
  $("auth-free").hidden = false;
  $("pw-hint").hidden = false;
  $("password").autocomplete = "new-password";
}
if (mode === "forgot") $("pw-block").hidden = true;

fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" }).then(r => r.json()).then(j => {
  csrf = j.csrf;
  if (j.user && mode !== "forgot") location.replace(next);
}).catch(() => {});

$("pw-eye").addEventListener("click", () => {
  const pw = $("password"), show = pw.type === "password";
  pw.type = show ? "text" : "password";
  $("pw-eye").textContent = show ? "Hide" : "Show";
  $("pw-eye").setAttribute("aria-pressed", String(show));
  $("pw-eye").setAttribute("aria-label", show ? "Hide password" : "Show password");
});

function showErr(m) { $("auth-err").textContent = m || ""; $("auth-err").hidden = !m; }
function showOk(m) { $("auth-ok").textContent = m || ""; $("auth-ok").hidden = !m; }
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

$("auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  showErr(""); showOk("");
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!EMAIL.test(email)) return showErr("Enter a valid email address.");
  if (mode !== "forgot" && password.length < 8)
    return showErr(mode === "signup" ? "Password must be at least 8 characters." : "Wrong email or password.");
  const btn = $("auth-submit");
  btn.disabled = true;
  try {
    if (!csrf) { const m = await (await fetch("/api/auth/me", { credentials: "same-origin" })).json(); csrf = m.csrf; }
    const path = mode === "signup" ? "/api/auth/signup" : mode === "forgot" ? "/api/auth/forgot" : "/api/auth/signin";
    const body = mode === "forgot" ? { email } : { email, password };
    const r = await fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf || "" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (mode === "forgot") { showOk(j.message || "Password reset is coming soon."); return; }
    if (!r.ok) {
      if (j.csrf) csrf = null;
      return showErr(j.error || "Something went wrong. Try again.");
    }
    location.replace(next);
  } catch (_) {
    showErr("Network problem. Check your connection and try again.");
  } finally { btn.disabled = false; }
});
})();
