#!/usr/bin/env python3
"""CHRONOS rule-based strategies (Phase 2), used by `tester.py --mode rule` and strategy comparison.

Each rule reads only candles of the setup timeframe that have closed at the signal time t (bar open + step).
The order is placed at t and simulated on the execution candles (15m, or 5m) by tester.simulate_order, with the
same fees, slippage, tie rules and one-trade-at-a-time rule as every other mode. Rules for a long are mirrored for
a short. ATR is ATR(14) of the setup timeframe at the signal bar. The target is always rr x risk, where rr is the
R:R of the trader's own levels, so every strategy in a comparison uses the same reward-to-risk.

These are honest, simple, mechanical versions of common ideas. They are not anyone's personal method.
"""
import bisect

import tester as T

RULES = {
    "ema_cross": dict(
        label="EMA crossover",
        params=dict(fast=20, slow=50, stop_atr=1.5),
        bounds=dict(fast=(2, 200), slow=(3, 400), stop_atr=(0.1, 10)),
        doc="Long when the fast EMA closes above the slow EMA after being at or below it on the previous candle "
            "(short: crosses below). Enter at market on the next candle. Stop stop_atr x ATR from the signal close."),
    "breakout": dict(
        label="Breakout",
        params=dict(lookback=20, stop_atr=1.5),
        bounds=dict(lookback=(5, 200), stop_atr=(0.1, 10)),
        doc="Long when a candle closes above the highest high of the previous `lookback` candles, and the candle "
            "before had not (short: closes below the lowest low). Enter at market on the next candle. "
            "Stop stop_atr x ATR from the signal close."),
    "liquidity_sweep": dict(
        label="Liquidity sweep",
        params=dict(lookback=20, stop_atr=0.25),
        bounds=dict(lookback=(5, 200), stop_atr=(0, 5)),
        doc="ICT-style sweep. Long when a candle trades below the lowest low of the previous `lookback` candles "
            "(where sell stops sit) and closes back above it (short: above the highest high, closes back below). "
            "Enter at market on the next candle. Stop stop_atr x ATR beyond the sweep candle's extreme."),
    "fvg": dict(
        label="Fair value gap retest",
        params=dict(min_gap_atr=0.2, stop_atr=0.25, valid_bars=12),
        bounds=dict(min_gap_atr=(0, 5), stop_atr=(0, 5), valid_bars=(1, 200)),
        doc="Bullish gap: candle 3's low is above candle 1's high by at least min_gap_atr x ATR (bearish: candle 3's "
            "high below candle 1's low). When candle 3 closes, rest a limit at the near edge of the gap (candle 3's "
            "low for a long). Stop stop_atr x ATR beyond the far edge. The limit is cancelled after valid_bars candles."),
}


def clean_params(rule, params):
    """Defaults filled in, numbers checked. Raises ValueError on anything out of bounds."""
    if rule not in RULES:
        raise ValueError(f"unknown rule '{rule}' (use {', '.join(RULES)})")
    spec = RULES[rule]
    out = dict(spec["params"])
    for k, v in (params or {}).items():
        if k not in spec["params"]:
            raise ValueError(f"{rule}: unknown parameter '{k}'")
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{rule}: {k} must be a number") from None
        lo, hi = spec["bounds"][k]
        if not (lo <= v <= hi) or v != v:
            raise ValueError(f"{rule}: {k} must be between {lo:g} and {hi:g}")
        out[k] = int(v) if isinstance(spec["params"][k], int) else v
    if rule == "ema_cross" and out["fast"] >= out["slow"]:
        raise ValueError("ema_cross: fast must be smaller than slow")
    return out


