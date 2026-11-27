#!/usr/bin/env python3
"""Phase 1 engine checks, all offline on hand-built candles:
results maths, slippage, same-candle ambiguity, no-overlap, look-ahead guard, gap handling, traceability."""
import os, random, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))
import tester as T
import metrics as MX

M15 = 900_000
T0 = 1_700_000_000_000 - 1_700_000_000_000 % T.H4


def tr(r, status=None, exit_ms=0, filled=None, hours=1.0, r_gross=None, market="A"):
    status = status or ("tp" if r > 0 else "sl")
    return dict(status=status, r_net=r, r_gross=r if r_gross is None else r_gross, exit=exit_ms,
                filled=filled if filled is not None else exit_ms - 3600_000, placed=exit_ms - 7200_000, hours=hours,
                market=market)


def flat(n, p=100.0, t0=T0, w=0.5):
    return [(t0 + i * M15, p, p + w, p - w, p) for i in range(n)]


class TestMetricsMaths(unittest.TestCase):
    SEQ = [2.0, -1.0, -1.0, 2.0, 2.0, 2.0, -1.0, -1.0, -1.0, -1.0, 0.0, 3.0]

    def test_profit_factor(self):
        self.assertAlmostEqual(MX.profit_factor(self.SEQ), 11.0 / 6.0)
        self.assertIsNone(MX.profit_factor([1.0, 2.0]))  # no losses: undefined, not infinity
        self.assertEqual(MX.profit_factor([-1.0]), 0.0)

    def test_streaks(self):
        self.assertEqual(MX.streaks(self.SEQ), (3, 4))
        self.assertEqual(MX.streaks([1, 1, 0, 1]), (2, 0))  # a flat trade ends a streak
        self.assertEqual(MX.streaks([]), (0, 0))

    def test_drawdown(self):
        # equity: 2,1,0,2,4,6,5,4,3,2,2,5 -> peak 6, trough 2 -> -4
        self.assertAlmostEqual(MX.max_drawdown(self.SEQ), -4.0)
        self.assertAlmostEqual(MX.max_drawdown([-1, -1, 3]), -2.0)  # from the 0 start
        self.assertEqual(MX.max_drawdown([1, 2, 3]), 0.0)

    def test_block_expectancy_and_payoff(self):
        b = MX.block(self.SEQ)
        self.assertEqual((b["trades"], b["wins"], b["losses"], b["flat"]), (12, 5, 6, 1))
        self.assertAlmostEqual(b["total_r"], 5.0)
        self.assertAlmostEqual(b["expectancy_r"], round(5.0 / 12, 3))
        self.assertAlmostEqual(b["avg_win_r"], 2.2)
        self.assertAlmostEqual(b["avg_loss_r"], -1.0)
        self.assertAlmostEqual(b["avg_rr_achieved"], 2.2)
        self.assertAlmostEqual(b["win_rate"], round(5 / 12, 3))
        self.assertAlmostEqual(b["max_drawdown_r"], -4.0)
        self.assertEqual(b["max_drawdown_pct"], -4.0)  # 1% risk per trade
        self.assertEqual((b["longest_win_streak"], b["longest_loss_streak"]), (3, 4))
        self.assertAlmostEqual(b["profit_factor"], round(11 / 6, 2))

    def test_compute_orders_by_exit_and_separates_gross_net(self):
        trades = [tr(1.8, exit_ms=3 * 3600_000, r_gross=2.0), tr(-1.1, exit_ms=2 * 3600_000, r_gross=-1.0),
                  dict(status="unfilled", placed=0), dict(status="open_at_end", placed=1, filled=2)]
        for t in trades[:2]:
            t["r_fee"], t["r_slip"] = 0.08, round(abs(t["r_gross"] - t["r_net"]) - 0.08, 4)
        s = MX.compute(trades, setups_identified=7, setups_basis="test")
        self.assertEqual(s["setups_identified"], 7)
        self.assertEqual((s["orders_placed"], s["trades_executed"], s["unfilled"], s["open_at_end"], s["closed"]), (4, 3, 1, 1, 2))
        self.assertEqual([round(p["n"], 2) for p in s["equity"]], [-1.1, 0.7])  # the -1.1 exited first
        self.assertAlmostEqual(s["gross"]["total_r"], 1.0)
        self.assertAlmostEqual(s["net"]["total_r"], 0.7)
        self.assertAlmostEqual(s["costs"]["total_r"], 0.3)
        self.assertAlmostEqual(s["costs"]["share_of_gross_profit"], 0.3)
        self.assertEqual(sum(b["count"] for b in s["r_hist"]["net"]), 2)

    def test_histogram_edges(self):
        h = MX.histogram([-2.0, -1.0, -0.99, 0.0, 0.01, 3.0, 3.5])
        d = {b["label"]: b["count"] for b in h}
        self.assertEqual(d["<= -1.5R"], 1)
        self.assertEqual(d["-1.5 to -1R"], 1)
        self.assertEqual(d["-1 to -0.5R"], 1)
        self.assertEqual(d["-0.5 to 0R"], 1)
        self.assertEqual(d["0 to 0.5R"], 1)
        self.assertEqual(d["2 to 3R"], 1)
        self.assertEqual(d["> 3R"], 1)

    def test_explain_costs_flip_and_small_sample(self):
        trades = [tr(0.05, r_gross=0.2, exit_ms=i * 3600_000 + 7200_000) for i in range(6)] + \
                 [tr(-0.2, status="time", r_gross=0.05, exit_ms=(i + 7) * 3600_000) for i in range(6)]
        s = MX.compute(trades)
        ids = {f["id"]: f for f in MX.explain(s, trades, 15)}
        self.assertIn("sample", ids)
        self.assertEqual(ids["sample"]["level"], "warn")
        self.assertIn("costs_flip", ids)
        self.assertEqual(MX.explain(MX.compute([]), [], 15)[0]["id"], "no_trades")

    def test_count_overlaps(self):
        a = dict(status="tp", filled=10, exit=50, market="X")
        b = dict(status="sl", filled=40, exit=80, market="X")  # opens before a closed
        c = dict(status="sl", filled=40, exit=80, market="Y")  # other market: not an overlap
        self.assertEqual(MX.count_overlaps([a, b, c]), 1)
        self.assertEqual(MX.count_overlaps([a, dict(b, filled=50)]), 0)


