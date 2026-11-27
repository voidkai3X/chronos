#!/usr/bin/env python3
"""Offline checks for the Edge Score and the advanced analytics (synthetic trade lists, no network).

Worked example: a 90% win rate with tiny wins and big losses (negative expectancy) must score low, and a 40% win
rate with positive, consistent expectancy over enough trades must score higher.
Run: python3 tests/test_edge_score.py
"""
import os, random, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import edge_score as ES
import metrics as MX
import tester as T

H = 3600_000


def trades_from(rs, cost=0.05, conds=None, seed=1):
    """Closed trades in time order. r_gross = r_net + cost, so doubling costs takes another `cost` off."""
    rnd = random.Random(seed)
    out = []
    for i, r in enumerate(rs):
        t = dict(status="tp" if r > 0 else "sl", placed=i * 10 * H, filled=i * 10 * H + H, exit=i * 10 * H + 5 * H,
                 r_net=r, r_gross=r + cost)
        if conds is not False:
            t["conditions"] = conds[i] if conds else dict(trend=rnd.choice(["trending", "ranging"]),
                                                          vol=rnd.choice(["high", "low"]),
                                                          bias=rnd.choice(["bullish", "bearish"]))
        out.append(t)
    return out


def comp(e, key):
    return next(c for c in e["components"] if c["key"] == key)


class WorkedExample(unittest.TestCase):
    def test_high_win_rate_negative_ev_scores_low(self):
        rs = [(-1.2 if i % 10 == 0 else 0.1) for i in range(200)]  # 90% wins, avg = 0.9*0.1 - 0.1*1.2 = -0.03R
        e = ES.score(trades_from(rs))
        self.assertAlmostEqual(sum(rs) / len(rs), -0.03, places=6)
        self.assertLessEqual(e["score"], 15)
        self.assertEqual(e["label"], "No evidence")
        self.assertEqual(comp(e, "sample")["points"], 0, "a big losing sample earns nothing")
        self.assertEqual(ES.verdict(dict(closed=200, avg_r_net=-0.03, t_stat=-1), e), "no edge (expectancy <= 0 after fees)")

    def test_modest_win_rate_positive_consistent_scores_higher(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(150)]  # 40% wins at 2R: avg = 0.8 - 0.6 = +0.2R
        good = ES.score(trades_from(rs))
        bad = ES.score(trades_from([(-1.2 if i % 10 == 0 else 0.1) for i in range(200)]))
        self.assertGreater(good["score"], bad["score"] + 40)
        # t = 1.67: the 90% range of average R still touches zero, so it stops at Promising (capped at 60)
        self.assertEqual((good["score"], good["label"]), (60, "Promising"))
        self.assertEqual(comp(good, "time")["value"], "4 of 4 periods positive")
        v = ES.verdict(dict(closed=150, avg_r_net=0.2, t_stat=good["t_stat"]), good)
        self.assertTrue(v.startswith("possible edge, not proven (Edge Score 60/100"), v)
        # the same edge over 400 trades is far less likely to be noise -> Strong
        more = ES.score(trades_from([2.0 if i % 5 in (0, 2) else -1.0 for i in range(400)]))
        self.assertGreater(ES.bootstrap([2.0 if i % 5 in (0, 2) else -1.0 for i in range(400)])["expectancy"]["low"], 0)
        self.assertEqual(more["label"], "Strong")
        v = ES.verdict(dict(closed=400, avg_r_net=0.2, t_stat=more["t_stat"]), more)
        self.assertTrue(v.startswith("edge on this sample (Edge Score"), v)

    def test_win_rate_alone_is_not_rewarded(self):
        # same expectancy, very different win rates -> scores within a few points
        hi_wr = [0.3 if i % 10 else -1.7 for i in range(120)]       # 90% wins, avg +0.10
        lo_wr = [2.5 if i % 10 in (0, 5) else -0.5 for i in range(120)]  # 20% wins, avg +0.10
        a, b = ES.score(trades_from(hi_wr)), ES.score(trades_from(lo_wr))
        self.assertAlmostEqual(sum(hi_wr) / 120, sum(lo_wr) / 120, places=9)
        self.assertEqual(comp(a, "expectancy")["points"], comp(b, "expectancy")["points"])
        self.assertNotIn("win", " ".join(c["key"] for c in a["components"]))


