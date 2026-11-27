#!/usr/bin/env python3
"""Offline Phase 2 checks (synthetic candles, no network).

1. Timeframe resampling: 15m and 5m candles aggregate into 15m/30m/1h/4h/1d bars with the right open, high, low,
   close and UTC alignment, and an unfinished last bar is dropped.
2. Higher-timeframe confirmation has no look-ahead: its answer at time t is the same when every candle after t is
   deleted or replaced, and it only reads HTF candles that had closed by t.
3. Market-condition tags are past-only in the same way.
4. Rule signals (EMA crossover, breakout, liquidity sweep, FVG) never read candles after the signal candle.
5. Trade replay data integrity: /api/candles reloads the engine's exact range and returns the very candles the
   trade's fill and exit were decided on; bad windows are refused.
6. Request validation for timeframes, HTF confirmation, timeframe sweeps and strategy comparisons.
Run: python3 tests/test_phase2.py   (or pytest tests/)
"""
import importlib.util, json, os, random, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tester as T
import conditions as C
import strategies as R
import strategy_compare as SC
from test_pattern import M15, T0, random_walk

M5 = 300_000
DAY = 86_400_000


T0D = T0 - T0 % 86_400_000  # UTC midnight, so the first daily bar is whole


def walk(n, step, seed=3, t0=T0D):
    rnd, p, out = random.Random(seed), 100.0, []
    for i in range(n):
        o = p
        c = o * (1 + rnd.gauss(0, 0.002) + (rnd.random() < 0.01) * rnd.choice((-1, 1)) * 0.01)
        h, l = max(o, c) * (1 + abs(rnd.gauss(0, 0.0008))), min(o, c) * (1 - abs(rnd.gauss(0, 0.0008)))
        out.append((t0 + i * step, o, h, l, c))
        p = c
    return out


def known_at(base, t, step=M15):
    """Candles that had closed by time t."""
    return [b for b in base if b[0] + step <= t]


def scramble_after(base, t, step=M15, seed=99):
    """Same candles up to t; every later candle replaced with something very different."""
    rnd = random.Random(seed)
    out = []
    for b in base:
        if b[0] + step <= t:
            out.append(b)
        else:
            o = b[1] * rnd.choice((0.7, 1.4))
            c = o * rnd.choice((0.9, 1.1))
            out.append((b[0], o, max(o, c) * 1.02, min(o, c) * 0.98, c))
    return out