class TestExecution(unittest.TestCase):
    def test_slippage_on_market_entry_and_stop_exit(self):
        bars = flat(4) + [(T0 + 4 * M15, 100.0, 100.4, 98.5, 99.0)]  # SL 99 hit on bar 4
        t, _ = T.simulate_order(bars, 1, T0 + M15, 100.0, 99.0, 102.0, 1, "market", 3600_000 * 10, 1.0,
                                slip_bps=10)  # 10 bps = 0.1 on 100
        self.assertEqual(t["status"], "sl")
        self.assertAlmostEqual(t["fill_px_theoretical"], 100.0)
        self.assertAlmostEqual(t["fill_px"], 100.1)
        self.assertAlmostEqual(t["exit_px_theoretical"], 99.0)
        self.assertAlmostEqual(t["exit_px"], 99.0 - 0.099)
        self.assertAlmostEqual(t["r_gross"], -1.0)
        self.assertAlmostEqual(t["r_slip"], 0.199, places=4)
        self.assertAlmostEqual(t["r_net"], -1.199, places=4)
        self.assertEqual(t["exit_reason"], "sl")

    def test_no_slippage_on_limit_entry_and_tp(self):
        bars = flat(2) + [(T0 + 2 * M15, 100.0, 100.2, 98.9, 99.5), (T0 + 3 * M15, 99.5, 101.5, 99.2, 101.0)]
        t, _ = T.simulate_order(bars, 1, T0 + M15, 99.0, 98.0, 101.0, 1, "limit", 3600_000 * 10, 1.0, slip_bps=50)
        self.assertEqual(t["status"], "tp")
        self.assertEqual(t["fill_px"], 99.0)
        self.assertEqual(t["exit_px"], 101.0)
        self.assertEqual(t["r_slip"], 0.0)
        self.assertAlmostEqual(t["r_net"], t["r_gross"])

    def test_tick_slippage(self):
        a = type("A", (), dict(slippage_ticks=3, slippage_bps=None))()
        s = T.slippage_for(a, {"tick_size": 0.01})
        self.assertEqual((s["unit"], s["abs"]), ("ticks", 0.03))
        b = type("A", (), dict(slippage_ticks=0, slippage_bps=None))()
        self.assertEqual(T.slippage_for(b, {})["bps"], T.DEFAULT_SLIPPAGE_BPS)

    def test_same_candle_tp_and_sl_counts_as_loss_and_is_flagged(self):
        bars = flat(2) + [(T0 + 2 * M15, 100.0, 100.2, 98.9, 99.5), (T0 + 3 * M15, 99.5, 102.5, 97.5, 100.0)]
        t, _ = T.simulate_order(bars, 1, T0 + M15, 99.0, 98.0, 102.0, 1, "limit", 3600_000 * 10, 1.0)
        self.assertEqual(t["status"], "sl")
        self.assertTrue(t.get("same_bar_tp_sl"))
        s = MX.compute([dict(t, market="A")])
        self.assertEqual(s["integrity"]["same_candle_tp_sl"], 1)

    def test_tp_on_fill_bar_not_counted_but_flagged(self):
        bars = flat(2) + [(T0 + 2 * M15, 100.0, 102.5, 98.9, 101.0)] + flat(20, 101.0, T0 + 3 * M15, 0.2)
        t, _ = T.simulate_order(bars, 1, T0 + M15, 99.0, 98.0, 102.0, 1, "limit", 3600_000 * 10, 1.0,
                                max_hold_ms=2 * 3600_000)
        self.assertNotEqual(t["status"], "tp")
        self.assertTrue(t.get("tp_touched_on_fill_bar"))

    def test_gap_through_stop_fills_at_open(self):
        bars = flat(2) + [(T0 + 2 * M15, 100.0, 100.2, 98.9, 99.5), (T0 + 3 * M15, 97.0, 97.5, 96.5, 97.0)]
        t, _ = T.simulate_order(bars, 1, T0 + M15, 99.0, 98.0, 102.0, 1, "limit", 3600_000 * 10, 1.0)
        self.assertEqual((t["status"], t["exit_px"], t["exit_reason"]), ("sl", 97.0, "sl_gap_open"))
        self.assertAlmostEqual(t["r_gross"], -2.0)


