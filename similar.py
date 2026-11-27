#!/usr/bin/env python3
"""CHRONOS similarity mode: fingerprint a setup's recent price action and replay the same
trade on the most similar past windows (same market, or a compare / basket).

The path is ATR-normalised then z-scored so the shape, not the price level, is what matches.
Extra features (higher-timeframe slope, ATR percentile, swing distances, sweeps, impulse,
where price sits in the window) are already scale-free. Search is causal. Overlapping
windows are dropped. Live scan reuses the same fingerprint on each market's latest window.

Read-only. Never places orders.
"""
import math, time

import tester as T

# defaults
SIMILAR_TF = "1h"
SIMILAR_BARS = 24          # 24 x 1h = 1 day of structure
SIMILAR_K = 30             # max non-overlapping historical matches per market
SWING_LOOKBACK = 12        # bars, on the similar-tf
ATR_PCT_LOOKBACK = 720     # bars (~30 days on 1h) for the ATR percentile, using only the past
SCORE_TAU = 0.75           # score = 100 * exp(-distance / tau)
SCAN_TOP = 10
MIN_SCORE_LIVE = 0.0       # report the closest; the UI still shows the score

HTF_OF = {"5m": "30m", "15m": "1h", "30m": "1h", "1h": "4h", "4h": "4h", "1d": "4h"}

# path carries most of the weight; features break ties and reject wrong regimes
PATH_WEIGHT = 0.60
FEAT_WEIGHTS = {
    "htf_slope": 0.08,
    "atr_pctile": 0.06,
    "dist_swing_high": 0.06,
    "dist_swing_low": 0.06,
    "swept_high": 0.04,
    "swept_low": 0.04,
    "impulse_atr": 0.04,
    "close_loc": 0.02,
}

DISCLAIMER = "Not advice. Past results don't guarantee future results."


def htf_of(tf):
    return HTF_OF.get(tf, "4h")


def _bstep(base):
    return min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51])) if len(base) > 50 else T.STEP["15m"]


def series(base, tf, interval="15m"):
    """Complete similar-tf bars from a 15m (or interval) base. Each is (open,o,h,l,c,last)."""
    step = T.STEP[tf]
    bstep = _bstep(base)
    return T.complete_bars(T.resample(base, step), step, bstep), step


def _znorm(xs):
    n = len(xs)
    if n == 0:
        return []
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / n
    sd = math.sqrt(var) if var > 1e-18 else 1.0
    return [(x - mu) / sd for x in xs]


def _swings(zb, i, lookback):
    """Prior swing high/low using only bars before i (causal)."""
    lo = max(0, i - lookback)
    prior = zb[lo:i]
    if not prior:
        return zb[i][2], zb[i][3]
    return max(b[2] for b in prior), min(b[3] for b in prior)


def _swept(zb, start, end, lookback):
    """In bars start..end inclusive, did a bar wick through a prior swing and close back inside?"""
    sh = sl_ = False
    for k in range(start, end + 1):
        hi, lo = _swings(zb, k, lookback)
        b = zb[k]
        if b[2] > hi and b[4] < hi:
            sh = True
        if b[3] < lo and b[4] > lo:
            sl_ = True
    return sh, sl_


def _atr_pctile(atr, i, lookback):
    a = atr[i]
    if a is None:
        return None
    lo = max(0, i - lookback)
    hist = [x for x in atr[lo:i + 1] if x is not None]
    if len(hist) < 10:
        return None
    return sum(1 for x in hist if x <= a) / len(hist)


def _htf_slope(h4c, ema, atr4, t, sgn_atr=True):
    """(EMA50 now - EMA50 5 bars back) / 4h ATR, using only 4h bars already closed at t."""
    close_t = [b[0] + T.H4 for b in h4c]
    h = 0
    for j, ct in enumerate(close_t):
        if ct <= t:
            h = j
        else:
            break
    if h < 5 or ema[h] is None or ema[h - 5] is None or not atr4 or atr4[h] in (None, 0):
        return None
    return (ema[h] - ema[h - 5]) / atr4[h]