class Caps(unittest.TestCase):
    def test_too_few_trades(self):
        e = ES.score(trades_from([2.0] * 10))
        self.assertLessEqual(e["score"], 15)
        self.assertTrue(any("fewer than 15" in c["reason"] for c in e["caps"]))
        self.assertTrue(ES.verdict(dict(closed=10, avg_r_net=2.0, t_stat=0), e).startswith("too few samples"))

    def test_small_sample_capped(self):
        rs = [2.0 if i % 2 == 0 else -1.0 for i in range(24)]
        e = ES.score(trades_from(rs))
        self.assertLessEqual(e["score"], 40)
        self.assertTrue(any("fewer than 30" in c["reason"] for c in e["caps"]))

    def test_uncertain_mean_capped_at_promising(self):
        rnd = random.Random(5)
        rs = [rnd.choice([3.0, -1.0, -1.0, -1.0]) + 0.05 for _ in range(60)]
        e = ES.score(trades_from(rs))
        lo = ES.bootstrap(rs)["expectancy"]["low"]
        if sum(rs) > 0 and lo <= 0:
            self.assertLessEqual(e["score"], 60)
            self.assertTrue(any("includes zero" in c["reason"] for c in e["caps"]))

    def test_strong_needs_50_trades_and_t_of_2(self):
        # 45 trades of a clear +R edge: every other test passes, but under 50 trades it stays below Strong
        rs = [2.0 if i % 5 in (0, 2, 4) else -1.0 for i in range(45)]
        e = ES.score(trades_from(rs, cost=0.01))
        self.assertGreater(e["raw"], 69)
        self.assertEqual((e["score"], e["label"]), (69, "Promising"))
        self.assertTrue(any("50+ closed trades" in c["reason"] for c in e["caps"]))
        # enough trades but t below 2 -> also capped at 69 (if the raw score would otherwise reach Strong)
        rnd = random.Random(11)
        for _ in range(200):
            rs = [rnd.choice([2.6, -1.0, -1.0]) for _ in range(120)]
            e = ES.score(trades_from(rs, cost=0.01))
            if 0 < e["t_stat"] < 2:
                self.assertLessEqual(e["score"], 69)
                break

    def test_costs_double_kills_edge(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(150)]  # +0.2R
        e = ES.score(trades_from(rs, cost=0.3))  # doubling costs takes 0.3R more -> -0.1R
        self.assertEqual(comp(e, "costs")["points"], 0)
        self.assertLessEqual(e["score"], 60)
        self.assertTrue(any("costs are doubled" in c["reason"] for c in e["caps"]))
        self.assertAlmostEqual(e["stressed_expectancy_r"], -0.1, places=6)

    def test_bands_and_weights(self):
        self.assertEqual(sum(ES.WEIGHTS.values()), 100)
        self.assertEqual([ES._band(x)[1] for x in (0, 19, 20, 44, 45, 69, 70, 100)],
                         ["No evidence", "No evidence", "Weak", "Weak", "Promising", "Promising", "Strong", "Strong"])

    def test_empty_and_all_wins(self):
        e = ES.score([])
        self.assertEqual(e["score"], 0)
        e = ES.score(trades_from([1.0] * 40))
        self.assertEqual(comp(e, "pf")["value"], "no losing trades")
        self.assertLessEqual(e["score"], 100)


class Components(unittest.TestCase):
    def test_points_add_up_and_contributions_shown(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(150)]
        e = ES.score(trades_from(rs))
        self.assertAlmostEqual(sum(c["points"] for c in e["components"]), e["raw"], places=0)
        for c in e["components"]:
            self.assertTrue(0 <= c["points"] <= c["weight"])
            self.assertTrue(c["name"] and c["detail"] and c["value"])

    def test_time_consistency_catches_one_lucky_stretch(self):
        rs = [3.0] * 30 + [-1.0, -0.5] * 45  # all profit in the first quarter
        e = ES.score(trades_from(rs))
        self.assertEqual(comp(e, "time")["value"], "1 of 4 periods positive")
        steady = ES.score(trades_from([2.0 if i % 5 in (0, 2) else -1.0 for i in range(120)]))
        self.assertGreater(comp(steady, "time")["points"], comp(e, "time")["points"])

    def test_conditions_component(self):
        conds = [dict(trend="trending" if i % 2 else "ranging", vol="high", bias="bullish") for i in range(100)]
        rs = [2.0 if i % 2 else -1.0 for i in range(100)]  # wins only when trending
        e = ES.score(trades_from(rs, conds=conds))
        c = comp(e, "conditions")
        self.assertEqual(c["value"], "3 of 4 groups positive")  # trending+, ranging-, high+, bullish+
        e2 = ES.score(trades_from(rs, conds=False))
        self.assertEqual(comp(e2, "conditions")["points"], 0)

    def test_deterministic(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(90)]
        self.assertEqual(ES.score(trades_from(rs)), ES.score(trades_from(rs)))


