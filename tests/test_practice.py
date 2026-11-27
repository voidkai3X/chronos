#!/usr/bin/env python3
"""Offline tests for Blind Replay: no future candle ever reaches the client before the session ends (every endpoint),
conservative fill/exit rules, ownership, sign-in + CSRF, challenge rules, stats and account deletion.
Candles here are synthetic test fixtures injected through practice.fetch_bars; production always loads real data.
Run: python3 tests/test_practice.py
"""
import importlib.util, json, os, random, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="chronos-practice-")
os.environ.pop("DATABASE_URL", None)
os.environ["SQLITE_PATH"] = os.path.join(TMP, "p.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-production"
os.environ["COOKIE_INSECURE"] = "1"
os.environ.pop("REQUIRE_LOGIN", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("chronos_web_practice", os.path.join(ROOT, "web", "app.py"))
web = importlib.util.module_from_spec(spec)
sys.modules["chronos_web_practice"] = web
spec.loader.exec_module(web)
auth, practice, tester = web.auth, web.practice, web.tester
from sqlalchemy import select

PW = "correct horse battery"
MARKETS = [{"market_id": "binance|BTCUSDT", "symbol": "BTC", "coin": "BTCUSDT", "provider": "binance", "default_venue": True},
           {"market_id": "veranta|EURUSD", "symbol": "EUR", "coin": "EURUSD", "provider": "veranta", "default_venue": True}]
H = 3_600_000


def fake_fetch(m, interval, a, b):
    """Synthetic fixture: a random walk with unique, many-digit prices so any leaked value is detectable."""
    rng = random.Random(a)
    step = tester.STEP[interval]
    t = a - a % step + step
    px, out = 100.0, []
    while t < b:
        o = px * (1 + rng.uniform(-0.0004, 0.0004))  # tiny gap so no future value equals a visible one
        c = o * (1 + rng.uniform(-0.004, 0.004))
        hi = max(o, c) * (1 + rng.uniform(0.0001, 0.003))
        lo = min(o, c) * (1 - rng.uniform(0.0001, 0.003))
        out.append((t, round(o, 9), round(hi, 9), round(lo, 9), round(c, 9)))
        px, t = c, t + step
    return out, {"segments": [{"source": "mfp", "label": "MFP feed", "from_ms": a, "to_ms": b}]}


class Base(unittest.TestCase):
    def setUp(self):
        auth.reset_engine()
        if os.path.exists(os.environ["SQLITE_PATH"]):
            os.remove(os.environ["SQLITE_PATH"])
        auth.reset_auth_limits()
        web.reset_rate_limits()
        web.app.config["TESTING"] = True
        self._m, self._f, self._rl = tester.load_markets, practice.fetch_bars, web.RATE_HEAVY_PER_MIN
        web.RATE_HEAVY_PER_MIN = 1000
        tester.load_markets = lambda: MARKETS
        practice.fetch_bars = fake_fetch

    def tearDown(self):
        tester.load_markets, practice.fetch_bars, web.RATE_HEAVY_PER_MIN = self._m, self._f, self._rl

    def client(self, email):
        c = web.app.test_client()
        c.ip = "198.51.100.%d" % (len(email) % 200 + 1)
        r = self.call(c, "post", "/api/auth/signup", {"email": email, "password": PW})
        self.assertIn(r.status_code, (200, 201), r.get_json())
        return c

    def call(self, c, method, path, body=None, csrf=True):
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = c.get("/api/auth/me").get_json()["csrf"]
        kw = dict(headers=headers, environ_base={"REMOTE_ADDR": getattr(c, "ip", "198.51.100.9")})
        if body is not None:
            kw["json"] = body
        return getattr(c, method)(path, **kw)

    def start(self, c, **kw):
        body = dict(market="binance|BTCUSDT", tf="1h", mode="practice")
        body.update(kw)
        r = self.call(c, "post", "/api/practice/sessions", body)
        self.assertEqual(r.status_code, 201, r.get_json())
        return r.get_json()["session"]

    def truth(self, sid):
        with auth.engine().connect() as c:
            r = c.execute(select(practice.practice_sessions).where(practice.practice_sessions.c.id == sid)).mappings().first()
            return json.loads(r["bars"]), dict(r)


class TestNoFutureLeak(Base):
    def assert_no_future(self, text, bars, cursor, shift):
        for b in bars[cursor + 1:]:
            for v in b[1:]:
                self.assertNotIn(repr(v), text, "a future price reached the client")
            self.assertNotIn(str(b[0]), text, "a future real timestamp reached the client")
            self.assertNotIn(str(b[0] - shift), text, "a future (shifted) timestamp reached the client")

    def test_every_endpoint_while_active(self):
        for hide in (True, False):
            c = self.client(f"leak{int(hide)}@example.com")
            s = self.start(c, hide_dates=hide)
            bars, row = self.truth(s["id"])
            shift = json.loads(row["settings"]).get("shift", 0)
            self.assertEqual(bool(shift), hide)
            self.assertGreaterEqual(len(bars), practice.HISTORY_BARS + practice.MIN_HIDDEN)
            self.assertEqual(len(s["candles"]), s["cursor"] + 1)
            self.assertEqual([x[1:] for x in s["candles"]], [b[1:] for b in bars[:s["cursor"] + 1]])
            last = s["candles"][-1][4]
            sid = s["id"]
            steps = [("get", f"/api/practice/sessions/{sid}", None), ("get", "/api/practice/active", None),
                     ("post", f"/api/practice/sessions/{sid}/advance", {"n": 3}),
                     ("post", f"/api/practice/sessions/{sid}/skip", {}),
                     ("post", f"/api/practice/sessions/{sid}/orders", {"side": "long", "kind": "limit", "entry": last * 0.5, "sl": last * 0.4, "tp": last * 0.9}),
                     ("post", f"/api/practice/sessions/{sid}/advance", {"n": 20}),
                     ("post", f"/api/practice/sessions/{sid}/cancel", {}),
                     ("get", "/api/practice/sessions", None), ("get", "/api/practice/stats", None)]
            for m, p, b in steps:
                r = self.call(c, m, p, b)
                self.assertLess(r.status_code, 300, (p, r.get_json()))
                _, row = self.truth(sid)
                self.assert_no_future(r.get_data(as_text=True), bars, row["cursor"], shift)
                js = r.get_json()
                if js.get("session") and js["session"].get("candles") is not None:
                    self.assertEqual(len(js["session"]["candles"]), row["cursor"] + 1)
                    if hide:
                        self.assertTrue(all(x[0] < bars[0][0] for x in js["session"]["candles"]))
                        self.assertEqual(js["session"]["sources"], [])

    def test_reveal_only_after_finish(self):
        c = self.client("reveal@example.com")
        s = self.start(c)
        bars, _ = self.truth(s["id"])
        r = self.call(c, "post", f"/api/practice/sessions/{s['id']}/finish", {})
        js = r.get_json()["session"]
        self.assertEqual(js["status"], "finished")
        self.assertEqual(len(js["candles"]), len(bars))
        self.assertEqual(js["candles"][0][0], bars[0][0])   # real dates once finished
        self.assertTrue(js["sources"])
        r = self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 1})
        self.assertEqual(r.status_code, 409)

    def test_advance_reveals_exactly_n(self):
        c = self.client("adv@example.com")
        s = self.start(c, hide_dates=False)
        r = self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 5}).get_json()["session"]
        self.assertEqual(r["cursor"], s["cursor"] + 5)
        self.assertEqual(len(r["candles"]), s["visible"] + 5)
        bad = self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 999})
        self.assertEqual(bad.status_code, 400)

    def test_window_runs_out_and_finishes(self):
        c = self.client("end@example.com")
        s = self.start(c, hide_dates=False)
        for _ in range(100):
            js = self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 20}).get_json()["session"]
            if js["status"] != "active":
                break
        self.assertEqual(js["status"], "finished")
        self.assertEqual(js["remaining"], 0)


