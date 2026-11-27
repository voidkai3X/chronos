#!/usr/bin/env python3
"""CHRONOS market-condition tags (Phase 2).

Every trade is tagged at its signal time (the moment the order was decided) using only 4h candles that had
already closed at that moment. Nothing after the signal is used, so the tag is what a trader could have seen.

Definitions (also shown on the results page):
  Trend vs range  Kaufman efficiency ratio (ER) over the last 20 closed 4h candles:
                  |close now - close 20 candles ago| / sum of |close-to-close moves|.
                  ER >= 0.30 = trending, below = ranging. ER is 1 for a straight line and near 0 for chop.
  Volatility      4h ATR(14) now, ranked against the ATR(14) of the previous 90 closed 4h candles (about 15 days).
                  At or above the median = high volatility, below = low volatility.
  Direction       4h EMA(50) and its change over the last 5 closed 4h candles.
                  Close above EMA50 and EMA50 rising = bullish; close below and falling = bearish; otherwise mixed.
"""
import bisect

import tester as T

ER_N = 20
ER_TRENDING = 0.30
VOL_LOOKBACK = 90
VOL_MIN_HISTORY = 30
EMA_N = 50
SLOPE_N = 5
SMALL = 15      # below this many closed trades a group is a small sample
TOO_FEW = 5     # below this a group is not judged at all

DIMENSIONS = [
    ("trend", "Trend vs range", ["trending", "ranging"]),
    ("vol", "Volatility", ["high", "low"]),
    ("bias", "Direction", ["bullish", "bearish", "mixed"]),
]
LABELS = {"trending": "Trending", "ranging": "Ranging", "high": "High volatility", "low": "Low volatility",
          "bullish": "Bullish", "bearish": "Bearish", "mixed": "Mixed direction"}
DEFINITIONS = {
    "trend": f"Efficiency ratio of the last {ER_N} closed 4h candles. {ER_TRENDING:.2f} or more = trending; "
             "below = ranging.",
    "vol": f"4h ATR(14) ranked against the previous {VOL_LOOKBACK} closed 4h candles (about 15 days). "
           "Median or above = high; below = low.",
    "bias": f"Last closed 4h close vs 4h EMA({EMA_N}) and the EMA's change over {SLOPE_N} candles. "
            "Above and rising = bullish; below and falling = bearish; anything else = mixed.",
    "timing": "Measured at the signal time with 4h candles that had already closed. No later data is used.",
}


class Context:
    """4h indicators from the base candles; tag(t) only reads candles closed at or before t."""

    def __init__(self, base):
        bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51])) if len(base) > 1 else T.STEP["15m"]
        self.bars = T.complete_bars(T.resample(base, T.H4), T.H4, bstep)
        closes = [b[4] for b in self.bars]
        self.close_t = [b[0] + T.H4 for b in self.bars]
        self.atr = T.atr_series(self.bars) if self.bars else []
        self.ema = T.ema_series(closes, EMA_N) if self.bars else []
        self.closes = closes

    def index(self, t):
        return bisect.bisect_right(self.close_t, t) - 1

    def tag(self, t):
        h = self.index(t)
        out = dict(trend=None, er=None, vol=None, atr_pct=None, bias=None, asof=None)
        if h < 0:
            return out
        out["asof"] = self.close_t[h]
        c = self.closes
        if h >= ER_N:
            move = abs(c[h] - c[h - ER_N])
            path = sum(abs(c[k] - c[k - 1]) for k in range(h - ER_N + 1, h + 1))
            er = move / path if path > 0 else 0.0
            out["er"] = round(er, 3)
            out["trend"] = "trending" if er >= ER_TRENDING else "ranging"
        a = self.atr[h] if h < len(self.atr) else None
        if a is not None:
            hist = [x for x in self.atr[max(0, h - VOL_LOOKBACK):h] if x is not None]
            if len(hist) >= VOL_MIN_HISTORY:
                pct = sum(1 for x in hist if x <= a) / len(hist)
                out["atr_pct"] = round(pct, 3)
                out["vol"] = "high" if pct >= 0.5 else "low"
        if h >= SLOPE_N and self.ema[h] is not None and self.ema[h - SLOPE_N] is not None:
            slope = self.ema[h] - self.ema[h - SLOPE_N]
            if c[h] > self.ema[h] and slope > 0:
                out["bias"] = "bullish"
            elif c[h] < self.ema[h] and slope < 0:
                out["bias"] = "bearish"
            else:
                out["bias"] = "mixed"
        return out


def tag_trades(trades, base, ctx=None):
    ctx = ctx or Context(base)
    for t in trades:
        anchor = t.get("signal") or t.get("placed")
        if anchor is not None:
            t["conditions"] = ctx.tag(anchor)
    return ctx