def fingerprint(zb, atr, i, n, htf=None, swing_lookback=SWING_LOOKBACK, atr_pct_lookback=ATR_PCT_LOOKBACK):
    """Causal fingerprint of the n bars ending at index i (bar i is the last, already closed).

    htf is an optional dict {h4c, ema, atr4} used for the higher-timeframe slope at the bar's close.
    Returns None if there is not enough history.
    """
    if i < n - 1 or atr[i] in (None, 0):
        return None
    win = zb[i - n + 1:i + 1]
    a = atr[i]
    # ATR-normalised close path from the first close, then z-scored (scale-free shape)
    c0 = win[0][4]
    raw = [(b[4] - c0) / a for b in win]
    path = _znorm(raw)
    hi, lo = max(b[2] for b in win), min(b[3] for b in win)
    close = win[-1][4]
    rng = hi - lo
    sh, sl_prior = _swings(zb, i, swing_lookback)
    swept_h, swept_l = _swept(zb, i - n + 1, i, swing_lookback)
    t_known = win[-1][0] + (win[-1][0] - win[-2][0] if len(win) > 1 else T.STEP["1h"])
    # if the bar is a 6-tuple from resample, known time is open + step; caller can overwrite
    def _clip(x, lo, hi):
        return lo if x < lo else hi if x > hi else x
    feat = {
        "htf_slope": 0.0,
        "atr_pctile": _atr_pctile(atr, i, atr_pct_lookback),
        "dist_swing_high": _clip((sh - close) / a, -1.0, 10.0),
        "dist_swing_low": _clip((close - sl_prior) / a, -1.0, 10.0),
        "swept_high": 1.0 if swept_h else 0.0,
        "swept_low": 1.0 if swept_l else 0.0,
        "impulse_atr": _clip(rng / a, 0.0, 10.0),
        "close_loc": _clip((close - lo) / rng if rng > 0 else 0.5, 0.0, 1.0),
    }
    if feat["atr_pctile"] is None:
        return None
    if htf:
        slp = _htf_slope(htf["h4c"], htf["ema"], htf["atr4"], t_known)
        if slp is None:
            return None
        feat["htf_slope"] = _clip(slp, -4.0, 4.0)
    return dict(t=t_known, idx=i, path=path, feat=feat, atr=a, ref=close,
                open_ms=win[0][0], end_ms=win[-1][0])


def distance(fp_a, fp_b, path_weight=PATH_WEIGHT, feat_weights=None):
    """Z-normalised Euclidean on the path plus weighted squared feature differences."""
    feat_weights = feat_weights or FEAT_WEIGHTS
    pa, pb = fp_a["path"], fp_b["path"]
    if len(pa) != len(pb):
        raise ValueError("fingerprints must cover the same number of bars")
    mse = sum((x - y) ** 2 for x, y in zip(pa, pb)) / len(pa)
    acc = path_weight * mse
    for k, w in feat_weights.items():
        acc += w * (fp_a["feat"][k] - fp_b["feat"][k]) ** 2
    return math.sqrt(max(0.0, acc))


def score(dist, tau=SCORE_TAU):
    return 100.0 * math.exp(-dist / tau)


def _htf_ctx(base):
    bstep = _bstep(base)
    h4c = T.complete_bars(T.resample(base, T.H4), T.H4, bstep)
    return dict(h4c=h4c, ema=T.ema_series([b[4] for b in h4c]), atr4=T.atr_series(h4c))


def prepare(base, tf=SIMILAR_TF, interval="15m"):
    zb, step = series(base, tf, interval)
    # known time = open + step for a complete bar
    for i, b in enumerate(zb):
        # keep a uniform known time on the dict we build later via open+step
        pass
    atr = T.atr_series(zb)
    htf = _htf_ctx(base)
    return zb, step, atr, htf


def fingerprint_at(zb, atr, step, i, n, htf=None, **kw):
    fp = fingerprint(zb, atr, i, n, htf=htf, **kw)
    if fp is not None:
        fp["t"] = zb[i][0] + step  # time the last bar is closed / known
    return fp