class Analytics(unittest.TestCase):
    def test_bootstrap_ranges(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(150)]
        b = ES.bootstrap(rs)
        self.assertLess(b["expectancy"]["low"], 0.2)
        self.assertGreater(b["expectancy"]["high"], 0.2)
        self.assertLessEqual(b["win_rate"]["low"], 0.4 <= b["win_rate"]["high"])
        self.assertEqual(b["expectancy"]["actual"], 0.2)
        self.assertIsNone(ES.bootstrap([1.0]))

    def test_monte_carlo_drawdown(self):
        rs = [2.0] * 20 + [-1.0] * 30  # worst possible real order: wins first, then 30R down
        mc = ES.monte_carlo_drawdown(rs)
        self.assertEqual(mc["actual"], 30.0)
        self.assertLessEqual(mc["p95"], 30.0)
        self.assertLessEqual(mc["best"], mc["median"])
        self.assertLessEqual(mc["median"], mc["p95"])
        self.assertLessEqual(mc["p95"], mc["worst"])
        self.assertLessEqual(mc["worst"], 30.0, "no order can lose more than all the losers in a row")

    def test_walk_forward(self):
        closed = trades_from([2.0 if i % 5 in (0, 2) else -1.0 for i in range(60)] + [-1.0] * 30)
        wf = ES.walk_forward(MX.closed_trades(closed))
        self.assertEqual(wf["in_sample"]["trades"] + wf["out_of_sample"]["trades"], 90)
        self.assertEqual(wf["in_sample"]["trades"], 60)
        self.assertGreater(wf["in_sample"]["expectancy_r"], 0)
        self.assertEqual(wf["out_of_sample"]["expectancy_r"], -1.0)
        self.assertTrue(wf["faded"])
        self.assertLess(wf["in_sample"]["to_ms"], wf["out_of_sample"]["from_ms"])
        self.assertIsNone(ES.walk_forward(MX.closed_trades(closed)[:5]))

    def test_only_real_trades_used(self):
        tr = trades_from([1.0, -1.0] * 20) + [dict(status="unfilled", placed=1), dict(status="open_at_end", placed=2, r_net=5.0)]
        e, an = ES.analyse(tr)
        self.assertEqual(e["trades"], 40)
        self.assertEqual(an["bootstrap"]["expectancy"]["actual"], 0.0)


class EngineIntegration(unittest.TestCase):
    def test_verdict_follows_score(self):
        rs = [2.0 if i % 5 in (0, 2) else -1.0 for i in range(400)]
        tr = trades_from(rs)
        for t in tr:
            t["sl_then_tp"] = False
        g = dict(rr=2.0)
        s = T.summarize(tr, g)
        edge, an = T.apply_edge(s, tr)
        self.assertEqual(s["edge_score"], edge["score"])
        self.assertTrue(s["verdict"].startswith("edge on this sample"))
        self.assertTrue(T.pair_works(s))
        weak = trades_from([1.0 if i % 2 else -0.9 for i in range(40)])
        s2 = T.summarize(weak, g)
        e2, _ = T.apply_edge(s2, weak)
        self.assertIn(e2["band"], ("none", "weak"))
        self.assertTrue(s2["verdict"].startswith("no reliable edge"), s2["verdict"])
        self.assertFalse(T.pair_works(s2))
        self.assertEqual(T.short_verdict(s2), "no reliable edge")


if __name__ == "__main__":
    unittest.main(verbosity=1)
