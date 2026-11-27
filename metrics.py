#!/usr/bin/env python3
"""CHRONOS results dashboard maths. Pure functions on the engine's trade list (no data fetching).

Two views of every closed trade:
  gross / theoretical : r_gross, the price move between the theoretical fill and exit, no fees, no slippage.
  net / after costs   : r_net, after slippage on market-type fills and MFP fees + swap.
Every number here is computed from the trades passed in. Nothing is estimated or filled in.
"""
import math, statistics, time

CLOSED = ("tp", "sl", "time")
RISK_PCT = 1.0  # % of account risked per trade, used only to express R as a % return (simple, not compounded)
HIST_EDGES = [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0]  # R buckets: (-inf,-1.5], (-1.5,-1], ..., (3, inf)
SESSIONS = [("Asia", 0, 7), ("London", 7, 13), ("New York", 13, 21), ("Late US", 21, 24)]  # UTC hours of the fill


def closed_trades(trades):
    """Closed trades in the order they finished (exit time, then entry time)."""
    c = [t for t in trades if t.get("status") in CLOSED]
    return sorted(c, key=lambda t: (t.get("exit") or 0, t.get("filled") or t.get("placed") or 0))


def streaks(rs):
    """Longest run of wins (R > 0) and of losses (R < 0). A flat trade (R == 0) ends both runs."""
    bw = bl = w = l = 0
    for r in rs:
        if r > 0:
            w, l = w + 1, 0
        elif r < 0:
            w, l = 0, l + 1
        else:
            w = l = 0
        bw, bl = max(bw, w), max(bl, l)
    return bw, bl


def max_drawdown(rs):
    """Largest peak-to-trough fall of the cumulative R curve, starting from 0. Returned as a value <= 0."""
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd


def profit_factor(rs):
    """Sum of winning R / sum of losing R (absolute). None when there are no losing trades (undefined)."""
    gw = sum(r for r in rs if r > 0)
    gl = -sum(r for r in rs if r < 0)
    if gl <= 0:
        return None
    return gw / gl


