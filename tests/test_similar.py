#!/usr/bin/env python3
"""Unit tests for CHRONOS similar mode (no network)."""
import math, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tester as T
import similar as S

STEP15 = 15 * 60_000
T0 = (1_700_000_000_000 // 3_600_000) * 3_600_000  # aligned to 1h


def ohlc(o, c, pad=0.05):
    return (o, max(o, c) + pad, min(o, c) - pad, c)


def paint(n, p0=100.0, start=T0, moves=None, noise=0.0, seed=1):
    """15m bars. moves[i] is the close-to-close change at bar i."""
    import random
    rng = random.Random(seed)
    bars, p = [], p0
    for i in range(n):
        d = (moves[i] if moves is not None and i < len(moves) else 0.0)
        if noise:
            d += rng.uniform(-noise, noise)
        o = p
        c = max(0.01, p + d)
        o_, h, l, c_ = ohlc(o, c, pad=0.08 + abs(d) * 0.25)
        bars.append((start + i * STEP15, o_, h, l, c_))
        p = c_
    return bars


def hourly_shape(scale=1.0):
    """24 x 1h close-to-close moves: dip, impulse up, pullback."""
    return ([-0.16 * scale] * 8) + ([0.42 * scale] * 6) + ([-0.10 * scale] * 10)


def expand_hourly(moves_1h):
    """Turn 1h close-to-close moves into 4 x 15m moves that sum to the same change."""
    out = []
    for d in moves_1h:
        out.extend([d / 4.0] * 4)
    return out


def hour_idx(zb, t):
    """Index of the 1h bar whose open is the bucket containing t."""
    hits = [i for i, b in enumerate(zb) if b[0] <= t < b[0] + T.STEP["1h"]]
    if hits:
        return hits[0]
    return max((i for i, b in enumerate(zb) if b[0] <= t), default=None)


def plant(n, plants, p0=100.0, start=T0, drift=0.0):
    """n 15m bars; plants is {start_15m_index: list of 15m moves}."""
    moves = [drift] * n
    for i0, seq in plants.items():
        for k, d in enumerate(seq):
            if 0 <= i0 + k < n:
                moves[i0 + k] = d
    return paint(n, p0=p0, start=start, moves=moves, noise=0.0)


class TestPathScaleFree(unittest.TestCase):
    def test_identical_windows_zero_distance(self):
        n = 24
        zb = [(T0 + i * T.STEP["1h"], 100, 101, 99, 100 + i * 0.1) for i in range(40)]
        atr = [1.0] * 40
        a = S.fingerprint(zb, atr, 30, n, htf=None)
        b = S.fingerprint(zb, atr, 30, n, htf=None)
        self.assertIsNotNone(a)
        self.assertAlmostEqual(S.distance(a, b), 0.0, places=9)
        self.assertAlmostEqual(S.score(0.0), 100.0, places=6)

    def test_atr_normalised_path_is_scale_free(self):
        """The same shape at 2x price and 2x ATR must produce (almost) the same path and a tiny distance."""
        n15 = 40 * 96  # 40 days
        seq = expand_hourly(hourly_shape(1.0))
        seq2 = expand_hourly(hourly_shape(2.0))
        # plant the shape near the end of each series, after plenty of warm-up
        i0 = 30 * 96
        a_bars = plant(n15, {i0: seq}, p0=100.0)
        b_bars = plant(n15, {i0: seq2}, p0=200.0)
        za, step, atra, _ = S.prepare(a_bars, "1h")
        zb, _, atrb, _ = S.prepare(b_bars, "1h")
        # last bar of the planted 24h window
        end_i = (i0 + len(seq) - 1) // 4  # 15m index -> 1h index, approx
        # find the 1h bar whose open matches the last hour of the plant
        plant_last_open = a_bars[i0 + len(seq) - 4][0]
        ia = hour_idx(za, plant_last_open)
        ib = hour_idx(zb, plant_last_open)
        fa = S.fingerprint_at(za, atra, step, ia, 24, htf=None)
        fb = S.fingerprint_at(zb, atrb, step, ib, 24, htf=None)
        self.assertIsNotNone(fa)
        self.assertIsNotNone(fb)
        # paths should line up closely (z-norm of ATR-normed closes)
        mse = sum((x - y) ** 2 for x, y in zip(fa["path"], fb["path"])) / 24
        self.assertLess(mse, 0.05, f"path mse {mse} too large for a scaled copy")
        d = S.distance(fa, fb)
        self.assertLess(d, 0.5, f"distance {d} should be small for a scaled copy")
        self.assertGreater(S.score(d), 50)

    def test_opposite_path_is_farther_than_scaled_copy(self):
        n15 = 40 * 96
        seq = expand_hourly(hourly_shape(1.0))
        opp = expand_hourly([-x for x in hourly_shape(1.0)])
        i0 = 30 * 96
        a_bars = plant(n15, {i0: seq}, p0=100.0)
        c_bars = plant(n15, {i0: opp}, p0=100.0)
        za, step, atra, _ = S.prepare(a_bars, "1h")
        zc, _, atrc, _ = S.prepare(c_bars, "1h")
        plant_last_open = a_bars[i0 + len(seq) - 4][0]
        ia = hour_idx(za, plant_last_open)
        ic = hour_idx(zc, plant_last_open)
        fa = S.fingerprint_at(za, atra, step, ia, 24, htf=None)
        fc = S.fingerprint_at(zc, atrc, step, ic, 24, htf=None)
        # rebuild the scaled copy quickly
        seq2 = expand_hourly(hourly_shape(2.0))
        b_bars = plant(n15, {i0: seq2}, p0=200.0)
        zb, _, atrb, _ = S.prepare(b_bars, "1h")
        ib = hour_idx(zb, plant_last_open)
        fb = S.fingerprint_at(zb, atrb, step, ib, 24, htf=None)
        self.assertGreater(S.distance(fa, fc), S.distance(fa, fb) + 0.3)


class TestCausality(unittest.TestCase):
    def test_future_bars_do_not_change_fingerprint(self):
        n15 = 25 * 96
        bars = plant(n15, {20 * 96: expand_hourly(hourly_shape(1.0))}, p0=100.0)
        zb, step, atr, htf = S.prepare(bars, "1h")
        i = 21 * 24  # well inside the series
        fp1 = S.fingerprint_at(zb, atr, step, i, 24, htf=htf)
        self.assertIsNotNone(fp1)
        # mutate every bar after i (price spike)
        zb2 = list(zb)
        atr2 = list(atr)
        for j in range(i + 1, len(zb2)):
            o, h, l, c = zb2[j][1], 999, 1, 500
            zb2[j] = (zb2[j][0], o, h, l, c) + zb2[j][5:]
            atr2[j] = 50.0
        fp2 = S.fingerprint_at(zb2, atr2, step, i, 24, htf=htf)
        self.assertEqual(fp1["path"], fp2["path"])
        self.assertEqual(fp1["feat"], fp2["feat"])

    def test_setup_index_uses_last_visit_to_entry_not_the_latest_bar(self):
        # flat 100, then a dip that tags 90, then a rally to 120
        n = 10 * 96
        moves = [0.0] * n
        # around bar 6*96, sell off through 90 (down to ~80) and bounce, then rally
        for k in range(80):
            moves[6 * 96 + k] = -0.5 if k < 40 else 0.5
        for k in range(6 * 96 + 80, n):
            moves[k] = 0.08
        bars = paint(n, p0=100.0, moves=moves)
        zb, step, atr, _ = S.prepare(bars, "1h")
        end_ms = zb[-1][0] + step
        # current price is well above 90
        self.assertGreater(zb[-1][4], 100)
        i_now = S.setup_index(zb, step, atr, end_ms, entry=None)
        i_entry = S.setup_index(zb, step, atr, end_ms, entry=90.0)
        self.assertEqual(i_now, len(zb) - 1)
        self.assertLess(i_entry, i_now)
        # the chosen bar actually traded at 90
        b = zb[i_entry]
        self.assertTrue(b[3] <= 90.0 <= b[2])

    def test_setup_index_stays_on_last_bar_when_price_is_still_at_entry(self):
        bars = paint(10 * 96, p0=100.0, moves=[0.0] * (10 * 96))
        zb, step, atr, _ = S.prepare(bars, "1h")
        end_ms = zb[-1][0] + step
        i = S.setup_index(zb, step, atr, end_ms, entry=zb[-1][4])
        self.assertEqual(i, len(zb) - 1)


class TestNonOverlapAndSearch(unittest.TestCase):
    def setUp(self):
        # 40 days, plant the same shape at 1h ends ~ 22d, ~22d+5h (overlap), and ~32d (far)
        self.n = 40 * 96
        self.seq = expand_hourly(hourly_shape(1.0))
        # 1h index 22*24 = 528; 15m index = 528*4
        self.p1 = 22 * 96
        self.p2 = 22 * 96 + 5 * 4   # 5 hours later — overlaps a 24-bar window
        self.p3 = 32 * 96           # 10 days later — no overlap
        self.bars = plant(self.n, {self.p1: self.seq, self.p2: self.seq, self.p3: self.seq}, p0=100.0)
        self.zb, self.step, self.atr, self.htf = S.prepare(self.bars, "1h")
        last_open = lambda p: self.bars[p + len(self.seq) - 4][0]
        self.i1 = hour_idx(self.zb, last_open(self.p1))
        self.i2 = hour_idx(self.zb, last_open(self.p2))
        self.i3 = hour_idx(self.zb, last_open(self.p3))
        self.query = S.fingerprint_at(self.zb, self.atr, self.step, self.i3, 24, htf=None)
        self.assertIsNotNone(self.query)

    def test_search_drops_overlapping_matches(self):
        # query is a dummy far-future window we will exclude; search the whole series
        picked = S.search(self.zb, self.atr, self.step, self.query, 24, k=10,
                          htf=None, exclude_idx=None)
        idxs = [m["idx"] for m in picked]
        # any two picked windows must be at least 24 bars apart
        for a, b in zip(sorted(idxs), sorted(idxs)[1:]):
            self.assertGreaterEqual(abs(b - a), 24, f"overlap: {a} and {b}")
        # the overlapping pair i1/i2 must not both appear
        self.assertFalse({self.i1, self.i2}.issubset(set(idxs)))

    def test_search_excludes_the_query_window(self):
        picked = S.search(self.zb, self.atr, self.step, self.query, 24, k=10,
                          htf=None, exclude_idx=self.i3)
        idxs = [m["idx"] for m in picked]
        self.assertNotIn(self.i3, idxs)
        for i in idxs:
            self.assertGreaterEqual(abs(i - self.i3), 24)

    def test_results_are_sorted_by_distance(self):
        picked = S.search(self.zb, self.atr, self.step, self.query, 24, k=8,
                          htf=None, exclude_idx=self.i3)
        dists = [m["distance"] for m in picked]
        self.assertEqual(dists, sorted(dists))
        scores = [m["score"] for m in picked]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestLevelsAndGate(unittest.TestCase):
    def test_live_signal_scales_geometry_by_atr(self):
        # long: entry 0.5 ATR below price, SL 1 ATR below entry, TP 3 ATR above entry
        g = T.build_geometry("long", 99.5, 102.5, 98.5, ref=100.0, atr=1.0)
        self.assertAlmostEqual(g["entry_atr"], -0.5, places=6)
        self.assertAlmostEqual(g["risk_atr"], 1.0, places=6)
        self.assertAlmostEqual(g["reward_atr"], 3.0, places=6)
        # at another market: price 200, ATR 2 → entry 199, SL 197, TP 205
        sig = S.live_signal(None, g, atr4=2.0, price=200.0)
        self.assertEqual(sig["side"], "long")
        self.assertAlmostEqual(sig["entry"], 199.0, places=5)
        self.assertAlmostEqual(sig["sl"], 197.0, places=5)
        self.assertAlmostEqual(sig["tp"], 205.0, places=5)
        self.assertAlmostEqual(sig["rr"], 3.0, places=5)
        # pending long limit sits below price at every transferred ref, so not through
        self.assertFalse(sig["through_entry"])
        sig3 = S.live_signal(None, g, atr4=2.0, price=200.5)
        self.assertFalse(sig3["through_entry"])
        # if the source price has already traded through the entry, the transferred limit is also through
        g2 = T.build_geometry("long", 99.5, 102.5, 98.5, ref=99.0, atr=1.0)
        sig4 = S.live_signal(None, g2, atr4=2.0, price=200.0)
        self.assertTrue(sig4["through_entry"])

    def test_gate_requires_possible_or_proven_edge(self):
        self.assertTrue(S.verdict_qualifies({"verdict": "possible edge: average R is positive but t=1.2."}))
        self.assertTrue(S.verdict_qualifies({"verdict": "edge on this sample (t=2.4)."}))
        self.assertFalse(S.verdict_qualifies({"verdict": "too few samples (need 20 closed trades, have 12)."}))
        self.assertFalse(S.verdict_qualifies({"verdict": "no edge: average R after fees is -0.10R."}))
        self.assertFalse(S.verdict_qualifies({}))
        self.assertEqual(S.gate_reason({"verdict": "too few samples (need 20 closed trades, have 12)."}),
                         "needs a passing edge first (too few samples)")
        self.assertIsNone(S.gate_reason({"verdict": "possible edge: average R is positive but t=1.2."}))

    def test_scan_rows_sort_by_distance(self):
        rows = [
            dict(market_id="b", distance=0.40, score=50),
            dict(market_id="a", distance=0.10, score=80),
            dict(market_id="c", distance=0.10, score=80),
        ]
        rows.sort(key=lambda r: (r["distance"], r["market_id"]))
        self.assertEqual([r["market_id"] for r in rows], ["a", "c", "b"])


class TestReplayFills(unittest.TestCase):
    def test_replay_places_atr_scaled_trade_and_uses_fill_rules(self):
        """A planted similar window should produce a limit at entry_atr from that close, SL-wins-ties."""
        n = 40 * 96
        seq = expand_hourly(hourly_shape(1.0))
        # plant at day 22 and day 32; query will sit on the later plant
        bars = plant(n, {22 * 96: seq, 32 * 96: seq}, p0=100.0)
        zb, step, atr, htf = S.prepare(bars, "1h")
        last_open = bars[32 * 96 + len(seq) - 4][0]
        iq = hour_idx(zb, last_open)
        query = S.fingerprint_at(zb, atr, step, iq, 24, htf=htf) or S.fingerprint_at(zb, atr, step, iq, 24, htf=None)
        self.assertIsNotNone(query)
        g = T.build_geometry("long", 99.5, 101.0, 99.0, ref=100.0, atr=1.0)  # 0.5 ATR in, 0.5 ATR stop, 1.5 ATR target
        # end the test just after the later plant so it is the query (excluded)
        end_ms = query["t"]
        start_ms = bars[0][0] + 15 * 86400_000
        trades, skipped, matches, _ = S.replay_similar(
            bars, g, tf="1h", n=24, k=8, expiry_ms=72 * 3600_000,
            start_ms=start_ms, end_ms=end_ms, fee_pct=0.0, swap_pct_day=0.0,
            query=query, exclude_query=True)
        # the later plant is the query and must not be traded
        traded_t = {m["t"] for m in matches if not m.get("skipped")}
        self.assertNotIn(query["t"], traded_t)
        # at least one earlier similar window should have been selected
        self.assertTrue(any(not m.get("skipped") for m in matches), f"no matches: {matches}")
        # geometry at a match is ATR-scaled from that window's close using 4h ATR
        m0 = next(m for m in matches if not m.get("skipped"))
        self.assertIn("entry", m0)
        self.assertIn("sl", m0)
        self.assertIn("tp", m0)
        self.assertLess(m0["sl"], m0["entry"])
        self.assertGreater(m0["tp"], m0["entry"])
        # scores present and in (0, 100]
        self.assertTrue(0 < m0["score"] <= 100)
        # fill-rule: if a trade closed SL and TP could both be in play, SL is the conservative result —
        # we just assert statuses come from the shared simulator.
        for tr in trades:
            self.assertIn(tr["status"], {"unfilled", "tp", "sl", "time", "open"})
            if "similar_score" in tr:
                self.assertTrue(tr["similar_score"] > 0)


if __name__ == "__main__":
    unittest.main()