def setup_index(zb, step, atr, end_ms, entry=None, at_ms=None):
    """Index of the setup moment: an explicit time, else the last bar that traded at the entry price.

    If price is still within 0.5 ATR of the entry, the latest bar is used (the setup is live).
    """
    last = max((j for j, b in enumerate(zb) if b[0] + step <= end_ms), default=-1)
    if last < 0:
        return -1
    if at_ms is not None:
        return max((j for j, b in enumerate(zb) if b[0] + step <= at_ms), default=-1)
    if entry is None:
        return last
    a = atr[last] if last < len(atr) and atr[last] not in (None, 0) else None
    if a and abs(zb[last][4] - entry) <= 0.5 * a:
        return last
    for j in range(last, -1, -1):
        b = zb[j]
        if b[3] <= entry <= b[2]:
            return j
    best, bestd = last, abs(zb[last][4] - entry)
    for j in range(last, -1, -1):
        d = abs(zb[j][4] - entry)
        if d < bestd - 1e-12:
            best, bestd = j, d
    return best


def query_fingerprint(base, tf=SIMILAR_TF, n=SIMILAR_BARS, end_ms=None, interval="15m",
                      entry=None, at_ms=None):
    """Fingerprint of the last N complete bars at the setup moment (entry time/price), not look-ahead."""
    zb, step, atr, htf = prepare(base, tf, interval)
    end_ms = end_ms if end_ms is not None else (zb[-1][0] + step if zb else 0)
    i = setup_index(zb, step, atr, end_ms, entry=entry, at_ms=at_ms)
    if i < 0:
        return None, zb, step, atr, htf
    return fingerprint_at(zb, atr, step, i, n, htf=htf), zb, step, atr, htf


def public_query(fp):
    if not fp:
        return None
    feat = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in (fp.get("feat") or {}).items()}
    return dict(t=fp["t"], idx=fp.get("idx"), ref=fp["ref"], atr=fp["atr"],
                open_ms=fp.get("open_ms"), end_ms=fp.get("end_ms"),
                path=[round(x, 6) for x in fp["path"]], feat=feat)


def search(zb, atr, step, query, n, k=SIMILAR_K, start_ms=None, end_ms=None, htf=None,
           exclude_idx=None, trend_filter=False, side="long"):
    """K nearest non-overlapping windows, excluding the query window and anything after end_ms.

    Overlap rule: two windows may not share any similar-tf bar. Greedy by increasing distance,
    ties broken by earlier time.
    """
    sgn = 1 if side == "long" else -1
    cands = []
    q_idx = query.get("idx") if exclude_idx is None else exclude_idx
    for i in range(n - 1, len(zb)):
        if q_idx is not None and abs(i - q_idx) < n:
            continue  # overlaps the query window (including the query itself)
        t = zb[i][0] + step
        if start_ms and t < start_ms:
            continue
        if end_ms and t > end_ms:
            break
        fp = fingerprint_at(zb, atr, step, i, n, htf=htf)
        if fp is None:
            continue
        if trend_filter:
            slp = fp["feat"]["htf_slope"]
            if slp * sgn <= 0:
                continue
        d = distance(query, fp)
        cands.append((d, t, i, fp))
    cands.sort(key=lambda x: (x[0], x[1]))
    taken, picked = [], []
    for d, t, i, fp in cands:
        if any(abs(i - j) < n for j in taken):
            continue
        taken.append(i)
        picked.append(dict(distance=round(d, 4), score=round(score(d), 2), t=t, idx=i,
                           ref=fp["ref"], atr=fp["atr"], open_ms=fp["open_ms"], end_ms=fp["end_ms"]))
        if len(picked) >= k:
            break
    return picked