def _bucket(rs):
    import metrics as MX
    b = MX.block(rs)
    n = len(rs)
    sample = "ok" if n >= SMALL else ("small" if n >= TOO_FEW else "too_few")
    return dict(trades=n, wins=b["wins"], losses=b["losses"], win_rate=b["win_rate"], expectancy_r=b["expectancy_r"],
                profit_factor=b["profit_factor"], profit_factor_infinite=b["profit_factor_infinite"],
                total_r=b["total_r"], avg_win_r=b["avg_win_r"], avg_loss_r=b["avg_loss_r"], sample=sample)


def breakdown(trades):
    """Per-condition results on closed trades (after costs)."""
    closed = [t for t in trades if t.get("status") in ("tp", "sl", "time")]
    dims = []
    for key, title, values in DIMENSIONS:
        rows = []
        for v in values:
            rs = [t["r_net"] for t in closed if (t.get("conditions") or {}).get(key) == v]
            rows.append(dict(value=v, label=LABELS[v], **_bucket(rs)))
        unknown = sum(1 for t in closed if (t.get("conditions") or {}).get(key) is None)
        dims.append(dict(key=key, title=title, rows=rows, unknown=unknown, definition=DEFINITIONS[key]))
    return dims


def _fmt(r):
    return f"{r['label'].lower()} ({r['expectancy_r']:+.2f}R per trade over {r['trades']})"


def summarize(dims, total_closed):
    """Plain-language consistency read. Returns dict(status, headline, lines)."""
    lines, dependent, consistent, unclear, judged = [], [], [], [], 0
    for d in dims:
        rows = [r for r in d["rows"] if r["trades"] >= TOO_FEW]
        if len(rows) < 2:
            seen = [r for r in d["rows"] if r["trades"]]
            if seen:
                lines.append(f"{d['title']}: almost every trade fell in one group "
                             f"({', '.join(r['label'].lower() + ' ' + str(r['trades']) for r in seen)}), so this can't be compared.")
            continue
        judged += 1
        best = max(rows, key=lambda r: r["expectancy_r"])
        worst = min(rows, key=lambda r: r["expectancy_r"])
        small = any(r["sample"] != "ok" for r in (best, worst))
        caveat = " Small groups: this could be chance." if small else ""
        if best["expectancy_r"] > 0 >= worst["expectancy_r"]:
            lines.append(f"{d['title']}: made money in {_fmt(best)} but not in {_fmt(worst)}.{caveat}")
            (unclear if small else dependent).append(d["key"])
        elif best["expectancy_r"] <= 0:
            lines.append(f"{d['title']}: lost money in every group; best was {_fmt(best)}.{caveat}")
            consistent.append(d["key"])
        else:
            gap = best["expectancy_r"] - worst["expectancy_r"]
            if gap >= 0.25:
                lines.append(f"{d['title']}: positive in every group, but much better in {_fmt(best)} than {_fmt(worst)}.{caveat}")
            else:
                lines.append(f"{d['title']}: similar in every group ({_fmt(best)} vs {_fmt(worst)}).{caveat}")
            consistent.append(d["key"])
    if total_closed < TOO_FEW * 2 or judged == 0:
        status, head = "insufficient", ("Not enough closed trades to compare market conditions. "
                                        "Run a longer window or a broader setup.")
    elif dependent:
        names = ", ".join(next(d["title"].lower() for d in dims if d["key"] == k) for k in dependent)
        status, head = "dependent", (f"Condition-dependent: results change sign with {names}. "
                                     "The idea may only work in some markets.")
    elif unclear:
        status, head = "unclear", ("Results differ between conditions, but the groups are too small to tell "
                                   "a real difference from chance.")
    elif consistent and all(max(r["expectancy_r"] for r in d["rows"] if r["trades"] >= TOO_FEW) <= 0
                            for d in dims if d["key"] in consistent):
        status, head = "negative", ("Lost money in every market condition tested, so the result does not hinge on "
                                    "trend, volatility or direction.")
    else:
        status, head = "consistent", ("No clear dependence on these conditions in this sample. "
                                      "Groups under 15 trades are still weak evidence.")
    return dict(status=status, headline=head, lines=lines)


def analyse(trades, base, end_ms=None, ctx=None):
    """Tag trades (in place) and return the condition block for the result JSON."""
    ctx = tag_trades(trades, base, ctx)
    dims = breakdown(trades)
    closed = sum(1 for t in trades if t.get("status") in ("tp", "sl", "time"))
    now = ctx.tag(end_ms if end_ms is not None else (ctx.close_t[-1] if ctx.close_t else 0))
    return dict(dimensions=dims, summary=summarize(dims, closed), now=now, definitions=DEFINITIONS,
                min_group=SMALL, too_few=TOO_FEW)
