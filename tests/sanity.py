#!/usr/bin/env python3
"""Sanity checks for tester.py: no look-ahead, conservative tie rule, unbiased coin-flip baseline."""
import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tester as T

def setup_for(base, side, entry_atr, risk_atr, reward_atr):
    h4 = T.resample(base, T.H4)
    h4c = [b for b in h4 if b[5] >= b[0] + T.H4 - 900_000]
    atr = T.atr_series(h4c)[-1]; ref = base[-1][4]; sg = 1 if side == "long" else -1
    e = ref + entry_atr * atr
    return T.build_geometry(side, e, e + sg * reward_atr * atr, e - sg * risk_atr * atr, ref, atr)

def run(base, g, **kw):
    return T.replay(base, T.resample(base, T.H4), g, **kw)[0]

ms = T.load_markets()
now = int(time.time() * 1000); end = now - now % 900_000; start = end - 59 * 86400_000

# 1) look-ahead: cutting off the future must not change any trade that finished before the cut
fails = 0
for name in ["BTCUSDT", "XAUUSDT", "xyz:AAPL", "ETHUSDT", "TSLAUSDT"]:
    m = T.resolve_market(name, ms)
    base = T.fetch_candles(m, "15m", start, end)
    for side, ea in (("long", -0.5), ("short", 0.7)):
        g = setup_for(base, side, ea, 1.0, 3.0)
        full = run(base, g, start_ms=start + 14 * 86400_000, max_hold_ms=7 * 86400_000)
        for cut_days in (10, 20, 30):
            cut = end - cut_days * 86400_000
            part = run([b for b in base if b[0] < cut], g, start_ms=start + 14 * 86400_000, max_hold_ms=7 * 86400_000)
            done_full = [t for t in full if t.get("exit", 1e18) <= cut and t["status"] in ("tp", "sl", "time")]
            done_part = [t for t in part if t["status"] in ("tp", "sl", "time")]
            strip = lambda L: [{k: v for k, v in t.items() if k not in ("sl_then_tp", "extra_stop_needed_atr", "shallow_sweep")} for t in L]
            # sweep-check fields legitimately look after the exit (labelled as such); everything else must match
            if strip(done_full) != strip(done_part)[:len(done_full)]:
                fails += 1
                print("LOOKAHEAD MISMATCH", name, side, cut_days)
print("look-ahead truncation test:", "PASS" if not fails else f"FAIL x{fails}")

# 2) tie rule: one bar that spans both TP and SL after fill -> SL
t0 = 1_700_000_000_000 - 1_700_000_000_000 % T.H4
bars = []
p = 100.0
for i in range(384):  # flat warm-up (96h, ends on a 4h close) so ATR exists
    bars.append((t0 + i * 900_000, p, p + 0.5, p - 0.5, p))
k = len(bars)
tt = bars[-1][0] + 900_000
g = T.build_geometry("long", 99.0, 102.0, 98.0, 100.0, 1.0)
bars.append((tt, 100.0, 100.2, 98.9, 99.5))            # fills at 99
bars.append((tt + 900_000, 99.5, 102.5, 97.5, 100.0))  # TP and SL both inside -> must be SL
for i in range(2, 50):
    bars.append((tt + i * 900_000, 100, 100.5, 99.5, 100))
tr = T.replay(bars, T.resample(bars, T.H4), g, mode="pct", start_ms=tt)[0]
first = ([x for x in tr if "filled" in x] or [{"status": str(tr[:2])}])[0]
print("same-bar TP+SL counts as SL:", "PASS" if first["status"] == "sl" else f"FAIL ({first['status']})")

# 3) coin-flip baseline: market entry, 1:1 at 1 ATR, no fees -> ~50% wins across markets
w = n = 0
for name in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "XAUUSDT", "TSLAUSDT", "NVDAUSDT", "xyz:AAPL", "DOGEUSDT", "BNBUSDT"]:
    m = T.resolve_market(name, ms)
    base = T.fetch_candles(m, "15m", start, end)
    for side in ("long", "short"):
        g = setup_for(base, side, 0.0, 1.0, 1.0)
        g["entry_type"] = "market"
        tr = run(base, g, start_ms=start + 14 * 86400_000, max_hold_ms=None)
        c = [x for x in tr if x["status"] in ("tp", "sl")]
        w += sum(x["status"] == "tp" for x in c); n += len(c)
print(f"coin-flip 1:1 baseline: {w}/{n} wins = {w / n:.1%} (expect a bit under 50%: fill-bar and tie rules favour SL)")
