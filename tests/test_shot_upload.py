#!/usr/bin/env python3
"""Contract + Playwright Pixel checks for the screenshot file picker.

Does not call OpenAI. Set CHRONOS_URL to test a running site (e.g. production);
otherwise a local app.py is started with a dummy key so the panel stays enabled.
"""
from pathlib import Path
import glob, os, re, socket, subprocess, sys, time, unittest, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "web", "static", "app.js")
HTML = os.path.join(ROOT, "web", "static", "index.html")
CSS = os.path.join(ROOT, "web", "static", "style.css")
PW = os.path.join(ROOT, "tests", "shot_upload.cjs")


class TestUploadContracts(unittest.TestCase):
    def test_file_input_is_not_nested_in_the_drop_label(self):
        html = Path(HTML).read_text(encoding="utf-8")
        m = re.search(r'<label class="shot-drop"[^>]*>.*?</label>', html, re.S)
        self.assertTrue(m)
        self.assertNotIn("<input", m.group(0))
        self.assertIn('for="shot-file"', m.group(0))
        self.assertIn('id="shot-choose"', html)
        self.assertIn("Choose file", html)
        self.assertIn('id="shot-file"', html)
        self.assertIn("shot-choose-input", html)
        self.assertNotRegex(html, r'id="shot-file"[^>]*\bhidden\b')

    def test_js_listens_to_input_and_change_and_sniffs_magic(self):
        js = Path(JS).read_text(encoding="utf-8")
        self.assertIn('["change", "input"]', js)
        self.assertIn("onShotInputEvent", js)
        self.assertIn("sniffImageMagic", js)
        self.assertIn('type.startsWith("image/")', js)
        self.assertNotIn('$("shot-drop").addEventListener("click"', js)
        self.assertIn("syncShotFromInput", js)

    def test_choose_button_is_a_full_size_opacity_zero_input(self):
        css = Path(CSS).read_text(encoding="utf-8")
        self.assertIn(".shot-choose-input", css)
        self.assertIn("opacity:0", css)
        # must not clip the native control to 1px
        block = css.split(".shot-choose-input")[1].split("}")[0]
        self.assertNotIn("clip:", block)
        self.assertIn("inset:0", block)
        self.assertIn("width:100%", block)



def _playwright_node_path():
    """Directory that contains the `playwright` package, for NODE_PATH."""
    here = Path(__file__).resolve()
    candidates = []
    npm_npx = Path.home() / ".npm" / "_npx"
    if npm_npx.is_dir():
        candidates.extend(sorted(npm_npx.glob("*/node_modules")))
    for pattern in (
        "/usr/local/lib/pnpm/**/node_modules",
        "/usr/local/lib/node_modules",
        str(Path(ROOT) / "node_modules"),
    ):
        for p in glob.glob(pattern, recursive=True):
            candidates.append(Path(p))
    # Also honour an existing NODE_PATH
    for p in (os.environ.get("NODE_PATH") or "").split(os.pathsep):
        if p:
            candidates.append(Path(p))
    seen = []
    for p in candidates:
        if (p / "playwright" / "package.json").is_file() and p not in seen:
            seen.append(p)
    if not seen:
        raise RuntimeError("playwright is not installed for Node (looked in npx cache and NODE_PATH)")
    return os.pathsep.join(str(p) for p in seen)

def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _wait_http(url, timeout=20):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
                last = f"HTTP {r.status}"
        except Exception as e:
            last = str(e)
        time.sleep(0.2)
    raise RuntimeError(f"server did not start at {url}: {last}")


class TestUploadPlaywright(unittest.TestCase):
    def test_pixel_picker_attaches_empty_mime_file(self):
        url = os.environ.get("CHRONOS_URL", "").rstrip("/")
        proc = None
        if not url:
            port = _free_port()
            env = os.environ.copy()
            env["HOST"] = "127.0.0.1"
            env["PORT"] = str(port)
            env["OPENAI_API_KEY"] = "sk-test-not-used"
            proc = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "web", "app.py")],
                cwd=ROOT, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            )
            url = f"http://127.0.0.1:{port}"
        try:
            _wait_http(url + "/api/health")
            out_dir = os.path.join(ROOT, "web", "screenshots")
            os.makedirs(out_dir, exist_ok=True)
            shot = os.path.join(out_dir, "shot-picker-pixel.png")
            env = os.environ.copy()
            env["NODE_PATH"] = _playwright_node_path()
            r = subprocess.run(
                ["node", PW, url, shot],
                cwd=ROOT, capture_output=True, text=True, timeout=120, env=env,
            )
            sys.stdout.write(r.stdout)
            sys.stderr.write(r.stderr)
            self.assertEqual(r.returncode, 0, r.stdout + "\n" + r.stderr)
            self.assertIn("ALL_OK", r.stdout)
        finally:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