class TestExecutionRules(unittest.TestCase):
    def t(self, **kw):
        base = dict(kind="market", side="long", entry=100.0, sl=99.0, tp=102.0, risk_amount=100.0, units=100.0,
                    status="pending", fill_price=None, fill_ms=None)
        base.update(kw)
        return base

    def test_market_fills_at_next_open(self):
        t = self.t()
        ch = practice.step_trade(t, [1, 100.2, 100.5, 99.9, 100.4], 5, H, "crypto")
        self.assertEqual(ch["status"], "open")
        self.assertEqual(ch["fill_price"], 100.2)
        self.assertAlmostEqual(ch["units"], 100 / 1.2)

    def test_tp_ignored_on_fill_candle_sl_counts(self):
        t = self.t()
        ch = practice.step_trade(t, [1, 100.0, 103.0, 99.95, 101.0], 5, H, "crypto")
        self.assertEqual(ch["status"], "open")          # TP touched on the fill candle: not counted
        ch = practice.step_trade(self.t(), [1, 100.0, 103.0, 98.0, 101.0], 5, H, "crypto")
        self.assertEqual((ch["status"], ch["exit_reason"]), ("closed", "sl"))

    def test_same_candle_sl_and_tp_is_sl(self):
        t = self.t(status="open", fill_price=100.0, fill_ms=0)
        ch = practice.step_trade(t, [H, 100.0, 103.0, 98.5, 101.0], 6, H, "crypto")
        self.assertEqual(ch["exit_reason"], "sl")
        self.assertEqual(ch["exit_price"], 99.0)
        self.assertEqual(ch["r_gross"], -1.0)
        self.assertLess(ch["r_net"], -1.0)               # fees deducted

    def test_gaps(self):
        t = self.t(status="open", fill_price=100.0, fill_ms=0)
        ch = practice.step_trade(t, [H, 98.0, 98.5, 97.0, 98.2], 6, H, "crypto")
        self.assertEqual((ch["exit_reason"], ch["exit_price"]), ("gap_sl", 98.0))
        self.assertEqual(ch["r_gross"], -2.0)
        ch = practice.step_trade(self.t(status="open", fill_price=100.0, fill_ms=0), [H, 104.0, 105, 103.5, 104], 6, H, "crypto")
        self.assertEqual((ch["exit_reason"], ch["exit_price"]), ("tp", 102.0))   # gap through target: credited at the target
        self.assertEqual(ch["r_gross"], 2.0)

    def test_limit_fills_at_limit_never_better(self):
        t = self.t(kind="limit", entry=99.5, sl=99.0, tp=101.0)
        self.assertEqual(practice.step_trade(t, [1, 100, 100.2, 99.6, 100], 5, H, "crypto"), {})
        ch = practice.step_trade(t, [1, 99.8, 100.0, 99.2, 99.9], 5, H, "crypto")
        self.assertEqual((ch["status"], ch["fill_price"]), ("open", 99.5))

    def test_open_beyond_stop_cancels(self):
        ch = practice.step_trade(self.t(), [1, 98.9, 99.2, 98.5, 99.0], 5, H, "crypto")
        self.assertEqual(ch["status"], "cancelled")

    def test_short_side(self):
        t = self.t(side="short", entry=100.0, sl=101.0, tp=98.0, status="open", fill_price=100.0, fill_ms=0)
        ch = practice.step_trade(t, [H, 99.5, 99.8, 97.9, 98.1], 6, H, "crypto")
        self.assertEqual((ch["exit_reason"], ch["r_gross"]), ("tp", 2.0))

    def test_4h_drops_incomplete_groups(self):
        base = 1_700_000_000_000 - 1_700_000_000_000 % (4 * H)
        raw = [(base + i * H, 1, 2, 0.5, 1.5) for i in range(4)] + [(base + 4 * H + i * H, 1, 2, 0.5, 1.5) for i in range(3)]
        out = practice.build_window(raw, "4h", 4 * H)
        self.assertEqual([o[0] for o in out], [base])