def replay_similar(base, g, tf=SIMILAR_TF, n=SIMILAR_BARS, k=SIMILAR_K, expiry_ms=72 * 3600_000,
                   start_ms=None, end_ms=None, fee_pct=0.0, swap_pct_day=0.0, max_hold_ms=None,
                   sweep_window_ms=None, sweep_atr=1.0, trend_filter=False, interval="15m",
                   query=None, exclude_query=True, slip_bps=0.0, slip_abs=0.0, allow_overlap=False, gate=None):
    """Find similar windows and place the setup's ATR geometry at each, oldest first, one trade at a time.

    query: a fingerprint from the source setup (required for other markets; on the source market
    it is built from the latest window if omitted). The query window itself is not traded.
    """
    zb, step, atr, htf = prepare(base, tf, interval)
    if query is None:
        q, _, _, _, _ = query_fingerprint(base, tf, n, end_ms=end_ms, interval=interval)
        query = q
    if query is None:
        return [], 0, [], None
    # identify query idx on this series if the query was built here (same t)
    exclude_idx = None
    if exclude_query and query:
        # only exclude the query window when this series actually contains it (the source market)
        for i, b in enumerate(zb):
            if abs((b[0] + step) - query["t"]) < step // 2:
                exclude_idx = i
                break
    matches = search(zb, atr, step, query, n, k=k, start_ms=start_ms, end_ms=end_ms, htf=htf,
                     exclude_idx=exclude_idx, trend_filter=trend_filter, side=g["side"])
    # replay in chronological order; one order at a time (existing fill rules)
    matches_ch = sorted(matches, key=lambda m: m["t"])
    bt = [b[0] for b in base]
    h4 = T.resample(base, T.H4)
    bstep = _bstep(base)
    h4c = [b for b in h4 if b[5] >= b[0] + T.H4 - bstep]
    atr4 = T.atr_series(h4c)
    close4 = [b[0] + T.H4 for b in h4c]
    trades, busy, skipped = [], 0, 0
    placed_matches = []
    for m in matches_ch:
        t = m["t"]
        if t < busy:
            # a previous similar trade is still live — skip (one at a time)
            m = dict(m, skipped="busy")
            placed_matches.append(m)
            continue
        if gate is not None and not gate.ok(t):
            placed_matches.append(dict(m, skipped="htf"))
            skipped += 1
            continue
        # 4h ATR known at t (last complete 4h bar)
        h = 0
        for j, ct in enumerate(close4):
            if ct <= t:
                h = j
            else:
                break
        a4 = atr4[h] if atr4 and atr4[h] not in (None, 0) else None
        if a4 is None:
            skipped += 1
            continue
        ref = m["ref"]
        geo = T.transfer_geometry(g, ref, a4, g.get("market_id"))
        j = _bisect_left(bt, t)
        if j >= len(base):
            break
        tr, nxt = T.simulate_order(base, j, t, geo["entry"], geo["sl"], geo["tp"], geo["sgn"],
                                   geo["entry_type"], expiry_ms, a4, risk=abs(geo["entry"] - geo["sl"]),
                                   fee_pct=fee_pct, swap_pct_day=swap_pct_day, max_hold_ms=max_hold_ms,
                                   sweep_window_ms=sweep_window_ms, sweep_atr=sweep_atr,
                                   tr=dict(ref=ref, similar_distance=m["distance"], similar_score=m["score"],
                                           similar_t=t), slip_bps=slip_bps, slip_abs=slip_abs)
        trades.append(tr)
        m = dict(m, entry=geo["entry"], sl=geo["sl"], tp=geo["tp"],
                 status=tr["status"], r_net=tr.get("r_net"), exit=tr.get("exit"))
        placed_matches.append(m)
        if allow_overlap:
            continue
        if nxt is None:
            break
        busy = nxt
    return trades, skipped, placed_matches, query


def _bisect_left(xs, t):
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def verdict_qualifies(result):
    """Edge or possible-edge: 15+ closed trades and positive average R after fees."""
    return T.pair_works(result)


def gate_reason(result):
    if verdict_qualifies(result):
        return None
    short = T.short_verdict(result or {})
    if short == "n/a" or not result:
        return "needs a passing edge first"
    return f"needs a passing edge first ({short})"


CRYPTO_SCAN = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "HYPE", "LINK", "AVAX",
               "SUI", "ADA", "UNI", "NEAR", "AAVE", "ONDO", "WLD")


def _in_crypto_core(m, want):
    sym = (m.get("symbol") or "").upper()
    coin = (m.get("coin") or "").upper()
    mid = (m.get("market_id") or "").upper()
    for s in want:
        if sym == s or coin == s or sym.startswith(s) or coin.startswith(s) or f"|{s}" in mid:
            return True
    return False


