#!/usr/bin/env python3
"""CHRONOS strategy comparison (Phase 2): run 2-6 setups under identical conditions and compare them.

  python3 strategy_compare.py spec.json [out.json]

spec = {market, side, entry, tp, sl, days, tf, slippage, slippage_unit, htf_confirm, expiry, max_hold, end_ms,
        strategies: [{name, type, params}]}

Identical conditions: same market, side, window (one fixed end time), setup timeframe, execution candles, fees,
slippage, expiry, time stop, higher-timeframe filter and one-trade-at-a-time rule. Rule strategies use the R:R of
the trader's own levels. Read-only; never places orders.
"""
import json, sys, time

import tester as T
import strategies as R

# type -> how CHRONOS tests it. kind: "levels" (your own levels), "rule" (mechanical rule), "approx" (approximation)
TYPES = {
    "similar": dict(label="Your levels at similar past moments", kind="levels", mode="similar",
                    note="Your entry, stop and target, placed where past price action looked like your chart."),
    "every_close": dict(label="Your levels at every candle close", kind="levels", mode="every-close",
                        note="Your entry, stop and target shape, placed at every setup-timeframe close."),
    "zone_retest": dict(label="Supply/demand zone retest", kind="approx", mode="pattern", style="limit",
                        note="CHRONOS's zone rule (small base, then a strong move). The closest rule CHRONOS has to an "
                             "order block; it is not the same as a hand-drawn order block."),
    "zone_sweep": dict(label="Zone liquidity sweep", kind="approx", mode="pattern", style="sweep",
                       note="After a tap of a fresh zone, wait for a sweep of the zone's far edge and a close back inside."),
    "zone_bos": dict(label="Zone tap + break of structure", kind="approx", mode="pattern", style="confirm",
                     note="SMC-style: after a tap of a fresh zone, wait for a candle to close through the last swing."),
    "ema_cross": dict(label="EMA crossover", kind="rule", mode="rule", rule="ema_cross"),
    "breakout": dict(label="Breakout", kind="rule", mode="rule", rule="breakout"),
    "liquidity_sweep": dict(label="Liquidity sweep (ICT-style)", kind="rule", mode="rule", rule="liquidity_sweep"),
    "fvg": dict(label="Fair value gap retest", kind="rule", mode="rule", rule="fvg"),
}
for _k, _v in TYPES.items():
    if _v.get("rule"):
        _v["note"] = R.RULES[_v["rule"]]["doc"]
# common names a trader may type, and what CHRONOS can actually test for them
NAMED = {
    "Order block": ("zone_retest", "approximated by the supply/demand zone rule"),
    "Smart Money Concepts (SMC)": ("zone_bos", "approximated by zone tap + break of structure; SMC has more parts"),
    "ICT liquidity sweep": ("liquidity_sweep", "tested as a sweep of the last N-candle high/low"),
    "Fair value gap (FVG)": ("fvg", "tested by rule"),
    "Breakout": ("breakout", "tested by rule"),
    "EMA crossover": ("ema_cross", "tested by rule"),
}
MAX_STRATEGIES, MIN_STRATEGIES = 6, 2


def validate(spec):
    if not isinstance(spec, dict):
        raise ValueError("spec must be an object")
    sts = spec.get("strategies")
    if not isinstance(sts, list) or not (MIN_STRATEGIES <= len(sts) <= MAX_STRATEGIES):
        raise ValueError(f"define {MIN_STRATEGIES} to {MAX_STRATEGIES} strategies")
    clean = []
    for i, st in enumerate(sts):
        if not isinstance(st, dict):
            raise ValueError(f"strategy {i + 1} must be an object")
        typ = str(st.get("type") or "")
        if typ not in TYPES:
            raise ValueError(f"strategy {i + 1}: unknown type '{typ}'")
        name = str(st.get("name") or TYPES[typ]["label"]).strip()[:48] or TYPES[typ]["label"]
        params = st.get("params") or {}
        if TYPES[typ].get("rule"):
            params = R.clean_params(TYPES[typ]["rule"], params)
        elif params:
            raise ValueError(f"strategy {i + 1}: {TYPES[typ]['label']} takes no parameters here")
        clean.append(dict(name=name, type=typ, params=params))
    tf = str(spec.get("tf") or "1h")
    if tf not in T.SETUP_TFS:
        raise ValueError(f"tf must be one of {', '.join(T.SETUP_TFS)}")
    return clean, tf