class Resample(unittest.TestCase):
    def check(self, base, bstep, step):
        bars = T.complete_bars(T.resample(base, step), step, bstep)
        self.assertTrue(bars)
        groups = {}
        for b in base:
            groups.setdefault(b[0] - b[0] % step, []).append(b)
        for k in groups:
            self.assertEqual(k % step, 0)
        for r in bars:
            g = groups[r[0]]
            self.assertEqual(len(g), step // bstep, "complete bars contain every base candle")
            self.assertEqual(r[0] % step, 0, "aligned to UTC epoch")
            self.assertEqual(r[1], g[0][1])
            self.assertEqual(r[2], max(x[2] for x in g))
            self.assertEqual(r[3], min(x[3] for x in g))
            self.assertEqual(r[4], g[-1][4])
        return bars

    def test_from_15m(self):
        base = walk(96 * 20 + 37, M15)  # 20 days and a bit: the last day is unfinished
        for tf in ("30m", "1h", "4h", "1d"):
            bars = self.check(base, M15, T.STEP[tf])
            last_open = base[-1][0] - base[-1][0] % T.STEP[tf]
            self.assertNotEqual(bars[-1][0], last_open if (base[-1][0] + M15) % T.STEP[tf] else None,
                                f"{tf}: unfinished last bar dropped")
        self.assertEqual(len(T.complete_bars(T.resample(base, DAY), DAY, M15)), 20)

    def test_from_5m(self):
        base = walk(288 * 6 + 10, M5, seed=5)
        for tf in ("15m", "30m", "1h", "4h", "1d"):
            self.check(base, M5, T.STEP[tf])
        # 5m -> 1h equals 5m -> 15m -> 1h
        direct = T.complete_bars(T.resample(base, 3600_000), 3600_000, M5)
        m15 = [b[:5] for b in T.complete_bars(T.resample(base, M15), M15, M5)]
        twostep = T.complete_bars(T.resample(m15, 3600_000), 3600_000, M15)
        self.assertEqual([b[:5] for b in direct], [b[:5] for b in twostep])

    def test_missing_last_candle_makes_bar_incomplete(self):
        base = walk(96 * 3, M15)
        holed = [b for i, b in enumerate(base) if i != 96 + 95]  # last 15m of day 2 missing
        days = T.complete_bars(T.resample(holed, DAY), DAY, M15)
        self.assertNotIn(base[96][0] - base[96][0] % DAY, [d[0] for d in days])

    def test_exec_interval(self):
        self.assertEqual(T.exec_interval_for("5m"), "5m")
        for tf in ("15m", "30m", "1h", "4h", "1d"):
            self.assertEqual(T.exec_interval_for(tf), "15m")


class HTFNoLookAhead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = walk(96 * 70, M15, seed=11)

    def times(self, every=53):
        start = self.base[0][0] + 40 * DAY
        return [b[0] + M15 for b in self.base if b[0] >= start][::every]

    def test_same_answer_without_the_future(self):
        for htf in T.HTF_CHOICES:
            for sgn in (1, -1):
                full = T.HTFGate(self.base, htf, sgn)
                states = set()
                for t in self.times():
                    s = full.state(t)
                    states.add(s)
                    self.assertEqual(s, T.HTFGate(known_at(self.base, t), htf, sgn).state(t), f"{htf} at {t}")
                    self.assertEqual(s, T.HTFGate(scramble_after(self.base, t), htf, sgn).state(t), f"{htf} at {t}")
                self.assertTrue({"up", "down"} & states, f"{htf}: test data exercises the rule")

    def test_reads_only_closed_htf_candles(self):
        g = T.HTFGate(self.base, "4h", 1)
        for t in self.times(31):
            h = __import__("bisect").bisect_right(g.close_t, t) - 1
            self.assertLessEqual(g.close_t[h], t)
            if h + 1 < len(g.close_t):
                self.assertGreater(g.close_t[h + 1], t)

    def test_not_enough_history_is_not_confirmation(self):
        g = T.HTFGate(self.base[:96 * 2], "1d", 1)
        self.assertIsNone(g.state(self.base[96 * 2 - 1][0] + M15))
        self.assertFalse(g.ok(self.base[96 * 2 - 1][0] + M15))
        self.assertEqual(g.info()["not_enough_history"], 1)

    def test_ok_matches_side(self):
        for t in self.times(97):
            up, dn = T.HTFGate(self.base, "4h", 1), T.HTFGate(self.base, "4h", -1)
            s = up.state(t)
            self.assertEqual(up.ok(t), s == "up")
            self.assertEqual(dn.ok(t), s == "down")


class ConditionTagsPastOnly(unittest.TestCase):
    def test_same_tag_without_the_future(self):
        base = walk(96 * 60, M15, seed=21)
        full = C.Context(base)
        seen = {"trend": set(), "vol": set(), "bias": set()}
        for b in base[96 * 25::71]:
            t = b[0] + M15
            tag = full.tag(t)
            for k in seen:
                seen[k].add(tag[k])
            self.assertEqual(tag, C.Context(known_at(base, t)).tag(t))
            self.assertEqual(tag, C.Context(scramble_after(base, t)).tag(t))
            self.assertLessEqual(tag["asof"], t)
        self.assertEqual(seen["trend"], {"trending", "ranging"})
        self.assertEqual(seen["vol"], {"high", "low"})
        self.assertTrue({"bullish", "bearish"} <= seen["bias"])

    def test_breakdown_and_sample_flags(self):
        trades = []
        for i in range(20):
            trades.append(dict(status="tp" if i % 3 == 0 else "sl", r_net=2.0 if i % 3 == 0 else -1.0,
                               conditions=dict(trend="trending" if i < 16 else "ranging", vol="high", bias="bullish")))
        dims = {d["key"]: {r["value"]: r for r in d["rows"]} for d in C.breakdown(trades)}
        self.assertEqual((dims["trend"]["trending"]["trades"], dims["trend"]["trending"]["sample"]), (16, "ok"))
        self.assertEqual((dims["trend"]["ranging"]["trades"], dims["trend"]["ranging"]["sample"]), (4, "too_few"))
        self.assertEqual(dims["trend"]["trending"]["wins"], 6)
        self.assertEqual(dims["vol"]["low"]["trades"], 0)


class RuleSignalsPastOnly(unittest.TestCase):
    def test_no_future_candles(self):
        base = walk(96 * 30, M15, seed=8)
        bars = T.complete_bars(T.resample(base, 3600_000), 3600_000, M15)
        for rule in R.RULES:
            p = R.clean_params(rule, {})
            for sgn in (1, -1):
                full = R.signals(bars, T.atr_series(bars), rule, p, sgn)
                self.assertTrue(full, f"{rule} {sgn}: synthetic data produces signals")
                for k in range(60, len(bars), 37):
                    cut = bars[:k + 1]
                    part = R.signals(cut, T.atr_series(cut), rule, p, sgn)
                    self.assertEqual(part, [s for s in full if s[0] <= k], f"{rule} cut at {k}")

    def test_rule_trades_fill_after_signal(self):
        base = random_walk(6000)
        g = T.build_geometry("long", 1.0, 1.01, 0.995, 1.0, 0.01)
        for rule in R.RULES:
            trades, found, _ = R.replay_rule(base, rule, {}, g, tf="1h", start_ms=base[0][0] + 10 * DAY,
                                             max_hold_ms=7 * DAY)
            self.assertGreaterEqual(found, len(trades))
            for t in trades:
                self.assertEqual(t["placed"] % 3600_000, 0, "decided at a 1h close")
                if t.get("filled"):
                    self.assertGreaterEqual(t["filled"], t["placed"])
                    self.assertAlmostEqual((t["tp"] - t["entry"]) / (t["entry"] - t["sl"]), g["rr"], places=6)

    def test_param_bounds(self):
        with self.assertRaises(ValueError):
            R.clean_params("ema_cross", {"fast": 50, "slow": 20})
        with self.assertRaises(ValueError):
            R.clean_params("breakout", {"lookback": 100000})


def load_web():
    spec = importlib.util.spec_from_file_location("chronos_web_p2", os.path.join(ROOT, "web", "app.py"))
    web = importlib.util.module_from_spec(spec)
    sys.modules["chronos_web_p2"] = web
    spec.loader.exec_module(web)
    return web


MKT = dict(market_id="binance|BTCUSDT", provider="binance", coin="BTCUSDT", symbol="BTC")


class ReplayIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.pop("REQUIRE_LOGIN", None)
        cls.web = load_web()
        cls.web.app.config["TESTING"] = True
        cls.base = random_walk(6000)
        b = cls.base
        cls.prov = dict(segments=[dict(source="mfp", label="MFP feed", from_ms=b[0][0], to_ms=b[-1][0] + M15)])
        g = T.build_geometry("long", 1.0, 1.01, 0.995, 1.0, 0.01)
        trades, _, _ = R.replay_rule(b, "fvg", {}, g, tf="1h", start_ms=b[0][0] + 10 * DAY, max_hold_ms=7 * DAY)
        cls.load_range = (b[0][0], b[-1][0] + M15)
        T.annotate_trades(trades, b, cls.prov, M15, "15m", MKT, load_range=cls.load_range)
        cls.trades = [t for t in trades if t.get("bar_check")]
        cls.calls = []

        def fake_load(m, interval, lf, lt, kind=None, use_backups=True):
            cls.calls.append((m["market_id"], interval, lf, lt))
            return [x for x in cls.base if lf <= x[0] < lt], cls.prov

        import sources
        cls._orig = sources.load
        sources.load = fake_load
        cls.web.tester.load_markets = lambda: [MKT]

    @classmethod
    def tearDownClass(cls):
        import sources
        sources.load = cls._orig

    def get(self, **q):
        self.web.reset_rate_limits()
        return self.web.app.test_client().get("/api/candles", query_string=q, environ_base={"REMOTE_ADDR": "198.51.100.7"})

    def test_candles_match_engine(self):
        self.assertGreaterEqual(len(self.trades), 5)
        for t in self.trades[:12]:
            w = t["candle_window"]
            r = self.get(market=MKT["market_id"], interval=w["interval"], **{"from": w["from_ms"], "to": w["to_ms"],
                         "load_from": w["load_from_ms"], "load_to": w["load_to_ms"]})
            self.assertEqual(r.status_code, 200, r.get_json())
            j = r.get_json()
            self.assertEqual(self.calls[-1], (MKT["market_id"], "15m", w["load_from_ms"], w["load_to_ms"]),
                             "reloads exactly the range the engine loaded")
            rows = j["candles"]
            self.assertTrue(all(w["from_ms"] <= c[0] < w["to_ms"] for c in rows))
            self.assertEqual([tuple(c) for c in rows], [x for x in self.base if w["from_ms"] <= x[0] < w["to_ms"]],
                             "stored candles only, nothing filled in")
            by_t = {c[0]: c for c in rows}
            for key, want in t["bar_check"].items():
                self.assertEqual(by_t.get(want[0]), want, f"{key} candle identical")
            self.assertTrue(w["from_ms"] <= t["signal"] < w["to_ms"])
            self.assertTrue(w["from_ms"] <= t["exit_bar"] < w["to_ms"])
            self.assertEqual(j["sources"][0]["source"], "mfp")

    def test_bad_windows_refused(self):
        b0 = self.base[0][0]
        r = self.get(market=MKT["market_id"], interval="15m", **{"from": b0, "to": b0 + 4000 * M15})
        self.assertEqual(r.status_code, 400)
        r = self.get(market=MKT["market_id"], interval="15m", **{"from": b0 + DAY, "to": b0 + 2 * DAY,
                     "load_from": b0 + DAY + M15, "load_to": b0 + 2 * DAY})
        self.assertEqual(r.status_code, 400)
        r = self.get(market=MKT["market_id"], interval="4h", **{"from": b0, "to": b0 + DAY})
        self.assertEqual(r.status_code, 400)


class Validation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = load_web()
        cls.web.resolve = lambda s: MKT

    def body(self, **kw):
        d = dict(market="BTC", side="long", entry=100, tp=103, sl=99)
        d.update(kw)
        return self.web.parse_test_body(d)

    def test_tf_maps_to_mode(self):
        p = self.body(mode="similar", tf="30m")
        self.assertEqual((p["similar_tf"], p["setup_tf"], p["interval"]), ("30m", "30m", "15m"))
        p = self.body(mode="every-close", tf="5m")
        self.assertEqual((p["close_tf"], p["interval"]), ("5m", "5m"))
        p = self.body(mode="pattern", tf="1d")
        self.assertEqual((p["zone_tf"], p["interval"]), ("1d", "15m"))
        p = self.body(mode="every-close")
        self.assertEqual(p["close_tf"], "4h")
        with self.assertRaises(ValueError):
            self.body(tf="2h")

    def test_htf_must_be_higher(self):
        self.assertEqual(self.body(tf="1h", htf_confirm="4h")["htf"], "4h")
        for tf, htf in (("4h", "4h"), ("4h", "1h"), ("1d", "1d")):
            with self.assertRaises(ValueError):
                self.body(tf=tf, htf_confirm=htf)
        with self.assertRaises(ValueError):
            self.body(tf="1h", htf_confirm="1w")

    def test_sweep(self):
        self.assertEqual(self.body(tf_sweep=True)["tf_sweep"], list(T.SETUP_TFS))
        self.assertEqual(self.body(tf_sweep=["1h", "4h"])["tf_sweep"], ["1h", "4h"])
        self.assertIsNone(self.body()["tf_sweep"])
        for bad in (["1h"], ["1h", "2h"], ["1h", "1h"]):
            with self.assertRaises(ValueError):
                self.body(tf_sweep=bad)

    def test_engine_cmd_flags(self):
        p = self.body(mode="every-close", tf="1h", htf_confirm="1d", tf_sweep=True)
        cmd = self.web.engine_cmd(p, "/tmp/x.json")
        s = " ".join(cmd)
        for want in ("--setup-tf 1h", "--htf-confirm 1d", "--interval 15m", "--tf-sweep"):
            self.assertIn(want, s)

    def test_tf_row(self):
        row = T.tf_row("1h", "15m", err="no data")
        self.assertEqual((row["tf"], row["interval"]), ("1h", "15m"))


class StrategyCompareSpec(unittest.TestCase):
    SPEC = dict(market="binance|BTCUSDT", side="long", entry=100.0, tp=103.0, sl=99.0, days=60, tf="1h",
                strategies=[dict(type="similar"), dict(type="ema_cross", params={"fast": 10, "slow": 30}),
                            dict(type="zone_retest"), dict(type="breakout", name="My breakout")])

    def test_validate(self):
        clean, tf = SC.validate(self.SPEC)
        self.assertEqual(tf, "1h")
        self.assertEqual([c["type"] for c in clean], ["similar", "ema_cross", "zone_retest", "breakout"])
        self.assertEqual(clean[1]["params"]["fast"], 10)
        self.assertEqual(clean[3]["name"], "My breakout")
        for bad in ([dict(type="similar")], [dict(type="similar")] * 7, [dict(type="similar"), dict(type="astrology")],
                    [dict(type="similar"), dict(type="ema_cross", params={"fast": 60, "slow": 20})],
                    [dict(type="similar", params={"x": 1}), dict(type="breakout")]):
            with self.assertRaises(ValueError):
                SC.validate(dict(self.SPEC, strategies=bad))
        with self.assertRaises(ValueError):
            SC.validate(dict(self.SPEC, tf="2h"))

    def test_identical_conditions(self):
        clean, tf = SC.validate(self.SPEC)
        argvs = [SC.argv_for(self.SPEC, st, tf, 1_760_000_000_000) for st in clean]
        common = ("--days", "--interval", "--end-ms", "--max-hold")
        for flag in common:
            vals = {a[a.index(flag) + 1] for a in argvs}
            self.assertEqual(len(vals), 1, f"{flag} identical for every strategy")
        for a in argvs:
            self.assertEqual(a[0], self.SPEC["market"])
            self.assertTrue(any(x.startswith("--slippage-bps=") for x in a))
        self.assertIn("--similar-tf", argvs[0])
        self.assertEqual(argvs[1][argvs[1].index("--rule") + 1], "ema_cross")
        self.assertIn("--setup-tf", argvs[1])
        self.assertIn("--zone-tf", argvs[2])
        a5 = SC.argv_for(dict(self.SPEC, tf="5m"), clean[3], "5m", 1)
        self.assertEqual(a5[a5.index("--interval") + 1], "5m")

    def test_named_patterns_are_real_types(self):
        for name, (typ, _) in SC.NAMED.items():
            self.assertIn(typ, SC.TYPES, name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
