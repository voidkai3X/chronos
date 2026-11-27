#!/usr/bin/env python3
"""CHRONOS Edge Score (0-100) and advanced analytics, computed only from a test's own closed trades.

The score is a historical statistical assessment of one backtest. It is not a prediction and not a guarantee.
It never rewards win rate on its own: everything is measured in R after costs (fees, swap and slippage).

Components (weights add up to 100), each scored 0..1 then multiplied by its weight:
  confidence  25  t-statistic of the average R after costs (t of 3 or more = full marks)
  expectancy  15  average R per trade after costs (+0.30R or more = full marks)
  sample      15  closed trades (15 = 0, 100 or more = full marks; 0 when the average R is not positive)
  pf          10  profit factor after costs (1.0 = 0, 2.0 or more = full marks)
  drawdown    10  net R earned / 95th-percentile Monte Carlo drawdown (3x or more = full marks)
  time        10  share of chronological segments (3 or 4) with a positive average R
  conditions  10  share of market-condition groups (5+ trades) with a positive average R
  costs        5  share of the average R left when every trade's costs are doubled
Caps (the score cannot go above these):
  fewer than 15 closed trades      -> 15
  average R after costs <= 0       -> 15
  fewer than 30 closed trades      -> 40
  90% bootstrap range of average R includes 0 -> 60
  edge gone when costs are doubled -> 60
  fewer than 50 closed trades, or t below 2 -> 69 (Strong needs both)
Bands: 0-19 No evidence, 20-44 Weak, 45-69 Promising, 70-100 Strong.
"""
import math, random, statistics

import metrics as MX

VERSION = 1
WEIGHTS = dict(confidence=25, expectancy=15, sample=15, pf=10, drawdown=10, time=10, conditions=10, costs=5)
NAMES = dict(confidence="Statistical confidence", expectancy="Average R after costs", sample="Sample size",
             pf="Profit factor", drawdown="Drawdown vs return", time="Consistency over time",
             conditions="Consistency across market conditions", costs="Survives double costs")
T_FULL, EXP_FULL, N_MIN, N_FULL, PF_FULL, RECOVERY_FULL = 3.0, 0.30, 15, 100, 2.0, 3.0
CAP_TOO_FEW, CAP_NEGATIVE, CAP_SMALL, CAP_UNCERTAIN, CAP_NOT_STRONG = 15, 15, 40, 60, 69
SMALL_N, STRONG_N, STRONG_T = 30, 50, 2.0
GROUP_MIN = 5
BANDS = [(70, "strong", "Strong"), (45, "promising", "Promising"), (20, "weak", "Weak"), (0, "none", "No evidence")]
BOOT_N, MC_N, SEED = 2000, 1000, 20261127
DISCLAIMER = ("The Edge Score is a historical statistical assessment of this backtest only. It is not a prediction, "
              "not financial advice and not a guarantee of future results.")
CONDITION_KEYS = ("trend", "vol", "bias")


def _clamp(x):
    return max(0.0, min(1.0, x))


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def tstat(rs):
    n = len(rs)
    if n < 2:
        return 0.0
    sd = statistics.pstdev(rs)
    return _mean(rs) / (sd / math.sqrt(n)) if sd > 0 else 0.0


