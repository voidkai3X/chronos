#!/usr/bin/env python3
"""Offline checks for the multi-source merge (sources.py). No network.

1. No duplicate bars, output sorted, every openTime a multiple of the interval (UTC epoch alignment).
2. MFP precedence: on overlapping timestamps the MFP bar is kept, backups only add bars OLDER than MFP's first bar.
3. Gaps are preserved: holes inside MFP or the backup are not filled from anywhere and are reported by find_gaps.
4. Overlap check: small diffs pass, a constant basis is ratio-adjusted, a different instrument is rejected.
5. Misaligned backup bars (e.g. a feed stamped at :07) are dropped, never shifted.
6. Atomic, source-keyed cache: written via rename, read back, separate files per source.
Run: python3 tests/test_merge.py   (or pytest tests/)
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources as S

M15 = 900_000
T0 = 1_780_000_000_000 - 1_780_000_000_000 % 86_400_000  # a UTC midnight


def series(t_from, n, price=100.0, drift=0.01, skip=()):
    out = {}
    for i in range(n):
        if i in skip:
            continue
        t = t_from + i * M15
        p = price + drift * ((t - T0) // M15)  # one price path in absolute time, so sources agree on overlap
        out[t] = (t, p, p + 0.05, p - 0.05, p + 0.01)
    return out


def test_no_duplicates_alignment_precedence():
    mfp = series(T0 + 400 * M15, 600, price=100.0)
    bk = series(T0, 1000, price=100.0)  # overlaps MFP for 600 bars
    bk = {t: (t, b[1], b[2], b[3], b[4] * (1 + 0.0004)) for t, b in bk.items()}  # 0.04% off: passes 0.15%
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("okx", bk)], M15, T0, T0 + 1000 * M15)
    ts = [b[0] for b in bars]
    assert ts == sorted(set(ts)), "duplicate or unsorted bars"
    assert all(t % M15 == 0 for t in ts), "misaligned bar"
    assert len(bars) == 1000
    for t in mfp:
        assert dict((b[0], b) for b in bars)[t] == mfp[t], "MFP bar replaced on overlap"
    first_mfp = min(mfp)
    assert all(b[0] < first_mfp for b in bars if b != mfp.get(b[0])), "backup bar used at/after MFP start"
    assert checks[0]["status"] == "ok" and checks[0]["bars_added"] == 400, checks
    assert [s["source"] for s in segs] == ["okx", "mfp"] and segs[0]["to_ms"] == first_mfp, segs


def test_gaps_preserved():
    mfp = series(T0 + 500 * M15, 500, skip=range(100, 110))         # 10-bar hole inside MFP
    bk = series(T0, 700, skip=range(200, 205))                      # 5-bar hole inside the backup
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("okx", bk)], M15, T0, T0 + 1000 * M15)
    ts = set(b[0] for b in bars)
    for i in range(100, 110):
        assert T0 + (500 + i) * M15 not in ts, "MFP hole was filled"
    for i in range(200, 205):
        assert T0 + i * M15 not in ts, "backup hole was filled"
    g = S.find_gaps(bars, M15)
    assert g["gaps"] == 2 and g["missing_bars"] == 15, g


def test_basis_adjust_and_reject():
    mfp = series(T0 + 400 * M15, 600, price=100.0, drift=0.0)
    basis = {t: (t, b[1] * 1.01, b[2] * 1.01, b[3] * 1.01, b[4] * 1.01) for t, b in series(T0, 1000, 100.0, 0.0).items()}
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("bybit", basis)], M15, T0, T0 + 1000 * M15)
    c = checks[0]
    assert c["status"] == "basis-adjusted" and abs(c["ratio"] - 1 / 1.01) < 1e-6, c
    assert abs(bars[0][4] - mfp[min(mfp)][4]) < 1e-9, "adjusted backup should line up with MFP"
    other = {t: (t, b[1] * 3, b[2] * 3, b[3] * 3, b[4] * 3) for t, b in series(T0, 1000, 100.0, 0.0).items()}
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("okx", other)], M15, T0, T0 + 1000 * M15)
    assert checks[0]["status"].startswith("rejected") and len(bars) == 600, checks
    # FX threshold 0.05%: a 0.1% offset is outside it but fixable by the ratio
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("yahoo", {t: (t, b[1], b[2], b[3], b[4] * 1.001) for t, b in series(T0, 1000, 100.0, 0.0).items()})],
                                         M15, T0, T0 + 1000 * M15, threshold=0.0005)
    assert checks[0]["status"] == "basis-adjusted", checks


def test_chain_and_misaligned_dropped():
    mfp = series(T0 + 600 * M15, 400)
    b1 = series(T0 + 300 * M15, 400)  # covers 300..699
    b2 = {t + 7 * 60_000: (t + 7 * 60_000,) + b[1:] for t, b in series(T0, 400).items()}  # stamped at :07 -> dropped
    b3 = series(T0, 400)
    bars, segs, checks = S.merge_sources(("mfp", mfp), [("okx", b1), ("yahoo", b2), ("dukascopy", b3)], M15, T0, T0 + 1000 * M15)
    ts = [b[0] for b in bars]
    assert ts == [T0 + i * M15 for i in range(1000)], "chain did not backfill cleanly"
    assert [s["source"] for s in segs] == ["dukascopy", "okx", "mfp"], segs
    assert checks[1]["source"] == "yahoo" and checks[1].get("bars_added", 0) == 0, checks


def test_weekend_closure_not_a_gap():
    fri = T0 + ((4 - __import__("time").gmtime(T0 / 1000).tm_wday) % 7) * 86_400_000  # a Friday 00:00 UTC
    a = series(fri, 84)                       # Fri 00:00 .. 20:45
    b = series(fri + 3 * 86_400_000 - 2 * 3600_000, 50)  # Sun 22:00 ..
    bars = sorted(list(a.values()) + list(b.values()))
    g = S.find_gaps(bars, M15, session=True)
    assert g["gaps"] == 0 and g["weekend_closures"] == 1, g
    g = S.find_gaps(bars, M15, session=False)
    assert g["gaps"] == 1, g


def test_cache_atomic_and_keyed_by_source():
    old = S.CACHE_DIR
    with tempfile.TemporaryDirectory() as td:
        S.CACHE_DIR = td
        calls = []
        S.FETCHERS["fake"] = lambda sym, iv, s, e: (calls.append((s, e)), series(s, (e - s) // M15))[1]
        try:
            a = S.cached_fetch("fake", "X|Y:Z", "15m", T0, T0 + 100 * M15)
            b = S.cached_fetch("fake", "X|Y:Z", "15m", T0, T0 + 100 * M15)
            assert a == b and len(calls) == 1, "second call should be served from cache"
            S.cached_fetch("fake", "X|Y:Z", "15m", T0, T0 + 150 * M15)
            assert calls[-1] == (T0 + 100 * M15, T0 + 150 * M15), "only the missing range should be fetched"
            p = S.cache_path("fake", "X|Y:Z", "15m")
            assert os.path.dirname(p) == os.path.join(td, "fake") and os.path.exists(p)
            assert not [f for f in os.listdir(os.path.dirname(p)) if f.endswith(".tmp")], "temp file left behind"
        finally:
            S.FETCHERS.pop("fake", None)
            S.CACHE_DIR = old


def test_weekend_start_needs_no_backfill():
    import time as _t
    sat = T0 + ((5 - _t.gmtime(T0 / 1000).tm_wday) % 7) * 86_400_000  # a Saturday 00:00 UTC
    mon = sat + 2 * 86_400_000 - 2 * 3600_000                          # Sun 22:00 UTC, FX reopens
    asked = []
    real = S.cached_fetch
    S.cached_fetch = lambda src, sym, iv, s, e, refresh=False: (asked.append(src), series(mon, (e - mon) // M15) if src == "mfp" else {})[1]
    try:
        bars, prov = S.load({"market_id": "veranta|EURUSD", "provider": "veranta", "coin": "EURUSD"}, "15m", sat, sat + 10 * 86_400_000)
    finally:
        S.cached_fetch = real
    assert asked == ["mfp"] and not prov["backfill_needed"], (asked, prov["backfill_needed"])


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
