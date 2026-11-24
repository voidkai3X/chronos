#!/usr/bin/env python3
"""CHRONOS setup coaching. Rule-based: every finding is computed from the engine's own numbers for the tested
window, says how many closed trades it rests on, and never predicts profit.

build() reads the user's result, re-runs the same setup as a few variants (blind limit, limit with a wider stop,
sweep entry, confirmation entry) on the same market + window and on a basket for sample size, and returns
findings, a comparison table and suggested fixes. text() renders that for the CLI.
"""
import copy

import tester as T

MIN_N = 20            # closed trades needed before a number counts as evidence (same as the engine's verdict)
WIDE_EXTRA_ATR = 0.5  # the "wider stop" variant adds this much zone-tf ATR beyond the zone
EDGE_STOP_ATR = 0.5   # a stop closer than this beyond the zone's far edge counts as a zone-edge stop
USER_MIN_RR = 2.8     # the user's own rule; reported, never enforced

DISCLAIMER = ("These notes come from past candles in the tested window only. They describe what happened, not what "
              "will happen. Small samples and trying several variants both make results look better than they are. "
              "Not financial advice.")


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _ev(n, days, scope):
    return f"[past {days}d, {scope}, {n} closed trade{'s' if n != 1 else ''}]"


def _scope_name(out):
    return f"{len(out['basket'])}-market basket" if out.get("basket") else out["market"]["market_id"].split("|")[-1]


def _row(label, style, scope, buf, out, scope_idx=0):
    s = out["result"]
    ok = (s["closed"] >= MIN_N and s["win_rate"] is not None and s["win_rate"] > s["breakeven_win_rate"]
          and s["avg_r_net"] > 0)
    return dict(variant=label, entry_style=style, wide="wider" in label, scope=scope, scope_idx=scope_idx, sl_buffer_atr=round(buf, 3) if buf is not None else None,
                orders=s["placed"], fills=s["fills"], fill_rate=s["fill_rate"], closed=s["closed"], tp=s["tp"],
                sl=s["sl"], time_exits=s["time_exits"], win_rate=s["win_rate"], breakeven_win_rate=s["breakeven_win_rate"],
                avg_r_net=s["avg_r_net"], total_r_net=s["total_r_net"],
                t_stat=s["t_stat"] if s["closed"] >= 10 else None,  # t on a handful of trades is meaningless
                max_losing_streak=s["max_losing_streak"], sl_then_tp_pct_of_losses=s["sl_then_tp_pct_of_losses"],
                eligible=ok, verdict=s["verdict"])


def run_variants(a, mode, ctx, sl_buffer):
    """Re-run the setup as each variant on the single market and on the basket. Returns (rows, scopes, buf)."""
    base_a = copy.copy(a)
    base_a.insights = False
    base_a.json = False
    base_a.json_out = None
    if mode != "pattern":
        base_a.expiry = None  # pattern-mode default (a limit rests on a zone until it is touched / dies, max 30d)
        base_a.sl_buffer_atr = None
        sl_buffer, _, _ = T.pattern_sl_buffer(base_a, ctx)
    is_fx = T.asset_class(ctx["m"]) == "forex"
    basket = a.basket or ("fx" if is_fx else None)
    scopes = [(None, ctx["m"]["market_id"].split("|")[-1])] + ([(basket, None)] if basket else [])
    variants = [("limit (blind, first retest)", "limit", sl_buffer),
                (f"limit, SL +{WIDE_EXTRA_ATR:g} ATR wider", "limit", sl_buffer + WIDE_EXTRA_ATR),
                ("sweep entry", "sweep", sl_buffer),
                ("confirm entry", "confirm", sl_buffer)]
    rows = []
    for si, (bsk, name) in enumerate(scopes):
        for label, style, buf in variants:
            a2 = copy.copy(base_a)
            a2.entry_style = style
            a2.sl_buffer_atr = buf
            src = dict(how="what-if variant")
            out, _ = T.run_setup(a2, "pattern", ctx, buf, src, bsk)
            rows.append(_row(label, style, name or _scope_name(out), buf, out, si))
    return rows, [s[1] or basket for s in scopes], sl_buffer


def _find(rows, style, wide=False):
    """The variant rows for one entry style, single market first, then basket."""
    return [r for r in rows if r["entry_style"] == style and r["wide"] == wide]


