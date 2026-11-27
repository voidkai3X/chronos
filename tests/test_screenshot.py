#!/usr/bin/env python3
"""Offline tests for screenshot reading (vision is mocked; no network, no API key used)."""
import importlib.util, io, os, struct, sys, time, unittest, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))

spec_shot = importlib.util.spec_from_file_location("screenshot", os.path.join(ROOT, "web", "screenshot.py"))
shot = importlib.util.module_from_spec(spec_shot)
sys.modules["screenshot"] = shot
spec_shot.loader.exec_module(shot)

spec_web = importlib.util.spec_from_file_location("chronos_web", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec_web)
sys.modules["chronos_web"] = web
spec_web.loader.exec_module(web)


def png_bytes():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00\x00\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


PNG = png_bytes()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF" + struct.pack("<I", 16) + b"WEBP" + b"\x00" * 8

MARKETS = [
    {"market_id": "veranta|USDJPY", "symbol": "JPY", "coin": "USDJPY", "provider": "veranta", "default_venue": True},
    {"market_id": "veranta|EURUSD", "symbol": "EUR", "coin": "EURUSD", "provider": "veranta", "default_venue": True},
    {"market_id": "binance|BTCUSDT", "symbol": "BTC", "coin": "BTCUSDT", "provider": "binance", "default_venue": True},
    {"market_id": "other|BTCUSDT", "symbol": "BTC", "coin": "BTCUSDT", "provider": "other", "default_venue": False},
    {"market_id": "binance|XAUUSDT", "symbol": "XAU", "coin": "XAUUSDT", "provider": "binance", "default_venue": True},
    {"market_id": "hyperliquid|xyz:SP500", "symbol": "SP500", "coin": "xyz:SP500", "provider": "hyperliquid", "default_venue": True},
    {"market_id": "ostium|DJI", "symbol": "DJI", "coin": "DJI", "provider": "ostium", "default_venue": True},
    {"market_id": "composite|XYZ100", "symbol": "XYZ100", "coin": "XYZ100", "provider": "composite", "default_venue": True},
    {"market_id": "hyperliquid|xyz:BRENTOIL", "symbol": "BRENTOIL", "coin": "xyz:BRENTOIL", "provider": "hyperliquid", "default_venue": True},
]


def conf(v=0.95):
    return {k: v for k in ("symbol", "side", "timeframe", "entry", "take_profit", "stop_loss")}