def argv_for(spec, st, tf, end_ms):
    t = TYPES[st["type"]]
    iv = T.exec_interval_for(tf)
    av = [spec["market"], spec["side"], f"--entry={spec['entry']!r}", f"--tp={spec['tp']!r}", f"--sl={spec['sl']!r}",
          "--days", str(int(spec.get("days") or 90)), "--interval", iv, "--end-ms", str(end_ms),
          "--mode", t["mode"], "--max-hold", str(spec.get("max_hold") or "7d")]
    if spec.get("slippage_unit") == "ticks" and spec.get("slippage"):
        av.append(f"--slippage-ticks={float(spec['slippage'])!r}")
    else:
        sl = spec.get("slippage")
        av.append(f"--slippage-bps={float(T.DEFAULT_SLIPPAGE_BPS if sl in (None, '') else sl)!r}")
    htf = spec.get("htf_confirm") or "off"
    if htf != "off":
        av += ["--htf-confirm", htf]
    if t["mode"] != "pattern":
        av += ["--expiry", str(spec.get("expiry") or "72h")]
    if t["mode"] == "similar":
        av += ["--similar-tf", tf]
    elif t["mode"] == "pattern":
        av += ["--zone-tf", tf, "--entry-style", t["style"], "--confirm-tf", iv if T.STEP[iv] > T.STEP["15m"] or tf == "5m" else "15m"]
    else:
        av += ["--setup-tf", tf]
    if t["mode"] == "rule":
        av += ["--rule", t["rule"], "--rule-params", json.dumps(st["params"])]
    return av


def row_for(st, out, trades):
    t = TYPES[st["type"]]
    s, nt, gr = out["stats"], out["stats"]["net"], out["stats"]["gross"]
    closed = [x for x in trades if x.get("status") in ("tp", "sl", "time")]
    closed.sort(key=lambda x: x["exit"])
    eq, run = [], 0.0
    for x in closed:
        run += x["r_net"]
        eq.append([x["exit"], round(run, 3)])
    dq = out.get("data_quality") or {}
    flags = []
    if s["closed"] < T.MIN_SAMPLES:
        flags.append(f"only {s['closed']} closed trades (under {T.MIN_SAMPLES}): too few to judge")
    cs = (out.get("conditions") or {}).get("summary") or {}
    return dict(name=st["name"], type=st["type"], label=t["label"], kind=t["kind"], note=t["note"], params=st["params"],
                placement=out["params"].get("placement"), setups=s["setups_identified"], closed=s["closed"],
                unfilled=s["unfilled"], wins=nt["wins"], losses=nt["losses"], win_rate=nt["win_rate"],
                expectancy_r=nt["expectancy_r"], profit_factor=nt["profit_factor"],
                profit_factor_infinite=nt["profit_factor_infinite"], total_r=nt["total_r"],
                total_pct=nt["total_pct"], total_r_gross=gr["total_r"], expectancy_r_gross=gr["expectancy_r"],
                max_drawdown_r=nt["max_drawdown_r"], avg_win_r=nt["avg_win_r"], avg_loss_r=nt["avg_loss_r"],
                avg_rr_achieved=nt["avg_rr_achieved"], longest_loss_streak=nt["longest_loss_streak"],
                avg_hours=s["duration_h"]["avg"], costs_r=s["costs"]["total_r"], t_stat=nt["t_stat"],
                verdict=out["result"]["verdict"], dq=dq.get("status"), dq_messages=dq.get("messages") or [],
                skipped_by_filters=out.get("skipped_by_trend"), conditions=cs.get("headline"), equity=eq, flags=flags,
                edge_score=(out.get("edge") or {}).get("score"), edge_label=(out.get("edge") or {}).get("label"),
                edge_band=(out.get("edge") or {}).get("band"))