def random_walk(n, seed=7, p=100.0):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        o = p
        c = o * (1 + rnd.gauss(0, 0.004))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.002)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.002)))
        out.append((T0 + i * M15, o, h, l, c))
        p = c
    return out


class TestNoOverlapAndLookAhead(unittest.TestCase):
    def setUp(self):
        self.base = random_walk(96 * 40)
        h4c = [b for b in T.resample(self.base, T.H4) if b[5] >= b[0] + T.H4 - M15]
        atr = T.atr_series(h4c)[-1]
        ref = self.base[-1][4]
        self.g = T.build_geometry("long", ref - 0.3 * atr, ref + 1.7 * atr, ref - 1.3 * atr, ref, atr)
        self.start = T0 + 14 * 86400_000

    def run_ec(self, base, **kw):
        return T.replay(base, T.resample(base, T.H4), self.g, start_ms=self.start, max_hold_ms=3 * 86400_000,
                        fee_pct=0.03, slip_bps=2, **kw)[0]

    def test_one_trade_at_a_time_by_default(self):
        trades = self.run_ec(self.base)
        self.assertGreater(sum("filled" in t for t in trades), 10)
        self.assertEqual(MX.count_overlaps([dict(t, market="A") for t in trades]), 0)
        # every order is placed only after the previous one finished
        prev_end = 0
        for t in trades:
            self.assertGreaterEqual(t["placed"], prev_end)
            prev_end = t.get("exit") or (t["placed"] + 72 * 3600_000)

    def test_overlap_only_when_allowed(self):
        trades = self.run_ec(self.base, allow_overlap=True)
        self.assertGreater(MX.count_overlaps([dict(t, market="A") for t in trades]), 0)

    def test_truncating_the_future_changes_no_finished_trade(self):
        full = self.run_ec(self.base)
        strip = lambda L: [{k: v for k, v in t.items() if k not in ("sl_then_tp", "extra_stop_needed_atr", "shallow_sweep")}
                           for t in L]
        for cut_days in (5, 12, 20):
            cut = self.base[-1][0] - cut_days * 86400_000
            part = self.run_ec([b for b in self.base if b[0] < cut])
            done_full = [t for t in full if t.get("exit", 1e18) <= cut and t["status"] in ("tp", "sl", "time")]
            done_part = [t for t in part if t["status"] in ("tp", "sl", "time")]
            self.assertTrue(done_full)
            self.assertEqual(strip(done_full), strip(done_part)[:len(done_full)])

    def test_orders_use_only_closed_candles(self):
        for t in self.run_ec(self.base):
            self.assertEqual(t["placed"] % T.H4, 0)  # decided at a 4h close
            if "filled" in t:
                self.assertGreaterEqual(t["filled"], t["placed"])  # filled on a bar that opened after the decision

    def test_similar_mode_truncation(self):
        import similar as S
        g = dict(self.g, market_id="A")
        q, *_ = S.query_fingerprint(self.base, tf="1h", n=24, interval="15m")
        kw = dict(tf="1h", n=24, k=20, start_ms=self.start, interval="15m", query=q, fee_pct=0.03, slip_bps=2,
                  max_hold_ms=3 * 86400_000)
        full = S.replay_similar(self.base, g, end_ms=self.base[-1][0] + M15, **kw)[0]
        for t in full:
            if "filled" in t:
                self.assertGreaterEqual(t["filled"], t["placed"])
        self.assertEqual(MX.count_overlaps([dict(t, market="A") for t in full]), 0)