class TestImageAndParse(unittest.TestCase):
    def test_sniff_types(self):
        self.assertEqual(shot.sniff_image(PNG), "image/png")
        self.assertEqual(shot.sniff_image(JPEG), "image/jpeg")
        self.assertEqual(shot.sniff_image(WEBP), "image/webp")
        self.assertIsNone(shot.sniff_image(b"GIF89a"))
        self.assertIsNone(shot.sniff_image(b"<svg></svg>"))
        self.assertIsNone(shot.sniff_image(b"%PDF-1.4"))
        self.assertIsNone(shot.sniff_image(b""))

    def test_validate_rejects_bad_and_huge(self):
        data, mime = shot.validate_image(PNG, "chart.png")
        self.assertEqual(mime, "image/png")
        with self.assertRaises(shot.ScreenshotError) as e:
            shot.validate_image(b"not-an-image", "chart.png")
        self.assertIn("valid PNG", e.exception.message)
        with self.assertRaises(shot.ScreenshotError) as e:
            shot.validate_image(b"hello", "notes.txt")
        self.assertIn("PNG, JPG or WebP", e.exception.message)
        with self.assertRaises(shot.ScreenshotError) as e:
            shot.validate_image(b"\x89PNG" + b"x" * (shot.MAX_BYTES + 1), "big.png")
        self.assertIn("too large", e.exception.message)
        with self.assertRaises(shot.ScreenshotError):
            shot.validate_image(None)

    def test_pattern_and_prices(self):
        self.assertEqual(shot.clean_pattern("  SMC   order  "), "SMC order")
        self.assertEqual(len(shot.clean_pattern("x" * 400)), shot.PATTERN_MAX)
        self.assertEqual(shot.clean_price(159.5), 159.5)
        self.assertIsNone(shot.clean_price(0))
        self.assertIsNone(shot.clean_price(-1))
        self.assertIsNone(shot.clean_price("n/a"))
        self.assertIsNone(shot.clean_price(None))
        self.assertEqual(shot.normalize_side("BUY"), "long")
        self.assertEqual(shot.normalize_side("Sell"), "short")
        self.assertIsNone(shot.normalize_side("maybe"))
        self.assertEqual(shot.normalize_timeframe("H1"), ("1h", "1h"))
        self.assertEqual(shot.normalize_timeframe("4h"), ("4h", "4h"))
        self.assertEqual(shot.normalize_timeframe("M15"), ("15m", "15m"))
        disp, sim = shot.normalize_timeframe("D1")
        self.assertEqual(disp, "1d")  # recognised and shown in a standard form, but not a fingerprint timeframe
        self.assertIsNone(sim)
        self.assertEqual(shot.normalize_timeframe("M5"), ("5m", None))
        self.assertEqual(shot.normalize_timeframe("weird tf"), ("weird tf", None))

    def test_match_market_fuzzy(self):
        def mid(q):
            m = shot.match_market(q, MARKETS)
            return m["market_id"] if m else None
        self.assertEqual(mid("USDJPY"), "veranta|USDJPY")
        self.assertEqual(mid("USD/JPY"), "veranta|USDJPY")
        self.assertEqual(mid("usd jpy"), "veranta|USDJPY")
        self.assertEqual(mid("BTC"), "binance|BTCUSDT")  # default venue, not other|
        self.assertEqual(mid("btc/usdt"), "binance|BTCUSDT")
        self.assertEqual(mid("Bitcoin"), "binance|BTCUSDT")
        self.assertEqual(mid("XAUUSD"), "binance|XAUUSDT")
        self.assertEqual(mid("GOLD"), "binance|XAUUSDT")
        self.assertEqual(mid("S&P 500"), "hyperliquid|xyz:SP500")
        self.assertEqual(mid("NAS100"), "composite|XYZ100")
        self.assertEqual(mid("US30"), "ostium|DJI")
        self.assertEqual(mid("UKOIL"), "hyperliquid|xyz:BRENTOIL")
        self.assertIsNone(mid(""))
        self.assertIsNone(mid("NOTAPAIR"))

    def test_parse_does_not_invent_prices(self):
        parsed = shot.parse_vision_payload({
            "symbol": "USDJPY", "side": "short", "timeframe": "1h",
            "entry": None, "take_profit": None, "stop_loss": None,
            "confidence": conf(0.9), "notes": "levels not labelled",
        })
        self.assertEqual(parsed["symbol"], "USDJPY")
        self.assertEqual(parsed["side"], "short")
        self.assertIsNone(parsed["entry"])
        self.assertIsNone(parsed["tp"])
        self.assertIsNone(parsed["sl"])

    def test_parse_rejects_empty_and_non_positive(self):
        with self.assertRaises(shot.ScreenshotError) as e:
            shot.parse_vision_payload({
                "symbol": None, "side": None, "timeframe": None,
                "entry": None, "take_profit": None, "stop_loss": None,
                "confidence": conf(0), "notes": "",
            })
        self.assertIn("clearer screenshot", e.exception.message)
        parsed = shot.parse_vision_payload({
            "symbol": "BTC", "side": "long", "timeframe": "1h",
            "entry": 0, "take_profit": -5, "stop_loss": "na",
            "confidence": conf(0.9), "notes": "",
        })
        self.assertIsNone(parsed["entry"])
        self.assertIsNone(parsed["tp"])
        self.assertIsNone(parsed["sl"])

    def test_build_response_flags_missing_and_low_confidence(self):
        parsed = shot.parse_vision_payload({
            "symbol": "USDJPY", "side": "short", "timeframe": "1h",
            "entry": 159.5, "take_profit": None, "stop_loss": 160.4,
            "confidence": {**conf(0.95), "entry": 0.4, "symbol": 0.95, "side": 0.95, "stop_loss": 0.95},
            "notes": "TP line was cut off",
        })
        out = shot.build_response(parsed, MARKETS, pattern="SMC")
        self.assertEqual(out["pattern"], "SMC")
        self.assertEqual(out["market"]["market_id"], "veranta|USDJPY")
        self.assertEqual(out["side"], "short")
        self.assertEqual(out["entry"], 159.5)
        self.assertIsNone(out["tp"])
        self.assertEqual(out["sl"], 160.4)
        self.assertIn("take profit", out["missing"])
        self.assertIn("entry", out["low_confidence"])
        self.assertNotIn("market", out["missing"])
        self.assertIn("Review", out["review"])
        self.assertIn("TP line", out["notes"])

    def test_unmatched_symbol_left_blank(self):
        parsed = shot.parse_vision_payload({
            "symbol": "ZZZZZZ", "side": "long", "timeframe": "1h",
            "entry": 10, "take_profit": 12, "stop_loss": 9,
            "confidence": conf(0.9), "notes": "",
        })
        out = shot.build_response(parsed, MARKETS, pattern="")
        self.assertIsNone(out["market"])
        self.assertEqual(out["symbol_raw"], "ZZZZZZ")
        self.assertIn("market", out["missing"])

    def test_openai_error_messages_are_friendly(self):
        class Auth(Exception):
            status_code = 401
        class Busy(Exception):
            status_code = 429
        class Boom(Exception):
            status_code = 500
        class Slow(Exception):
            pass
        Slow.__name__ = "APITimeoutError"
        self.assertEqual(shot._map_openai_error(Auth()).status, 502)
        self.assertIn("API key", shot._map_openai_error(Auth()).message)
        self.assertEqual(shot._map_openai_error(Busy()).status, 503)
        self.assertIn("busy", shot._map_openai_error(Busy()).message)
        self.assertEqual(shot._map_openai_error(Boom()).status, 503)
        self.assertEqual(shot._map_openai_error(Slow()).status, 504)
        # never leak the exception text
        class Weird(Exception):
            status_code = 418
        e = shot._map_openai_error(Weird("sk-secret-should-not-leak"))
        self.assertNotIn("sk-secret", e.message)

    def test_quota_429_is_not_called_busy(self):
        class Quota(Exception):
            status_code = 429
            body = {"error": {"type": "insufficient_quota", "code": "insufficient_quota",
                              "message": "credit_balance_exhausted. You exceeded your current quota."}}
        class Rate(Exception):
            status_code = 429
            body = {"error": {"type": "rate_limit_error", "code": "rate_limit_exceeded",
                              "message": "Rate limit reached for gpt-4o-mini"}}
        class Billing(Exception):
            status_code = 429
            code = "billing_not_active"
        q = shot._map_openai_error(Quota("Error code: 429 - insufficient_quota"))
        self.assertEqual(q.status, 503)
        self.assertEqual(q.message, shot.QUOTA_MSG)
        self.assertNotIn("busy", q.message)
        r = shot._map_openai_error(Rate("Rate limit reached"))
        self.assertEqual(r.status, 503)
        self.assertIn("busy", r.message)
        self.assertNotEqual(r.message, shot.QUOTA_MSG)
        b = shot._map_openai_error(Billing("billing_not_active"))
        self.assertEqual(b.message, shot.QUOTA_MSG)
        # public message must not leak the raw SDK body
        self.assertNotIn("insufficient_quota", q.message)
        self.assertNotIn("credit_balance_exhausted", q.message)