def family_scan_markets(source, markets):
    """Default live-scan universe: the whole FX / index / commodity family, a liquid crypto core, or the source only."""
    fam = T.market_family(source)
    out, seen = [], set()

    def add(m):
        mid = m["market_id"]
        if mid in seen:
            return
        seen.add(mid)
        out.append(m)

    add(source)
    if fam == "crypto":
        want = set(CRYPTO_SCAN)
        want.add((source.get("symbol") or "").upper())
        want.add((source.get("coin") or "").upper())
        for m in markets:
            if T.market_family(m) == "crypto" and _in_crypto_core(m, want):
                add(m)
        return out
    others = [m for m in markets if T.market_family(m) == fam]
    others.sort(key=lambda m: (T._pref_rank(m, fam), m["symbol"].upper(), m["market_id"]))
    for m in others:
        add(m)
    return out


def resolve_scope(spec, source, markets, per_market=None):
    """family (default), all, passed (markets that passed in a compare/basket), or a comma list."""
    spec = (spec or "family").strip()
    if spec.lower() == "passed" and per_market:
        out = []
        for r in per_market:
            if verdict_qualifies(r.get("result") or {}):
                mid = r["market"]["market_id"]
                try:
                    out.append(T.resolve_market(mid, markets))
                except SystemExit:
                    continue
        return out, "passed"
    if spec.lower() == "family":
        return family_scan_markets(source, markets), "family"
    if spec.lower() == "all":
        return list(markets), "all"
    # comma list (no 5-cap: a scan scope can be a family)
    out, seen = [], set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            m = T.resolve_market(part, markets)
        except SystemExit as e:
            raise ValueError(str(e)) from None
        if m["market_id"] in seen:
            continue
        seen.add(m["market_id"])
        out.append(m)
    if not out:
        raise ValueError("scan scope is empty")
    return out, "list"


def live_signal(fp_now, g_user, atr4, price, flag=None):
    """ATR-scaled limit / SL / single TP from the user's geometry at this market's current price."""
    geo = T.transfer_geometry(g_user, price, atr4)
    dist_px = abs(geo["entry"] - price)
    dist_pct = dist_px / price * 100 if price else 0.0
    dist_atr = dist_px / atr4 if atr4 else None
    through = (price - geo["entry"]) * geo["sgn"] <= 0  # price already at or beyond the entry
    return dict(side=g_user["side"], entry=round(geo["entry"], 6), sl=round(geo["sl"], 6), tp=round(geo["tp"], 6),
                rr=round(geo["rr"], 4), entry_type=geo["entry_type"], price=round(price, 6),
                atr=round(atr4, 6), dist_pct=round(dist_pct, 3), dist_atr=round(dist_atr, 3) if dist_atr is not None else None,
                through_entry=bool(through), flag=flag)