def _rowtxt(r, days):
    return (f"{r['variant']} on {r['scope']}: {r['closed']} closed, win {_pct(r['win_rate'])} vs break-even "
            f"{_pct(r['breakeven_win_rate'])}, {r['avg_r_net']:+.2f}R per trade "
            + ("(enough trades and above break-even, but see the best-of-several warning)" if r["eligible"] else
               "(not enough evidence)" if r["closed"] < MIN_N else "(below break-even or no edge)")
            + f" {_ev(r['closed'], days, r['scope'])}")


def build(a, mode, ctx, out, trades, sl_buffer, sl_src):
    s, g, p = out["result"], out["setup"], out["params"]
    days = out["data"]["days"]
    scope = _scope_name(out)
    n = s["closed"]
    style = p.get("entry_style", "limit") if mode == "pattern" else "every-close"
    rows, scopes, pbuf = run_variants(a, mode, ctx, sl_buffer)
    F = []  # findings: dict(id, level, text, fix?)

    def add(id_, level, text, fix=None, evidence=None):
        F.append(dict(id=id_, level=level, text=text, fix=fix, evidence=evidence))

    ev = _ev(n, days, scope)
    atr_tf = a.zone_tf if mode == "pattern" else "4h"
    # 0. what was tested
    if mode != "pattern":
        add("mode", "info", "This run placed your setup's shape at every 4h close, not only where your pattern appears, so it "
            "checks your distances, not the setup idea. The comparison below uses pattern mode (zone retests) instead.")
    # 1. sample size
    if n < MIN_N:
        bigger = "run it across a basket (e.g. all FX pairs) or a longer window" if not out.get("basket") else "use a longer window"
        add("sample", "warn", (f"No closed trades at all, so there is nothing to judge. {ev}" if n == 0 else
            f"Only {n} closed trades, below the {MIN_N} needed to judge anything. Any win rate here can be luck. {ev}"), fix=f"Get more samples: {bigger} before trusting a number.")
    # 2. stops getting swept
    if s["sl"] >= 3 and (s["sl_then_tp_pct_of_losses"] or 0) >= 0.3:
        med = s["median_extra_stop_atr_for_swept"]
        add("swept", "warn", f"{s['sl_then_tp']} of {s['sl']} stop-outs ({_pct(s['sl_then_tp_pct_of_losses'])}) later reached the "
            f"target within {p['sweep_window']}" + (f"; the median of those needed {med:.2f} x {atr_tf} ATR more room" if med is not None else "")
            + f" ({s['shallow_sweeps']} went {p['sweep_atr']:g} ATR or less past the stop). Your stops are getting swept "
            f"before the move. {ev}",
            fix="Wait for the sweep (let price run the stops beyond the zone, then enter on the close back inside), "
                "or place the SL beyond that liquidity instead of just past the zone.",
            evidence=_find(rows, "sweep") + _find(rows, "limit", wide=True))
    elif s["sl"] >= 3 and s["shallow_sweep_pct_of_losses"] and s["shallow_sweep_pct_of_losses"] >= 0.2:
        add("wicks", "warn", f"{s['shallow_sweeps']} of {s['sl']} stop-outs were wicks of {p['sweep_atr']:g} ATR or less past the "
            f"stop before price hit the target. {ev}", fix="Give the stop more room beyond the zone / swing.",
            evidence=_find(rows, "limit", wide=True))
    # 3. entry rarely reached
    if s["placed"] >= 5 and s["fill_rate"] is not None and s["fill_rate"] < 0.4:
        why = ("your entry is far from price, so the replayed order rarely fills" if mode != "pattern" and abs(g.get("entry_atr", 0)) > 3
               else "price rarely came back to the entry / zone before the order was cancelled" if style in ("limit", "every-close")
               else "the trigger (sweep / break of structure) rarely happened after the tap")
        add("fills", "warn", f"Only {s['fills']} of {s['placed']} orders filled ({_pct(s['fill_rate'])}): {why}. "
            f"[past {days}d, {scope}, {s['placed']} orders]",
            fix="Move the entry closer to price, or wait for a fresher / nearer zone" if style in ("limit", "every-close")
            else "Accept fewer trades, or loosen the trigger (longer --trigger-window, smaller --sweep-min-atr).")
    if mode != "pattern" and abs(g.get("entry_atr", 0)) > 3:
        add("far", "warn", f"Your entry is {abs(g['entry_atr']):.1f} x 4h ATR ({abs(g['entry_pct']):.2f}%) from the current price. "
            "A limit that far away is a bet on a retest; test it in pattern mode so it is only placed at zones.")
    # 4. time exits
    if n >= 3 and s["time_exits"] / n >= 0.3:
        add("time", "warn", f"{s['time_exits']} of {n} trades ({_pct(s['time_exits'] / n)}) ended on the {p['max_hold']} time limit, "
            f"not at TP or SL. The target is far for the time window ({g['reward_atr']:.1f} x 4h ATR away) or the stop is so wide "
            f"the trade barely moves in R. {ev}", fix="Use a nearer target (lower R:R) or a tighter, structure-based stop.")
    # 4b. time exits across the what-if runs (catches setups whose own run had too few trades)
    vb = [r for r in rows if r["scope_idx"] == max(x["scope_idx"] for x in rows)]
    vc, vt = sum(r["closed"] for r in vb), sum(r["time_exits"] for r in vb)
    vtp = sum(r["tp"] for r in vb)
    if not any(f["id"] == "time" for f in F) and vc >= 5 and vt / vc >= 0.4:
        add("time_variants", "warn", f"Across the {len(vb)} variant runs on {vb[0]['scope']}, {vt} of {vc} closed trades "
            f"({_pct(vt / vc)}) ended on the {p['max_hold']} time limit and {vtp} reached the target. At 1:{g['rr']:.2f} with a "
            f"stop this wide the target is rarely reached in time. [past {days}d, {vb[0]['scope']}, {vc} closed trades, "
            "pooled over variants, so trades overlap]",
            fix="Use a nearer target (lower R:R), a longer time limit (--max-hold), or a higher timeframe trade plan.")
    # 5. below break-even with a blind entry
    if n >= 5 and s["win_rate"] is not None and s["win_rate"] < s["breakeven_win_rate"]:
        txt = (f"Win rate {_pct(s['win_rate'])} is below the {_pct(s['breakeven_win_rate'])} needed to break even at 1:{g['rr']:.2f} "
               f"after fees.")
        if style in ("limit", "every-close"):
            add("below_be", "warn", txt + f" These are blind limit entries at the first retest. {ev}",
                fix="Don't enter blind at the limit: wait for lower-timeframe confirmation (a body close through the last "
                    "swing in your direction) after price taps the zone.",
                evidence=_find(rows, "confirm"))
        else:
            add("below_be", "warn", txt + f" Even with the {style} trigger it lost on this window. {ev}")
    # 6. basket concentration
    if out.get("per_market"):
        pm = sorted(((r["market"]["market_id"].split("|")[-1], r["result"]["total_r_net"], r["result"]["closed"])
                     for r in out["per_market"]), key=lambda x: x[1])
        losers = [x for x in pm if x[1] <= -3]
        winners = [x for x in pm if x[1] >= 3]
        if losers:
            add("markets", "info", "Losses were concentrated in " + ", ".join(f"{m} ({r:+.1f}R, {c} trades)" for m, r, c in losers)
                + (("; the best were " + ", ".join(f"{m} ({r:+.1f}R, {c} trades)" for m, r, c in reversed(winners)))
                   if winners else "") + f". [past {days}d, per market; small per-market samples]",
                fix="Check whether the losing markets were trending against your side; don't assume the pattern behaves "
                    "the same on every pair.")
    # 7. first vs second half
    h1, h2 = s["first_half_avg_r"], s["second_half_avg_r"]
    if n >= 10 and h1 is not None and h2 is not None and h1 > 0 and h2 < h1 - 0.3:
        add("decay", "warn", f"It got weaker over time: {h1:+.2f}R per trade in the first half vs {h2:+.2f}R in the second. "
            f"Conditions may have changed. {ev}")
    # 8. zone-edge stop (user's own rule: stops well beyond the zone, not at its edge)
    if mode == "pattern":
        if sl_buffer < EDGE_STOP_ATR:
            add("edge_stop", "warn", f"Your stop sits only {sl_buffer:.2f} x {a.zone_tf} ATR beyond the zone's far edge "
                f"({sl_src['how']}). Your own rule is stops well beyond the zone, not at its edge; edge stops are where "
                "liquidity gets swept.", fix=f"Put the SL at least {EDGE_STOP_ATR:g}-1 ATR beyond the zone / sweep high.")
    else:
        b2 = copy.copy(a)
        b2.sl_buffer_atr = None
        buf2, src2, _ = T.pattern_sl_buffer(b2, ctx)
        if "zone" in src2 and buf2 < EDGE_STOP_ATR:
            add("edge_stop", "warn", f"Your stop sits only {buf2:.2f} x {a.zone_tf} ATR past the far edge of the zone your entry "
                "is in. Your own rule is stops well beyond the zone, not at its edge.",
                fix=f"Put the SL at least {EDGE_STOP_ATR:g}-1 ATR beyond the zone / sweep high.")
        if g.get("risk_atr", 9) < 1:
            add("tight", "warn", f"Your stop is {g['risk_atr']:.2f} x 4h ATR from entry, less than one normal 4h candle.")
    # 9. streak + R:R report
    if s["max_losing_streak"] >= 8:
        add("streak", "info", f"Worst losing streak was {s['max_losing_streak']} in a row (worst drawdown {s['max_drawdown_r']:.1f}R). "
            f"At 1:{g['rr']:.1f} long streaks are normal; size so you can survive them. {ev}")
    add("rr", "info", f"R:R tested: 1:{g['rr']:.2f}" + (f" (your own minimum is 1:{USER_MIN_RR})" if g["rr"] < USER_MIN_RR else "")
        + ". Every variant below keeps this R:R; only the entry and stop change.")

    # comparison verdict
    k = len(rows)
    good = [r for r in rows if r["eligible"]]
    if good:
        b = max(good, key=lambda r: r["avg_r_net"])
        best = dict(variant=b["variant"], scope=b["scope"], text=(
            f"Best of the {k} variant runs: {_rowtxt(b, days)}, t={b['t_stat']:.1f}"
            + (" (not statistically proven: t below 2)" if (b["t_stat"] or 0) < 2 else "")
            + f". Because it is the best of {k} tries on the same data, the real edge is probably smaller than this. "
            "Treat it as something to forward-test, not a proven fix."))
    else:
        best = dict(variant=None, scope=None, text=(
            f"Evidence is insufficient: none of the {k} variant runs had at least {MIN_N} closed trades and a result above "
            "break-even on this window. Don't read the comparison as a fix; it only shows how each entry style behaved here."))
    fixes = []
    for f in F:
        if f.get("fix"):
            evs = f.get("evidence") or []
            fixes.append(dict(id=f["id"], fix=f["fix"], evidence=[_rowtxt(r, days) for r in evs]))
    return dict(findings=[dict(id=f["id"], level=f["level"], text=f["text"]) for f in F], comparison=rows,
                comparison_note=(f"{k} variant runs on the same {days}-day window (pattern mode, {a.zone_tf} zones, same R:R "
                                 f"1:{g['rr']:.2f}, fees included). Picking the best of several variants overstates its edge "
                                 "(multiple comparisons)."),
                best=best, fixes=fixes, min_closed=MIN_N, sl_buffer_atr=round(pbuf, 3), disclaimer=DISCLAIMER)