class TestScreenshotAPI(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "OPENAI_VISION_MODEL", "RATE_SCREENSHOT_PER_DAY")}
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_VISION_MODEL", None)
        os.environ.pop("RATE_SCREENSHOT_PER_DAY", None)
        web.reset_rate_limits()
        web.app.config["TESTING"] = True
        self.c = web.app.test_client()
        self._vision = web.shot.call_vision
        self._markets = web.tester.load_markets
        web.tester.load_markets = lambda: MARKETS

    def tearDown(self):
        web.shot.call_vision = self._vision
        web.tester.load_markets = self._markets
        web.reset_rate_limits()
        web.app.config["TESTING"] = False
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _enable(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-not-used"

    def post_img(self, ip="203.0.113.80", data=None, name="chart.png", pattern=None, headers=None):
        body = {"image": (io.BytesIO(data if data is not None else PNG), name)}
        if pattern is not None:
            body["pattern"] = pattern
        return self.c.post("/api/screenshot", data=body, content_type="multipart/form-data",
                           headers=headers or {}, environ_base={"REMOTE_ADDR": ip})

    def test_status_disabled_without_key(self):
        r = self.c.get("/api/screenshot")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertFalse(j["enabled"])
        self.assertNotIn("model", j)
        h = self.c.get("/api/health")
        self.assertEqual(h.status_code, 200)
        self.assertFalse(h.get_json()["screenshot"])

    def test_post_disabled_without_key(self):
        r = self.post_img()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"], "Screenshot reading isn't enabled yet")
        # does not consume the heavy budget
        self.assertFalse(web._heavy_hits)

    def test_bad_image_is_400_and_not_daily(self):
        self._enable()
        r = self.post_img(data=b"hello", name="notes.txt")
        self.assertEqual(r.status_code, 400)
        self.assertIn("PNG, JPG or WebP", r.get_json()["error"])
        self.assertFalse(web._shot_hits)

    def test_missing_file(self):
        self._enable()
        r = self.c.post("/api/screenshot", data={"pattern": "SMC"},
                        content_type="multipart/form-data",
                        environ_base={"REMOTE_ADDR": "203.0.113.80"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("attach", r.get_json()["error"])

    def test_extracts_and_maps_market(self):
        self._enable()
        def fake(_b, _m, pattern=""):
            return shot.parse_vision_payload({
                "symbol": "USD/JPY", "side": "short", "timeframe": "H1",
                "entry": 159.5, "take_profit": 156.83, "stop_loss": 160.465,
                "confidence": conf(0.96), "notes": "",
            })
        web.shot.call_vision = fake
        r = self.post_img(pattern="SMC")
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["pattern"], "SMC")
        self.assertEqual(j["market"]["market_id"], "veranta|USDJPY")
        self.assertEqual(j["side"], "short")
        self.assertEqual(j["similar_tf"], "1h")
        self.assertEqual(j["entry"], 159.5)
        self.assertEqual(j["tp"], 156.83)
        self.assertEqual(j["sl"], 160.465)
        self.assertEqual(j["missing"], [])
        self.assertIn("Review", j["review"])
        self.assertIn("does not start", j["review"])

    def test_leaves_missing_prices_blank(self):
        self._enable()
        def fake(_b, _m, pattern=""):
            return shot.parse_vision_payload({
                "symbol": "BTC", "side": "long", "timeframe": "4h",
                "entry": 60400, "take_profit": None, "stop_loss": None,
                "confidence": {**conf(0.9), "take_profit": 0.1, "stop_loss": 0.1},
                "notes": "no SL or TP lines",
            })
        web.shot.call_vision = fake
        r = self.post_img()
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertEqual(j["market"]["market_id"], "binance|BTCUSDT")
        self.assertEqual(j["entry"], 60400)
        self.assertIsNone(j["tp"])
        self.assertIsNone(j["sl"])
        self.assertIn("take profit", j["missing"])
        self.assertIn("stop loss", j["missing"])

    def test_unreadable_is_422(self):
        self._enable()
        def fake(_b, _m, pattern=""):
            return shot.parse_vision_payload({
                "symbol": None, "side": None, "timeframe": None,
                "entry": None, "take_profit": None, "stop_loss": None,
                "confidence": conf(0), "notes": "not a chart",
            })
        web.shot.call_vision = fake
        r = self.post_img()
        self.assertEqual(r.status_code, 422)
        self.assertIn("clearer screenshot", r.get_json()["error"])

    def test_openai_failure_is_friendly(self):
        self._enable()
        def fake(_b, _m, pattern=""):
            raise shot.ScreenshotError("The vision service is busy, try again in a minute", 503)
        web.shot.call_vision = fake
        r = self.post_img()
        self.assertEqual(r.status_code, 503)
        self.assertIn("busy", r.get_json()["error"])

    def test_quota_error_reaches_the_client(self):
        self._enable()
        def fake(_b, _m, pattern=""):
            raise shot.ScreenshotError(shot.QUOTA_MSG, 503)
        web.shot.call_vision = fake
        r = self.post_img()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"], shot.QUOTA_MSG)

    def test_shares_heavy_budget_with_test(self):
        self._enable()
        web.shot.call_vision = lambda *_a, **_k: shot.parse_vision_payload({
            "symbol": "BTC", "side": "long", "timeframe": "1h",
            "entry": 1, "take_profit": 2, "stop_loss": 0.5,
            "confidence": conf(0.9), "notes": "",
        })
        ip = "198.51.100.40"
        for _ in range(3):
            self.assertNotEqual(self.c.post("/api/test", json={}, environ_base={"REMOTE_ADDR": ip}).status_code, 429)
        for _ in range(3):
            self.assertNotEqual(self.post_img(ip=ip).status_code, 429)
        r = self.post_img(ip=ip)
        self.assertEqual(r.status_code, 429)
        self.assertIn("too many tests", r.get_json()["error"])

    def test_daily_cap(self):
        self._enable()
        os.environ["RATE_SCREENSHOT_PER_DAY"] = "2"
        web.shot.call_vision = lambda *_a, **_k: shot.parse_vision_payload({
            "symbol": "BTC", "side": "long", "timeframe": "1h",
            "entry": 1, "take_profit": 2, "stop_loss": 0.5,
            "confidence": conf(0.9), "notes": "",
        })
        ip = "198.51.100.41"
        self.assertEqual(self.post_img(ip=ip).status_code, 200)
        self.assertEqual(self.post_img(ip=ip).status_code, 200)
        r = self.post_img(ip=ip)
        self.assertEqual(r.status_code, 429)
        self.assertIn("daily screenshot", r.get_json()["error"])
        # a different IP is still allowed
        self.assertEqual(self.post_img(ip="198.51.100.42").status_code, 200)

    def test_failed_openai_calls_do_not_count_against_daily_cap(self):
        self._enable()
        os.environ["RATE_SCREENSHOT_PER_DAY"] = "1"
        ip = "198.51.100.44"
        def fail(_b, _m, pattern=""):
            raise shot.ScreenshotError(shot.QUOTA_MSG, 503)
        def busy(_b, _m, pattern=""):
            raise shot.ScreenshotError(shot.BUSY_MSG, 503)
        def ok(_b, _m, pattern=""):
            return shot.parse_vision_payload({
                "symbol": "BTC", "side": "long", "timeframe": "1h",
                "entry": 1, "take_profit": 2, "stop_loss": 0.5,
                "confidence": conf(0.9), "notes": "",
            })
        web.shot.call_vision = fail
        self.assertEqual(self.post_img(ip=ip).status_code, 503)
        web.shot.call_vision = busy
        self.assertEqual(self.post_img(ip=ip).status_code, 503)
        self.assertFalse(web._shot_hits.get(ip))
        web.shot.call_vision = ok
        self.assertEqual(self.post_img(ip=ip).status_code, 200)
        r = self.post_img(ip=ip)
        self.assertEqual(r.status_code, 429)
        self.assertIn("daily screenshot", r.get_json()["error"])

    def test_unreadable_chart_does_count_against_daily_cap(self):
        self._enable()
        os.environ["RATE_SCREENSHOT_PER_DAY"] = "1"
        ip = "198.51.100.45"
        def unreadable(_b, _m, pattern=""):
            return shot.parse_vision_payload({
                "symbol": None, "side": None, "timeframe": None,
                "entry": None, "take_profit": None, "stop_loss": None,
                "confidence": conf(0), "notes": "not a chart",
            })
        web.shot.call_vision = unreadable
        self.assertEqual(self.post_img(ip=ip).status_code, 422)
        r = self.post_img(ip=ip)
        self.assertEqual(r.status_code, 429)
        self.assertIn("daily screenshot", r.get_json()["error"])

    def test_index_is_not_cached_and_assets_are_versioned(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        cc = r.headers.get("Cache-Control", "")
        self.assertIn("no-cache", cc)
        html = r.get_data(as_text=True)
        self.assertIn("/static/app.js?v=", html)
        self.assertIn("/static/style.css?v=", html)
        self.assertIn('id="shot-file"', html)
        self.assertIn('for="shot-file"', html)
        self.assertNotIn('id="shot-file" hidden', html)
        self.assertNotIn("shot-file hidden", html)

    def test_daily_window_expires(self):
        self._enable()
        os.environ["RATE_SCREENSHOT_PER_DAY"] = "1"
        ip = "198.51.100.43"
        web._shot_hits[ip] = [time.time() - 86401]
        web.shot.call_vision = lambda *_a, **_k: shot.parse_vision_payload({
            "symbol": "BTC", "side": "long", "timeframe": "1h",
            "entry": 1, "take_profit": 2, "stop_loss": 0.5,
            "confidence": conf(0.9), "notes": "",
        })
        self.assertEqual(self.post_img(ip=ip).status_code, 200)

    def test_status_enabled_with_key(self):
        self._enable()
        os.environ["OPENAI_VISION_MODEL"] = "gpt-4o-mini"
        r = self.c.get("/api/screenshot")
        j = r.get_json()
        self.assertTrue(j["enabled"])
        self.assertEqual(j["model"], "gpt-4o-mini")
        self.assertEqual(self.c.get("/api/health").get_json()["screenshot"], True)

    def test_clean_pattern_attached_on_test_error_is_harmless(self):
        # empty /api/test is still 400; pattern does not change validation
        r = self.c.post("/api/test", json={"pattern": "SMC"}, environ_base={"REMOTE_ADDR": "203.0.113.99"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("market", r.get_json()["error"])


class TestConfirmationPanel(unittest.TestCase):
    """Phase 1: uncertain values are never assumed; the page gets a per-field review list."""

    def build(self, **over):
        raw = {"symbol": "BTCUSDT", "side": "long", "pattern": "Fair value gap", "timeframe": "15m",
               "entry": 60000, "take_profit": 62000, "stop_loss": 59000,
               "confidence": {"symbol": 0.95, "side": 0.9, "pattern": 0.9, "timeframe": 0.9,
                              "entry": 0.95, "take_profit": 0.95, "stop_loss": 0.95}, "notes": ""}
        conf_over = over.pop("confidence", {})
        raw.update(over)
        raw["confidence"] = dict(raw["confidence"], **conf_over)
        return shot.build_response(shot.parse_vision_payload(raw), MARKETS, pattern=over.get("_pattern", ""))

    def test_clean_read_fills_everything(self):
        out = self.build()
        self.assertEqual((out["entry"], out["tp"], out["sl"], out["side"]), (60000, 62000, 59000, "long"))
        self.assertEqual(out["similar_tf"], "15m")
        self.assertEqual(out["pattern"], "Fair value gap")
        self.assertEqual(out["low_confidence"], [])
        self.assertEqual(out["suggested"], {})
        self.assertTrue(out["levels_consistent"])
        self.assertEqual({f["key"]: f["action"] for f in out["fields"]}["entry"], "filled")

    def test_very_low_confidence_price_is_left_blank_and_only_suggested(self):
        out = self.build(confidence={"stop_loss": 0.2})
        self.assertIsNone(out["sl"])
        self.assertEqual(out["suggested"]["sl"], 59000)
        self.assertIn("stop loss", out["missing"])
        f = {x["key"]: x for x in out["fields"]}["stop_loss"]
        self.assertEqual((f["action"], f["level"]), ("suggested", "very low"))
        self.assertIn("too unclear", out["review"])

    def test_low_confidence_side_and_market_not_applied(self):
        out = self.build(confidence={"side": 0.1, "symbol": 0.2})
        self.assertIsNone(out["side"])
        self.assertIsNone(out["market"])
        self.assertEqual(out["suggested"]["market"]["market_id"], "binance|BTCUSDT")
        self.assertIn("side", out["missing"])
        self.assertIn("market", out["missing"])

    def test_medium_confidence_is_filled_but_flagged(self):
        out = self.build(confidence={"take_profit": 0.55})
        self.assertEqual(out["tp"], 62000)
        self.assertIn("take profit", out["low_confidence"])
        self.assertEqual({f["key"]: f["action"] for f in out["fields"]}["take_profit"], "check")

    def test_levels_that_contradict_the_side_are_all_flagged(self):
        out = self.build(stop_loss=61000)  # long with stop above entry
        self.assertFalse(out["levels_consistent"])
        for k in ("side", "entry", "take profit", "stop loss"):
            self.assertIn(k, out["low_confidence"])
        self.assertIn("do not match the side", out["review"])

    def test_pattern_rules(self):
        self.assertEqual(self.build(_pattern="My ICT model")["pattern"], "My ICT model")  # trader's own label wins
        self.assertEqual(self.build(confidence={"pattern": 0.5})["pattern"], "")  # unsure pattern is not applied
        self.assertEqual(self.build(confidence={"pattern": 0.5})["pattern_read"], "Fair value gap")

    def test_unsupported_timeframe_is_reported_not_forced(self):
        out = self.build(timeframe="D1")
        self.assertEqual(out["timeframe"], "1d")
        self.assertIsNone(out["similar_tf"])
        self.assertIn("left at its default", out["review"])

    def test_schema_asks_for_pattern_with_confidence(self):
        self.assertIn("pattern", shot.SCHEMA["required"])
        self.assertIn("pattern", shot.SCHEMA["properties"]["confidence"]["required"])
        self.assertIn("Do not name a pattern from candle shapes alone", shot.SYSTEM)


if __name__ == "__main__":
    unittest.main()