class TestApi(Base):
    def test_signed_out_and_csrf(self):
        anon = web.app.test_client()
        for m, p in (("post", "/api/practice/sessions"), ("get", "/api/practice/sessions/1"), ("get", "/api/practice/stats"),
                     ("post", "/api/practice/sessions/1/advance")):
            self.assertEqual(getattr(anon, m)(p, json={}).status_code, 401)
        c = self.client("csrf@example.com")
        self.assertEqual(self.call(c, "post", "/api/practice/sessions", {"market": "binance|BTCUSDT"}, csrf=False).status_code, 403)
        self.assertEqual(self.call(c, "get", "/api/practice/stats", csrf=False).status_code, 200)
        self.assertEqual(self.call(c, "get", "/api/practice/active", csrf=False).status_code, 200)

    def test_ownership(self):
        a, b = self.client("a@example.com"), self.client("bb@example.com")
        s = self.start(a)
        for m, p in (("get", f"/api/practice/sessions/{s['id']}"), ("post", f"/api/practice/sessions/{s['id']}/advance"),
                     ("post", f"/api/practice/sessions/{s['id']}/finish")):
            self.assertEqual(self.call(b, m, p, {}).status_code, 404)

    def test_order_validation_and_one_position(self):
        c = self.client("ord@example.com")
        s = self.start(c, hide_dates=False)
        last = s["candles"][-1][4]
        p = f"/api/practice/sessions/{s['id']}/orders"
        self.assertEqual(self.call(c, "post", p, {"side": "long", "sl": last * 1.01, "tp": last * 1.02}).status_code, 400)
        self.assertEqual(self.call(c, "post", p, {"side": "long", "kind": "limit", "entry": last * 1.01, "sl": last * 0.9, "tp": last * 1.2}).status_code, 400)
        r = self.call(c, "post", p, {"side": "long", "sl": last * 0.5, "tp": last * 1.5, "risk_pct": 2})
        self.assertEqual(r.status_code, 201, r.get_json())
        t = r.get_json()["session"]["trades"][0]
        self.assertEqual((t["status"], t["risk_amount"]), ("pending", 200.0))
        self.assertEqual(self.call(c, "post", p, {"side": "long", "sl": last * 0.5, "tp": last * 1.5}).status_code, 409)
        js = self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 1}).get_json()["session"]
        bars, _ = self.truth(s["id"])
        self.assertEqual(js["trades"][0]["fill_price"], bars[js["cursor"]][1])
        js = self.call(c, "post", f"/api/practice/sessions/{s['id']}/close", {}).get_json()["session"]
        t = js["trades"][0]
        self.assertEqual((t["status"], t["exit_reason"], t["exit_price"]), ("closed", "manual", bars[js["cursor"]][4]))
        self.assertAlmostEqual(js["balance"], 10000 + t["pnl"], places=2)

    def test_challenge_caps_risk(self):
        c = self.client("ch@example.com")
        s = self.start(c, mode="challenge", risk_pct=3)
        self.assertEqual(s["settings"]["risk_pct"], 1.0)
        self.assertEqual(s["settings"]["challenge"]["fail_drawdown_pct"], 5.0)
        last = s["candles"][-1][4]
        r = self.call(c, "post", f"/api/practice/sessions/{s['id']}/orders", {"side": "long", "sl": last * 0.5, "tp": last * 1.5, "risk_pct": 2})
        self.assertEqual(r.status_code, 400)

    def test_challenge_check(self):
        rules = dict(practice.CHALLENGE)
        s = dict(start_balance=10000, balance=9499)
        self.assertEqual(practice._challenge_check(s, {"challenge": rules}, []), "failed")
        s["balance"] = 10600
        self.assertEqual(practice._challenge_check(s, {"challenge": rules}, []), "passed")
        s["balance"] = 10000
        self.assertEqual(practice._challenge_check(s, {"challenge": rules}, [{"status": "closed"}] * 10), "incomplete")
        self.assertIsNone(practice._challenge_check(s, {}, []))

    def test_strategy_mode(self):
        c = self.client("strat@example.com")
        spec = dict(name="Sweep", setup_type="similar", side="long", markets=["binance|BTCUSDT"], tf="1h", htf_confirm="off",
                    stop_pct=1, rr=2, expiry="72h", max_hold="3d", days=90, slippage=1, slippage_unit="bps",
                    entry_conditions="Sweep of the Asia low")
        r = self.call(c, "post", "/api/library/strategies", spec)
        self.assertEqual(r.status_code, 201, r.get_json())
        sid = r.get_json()["strategy"]["id"]
        s = self.start(c, mode="strategy", strategy_id=sid, market=None)
        self.assertEqual(s["strategy_id"], sid)
        self.assertEqual(s["settings"]["strategy"]["entry_conditions"], "Sweep of the Asia low")
        other = self.client("other@example.com")
        r = self.call(other, "post", "/api/practice/sessions", dict(mode="strategy", strategy_id=sid))
        self.assertEqual(r.status_code, 404)

    def test_new_session_abandons_previous(self):
        c = self.client("ab@example.com")
        s1 = self.start(c)
        s2 = self.start(c)
        js = self.call(c, "get", f"/api/practice/sessions/{s1['id']}").get_json()["session"]
        self.assertEqual(js["status"], "abandoned")
        self.assertEqual(self.call(c, "get", "/api/practice/active").get_json()["session"]["id"], s2["id"])

    def test_stats_and_delete_account(self):
        c = self.client("stats@example.com")
        s = self.start(c, hide_dates=False)
        last = s["candles"][-1][4]
        self.call(c, "post", f"/api/practice/sessions/{s['id']}/skip", {})
        self.call(c, "post", f"/api/practice/sessions/{s['id']}/orders", {"side": "long", "sl": last * 0.5, "tp": last * 1.5})
        self.call(c, "post", f"/api/practice/sessions/{s['id']}/advance", {"n": 2})
        self.call(c, "post", f"/api/practice/sessions/{s['id']}/close", {})
        st = self.call(c, "get", "/api/practice/stats?tz=60").get_json()["stats"]
        self.assertEqual(st["total"]["trades"], 1)
        self.assertEqual(st["skipped"], 1)
        self.assertEqual(len(st["daily"]), 14)
        self.assertEqual(len(st["weekly"]), 8)
        self.assertEqual(st["daily"][-1]["trades"], 1)
        uid = auth.get_user_by_email("stats@example.com")["id"]
        auth.delete_user(uid)
        with auth.engine().connect() as cn:
            self.assertEqual(cn.execute(select(practice.practice_sessions)).fetchall(), [])
            self.assertEqual(cn.execute(select(practice.practice_trades)).fetchall(), [])

    def test_no_data_is_reported_not_invented(self):
        c = self.client("nodata@example.com")
        practice.fetch_bars = lambda *a: ([], {"segments": []})
        r = self.call(c, "post", "/api/practice/sessions", dict(market="binance|BTCUSDT"))
        self.assertEqual(r.status_code, 422)
        def boom(*a):
            raise OSError("down")
        practice.fetch_bars = boom
        r = self.call(c, "post", "/api/practice/sessions", dict(market="binance|BTCUSDT"))
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