def scan_live(query, g_user, scope_markets, interval="15m", tf=SIMILAR_TF, n=SIMILAR_BARS,
              trend_filter=False, per_market=None, top=SCAN_TOP, refresh=False, end_ms=None):
    """Fingerprint each market's latest window and rank by similarity to the query setup."""
    now = int(time.time() * 1000)
    end_ms = end_ms or now
    step_i = T.STEP[interval]
    end_ms = end_ms - end_ms % step_i
    warm = 14 * 86400_000
    # enough history for ATR percentile lookback on this tf
    hist = max(40, int(ATR_PCT_LOOKBACK * T.STEP[tf] / 86400_000) + 2) * 86400_000
    start = end_ms - hist - warm
    failed = {}
    if per_market:
        for r in per_market:
            mid = r["market"]["market_id"]
            if not verdict_qualifies(r.get("result") or {}):
                failed[mid] = T.short_verdict(r.get("result") or {})
    rows, skipped = [], []
    for m in scope_markets:
        mid = m["market_id"]
        flag = None
        if mid in failed:
            flag = f"backtest: {failed[mid]}"
            # still scan, but flagged (caller may hide); we include them labelled
        try:
            base = T.fetch_candles(m, interval, start, end_ms, refresh=refresh)
        except Exception as e:
            skipped.append(dict(market_id=mid, reason=str(e)))
            continue
        if len(base) < 200:
            skipped.append(dict(market_id=mid, reason=f"not enough history ({len(base)} bars)"))
            continue
        fp, zb, step, atr, htf = query_fingerprint(base, tf, n, end_ms=end_ms, interval=interval)
        if fp is None:
            skipped.append(dict(market_id=mid, reason="could not fingerprint the latest window"))
            continue
        if trend_filter:
            sgn = 1 if g_user["side"] == "long" else -1
            if fp["feat"]["htf_slope"] * sgn <= 0:
                skipped.append(dict(market_id=mid, reason="higher-timeframe trend disagrees"))
                continue
        d = distance(query, fp)
        bstep = _bstep(base)
        h4c = [b for b in T.resample(base, T.H4) if b[5] >= b[0] + T.H4 - bstep]
        a4 = T.atr_series(h4c)[-1]
        if a4 in (None, 0):
            skipped.append(dict(market_id=mid, reason="no 4h ATR"))
            continue
        price = base[-1][4]
        sig = live_signal(fp, g_user, a4, price, flag=flag)
        prov = T.LAST_PROVENANCE.get((mid, interval)) or {}
        src = "MFP"
        segs = prov.get("segments") or []
        if segs:
            lab = (segs[-1].get("label") or segs[-1].get("source") or "MFP").split(":")[0]
            src = "MFP" if lab.lower() == "mfp" else lab
        rows.append(dict(market_id=mid, symbol=m.get("symbol") or mid.split("|")[-1],
                         coin=m.get("coin"), family=T.market_family(m),
                         distance=round(d, 4), score=round(score(d), 2),
                         scanned_at_ms=fp["t"], data_source=src, **sig))
    rows.sort(key=lambda r: (r["distance"], r["market_id"]))
    return dict(rows=rows[:top], all_rows=rows, skipped=skipped, scanned=len(scope_markets) - len(skipped),
                considered=len(scope_markets), generated_ms=now, generated=T.fmt_ts(now),
                query_t=query.get("t") if query else None)


def similar_matches_lines(matches, limit=20):
    if not matches:
        return ["No similar past windows were found in the test range."]
    L = [f"Top similar moments (up to {limit}, scored after non-overlap; times in WAT):"]
    L.append(f"  {'when':<18} {'score':>6} {'dist':>6} {'status':<14} {'R':>7}")
    shown = 0
    for m in sorted(matches, key=lambda x: (x.get("distance", 9), x.get("t", 0))):
        if m.get("skipped"):
            continue
        L.append(f"  {T.fmt_ts(m['t']):<18} {m['score']:>6.1f} {m['distance']:>6.3f} "
                 f"{(m.get('status') or '-'):<14} "
                 f"{'' if m.get('r_net') is None else f'{m['r_net']:+.2f}R':>7}")
        shown += 1
        if shown >= limit:
            break
    return L


def scan_report(scan, forced=False, reason=None):
    L = [f"LIVE SCAN (similar price action), {scan.get('generated', '')}."]
    if forced:
        L.append("FORCED SCAN: the backtest did not pass"
                 + (f" ({reason})" if reason else "")
                 + ". This is not a qualified edge. " + DISCLAIMER)
    else:
        L.append("Scan ran because the backtest passed (edge or possible edge). " + DISCLAIMER)
    L.append(f"Looked at {scan['considered']} markets, fingerprinted {scan['scanned']}. "
             f"Showing the {len(scan['rows'])} closest.")
    if not scan["rows"]:
        L.append("No live matches.")
    else:
        L.append(f"  {'pair':<22} {'side':<6} {'limit':>10} {'TP':>10} {'SL':>10} {'score':>6}  from price")
        for r in scan["rows"]:
            L.append(f"  {r['market_id']:<22} {r['side'].upper():<6} {r['entry']:>10.5g} {r['tp']:>10.5g} "
                     f"{r['sl']:>10.5g} {r['score']:>6.1f}  {r['dist_pct']:.2f}% / {r['dist_atr']} ATR"
                     + ("  [already through entry]" if r.get("through_entry") else "")
                     + (f"  ({r['flag']})" if r.get("flag") else ""))
    if scan.get("skipped"):
        L.append(f"Skipped {len(scan['skipped'])} markets (no history or no fingerprint).")
    L.append(DISCLAIMER)
    return "\n".join(L)