def block(rs, risk_pct=RISK_PCT):
    """All headline stats for one list of per-trade R values (already in finishing order)."""
    n = len(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    total = sum(rs)
    avg_w = sum(wins) / len(wins) if wins else None
    avg_l = sum(losses) / len(losses) if losses else None
    pf = profit_factor(rs)
    bw, bl = streaks(rs)
    dd = max_drawdown(rs)
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    exp = total / n if n else None
    rnd = lambda x, d=3: None if x is None else round(x, d)
    return dict(
        trades=n, wins=len(wins), losses=len(losses), flat=n - len(wins) - len(losses),
        win_rate=rnd(len(wins) / n) if n else None,
        total_r=round(total, 3), total_pct=round(total * risk_pct, 2),
        expectancy_r=rnd(exp), avg_win_r=rnd(avg_w), avg_loss_r=rnd(avg_l),
        avg_rr_achieved=rnd(avg_w / -avg_l) if (avg_w is not None and avg_l) else None,
        profit_factor=rnd(pf, 2), profit_factor_infinite=bool(n and pf is None and wins),
        max_drawdown_r=round(dd, 3), max_drawdown_pct=round(dd * risk_pct, 2),
        longest_win_streak=bw, longest_loss_streak=bl,
        best_r=rnd(max(rs)) if rs else None, worst_r=rnd(min(rs)) if rs else None,
        sd_r=rnd(sd), t_stat=rnd(exp / (sd / math.sqrt(n)), 2) if n > 1 and sd > 0 else None)


def histogram(rs, edges=HIST_EDGES):
    """Counts of R values per bucket. Bucket i covers (edges[i-1], edges[i]]; first is <= edges[0], last is > edges[-1]."""
    out = []
    lo = None
    for e in edges + [None]:
        if lo is None and e is not None:
            label, cnt = f"<= {e:g}R", sum(1 for r in rs if r <= e)
        elif e is None:
            label, cnt = f"> {lo:g}R", sum(1 for r in rs if r > lo)
        else:
            label, cnt = f"{lo:g} to {e:g}R", sum(1 for r in rs if lo < r <= e)
        out.append(dict(label=label, lo=lo, hi=e, count=cnt))
        lo = e
    return out


def monthly(closed):
    """R per calendar month (UTC) of the exit, gross and net."""
    m = {}
    for t in closed:
        k = time.strftime("%Y-%m", time.gmtime((t.get("exit") or t.get("filled") or t["placed"]) / 1000))
        row = m.setdefault(k, dict(month=k, trades=0, r_net=0.0, r_gross=0.0))
        row["trades"] += 1
        row["r_net"] += t["r_net"]
        row["r_gross"] += t["r_gross"]
    return [dict(r, r_net=round(r["r_net"], 3), r_gross=round(r["r_gross"], 3)) for _, r in sorted(m.items())]


def session_of(ms):
    h = time.gmtime(ms / 1000).tm_hour
    for name, a, b in SESSIONS:
        if a <= h < b:
            return name
    return SESSIONS[-1][0]


def by_session(closed):
    rows = []
    for name, a, b in SESSIONS:
        rs = [t["r_net"] for t in closed if session_of(t.get("filled") or t["placed"]) == name]
        rows.append(dict(session=name, utc=f"{a:02d}:00-{b:02d}:00", trades=len(rs),
                         total_r=round(sum(rs), 3), expectancy_r=round(sum(rs) / len(rs), 3) if rs else None,
                         win_rate=round(sum(1 for r in rs if r > 0) / len(rs), 3) if rs else None))
    return rows


def _median(xs):
    return round(statistics.median(xs), 2) if xs else None


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def compute(trades, setups_identified=None, setups_basis=None, risk_pct=RISK_PCT):
    """Full dashboard block for one market (or a pooled list). trades: engine trade dicts."""
    closed = closed_trades(trades)
    rg = [t["r_gross"] for t in closed]
    rn = [t["r_net"] for t in closed]
    fills = [t for t in trades if "filled" in t]
    hours = [t["hours"] for t in closed if t.get("hours") is not None]
    hw = [t["hours"] for t in closed if t.get("hours") is not None and t["r_net"] > 0]
    hl = [t["hours"] for t in closed if t.get("hours") is not None and t["r_net"] < 0]
    fee_r = sum(t.get("r_fee", 0.0) for t in closed)
    slip_r = sum(t.get("r_slip", 0.0) for t in closed)
    gross_total, net_total = sum(rg), sum(rn)
    cost_total = gross_total - net_total
    eq_g = eq_n = 0.0
    curve = []
    for t in closed:
        eq_g += t["r_gross"]
        eq_n += t["r_net"]
        curve.append(dict(t=t.get("exit"), g=round(eq_g, 4), n=round(eq_n, 4), s=t["status"]))
    return dict(
        risk_pct_per_trade=risk_pct,
        setups_identified=setups_identified if setups_identified is not None else len(trades),
        setups_basis=setups_basis or "orders placed",
        orders_placed=len(trades), trades_executed=len(fills),
        unfilled=sum(t.get("status") == "unfilled" for t in trades),
        open_at_end=sum(t.get("status") in ("open_at_end", "pending_at_end") for t in trades),
        closed=len(closed),
        exits=dict(tp=sum(t["status"] == "tp" for t in closed), sl=sum(t["status"] == "sl" for t in closed),
                   time=sum(t["status"] == "time" for t in closed)),
        gross=block(rg, risk_pct), net=block(rn, risk_pct),
        costs=dict(fees_r=round(fee_r, 3), slippage_r=round(slip_r, 3), total_r=round(cost_total, 3),
                   per_trade_r=round(cost_total / len(closed), 3) if closed else None,
                   share_of_gross_profit=round(cost_total / gross_total, 3) if gross_total > 0 else None),
        duration_h=dict(avg=_mean(hours), median=_median(hours), avg_win=_mean(hw), avg_loss=_mean(hl),
                        median_win=_median(hw), median_loss=_median(hl), max=round(max(hours), 2) if hours else None),
        equity=curve,
        r_hist=dict(net=histogram(rn), gross=histogram(rg)),
        monthly=monthly(closed),
        sessions=by_session(closed),
        integrity=dict(
            same_candle_tp_sl=sum(1 for t in closed if t.get("same_bar_tp_sl")),
            sl_gap_fills=sum(1 for t in closed if t.get("exit_reason") == "sl_gap_open"),
            tp_on_fill_bar_ignored=sum(1 for t in closed if t.get("tp_touched_on_fill_bar")),
            trades_across_gaps=sum(1 for t in trades if t.get("gap_bars_in_trade")),
            overlapping_trades=count_overlaps(trades)))


def count_overlaps(trades):
    """Filled trades whose fill happened before the previous filled trade on the same market had exited."""
    by_mkt = {}
    for t in trades:
        if "filled" in t:
            by_mkt.setdefault(t.get("market"), []).append(t)
    n = 0
    for lst in by_mkt.values():
        lst.sort(key=lambda t: t["filled"])
        busy = None
        for t in lst:
            if busy is not None and t["filled"] < busy:
                n += 1
            end = t.get("exit")
            busy = max(busy or 0, end) if end else float("inf")
    return n


# ---------------------------------------------------------------- "why it worked or failed"
def _pct(x):
    return f"{x * 100:.0f}%"


def explain(stats, trades, min_samples=15, verdict=None):
    """Plain-language findings computed only from these trades. Each: dict(id, level, title, text).
    level: good | warn | info. Never claims anything the numbers do not show."""
    F = []
    add = lambda id_, level, title, text: F.append(dict(id=id_, level=level, title=title, text=text))
    g, n_, c = stats["gross"], stats["net"], stats["costs"]
    closed = closed_trades(trades)
    N = stats["closed"]
    if N == 0:
        add("no_trades", "warn", "No closed trades",
            "No trade both filled and finished in the test window, so there is nothing to measure yet. "
            "Try a longer window, a longer order expiry, or an entry closer to price.")
        return F
    if N < min_samples:
        add("sample", "warn", "Too few trades to judge",
            f"Only {N} closed trades. With fewer than {min_samples}, a few lucky or unlucky trades decide the result, "
            "so treat every number here as a first look, not evidence.")
    elif N < 30:
        add("sample", "info", "Small sample",
            f"{N} closed trades is enough for a first verdict but still small. Results can swing a lot with 30+ more trades.")
    if stats["orders_placed"]:
        fr = stats["trades_executed"] / stats["orders_placed"]
        if stats["unfilled"] and fr < 0.5:
            add("fills", "warn", "Most orders never filled",
                f"{stats['unfilled']} of {stats['orders_placed']} orders expired unfilled ({_pct(1 - fr)}). "
                "The entry is often too far from where price went. A closer entry or longer expiry changes the sample.")
    # costs
    if g["total_r"] > 0 and n_["total_r"] <= 0:
        add("costs_flip", "warn", "Costs wiped out the edge",
            f"Before costs the trades made {g['total_r']:+.2f}R; after fees and slippage they made {n_['total_r']:+.2f}R. "
            f"Costs averaged {c['per_trade_r']:.3f}R per trade. Wider targets or fewer, larger moves reduce this drag.")
    elif c["share_of_gross_profit"] is not None and c["share_of_gross_profit"] >= 0.25:
        add("costs", "warn", "Costs take a big bite",
            f"Fees and slippage took {_pct(c['share_of_gross_profit'])} of the gross profit "
            f"({c['fees_r']:.2f}R fees, {c['slippage_r']:.2f}R slippage).")
    elif c["total_r"] > 0:
        add("costs", "info", "Costs",
            f"Fees and slippage cost {c['total_r']:.2f}R in total ({c['per_trade_r']:.3f}R per trade).")
    # win rate vs payoff
    if n_["avg_rr_achieved"] is not None and n_["win_rate"] is not None:
        be = 1 / (1 + n_["avg_rr_achieved"])
        wr = n_["win_rate"]
        lvl = "good" if wr > be else "warn"
        add("payoff", lvl, "Win rate vs payoff",
            f"Winners averaged {n_['avg_win_r']:+.2f}R and losers {n_['avg_loss_r']:+.2f}R (payoff {n_['avg_rr_achieved']:.2f}:1), "
            f"so the break-even win rate is about {_pct(be)}. The actual win rate was {_pct(wr)}"
            + (", above break-even." if wr > be else ", below break-even."))
    # speed of losses
    d = stats["duration_h"]
    if d["median_loss"] is not None and d["median_win"] is not None and stats["exits"]["sl"] >= 5:
        if d["median_loss"] < 0.5 * d["median_win"]:
            add("fast_losses", "warn", "Losers stop out fast",
                f"Half the losing trades were over within {d['median_loss']:g}h, while winners took a median {d['median_win']:g}h. "
                "Quick stop-outs often mean the entry is early or the stop sits inside normal noise.")
        else:
            add("durations", "info", "Time in trade",
                f"Median time in a winning trade {d['median_win']:g}h, in a losing trade {d['median_loss']:g}h.")
    # time exits
    te = stats["exits"]["time"]
    if te and te >= 0.25 * N:
        add("time_exits", "info", "Many time exits",
            f"{te} of {N} trades hit neither TP nor SL before the time stop. The target may be too far for the holding time.")
    # concentration
    if n_["total_r"] > 0 and N >= 5:
        top = sorted((t["r_net"] for t in closed), reverse=True)
        k = min(3, len(top))
        share = sum(top[:k]) / n_["total_r"] if n_["total_r"] else 0
        rest = n_["total_r"] - sum(top[:k])
        if rest <= 0:
            add("concentration", "warn", "Profit comes from a few trades",
                f"The best {k} trades made {sum(top[:k]):+.2f}R; without them the rest lost {rest:+.2f}R. "
                "The result depends on catching a handful of big moves.")
        elif share > 0.6:
            add("concentration", "info", "Profit is concentrated",
                f"The best {k} trades made {_pct(share)} of the net profit.")
    # consistency over time
    if N >= 10:
        half = N // 2
        a = sum(t["r_net"] for t in closed[:half]) / half
        b = sum(t["r_net"] for t in closed[half:]) / (N - half)
        if (a > 0) != (b > 0):
            add("halves", "warn", "Not consistent over time",
                f"First half of trades averaged {a:+.2f}R, second half {b:+.2f}R. The result flips sign within the test.")
        else:
            add("halves", "good" if a > 0 else "info", "Consistency over time",
                f"First half of trades averaged {a:+.2f}R, second half {b:+.2f}R.")
    # sessions
    ses = [s for s in stats["sessions"] if s["trades"] >= 5]
    if len(ses) >= 2:
        best = max(ses, key=lambda s: s["expectancy_r"])
        worst = min(ses, key=lambda s: s["expectancy_r"])
        if best["expectancy_r"] > 0 > worst["expectancy_r"]:
            add("sessions", "info", "Time of day matters",
                f"Trades filled in the {best['session']} session ({best['utc']} UTC) averaged {best['expectancy_r']:+.2f}R over "
                f"{best['trades']} trades; in {worst['session']} ({worst['utc']} UTC) {worst['expectancy_r']:+.2f}R over "
                f"{worst['trades']}. Small groups, so treat this as a lead to test, not a rule.")
    # streak / drawdown
    if n_["longest_loss_streak"] >= 5:
        add("streak", "info", "Losing streaks",
            f"The worst run was {n_['longest_loss_streak']} losses in a row and the deepest drawdown {n_['max_drawdown_r']:.2f}R "
            f"({n_['max_drawdown_pct']:.1f}% at {stats['risk_pct_per_trade']:g}% risk per trade).")
    # integrity notes
    integ = stats["integrity"]
    if integ["same_candle_tp_sl"]:
        add("ambiguous", "info", "Same-candle TP and SL",
            f"{integ['same_candle_tp_sl']} trade(s) touched both TP and SL inside one candle. The order inside a candle "
            "is unknown, so CHRONOS counted them as losses (the cautious choice).")
    if integ["trades_across_gaps"]:
        add("gaps", "warn", "Trades across missing data",
            f"{integ['trades_across_gaps']} trade(s) were open across a hole in the price data. Their exits may be less exact.")
    return F
