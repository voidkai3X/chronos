#!/usr/bin/env python3
"""Blueprint Phase 1 additions: recovery factor, max concurrent positions, data fingerprint, engine version,
and the fingerprint kept in library run snapshots."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "web")]
import metrics, tester  # noqa: E402


class TestPhase1(unittest.TestCase):
    def test_recovery_factor(self):
        b = metrics.block([1, -1, 2, -1, -1, 3])
        self.assertEqual(b["max_drawdown_r"], -2.0)
        self.assertEqual(b["recovery_factor"], 1.5)
        self.assertIsNone(metrics.block([1, 2])["recovery_factor"])   # no drawdown: undefined, not infinite
        self.assertLess(metrics.block([-1, -1])["recovery_factor"], 0)

    def test_max_concurrent(self):
        tr = [dict(filled=1, exit=5), dict(filled=2, exit=3), dict(filled=3, exit=4), dict(filled=5), dict(placed=1)]
        self.assertEqual(metrics.max_concurrent(tr), 2)   # exit at t=3 happens before the fill at t=3
        self.assertEqual(metrics.max_concurrent([]), 0)
        self.assertEqual(metrics.max_concurrent([dict(filled=1, exit=2), dict(filled=2, exit=3)]), 1)

    def test_compute_has_new_fields(self):
        trades = [dict(placed=0, filled=1, exit=5, r_gross=1.0, r_net=0.9, outcome="tp", hours=1.0),
                  dict(placed=0, filled=2, exit=6, r_gross=-1.0, r_net=-1.1, outcome="sl", hours=1.0)]
        st = metrics.compute(trades)
        self.assertEqual(st["max_concurrent"], 2)
        self.assertIn("recovery_factor", st["net"])

    def test_fingerprint(self):
        a = [(1, 1.0, 2.0, 0.5, 1.5), (2, 1.5, 2.5, 1.0, 2.0)]
        self.assertEqual(tester.data_fingerprint(a), tester.data_fingerprint(list(a)))
        self.assertNotEqual(tester.data_fingerprint(a), tester.data_fingerprint([a[0], (2, 1.5, 2.5, 1.0, 2.0000001)]))
        self.assertEqual(len(tester.data_fingerprint(a)), 16)

    def test_engine_version(self):
        old = os.environ.get("ASSET_VERSION")
        os.environ["ASSET_VERSION"] = "abc123def456789"
        try:
            self.assertEqual(tester.engine_version(), "abc123def456")
        finally:
            if old is None:
                os.environ.pop("ASSET_VERSION")
            else:
                os.environ["ASSET_VERSION"] = old
        self.assertTrue(tester.engine_version())

    def test_snapshot_keeps_fingerprint(self):
        import library
        out = dict(generated="x", engine_version="v1", market=dict(market_id="binance|BTCUSDT"), setup=dict(side="long", rr=2.0),
                   result=dict(closed=1, verdict="v"), stats=dict(net=dict()), data=dict(fingerprint="f00d", provenance=dict(segments=[])),
                   params=dict(), costs=dict(slippage=dict(text="")), fees=dict())
        snap = library.snapshot_from_output(out, {})
        self.assertEqual(snap["provenance"]["fingerprint"], "f00d")
        self.assertEqual(snap["engine_version"], "v1")


if __name__ == "__main__":
    unittest.main()
