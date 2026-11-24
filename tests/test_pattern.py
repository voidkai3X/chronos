#!/usr/bin/env python3
"""Offline checks for pattern mode (synthetic candles, no network).

1. No look-ahead in zone detection: zones found on data cut at time c are exactly the zones the full run had
   confirmed by c, and no order ever uses a zone confirmed after the order was placed.
2. Truncation: cutting off the future does not change any pattern trade that had already finished.
3. Hand-built supply retest: base -> drop -> rally back into the zone -> fills at the zone low -> hits TP.
Run: python3 tests/test_pattern.py   (or pytest tests/)
"""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tester as T

M15 = 900_000
T0 = 1_700_000_000_000 - 1_700_000_000_000 % T.H4


def bars_from_4h(ohlc, t0=T0):
    """Turn 4h OHLC bars into 16 x 15m bars each, walking open -> high -> low -> close (or open -> low -> high -> close
    for a bullish bar)."""
    out = []
    for n, (o, h, l, c) in enumerate(ohlc):
        pts = [o, h, l, c] if c < o else [o, l, h, c]
        path = []
        for s, (k0, k1) in enumerate(((0, 5), (5, 11), (11, 16))):  # vertices land exactly on bar edges
            path += [pts[s] + (pts[s + 1] - pts[s]) * (k - k0) / (k1 - k0) for k in range(k0, k1)]
        path.append(c)
        for k in range(16):
            a, b = path[k], path[k + 1]
            out.append((t0 + n * T.H4 + k * M15, a, max(a, b), min(a, b), b))
    return out


def random_walk(n=6000, seed=7):
    rnd, p, out = random.Random(seed), 1.0, []
    for i in range(n):
        o = p
        drift = rnd.gauss(0, 0.0012) + (rnd.random() < 0.01) * rnd.choice((-1, 1)) * 0.006  # occasional impulse
        c = o + drift
        h, l = max(o, c) + abs(rnd.gauss(0, 0.0005)), min(o, c) - abs(rnd.gauss(0, 0.0005))
        out.append((T0 + i * M15, o, h, l, c))
        p = c
    return out


def zones_on(base, side, tf):
    st = T.STEP[tf]
    zb = T.complete_bars(T.resample(base, st), st, M15)
    return T.detect_zones(zb, T.atr_series(zb), side, st)


def key(z):
    return (z["known"], round(z["proximal"], 10), round(z["distal"], 10))


def test_zone_detection_no_lookahead():
    base = random_walk()
    for side in ("short", "long"):
        for tf in ("30m", "1h", "4h"):
            full = zones_on(base, side, tf)
            assert full, f"synthetic data should produce {side} zones on {tf}"
            for frac in (0.3, 0.5, 0.7, 0.9):
                cut = base[int(len(base) * frac)][0]
                part = zones_on([b for b in base if b[0] < cut], side, tf)
                assert [key(z) for z in full if z["known"] <= cut] == [key(z) for z in part], (side, tf, frac)
                assert all(z["known"] <= cut for z in part)


def run_pattern(base, side, tf="1h", start=None):
    sg = 1 if side == "long" else -1
    g = T.build_geometry(side, 1.0, 1.0 + sg * 0.75, 1.0 - sg * 0.25, 1.0, 0.01)  # rr = 3 exactly
    return T.replay_pattern(base, g, zone_tf=tf, sl_buffer_atr=0.3, start_ms=start or base[0][0] + 5 * 86400_000,
                            max_hold_ms=7 * 86400_000)


def test_orders_never_use_future_zones():
    base = random_walk()
    n = 0
    for side in ("short", "long"):
        for tf in ("30m", "1h", "4h"):
            trades, _, zones, _ = run_pattern(base, side, tf)
            for t in trades:
                assert t["zone_known"] <= t["placed"], "order placed before its zone was confirmed"
                if "filled" in t:
                    assert t["filled"] >= t["placed"]
                    z = zones[t["zone"]]
                    assert not [x for x in z["touches"] if x < t["placed"]], "zone was not fresh when used"
                    n += 1
    assert n > 0, "expected some filled pattern trades on synthetic data"


def test_pattern_truncation():
    base = random_walk()
    strip = lambda L: [{k: v for k, v in t.items() if k not in ("sl_then_tp", "extra_stop_needed_atr", "shallow_sweep")}
                       for t in L]
    for side in ("short", "long"):
        full = run_pattern(base, side)[0]
        for frac in (0.5, 0.7, 0.9):
            cut = base[int(len(base) * frac)][0]
            part = run_pattern([b for b in base if b[0] < cut], side, start=base[0][0] + 5 * 86400_000)[0]
            done_f = [t for t in full if t["status"] in ("tp", "sl", "time") and t["exit"] <= cut]
            done_p = [t for t in part if t["status"] in ("tp", "sl", "time")]
            assert strip(done_f) == strip(done_p)[:len(done_f)], (side, frac)


def test_handbuilt_supply_retest_hits_tp():
    flat = [(100.0, 100.5, 99.5, 100.0)] * 20          # 20 x 4h: small candles, ATR ~1 -> they form the base
    leg = [(99.8, 99.9, 97.4, 97.5)]                    # strong bearish candle: 2.0 ATR below the base low (99.5)
    drift = [(97.5, 97.7, 97.3, 97.5)] * 3              # sits under the zone
    retest = [(97.5, 99.6, 97.4, 99.4)]                 # rallies back into the zone low 99.5 (no new zone)
    drop = [(99.4, 99.45, 95.0, 95.2)]                  # sells off through the target
    tail = [(95.2, 95.4, 95.0, 95.2)] * 5
    base = bars_from_4h(flat + leg + drift + retest + drop + tail)
    known = T0 + 21 * T.H4                              # the leg bar closes here
    g = T.build_geometry("short", 99.5, 96.5, 101.0, 97.5, 1.0)   # user R:R 1:2
    trades, _, zones, st = T.replay_pattern(base, g, zone_tf="4h", sl_buffer_atr=0.5, start_ms=T0 + 15 * T.H4)
    assert zones and zones[0]["known"] == known, zones
    z = zones[0]
    assert abs(z["proximal"] - 99.5) < 1e-9 and abs(z["distal"] - 100.5) < 1e-9, z
    filled = [t for t in trades if "filled" in t]
    assert len(filled) == 1, trades
    t = filled[0]
    assert t["placed"] >= known and abs(t["entry"] - 99.5) < 1e-9
    assert t["sl"] > z["distal"], "stop must sit beyond the zone, not at its edge"
    assert t["status"] == "tp", t
    assert abs(t["r_gross"] - 2.0) < 1e-6, t
    # and nothing was placed before the zone existed
    assert all(x["placed"] >= known for x in trades)
    # cut the data just before the leg candle closes: the zone does not exist yet, so no order at all
    early = [b for b in base if b[0] < known - M15]
    tr2, _, z2, _ = T.replay_pattern(early, g, zone_tf="4h", sl_buffer_atr=0.5, start_ms=T0 + 15 * T.H4)
    assert not z2 and not tr2, (z2, tr2)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, str(e)[:300])
    sys.exit(1 if fails else 0)