class TestGapsAndTraceability(unittest.TestCase):
    def test_gap_inside_trade_is_flagged(self):
        bars = flat(40)
        holed = bars[:20] + bars[30:]  # 10 candles missing on a weekday
        trades = [dict(status="sl", placed=bars[15][0], filled=bars[16][0], exit=bars[33][0], r_net=-1, r_gross=-1),
                  dict(status="tp", placed=bars[1][0], filled=bars[2][0], exit=bars[5][0], r_net=1, r_gross=1)]
        prov = dict(segments=[dict(source="mfp:binance|X", from_ms=bars[0][0], to_ms=bars[-1][0] + M15)])
        T.annotate_trades(trades, holed, prov, M15, "15m", {"provider": "binance"})
        self.assertEqual(trades[0]["gap_bars_in_trade"], 10)
        self.assertNotIn("gap_bars_in_trade", trades[1])
        self.assertEqual(trades[1]["source"], "mfp:binance|X")
        cw = trades[1]["candle_window"]
        self.assertEqual(cw["interval"], "15m")
        self.assertLess(cw["from_ms"], trades[1]["placed"])
        self.assertGreater(cw["to_ms"], trades[1]["exit"])
        self.assertEqual(MX.compute(trades)["integrity"]["trades_across_gaps"], 1)

    def test_weekend_closure_is_not_a_gap_for_session_markets(self):
        fri = 1_700_179_200_000  # Fri 17 Nov 2023 00:00 UTC
        bars = [(fri + i * M15, 1, 1, 1, 1) for i in range(80)] + [(fri + 3 * 86400_000 + i * M15, 1, 1, 1, 1) for i in range(10)]
        self.assertEqual(T._unexpected_gaps(bars, M15, session=True), [])
        self.assertEqual(len(T._unexpected_gaps(bars, M15, session=False)), 1)

    def test_data_quality_reports_insufficient_and_holes(self):
        data = dict(days=40, days_requested=90, bars=1000,
                    provenance=dict(gaps=dict(gaps=3, missing_bars=50), overlap_checks=[]))
        s = dict(closed=4)
        st = dict(integrity=dict(overlapping_trades=0))
        dq = T.data_quality(data, s, st)
        self.assertEqual(dq["status"], "insufficient")
        txt = " ".join(dq["messages"])
        self.assertIn("40 of the 90 days", txt)
        self.assertIn("50 candles are missing", txt)
        self.assertIn("Only 4 closed trades", txt)
        ok = T.data_quality(dict(days=90, days_requested=90, bars=1000, provenance=dict(gaps=dict(gaps=0, missing_bars=0))),
                            dict(closed=40), st)
        self.assertEqual((ok["status"], ok["messages"]), ("ok", []))


class TestWebParams(unittest.TestCase):
    def setUp(self):
        import app as A
        self.A = A
        self.A.resolve = lambda name: {"market_id": "binance|BTCUSDT", "coin": "BTCUSDT", "provider": "binance"}

    def body(self, **kw):
        d = dict(market="BTC", side="long", entry=100, tp=102, sl=99, mode="similar")
        d.update(kw)
        return d

    def test_default_slippage_and_flags(self):
        p = self.A.parse_test_body(self.body())
        self.assertEqual((p["slip_unit"], p["slip"], p["allow_overlap"]), ("bps", T.DEFAULT_SLIPPAGE_BPS, False))
        cmd = self.A.engine_cmd(p, "/tmp/x.json")
        self.assertIn(f"--slippage-bps={T.DEFAULT_SLIPPAGE_BPS!r}", cmd)
        self.assertNotIn("--allow-overlap", cmd)

    def test_ticks_and_overlap(self):
        p = self.A.parse_test_body(self.body(slippage=4, slippage_unit="ticks", allow_overlap=True))
        cmd = self.A.engine_cmd(p, "/tmp/x.json")
        self.assertIn("--slippage-ticks=4.0", cmd)
        self.assertIn("--allow-overlap", cmd)

    def test_rejects_bad_values(self):
        for kw in (dict(slippage=-1), dict(slippage=500), dict(slippage="abc"), dict(slippage_unit="pips"),
                   dict(allow_overlap=True, mode="pattern")):
            with self.assertRaises(ValueError):
                self.A.parse_test_body(self.body(**kw))


if __name__ == "__main__":
    unittest.main(verbosity=1)