def text(ins):
    L = ["INSIGHTS (rule-based, from this test's own numbers)"]
    for f in ins["findings"]:
        L.append(f"  {'!' if f['level'] == 'warn' else '-'} {f['text']}")
    L += ["", "WHAT-IF COMPARISON: " + ins["comparison_note"],
          f"  {'variant':<28} {'scope':<16} {'SLbuf':>5} {'closed':>6} {'TP':>3} {'SL':>3} {'time':>4} {'win':>4} {'BE':>4} "
          f"{'avgR':>6} {'totR':>6} {'t':>5}  evidence"]
    for r in ins["comparison"]:
        L.append(f"  {r['variant']:<28} {r['scope']:<16} {r['sl_buffer_atr']:>5.2f} {r['closed']:>6} {r['tp']:>3} {r['sl']:>3} "
                 f"{r['time_exits']:>4} {_pct(r['win_rate']):>4} {_pct(r['breakeven_win_rate']):>4} {r['avg_r_net']:>+6.2f} "
                 f"{r['total_r_net']:>+6.1f} {('%.1f' % r['t_stat']) if r['t_stat'] is not None else 'n/a':>5}  "
                 + ("ok (>=20, above BE)" if r["eligible"] else f"too few (<{ins['min_closed']})" if r["closed"] < ins["min_closed"]
                    else "below BE / no edge"))
    L += ["", ins["best"]["text"], "", "SUGGESTED FIXES"]
    for f in ins["fixes"] or [dict(fix="No specific fix: nothing in this result tripped a rule.", evidence=[])]:
        L.append(f"  * {f['fix']}")
        for e in f["evidence"]:
            L.append(f"      tested: {e}")
    L += ["", ins["disclaimer"]]
    return "\n".join(L)