def signals(bars, atr, rule, p, sgn):
    """[(i, entry_type, entry, sl, valid_bars, meta)] for closed setup-tf bars. Only bars[..i] are read for signal i."""
    out = []
    n = len(bars)
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    c = [b[4] for b in bars]
    if rule == "ema_cross":
        fast, slow = T.ema_series(c, int(p["fast"])), T.ema_series(c, int(p["slow"]))
        for i in range(1, n):
            if None in (fast[i], slow[i], fast[i - 1], slow[i - 1], atr[i]):
                continue
            up = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
            dn = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]
            if (sgn == 1 and up) or (sgn == -1 and dn):
                out.append((i, "market", c[i], c[i] - sgn * p["stop_atr"] * atr[i], None,
                            dict(fast=round(fast[i], 8), slow=round(slow[i], 8))))
    elif rule == "breakout":
        N = int(p["lookback"])
        for i in range(N + 1, n):
            if atr[i] is None:
                continue
            if sgn == 1:
                lvl, prev = max(h[i - N:i]), max(h[i - 1 - N:i - 1])
                hit = c[i] > lvl and not c[i - 1] > prev
            else:
                lvl, prev = min(lo[i - N:i]), min(lo[i - 1 - N:i - 1])
                hit = c[i] < lvl and not c[i - 1] < prev
            if hit:
                out.append((i, "market", c[i], c[i] - sgn * p["stop_atr"] * atr[i], None, dict(level=lvl)))
    elif rule == "liquidity_sweep":
        N = int(p["lookback"])
        for i in range(N, n):
            if atr[i] is None:
                continue
            if sgn == 1:
                lvl = min(lo[i - N:i])
                hit = lo[i] < lvl and c[i] > lvl
                sl = lo[i] - p["stop_atr"] * atr[i]
            else:
                lvl = max(h[i - N:i])
                hit = h[i] > lvl and c[i] < lvl
                sl = h[i] + p["stop_atr"] * atr[i]
            if hit and (c[i] - sl) * sgn > 0:
                out.append((i, "market", c[i], sl, None, dict(level=lvl)))
    elif rule == "fvg":
        for i in range(2, n):
            if atr[i] is None:
                continue
            if sgn == 1:
                gap = lo[i] - h[i - 2]
                near, far = lo[i], h[i - 2]
            else:
                gap = lo[i - 2] - h[i]
                near, far = h[i], lo[i - 2]
            if gap <= 0 or gap < p["min_gap_atr"] * atr[i]:
                continue
            sl = far - sgn * p["stop_atr"] * atr[i]
            out.append((i, "limit", near, sl, int(p["valid_bars"]), dict(gap_near=near, gap_far=far)))
    return out


def replay_rule(base, rule, params, g, tf="1h", fee_pct=0.0, swap_pct_day=0.0, start_ms=None, max_hold_ms=None,
                sweep_window_ms=None, sweep_atr=1.0, slip_bps=0.0, slip_abs=0.0, allow_overlap=False, gate=None,
                expiry_ms=72 * 3600_000):
    """Returns (trades, setups_found, skipped_by_filters). One order or trade at a time unless allow_overlap."""
    p = clean_params(rule, params)
    sgn, rr = g["sgn"], g["rr"]
    step = T.STEP[tf]
    bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51]))
    if step < bstep:
        raise SystemExit(f"setup timeframe {tf} is finer than the {bstep // 60000}m execution candles")
    bars = T.complete_bars(T.resample(base, step), step, bstep)
    atr = T.atr_series(bars)
    bt = [b[0] for b in base]
    trades, busy, found, skipped = [], 0, 0, 0
    for i, etype, entry, sl, valid, meta in signals(bars, atr, rule, p, sgn):
        t = bars[i][0] + step  # the signal candle has closed
        if start_ms and t < start_ms:
            continue
        found += 1
        if t < busy:
            continue
        if gate is not None and not gate.ok(t):
            skipped += 1
            continue
        risk = (entry - sl) * sgn
        if risk <= 0:
            continue
        tp = entry + sgn * rr * risk
        j = bisect.bisect_left(bt, t)
        if j >= len(base):
            break
        life = valid * step if valid else expiry_ms
        tr, nxt = T.simulate_order(base, j, t, entry, sl, tp, sgn, etype, life, atr[i], risk=risk, fee_pct=fee_pct,
                                   swap_pct_day=swap_pct_day, max_hold_ms=max_hold_ms, sweep_window_ms=sweep_window_ms,
                                   sweep_atr=sweep_atr, tr=dict(ref=bars[i][4], rule=rule, atr=atr[i], **meta),
                                   slip_bps=slip_bps, slip_abs=slip_abs)
        trades.append(tr)
        if allow_overlap:
            continue
        if nxt is None:
            break
        busy = nxt
    return trades, found, skipped


def describe(rule, params, tf, rr):
    p = clean_params(rule, params)
    ptxt = ", ".join(f"{k} {v:g}" for k, v in p.items())
    return f"{RULES[rule]['label']} on {tf} candles ({ptxt}), target {rr:.2f}R. {RULES[rule]['doc']}"