def _pct(sorted_xs, q):
    if not sorted_xs:
        return None
    k = (len(sorted_xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


# ---------------------------------------------------------------- analytics (real trades only)
def bootstrap(rs, n_boot=BOOT_N, seed=SEED):
    """90% bootstrap ranges (5th..95th percentile) for average R and win rate. Resamples the real trades."""
    n = len(rs)
    if n < 2:
        return None
    rnd = random.Random(seed)
    means, wins = [], []
    for _ in range(n_boot):
        s = rnd.choices(rs, k=n)
        means.append(sum(s) / n)
        wins.append(sum(1 for r in s if r > 0) / n)
    means.sort()
    wins.sort()
    r3 = lambda x: round(x, 3)
    return dict(samples=n_boot, level=0.9,
                expectancy=dict(low=r3(_pct(means, 0.05)), mid=r3(_pct(means, 0.5)), high=r3(_pct(means, 0.95)),
                                actual=r3(_mean(rs))),
                win_rate=dict(low=r3(_pct(wins, 0.05)), mid=r3(_pct(wins, 0.5)), high=r3(_pct(wins, 0.95)),
                              actual=r3(sum(1 for r in rs if r > 0) / n)),
                prob_avg_above_zero=r3(sum(1 for m in means if m > 0) / n_boot))


def monte_carlo_drawdown(rs, n_sims=MC_N, seed=SEED):
    """Max drawdown (R, as a positive number) when the same trades arrive in random orders."""
    if len(rs) < 2:
        return None
    rnd = random.Random(seed + 1)
    xs = list(rs)
    dds = []
    for _ in range(n_sims):
        rnd.shuffle(xs)
        dds.append(-MX.max_drawdown(xs))
    dds.sort()
    actual = -MX.max_drawdown(rs)
    r2 = lambda x: round(x, 2)
    return dict(sims=n_sims, actual=r2(actual), median=r2(_pct(dds, 0.5)), p95=r2(_pct(dds, 0.95)),
                worst=r2(dds[-1]), best=r2(dds[0]),
                share_worse_than_actual=round(sum(1 for d in dds if d > actual) / n_sims, 3))


def block(closed):
    rs = [t["r_net"] for t in closed]
    b = MX.block(rs)
    return dict(trades=len(rs), win_rate=b["win_rate"], expectancy_r=b["expectancy_r"],
                profit_factor=b["profit_factor"], profit_factor_infinite=b["profit_factor_infinite"],
                total_r=b["total_r"], max_drawdown_r=b["max_drawdown_r"],
                t_stat=round(tstat(rs), 2),
                from_ms=closed[0].get("filled") or closed[0].get("placed") if closed else None,
                to_ms=closed[-1].get("exit") if closed else None)


def segments(closed, k):
    n = len(closed)
    if k < 1 or n < k:
        return []
    cuts = [round(i * n / k) for i in range(k + 1)]
    return [closed[cuts[i]:cuts[i + 1]] for i in range(k)]


def walk_forward(closed, split=2 / 3):
    """First part of the trades (in time) vs the rest. Nothing is fitted on the first part: CHRONOS uses the
    trader's own levels, so this shows whether the later trades behaved like the earlier ones."""
    n = len(closed)
    if n < 10:
        return None
    k = max(1, min(n - 1, round(n * split)))
    ins, oos = block(closed[:k]), block(closed[k:])
    note = ("Out-of-sample has fewer than 15 trades, so treat the comparison as a rough check."
            if oos["trades"] < 15 else "")
    same = (ins["expectancy_r"] or 0) > 0 and (oos["expectancy_r"] or 0) > 0
    flip = (ins["expectancy_r"] or 0) > 0 >= (oos["expectancy_r"] or 0)
    return dict(split=round(split, 3), in_sample=ins, out_of_sample=oos, note=note,
                held_up=bool(same), faded=bool(flip))


# ---------------------------------------------------------------- the score
def _band(score):
    for lo, key, label in BANDS:
        if score >= lo:
            return key, label
    return "none", "No evidence"


def _condition_groups(closed):
    groups = []
    for key in CONDITION_KEYS:
        vals = {}
        for t in closed:
            v = (t.get("conditions") or {}).get(key)
            if v is not None:
                vals.setdefault(v, []).append(t["r_net"])
        for v, rs in vals.items():
            if len(rs) >= GROUP_MIN:
                groups.append((key, v, len(rs), _mean(rs)))
    return groups


def score(trades, boot=None, mc=None):
    """Edge Score for one backtest. trades: engine trade dicts (any status; only closed ones count)."""
    closed = MX.closed_trades(trades)
    rs = [t["r_net"] for t in closed]
    n = len(rs)
    avg = _mean(rs)
    t = tstat(rs)
    boot = boot if boot is not None else bootstrap(rs)
    mc = mc if mc is not None else monte_carlo_drawdown(rs)
    comps = []

    def add(key, frac, value, detail):
        frac = _clamp(frac)
        comps.append(dict(key=key, name=NAMES[key], weight=WEIGHTS[key], fraction=round(frac, 3),
                          points=round(frac * WEIGHTS[key], 1), value=value, detail=detail))

    lo = boot["expectancy"]["low"] if boot else None
    hi = boot["expectancy"]["high"] if boot else None
    add("confidence", t / T_FULL if avg > 0 else 0.0, f"t = {t:.2f}",
        f"How far the average R is from zero compared with the trade-to-trade noise. t of {T_FULL:g} or more scores full marks."
        + (f" 90% bootstrap range of average R: {lo:+.2f}R to {hi:+.2f}R." if boot else ""))
    add("expectancy", avg / EXP_FULL, f"{avg:+.3f}R per trade",
        f"Average result per closed trade after fees, swap and slippage. +{EXP_FULL:.2f}R or more scores full marks; zero or less scores nothing.")
    add("sample", (n - N_MIN) / (N_FULL - N_MIN) if avg > 0 else 0.0, f"{n} closed trades",
        f"Under {N_MIN} trades scores nothing; {N_FULL} or more scores full marks. A large sample of losing trades earns "
        "nothing here: it is strong evidence against an edge. Small samples are also capped (see below).")
    gw = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    pf = gw / gl if gl > 0 else None
    pf_frac = (1.0 if gw > 0 else 0.0) if pf is None else (pf - 1) / (PF_FULL - 1)
    add("pf", pf_frac, "no losing trades" if pf is None and n else (f"{pf:.2f}" if pf is not None else "n/a"),
        f"Money won / money lost, after costs. 1.0 breaks even and scores nothing; {PF_FULL:g} or more scores full marks.")
    total = sum(rs)
    dd95 = mc["p95"] if mc else None
    if total > 0 and dd95:
        rec = total / dd95
        add("drawdown", rec / RECOVERY_FULL, f"{total:+.1f}R earned vs {dd95:.1f}R drawdown",
            f"Net R divided by the 95th-percentile drawdown from {mc['sims']} random reorderings of the same trades. "
            f"{RECOVERY_FULL:g}x or more scores full marks.")
    else:
        add("drawdown", 1.0 if total > 0 and n else 0.0,
            f"{total:+.1f}R earned" + (f" vs {dd95:.1f}R drawdown" if dd95 else ""),
            "Net R divided by the 95th-percentile Monte Carlo drawdown. Nothing when the test lost money overall.")
    k = 4 if n >= 40 else (3 if n >= N_MIN else 0)
    segs = segments(closed, k) if k else []
    seg_avgs = [_mean([x["r_net"] for x in s]) for s in segs]
    pos = sum(1 for a in seg_avgs if a > 0)
    add("time", (pos / k) if k and avg > 0 else 0.0,
        f"{pos} of {k} periods positive" if k else "not enough trades to split",
        "The trades are split in time order into equal parts; each part with a positive average R counts. "
        "An edge that only showed up in one stretch scores low.")
    groups = _condition_groups(closed)
    gpos = sum(1 for g in groups if g[3] > 0)
    add("conditions", (gpos / len(groups)) if len(groups) >= 3 and avg > 0 else 0.0,
        f"{gpos} of {len(groups)} groups positive" if len(groups) >= 3 else f"only {len(groups)} groups with {GROUP_MIN}+ trades",
        f"Trend vs range, high vs low volatility and bullish vs bearish groups (tagged at each signal from closed 4h candles). "
        f"Groups need {GROUP_MIN}+ trades; with fewer than 3 such groups this scores nothing.")
    cost = [t2["r_gross"] - t2["r_net"] for t2 in closed]
    stressed = _mean([r - c for r, c in zip(rs, cost)]) if n else 0.0
    add("costs", (stressed / avg) if avg > 0 else 0.0, f"{stressed:+.3f}R per trade at 2x costs",
        "Every trade's own fees, swap and slippage doubled. Full marks if none of the edge is lost; nothing if it turns negative.")

    raw = sum(c["points"] for c in comps)
    caps = []
    if n < N_MIN:
        caps.append((CAP_TOO_FEW, f"fewer than {N_MIN} closed trades"))
    if avg <= 0:
        caps.append((CAP_NEGATIVE, "average R after costs is zero or negative"))
    if n < SMALL_N:
        caps.append((CAP_SMALL, f"fewer than {SMALL_N} closed trades"))
    if boot and lo is not None and lo <= 0:
        caps.append((CAP_UNCERTAIN, "the 90% range of average R includes zero"))
    if avg > 0 and stressed <= 0:
        caps.append((CAP_UNCERTAIN, "the edge disappears when costs are doubled"))
    if n < STRONG_N:
        caps.append((CAP_NOT_STRONG, f"Strong needs {STRONG_N}+ closed trades"))
    if t < STRONG_T:
        caps.append((CAP_NOT_STRONG, f"Strong needs a t-statistic of {STRONG_T:g} or more"))
    cap = min([c[0] for c in caps], default=100)
    final = int(round(min(raw, cap)))
    band, label = _band(final)
    applied = [dict(cap=c, reason=r) for c, r in caps if c < raw]
    return dict(version=VERSION, score=final, raw=round(raw, 1), band=band, label=label,
                capped=bool(applied), caps=applied, components=comps, trades=n,
                expectancy_r=round(avg, 3), t_stat=round(t, 2), stressed_expectancy_r=round(stressed, 3),
                segments=[dict(trades=len(s), expectancy_r=round(a, 3)) for s, a in zip(segs, seg_avgs)],
                condition_groups=[dict(dim=g[0], value=g[1], trades=g[2], expectancy_r=round(g[3], 3)) for g in groups],
                bands=[dict(min=b[0], key=b[1], label=b[2]) for b in BANDS],
                weights=WEIGHTS, disclaimer=DISCLAIMER)


def analyse(trades):
    """Edge Score plus the advanced analytics, from one test's trades."""
    closed = MX.closed_trades(trades)
    rs = [t["r_net"] for t in closed]
    boot = bootstrap(rs)
    mc = monte_carlo_drawdown(rs)
    return score(trades, boot=boot, mc=mc), dict(bootstrap=boot, monte_carlo=mc, walk_forward=walk_forward(closed))


def verdict(s, edge, min_samples=15):
    """Engine verdict text aligned with the Edge Score. Prefixes are stable for code that reads them:
    'too few samples', 'no edge', 'no reliable edge', 'possible edge', 'edge on this sample'."""
    n, avg, t = s["closed"], s["avg_r_net"], s["t_stat"]
    if n < min_samples:
        return f"too few samples ({n} closed trades, need {min_samples})"
    if avg <= 0:
        return "no edge (expectancy <= 0 after fees)"
    sc = f"Edge Score {edge['score']}/100"
    if edge["band"] in ("none", "weak"):
        return f"no reliable edge ({sc}: expectancy +{avg:.2f}R, t={t:.1f}; too uncertain to call an edge)"
    if edge["band"] == "promising":
        return f"possible edge, not proven ({sc}: expectancy +{avg:.2f}R, t={t:.1f}; noise can't be ruled out)"
    return f"edge on this sample ({sc}: expectancy +{avg:.2f}R, t={t:.1f}); past results only"