def run(spec):
    strategies, tf = validate(spec)
    now = int(time.time() * 1000)
    iv = T.exec_interval_for(tf)
    end_ms = now - now % T.STEP[iv]
    if spec.get("end_ms"):
        end_ms = min(int(spec["end_ms"]), end_ms)
    rows, first = [], None
    for st in strategies:
        try:
            a = T.build_parser().parse_args(argv_for(spec, st, tf, end_ms))
            out, trades, _txt = T.run_cli(a)
            first = first or out
            rows.append(row_for(st, out, trades))
        except SystemExit as ex:
            rows.append(dict(name=st["name"], type=st["type"], label=TYPES[st["type"]]["label"],
                             kind=TYPES[st["type"]]["kind"], note=TYPES[st["type"]]["note"], params=st["params"],
                             error=str(ex)))
    ok = [r for r in rows if not r.get("error")]
    if not ok:
        raise SystemExit(rows[0]["error"] if rows else "nothing to compare")
    d = first["data"]
    cond = dict(market=first["market"]["market_id"], side=spec["side"], days=d["days"], days_requested=d["days_requested"],
                from_ms=d["from_ms"], to_ms=d["to_ms"], setup_tf=tf, interval=iv, fees=first["fees"],
                slippage=first["costs"]["slippage"]["text"], expiry=str(spec.get("expiry") or "72h"),
                max_hold=str(spec.get("max_hold") or "7d"), htf_confirm=spec.get("htf_confirm") or "off",
                htf_rule=first["params"].get("htf_rule"), rr=round(first["setup"]["rr"], 3),
                one_at_a_time=True, provenance_text=d.get("provenance_text"))
    notes = ["Every row uses the same market, window, setup timeframe, execution candles, fees, slippage, time stop "
             "and filters. Rule-based rows use your levels' R:R for their target.",
             "Zone-based rows keep a limit on a zone for up to 30 days instead of the order expiry; that is part of "
             "the zone rule.",
             f"Comparing {len(ok)} strategies on the same data means the best-looking one is partly luck. Treat a "
             "lead as something to test again on a later window, not as proof."]
    if any(r["closed"] < T.MIN_SAMPLES for r in ok):
        notes.append(f"Rows with fewer than {T.MIN_SAMPLES} closed trades are too small to rank.")
    ranked = sorted([r for r in ok if r["closed"] >= T.MIN_SAMPLES], key=lambda r: r["expectancy_r"], reverse=True)
    lead = None
    if len(ranked) >= 2:
        a_, b_ = ranked[0], ranked[1]
        lead = (f"Highest expectancy after costs: {a_['name']} ({a_['expectancy_r']:+.3f}R per trade over {a_['closed']} "
                f"trades), next {b_['name']} ({b_['expectancy_r']:+.3f}R).")
        if a_["expectancy_r"] <= 0:
            lead += " None of them made money after costs in this window."
    elif len(ranked) == 1:
        lead = f"Only {ranked[0]['name']} had enough trades to judge ({ranked[0]['expectancy_r']:+.3f}R per trade)."
    else:
        lead = "No strategy had enough closed trades to judge in this window."
    return dict(tool="chronos-strategy-compare", generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                conditions=cond, rows=rows, notes=notes, lead=lead,
                types={k: dict(label=v["label"], kind=v["kind"], note=v["note"],
                               params=(R.RULES[v["rule"]]["params"] if v.get("rule") else {}))
                       for k, v in TYPES.items()},
                named={k: dict(type=v[0], how=v[1]) for k, v in NAMED.items()})


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        raise SystemExit(__doc__)
    spec = json.load(open(argv[0]))
    try:
        out = run(spec)
    except ValueError as ex:
        raise SystemExit(str(ex)) from None
    txt = json.dumps(out, indent=1)
    if len(argv) > 1:
        open(argv[1], "w").write(txt)
    else:
        print(txt)


if __name__ == "__main__":
    main()
