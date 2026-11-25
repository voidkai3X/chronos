#!/usr/bin/env python3
"""Offline tests for the web app's per-IP rate limits (no engine run, no network)."""
import importlib.util, os, sys, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("chronos_web", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec)
sys.modules["chronos_web"] = web
spec.loader.exec_module(web)


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        web.reset_rate_limits()
        web.app.config["TESTING"] = True
        self.c = web.app.test_client()

    def tearDown(self):
        web.reset_rate_limits()
        web.app.config["TESTING"] = False

    def post(self, ip, path="/api/test", json=None, headers=None, **kw):
        return self.c.post(path, json=json if json is not None else {},
                           headers=headers or {},
                           environ_base={"REMOTE_ADDR": ip}, **kw)

    def test_constants(self):
        self.assertEqual(web.RATE_HEAVY_PER_MIN, 6)
        self.assertEqual(web.RATE_HEAVY_CONCURRENT_PER_IP, 1)
        self.assertEqual(web.MAX_PARALLEL, 2)
        self.assertFalse(web.app.config.get("DEBUG"))

    def test_six_heavy_requests_then_429(self):
        codes = []
        for _ in range(6):
            r = self.post("203.0.113.10")
            codes.append(r.status_code)
            self.assertNotEqual(r.status_code, 429, r.get_json())
        self.assertTrue(all(c == 400 for c in codes), codes)  # empty body is invalid, but counts
        r = self.post("203.0.113.10")
        self.assertEqual(r.status_code, 429)
        self.assertIn("too many tests", r.get_json()["error"])

    def test_other_ip_is_not_blocked(self):
        for _ in range(6):
            self.assertEqual(self.post("203.0.113.10").status_code, 400)
        self.assertEqual(self.post("203.0.113.10").status_code, 429)
        r = self.post("203.0.113.20")
        self.assertEqual(r.status_code, 400)

    def test_compare_and_scan_share_the_same_budget(self):
        for _ in range(3):
            self.assertNotEqual(self.post("198.51.100.7", "/api/test").status_code, 429)
        for _ in range(2):
            self.assertNotEqual(self.post("198.51.100.7", "/api/compare").status_code, 429)
        self.assertNotEqual(self.post("198.51.100.7", "/api/scan").status_code, 429)
        r = self.post("198.51.100.7", "/api/test")
        self.assertEqual(r.status_code, 429)

    def test_light_routes_are_not_rate_limited(self):
        for _ in range(8):
            self.post("203.0.113.10")
        # /api/health and / are not heavy
        h = self.c.get("/api/health", environ_base={"REMOTE_ADDR": "203.0.113.10"})
        self.assertEqual(h.status_code, 200); h.close()
        home = self.c.get("/", environ_base={"REMOTE_ADDR": "203.0.113.10"})
        self.assertEqual(home.status_code, 200); home.close()

    def test_one_concurrent_heavy_run_per_ip(self):
        web._heavy_inflight["203.0.113.30"] = 1
        r = self.post("203.0.113.30")
        self.assertEqual(r.status_code, 429)
        self.assertIn("already running", r.get_json()["error"])
        # a different IP can still start
        r2 = self.post("203.0.113.31")
        self.assertEqual(r2.status_code, 400)

    def test_concurrent_slot_is_released(self):
        # a finished (invalid) request must free the inflight slot
        self.assertEqual(self.post("203.0.113.40").status_code, 400)
        self.assertNotIn("203.0.113.40", web._heavy_inflight)
        self.assertEqual(self.post("203.0.113.40").status_code, 400)

    def test_cf_connecting_ip_trusted_only_from_localhost(self):
        # spoofed header from a public client is ignored
        for _ in range(6):
            self.post("8.8.8.8", headers={"CF-Connecting-IP": "203.0.113.50"})
        # 8.8.8.8 is now exhausted; the spoofed IP is not
        self.assertEqual(self.post("8.8.8.8", headers={"CF-Connecting-IP": "203.0.113.50"}).status_code, 429)
        self.assertEqual(self.post("203.0.113.50").status_code, 400)
        # from the local tunnel, the Cloudflare header is the real client
        for _ in range(6):
            self.post("127.0.0.1", headers={"CF-Connecting-IP": "203.0.113.60"})
        self.assertEqual(self.post("127.0.0.1", headers={"CF-Connecting-IP": "203.0.113.60"}).status_code, 429)
        # a different forwarded client is still allowed
        self.assertEqual(self.post("127.0.0.1", headers={"CF-Connecting-IP": "203.0.113.61"}).status_code, 400)

    def test_minute_window_expires(self):
        ip = "203.0.113.70"
        now = time.time()
        web._heavy_hits[ip] = [now - 61] * 6
        r = self.post(ip)
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
