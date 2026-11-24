#!/usr/bin/env python3
"""Offline checks for the sweep / confirm entry styles (synthetic candles, no network).

1. Causal: cutting off the future never changes a sweep/confirm trade that had already finished, and changing every
   candle after a trade's trigger never changes that trade's tap, trigger, entry, SL or TP.
2. Order of events: zone confirmed <= tap <= trigger <= fill; SL beyond both the zone's far edge and the tap extreme.
3. Hand-built sweep: price taps a supply zone, wicks above its far edge, closes back inside -> short entered on that
   close (not before), SL above the sweep high, then TP. No sweep -> no sweep trade.
4. Hand-built confirm: tap, 15m swing low, push up, bearish 15m close below that swing low -> short entered there.
Run: python3 tests/test_entries.py   (or pytest tests/)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tester as T
from test_pattern import M15, T0, bars_from_4h, random_walk

DONE = ("tp", "sl", "time")


def run(base, side, style, tf="1h", start=None, **kw):
    sg = 1 if side == "long" else -1
    g = T.build_geometry(side, 1.0, 1.0 + sg * 0.75, 1.0 - sg * 0.25, 1.0, 0.01)  # rr = 3
    return T.replay_pattern_triggered(base, g, style, zone_tf=tf, confirm_tf="15m", sl_buffer_atr=0.3,
                                      start_ms=start or base[0][0] + 5 * 86400_000, max_hold_ms=7 * 86400_000, **kw)


STRIP = ("sl_then_tp", "extra_stop_needed_atr", "shallow_sweep")


def strip(L):
    return [{k: v for k, v in t.items() if k not in STRIP} for t in L]


def test_truncation_sweep_confirm():
    base = random_walk(n=8000, seed=11)
    n_done = 0
    for style in ("sweep", "confirm"):
        for side in ("short", "long"):
            for tf in ("30m", "1h"):
                full = run(base, side, style, tf)[0]
                for frac in (0.4, 0.6, 0.8):
                    cut = base[int(len(base) * frac)][0]
                    part = run([b for b in base if b[0] < cut], side, style, tf, start=base[0][0] + 5 * 86400_000)[0]
                    df = [t for t in full if t["status"] in DONE and t["exit"] <= cut]
                    dp = [t for t in part if t["status"] in DONE]
                    assert strip(df) == strip(dp)[:len(df)], (style, side, tf, frac)
                    n_done += len(df)
    assert n_done > 0, "expected finished sweep/confirm trades on synthetic data"


def test_future_changes_do_not_move_a_trigger():
    base = random_walk(n=8000, seed=5)
    for style in ("sweep", "confirm"):
        trades = [t for t in run(base, "short", style, "30m")[0] if "filled" in t]
        assert trades, style
        t0 = trades[len(trades) // 2]
        te = t0["trigger"]
        # scramble every candle that opens at or after the trigger time
        alt = [b if b[0] < te else (b[0], b[1] * 1.05, b[2] * 1.07, b[3] * 1.03, b[4] * 1.05) for b in base]
        again = [t for t in run(alt, "short", style, "30m")[0] if t.get("trigger") == te]
        assert again, (style, "trade with the same trigger vanished after changing only the future")
        for k in ("tap", "trigger", "entry", "sl", "tp", "zone_known"):
            assert again[0][k] == t0[k], (style, k)


def test_event_order_and_stop_placement():
    base = random_walk(n=8000, seed=3)
    n = 0
    for style in ("sweep", "confirm"):
        for side in ("short", "long"):
            sg = 1 if side == "long" else -1
            for t in run(base, side, style, "1h")[0]:
                assert t["zone_known"] <= t["tap"] == t["placed"]
                if "filled" not in t:
                    continue
                n += 1
                assert t["tap"] <= t["trigger"] <= t["filled"], t
                assert (t["zone_distal"] - t["sl"]) * sg > 0, "SL must sit beyond the zone's far edge"
                assert (t["tap_extreme"] - t["sl"]) * sg > 0, "SL must sit beyond the tap/sweep extreme"
                if style == "sweep":
                    assert (t["tap_extreme"] - t["zone_distal"]) * sg < 0, "a sweep trade needs price beyond the far edge"
                    assert (t["entry"] - t["zone_distal"]) * sg > 0, "sweep entry is a close back inside the zone"
                else:
                    assert (t["entry"] - t["swing_level"]) * sg > 0, "confirm entry is a close through the swing"
    assert n > 0


ZONE = [(100.0, 100.5, 99.5, 100.0)] * 20 + [(99.8, 99.9, 97.4, 97.5)] + [(97.5, 97.7, 97.3, 97.5)] * 3
G = T.build_geometry("short", 99.5, 96.5, 101.0, 97.5, 1.0)  # user R:R 1:2


def test_handbuilt_sweep_entry():
    # retest: rallies from 97.4 to 101.0 (0.5 above the zone's far edge 100.5), then 15m closes fall back below 100.5
    sweep = [(97.5, 101.0, 97.4, 100.2)]
    drop = [(100.2, 100.25, 95.0, 95.2)]
    tail = [(95.2, 95.4, 95.0, 95.2)] * 5
    base = bars_from_4h(ZONE + sweep + drop + tail)
    tr, _, zones, _ = T.replay_pattern_triggered(base, G, "sweep", zone_tf="4h", confirm_tf="15m", sl_buffer_atr=0.5,
                                                 start_ms=T0 + 15 * T.H4)
    z = zones[0]
    assert abs(z["proximal"] - 99.5) < 1e-9 and abs(z["distal"] - 100.5) < 1e-9, z
    filled = [t for t in tr if "filled" in t]
    assert len(filled) == 1, tr
    t = filled[0]
    t_sweep_high = T0 + 24 * T.H4 + 11 * M15  # the 15m bar that closes at the 101.0 high ends here
    assert t["trigger"] > t_sweep_high, "entered before the sweep had happened"
    assert t["trigger"] == T0 + 24 * T.H4 + 15 * M15, t  # first 15m close back under 100.5 (100.36)
    assert abs(t["entry"] - 100.36) < 1e-6, t
    assert abs(t["tap_extreme"] - 101.0) < 1e-9 and t["sl"] > 101.0, t
    assert t["filled"] >= t["trigger"] and t["status"] == "tp", t
    assert abs(t["r_gross"] - 2.0) < 0.01, t  # TP at the user's R:R; fill at the next open = trigger close here
    # cut the data before the close back inside: no trade may exist yet
    early = [b for b in base if b[0] < t["trigger"] - M15]
    tr2 = T.replay_pattern_triggered(early, G, "sweep", zone_tf="4h", confirm_tf="15m", sl_buffer_atr=0.5,
                                     start_ms=T0 + 15 * T.H4)[0]
    assert not [x for x in tr2 if "filled" in x], tr2
    # same zone, plain retest that never trades above the far edge: no sweep trade
    plain = bars_from_4h(ZONE + [(97.5, 99.6, 97.4, 99.4)] + drop + tail)
    tr3 = T.replay_pattern_triggered(plain, G, "sweep", zone_tf="4h", confirm_tf="15m", sl_buffer_atr=0.5,
                                     start_ms=T0 + 15 * T.H4)[0]
    assert not [x for x in tr3 if "filled" in x], tr3


def test_handbuilt_confirm_entry():
    pre = bars_from_4h(ZONE)
    t = pre[-1][0] + M15
    path = [(97.5, 97.8), (97.8, 98.3), (98.3, 98.8), (98.8, 99.2), (99.2, 99.7),  # rally, taps 99.5
            (99.7, 99.4), (99.4, 99.1, 98.9), (99.1, 99.5), (99.5, 99.8), (99.8, 100.0),  # swing low 98.9, push up
            (100.0, 99.6), (99.6, 99.3), (99.3, 98.7)]  # bearish closes; 98.7 < 98.9 = break of structure
    path += [(98.7 - 0.6 * k, 98.1 - 0.6 * k) for k in range(12)]  # sell-off
    path += [(91.5, 91.5)] * 40
    bars = []
    for p in path:
        o, c = p[0], p[1]
        lo = p[2] if len(p) > 2 else min(o, c) - 0.02
        bars.append((t, o, max(o, c) + 0.02, lo, c))
        t += M15
    base = pre + bars
    tr, _, zones, _ = T.replay_pattern_triggered(base, G, "confirm", zone_tf="4h", confirm_tf="15m", sl_buffer_atr=0.5,
                                                 start_ms=T0 + 15 * T.H4)
    filled = [x for x in tr if "filled" in x]
    assert len(filled) == 1, tr
    x = filled[0]
    assert abs(x["swing_level"] - 98.9) < 1e-9, x
    assert x["trigger"] == bars[12][0] + M15 and abs(x["entry"] - 98.7) < 1e-9, x
    assert x["sl"] > 100.5 + 0.4, "SL beyond the zone's far edge (tap high 100.02 is inside the zone)"
    assert x["status"] == "tp", x


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, str(e)[:400])
    sys.exit(1 if fails else 0)
