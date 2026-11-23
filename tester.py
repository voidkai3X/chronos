#!/usr/bin/env python3
"""CHRONOS: replay one setup's geometry across recent MyFundedPerps candles.

Read-only. Uses only the public, keyless MFP market-data WebSocket. Never places orders.

  python tester.py BTC long --entry 81000 --tp 83500 --sl 80200 [--days 60] [--interval 15m] [--expiry 72h]
  python tester.py XAUUSDT short --entry +0.5% --tp 3R --sl +1.2%      (percent / R form)

Levels: --entry  price | +-x% from current price
        --sl     price | +-x% from entry
        --tp     price | +-x% from entry | NR (N times the risk)
"""
import argparse, asyncio, json, math, os, pickle, statistics, sys, time, urllib.request

WS_URL = "wss://api-stream.myfundedperpetuals.com/v1/market-data"
MARKETS_URL = "https://developers.myfundedperpetuals.com/v1/markets"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "candle_cache")
STEP = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
H4 = STEP["4h"]
CHUNK = 900  # MFP history skips bars past ~1000-1500 per request, so 900-bar chunks

# MFP standard commissions and daily swap (docs.myfundedperpetuals.com/guides/commissions-and-fees.md)
FEES = {"crypto": (0.03, 0.03), "forex": (0.0025, 0.005), "tradfi": (0.005, 0.015)}  # (% per fill, % per day swap)
# Binance-listed MFP markets that are stocks, commodities or indices (tradfi fee tier). Checked against /v1/markets 2026-11-23.
TRADFI_COINS = {"XAUUSDT", "XAGUSDT", "XPDUSDT", "XPTUSDT", "CLUSDT", "COPPERUSDT", "NATGASUSDT", "AMDUSDT", "AVGOUSDT",
                "BABAUSDT", "BMNRUSDT", "COINUSDT", "CRCLUSDT", "CRWVUSDT", "DRAMUSDT", "GOOGLUSDT", "HOODUSDT", "INTCUSDT",
                "METAUSDT", "MRVLUSDT", "MSTRUSDT", "MUUSDT", "NBISUSDT", "NVDAUSDT", "ORCLUSDT", "PLTRUSDT", "QQQUSDT",
                "SAMSUNGUSDT", "SKHYNIXUSDT", "SNDKUSDT", "SPCXUSDT", "TSLAUSDT", "TSMUSDT", "WDCUSDT"}


# ---------------------------------------------------------------- data
def load_markets():
    path = os.path.join(CACHE_DIR, "markets.json")
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        if time.time() - os.path.getmtime(path) < 6 * 3600:
            return json.load(open(path))
    except OSError:
        pass
    with urllib.request.urlopen(MARKETS_URL, timeout=20) as r:
        data = json.load(r)["data"]
    json.dump(data, open(path, "w"))
    return data


def resolve_market(name, markets):
    n = name.strip().upper()
    cands = []
    for m in markets:
        coin, sym, mid = m["coin"].upper(), m["symbol"].upper(), m["market_id"].upper()
        if n in (coin, sym, mid) or n + "USDT" == coin or "XYZ:" + n == coin:
            cands.append(m)
    if not cands:
        raise SystemExit(f"market '{name}' not found in MFP /v1/markets ({len(markets)} live)")
    cands.sort(key=lambda m: (not m.get("default_venue", True), m["coin"].upper() != n))
    return cands[0]


def asset_class(m):
    if m["provider"] == "veranta":
        return "forex"
    if m["provider"] in ("hyperliquid", "ostium") or m["coin"].upper() in TRADFI_COINS:
        return "tradfi"
    return "crypto"


async def _fetch_chunks(provider, coin, interval, windows):
    import websockets
    out, sem = {}, asyncio.Semaphore(4)  # MFP: max 4 concurrent history requests
    async with websockets.connect(WS_URL, compression="deflate", open_timeout=15, max_size=2**25) as ws:
        pending = {}

        async def reader():
            while True:
                f = json.loads(await asyncio.wait_for(ws.recv(), 30))
                fut = pending.pop(f.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(f)

        async def one(i, s, e):
            async with sem:
                fut = asyncio.get_event_loop().create_future()
                pending[i] = fut
                await ws.send(json.dumps({"op": "req", "id": i, "method": "candles.history",
                                          "payload": {"provider": provider, "symbol": coin, "interval": interval,
                                                      "limit": 1000, "startTime": s, "endTime": e}}))
                f = await asyncio.wait_for(fut, 40)
                if f.get("op") == "err":
                    raise RuntimeError(f"MFP error: {f}")
                for x in f.get("result") or []:
                    if x.get("isFinal", True):
                        out[int(x["openTime"])] = (int(x["openTime"]), float(x["open"]), float(x["high"]),
                                                   float(x["low"]), float(x["close"]))

        rd = asyncio.create_task(reader())
        await asyncio.gather(*(one(i, s, e) for i, (s, e) in enumerate(windows, 1)))
        rd.cancel()
    return out


def fetch_candles(m, interval, start_ms, end_ms, refresh=False):
    """Chunked, cached fetch of final candles [start_ms, end_ms)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = f"{m['provider']}_{m['coin'].replace(':', '-')}_{interval}.pkl"
    path = os.path.join(CACHE_DIR, key)
    have = {}
    if not refresh and os.path.exists(path):
        have = pickle.load(open(path, "rb"))
    step = STEP[interval]
    w = CHUNK * step
    windows, c = [], start_ms
    first, last = (min(have), max(have)) if have else (None, None)
    while c < end_ms:
        e = min(end_ms, c + w)
        # skip chunks fully inside cached range (keep last 2 bars fresh)
        if not (have and c >= first and e <= last - step):
            windows.append((c, e))
        c = e
    if windows:
        have.update(asyncio.run(_fetch_chunks(m["provider"], m["coin"], interval, windows)))
        pickle.dump(have, open(path, "wb"))
    return [have[k] for k in sorted(have) if start_ms <= k < end_ms]


def resample(bars, step_ms):
    """Aggregate base bars into step_ms bars aligned to UTC epoch. Returns (openTime,o,h,l,c,complete)."""
    out = {}
    for t, o, h, l, c in bars:
        k = t - t % step_ms
        if k not in out:
            out[k] = [k, o, h, l, c, t]
        else:
            b = out[k]
            b[2], b[3], b[4], b[5] = max(b[2], h), min(b[3], l), c, t
    return [tuple(out[k]) for k in sorted(out)]


# ---------------------------------------------------------------- indicators (closed bars only)
def atr_series(b, n=14):
    tr, out = [], []
    for i, x in enumerate(b):
        pc = b[i - 1][4] if i else x[4]
        tr.append(max(x[2] - x[3], abs(x[2] - pc), abs(x[3] - pc)))
        if i + 1 < n:
            out.append(None)
        elif i + 1 == n:
            out.append(sum(tr) / n)
        else:
            out.append((out[-1] * (n - 1) + tr[-1]) / n)
    return out


def ema_series(vals, n=50):
    k, out, e = 2 / (n + 1), [], None
    for i, v in enumerate(vals):
        if i + 1 < n:
            out.append(None)
            continue
        e = sum(vals[:n]) / n if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


# ---------------------------------------------------------------- setup parsing
def parse_level(s, base, risk=None):
    s = str(s).strip()
    if s.upper().endswith("R"):
        if risk is None:
            raise SystemExit("R form only allowed for --tp")
        return ("R", float(s[:-1]))
    if s.endswith("%"):
        return ("price", base * (1 + float(s[:-1]) / 100))
    return ("price", float(s))


def build_geometry(side, entry, tp, sl, ref, atr):
    sgn = 1 if side == "long" else -1
    risk = (entry - sl) * sgn
    reward = (tp - entry) * sgn
    if risk <= 0:
        raise SystemExit("SL must be on the losing side of entry")
    if reward <= 0:
        raise SystemExit("TP must be on the winning side of entry")
    entry_type = "limit" if (entry - ref) * sgn < 0 else ("market" if entry == ref else "stop")
    return dict(side=side, sgn=sgn, entry=entry, tp=tp, sl=sl, ref=ref, atr=atr, rr=reward / risk,
                entry_type=entry_type,
                # relative geometry
                entry_atr=(entry - ref) / atr, risk_atr=risk / atr, reward_atr=reward / atr,
                entry_pct=(entry / ref - 1) * 100, risk_pct=risk / entry * 100, reward_pct=reward / entry * 100)


# ---------------------------------------------------------------- replay
def replay(base, h4, g, mode="atr", expiry_ms=72 * 3600_000, step_ms=H4, trend_filter=False,
           sweep_window_ms=None, sweep_atr=1.0, fee_pct=0.0, swap_pct_day=0.0, start_ms=None, max_hold_ms=None):
    """Walk forward through history. At each eligible 4h close (t) with no open order/trade, place the setup
    using only info known at t (last closed 4h close as reference, ATR/EMA from closed 4h bars), then
    simulate it on base bars with openTime >= t. Conservative fills: SL wins any same-bar tie."""
    sgn = g["sgn"]
    bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51]))
    h4c = [b for b in h4 if b[5] >= b[0] + H4 - bstep]  # complete 4h bars only
    atr = atr_series(h4c)
    ema = ema_series([b[4] for b in h4c])
    close_t = [b[0] + H4 for b in h4c]  # time the 4h bar is closed/known
    bt = [b[0] for b in base]
    import bisect
    sweep_window_ms = sweep_window_ms or expiry_ms
    trades, busy_until, skipped_trend = [], 0, 0
    for i, t in enumerate(close_t):
        if start_ms and t < start_ms:
            continue
        if t < busy_until or (t % step_ms):
            continue
        if atr[i] is None or (trend_filter and (i < 5 or ema[i - 5] is None)):
            continue
        if trend_filter and (ema[i] - ema[i - 5]) * sgn <= 0:
            skipped_trend += 1
            continue
        ref = h4c[i][4]
        if mode == "atr":
            a = atr[i]
            entry = ref + g["entry_atr"] * a
            risk = g["risk_atr"] * a
            reward = g["reward_atr"] * a
        else:
            entry = ref * (1 + g["entry_pct"] / 100)
            risk = entry * g["risk_pct"] / 100
            reward = entry * g["reward_pct"] / 100
        sl, tp = entry - sgn * risk, entry + sgn * reward
        j = bisect.bisect_left(bt, t)
        if j >= len(base):
            break
        tr = dict(placed=t, ref=ref, entry=entry, sl=sl, tp=tp, status="unfilled")
        # ---- fill
        fill_j = fill_px = None
        k = j
        while k < len(base) and base[k][0] < t + expiry_ms:
            _, o, h, l, c = base[k]
            if g["entry_type"] == "market":
                fill_j, fill_px = k, o
            elif g["entry_type"] == "limit":
                if (sgn == 1 and l <= entry) or (sgn == -1 and h >= entry):
                    fill_j, fill_px = k, (min(o, entry) if sgn == 1 else max(o, entry))
            else:  # stop entry
                if (sgn == 1 and h >= entry) or (sgn == -1 and l <= entry):
                    fill_j, fill_px = k, (max(o, entry) if sgn == 1 else min(o, entry))
            if fill_j is not None:
                break
            k += 1
        if fill_j is None:
            if k >= len(base):
                tr["status"] = "pending_at_end"
                trades.append(tr)
                break
            tr["status"] = "unfilled"
            busy_until = t + expiry_ms
            trades.append(tr)
            continue
        tr["filled"] = base[fill_j][0]
        tr["fill_px"] = fill_px
        # ---- manage
        exit_j = exit_px = None
        res = None
        for k in range(fill_j, len(base)):
            _, o, h, l, c = base[k]
            if max_hold_ms and base[k][0] >= tr["filled"] + max_hold_ms:
                exit_j, exit_px, res = k, o, "time"
                break
            hit_sl = (l <= sl) if sgn == 1 else (h >= sl)
            hit_tp = (h >= tp) if sgn == 1 else (l <= tp)
            if k == fill_j:
                # fill bar: intrabar order unknown -> SL counts, TP does not (conservative)
                if hit_sl:
                    exit_j, exit_px, res = k, sl, "sl"
                    break
                continue
            if hit_sl:  # includes TP+SL same bar -> SL
                gap = (o < sl) if sgn == 1 else (o > sl)
                exit_j, exit_px, res = k, (o if gap else sl), "sl"
                break
            if hit_tp:
                exit_j, exit_px, res = k, tp, "tp"
                break
        if res is None:
            tr["status"] = "open_at_end"
            trades.append(tr)
            break
        exit_t = base[exit_j][0] + (base[1][0] - base[0][0])
        hours = max(0, (exit_t - tr["filled"]) / 3600_000)
        gross = (exit_px - fill_px) * sgn / risk
        cost = (fee_pct / 100 * (fill_px + exit_px) + swap_pct_day / 100 / 24 * fill_px * math.floor(hours)) / risk
        tr.update(status=res, exit=exit_t, exit_px=exit_px, hours=round(hours, 2),
                  r_gross=round(gross, 4), r_net=round(gross - cost, 4))
        # ---- stop-too-tight check: after SL, did price reach TP within window (from fill)?
        if res == "sl":
            swept = False
            mae = 0.0
            for k2 in range(exit_j + (0 if exit_j > fill_j else 1), len(base)):
                if base[k2][0] >= tr["filled"] + sweep_window_ms:
                    break
                _, o, h, l, c = base[k2]
                ext = (sl - l) if sgn == 1 else (h - sl)
                if (h >= tp) if sgn == 1 else (l <= tp):
                    swept = True
                    break
                mae = max(mae, ext)
            tr["sl_then_tp"] = swept
            if swept:
                atr_t = risk / g["risk_atr"]  # ATR scale for this trade
                tr["extra_stop_needed_atr"] = round(mae / atr_t, 3)
                # shallow sweep: price went past SL by no more than sweep_atr ATR, then hit TP
                tr["shallow_sweep"] = mae / atr_t <= sweep_atr
        trades.append(tr)
        busy_until = exit_t
    return trades, skipped_trend


def summarize(trades, g, min_samples=20):
    closed = [t for t in trades if t["status"] in ("tp", "sl", "time")]
    fills = [t for t in trades if "filled" in t]
    tp = sum(t["status"] == "tp" for t in closed)
    sl = sum(t["status"] == "sl" for t in closed)
    n = len(closed)
    rs = [t["r_net"] for t in closed]
    rg = [t["r_gross"] for t in closed]
    streak = mx = 0
    eq = peak = dd = 0.0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        mx = max(mx, streak)
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    avg = sum(rs) / n if n else 0.0
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    tstat = avg / (sd / math.sqrt(n)) if n > 1 and sd > 0 else 0.0
    wins = [t["r_net"] for t in closed if t["status"] == "tp"]
    losses = [-t["r_net"] for t in closed if t["status"] == "sl"]
    if wins and losses:  # after fees, from realised TP / SL outcomes
        aw, al = sum(wins) / len(wins), sum(losses) / len(losses)
        be = al / (aw + al)
    else:
        be = 1 / (1 + g["rr"])
    swept = [t for t in closed if t.get("sl_then_tp")]
    shallow = [t for t in swept if t.get("shallow_sweep")]
    half = n // 2
    h1 = sum(rs[:half]) / half if half else None
    h2 = sum(rs[half:]) / (n - half) if n - half else None
    if n < min_samples:
        verdict = f"too few samples ({n} closed trades, need {min_samples})"
    elif avg <= 0:
        verdict = "no edge (expectancy <= 0 after fees)"
    elif tstat < 2:
        verdict = f"possible edge, not proven (expectancy +{avg:.2f}R, t={tstat:.1f}; noise can't be ruled out)"
    else:
        verdict = f"edge on this sample (expectancy +{avg:.2f}R, t={tstat:.1f}); past results only"
    extra = sorted(t["extra_stop_needed_atr"] for t in swept if "extra_stop_needed_atr" in t)
    return dict(
        placed=len(trades), fills=len(fills), unfilled=sum(t["status"] == "unfilled" for t in trades),
        open_at_end=sum(t["status"] in ("open_at_end", "pending_at_end") for t in trades),
        closed=n, tp=tp, sl=sl, time_exits=sum(t["status"] == "time" for t in closed),
        fill_rate=round(len(fills) / len(trades), 3) if trades else None,
        win_rate=round(tp / n, 3) if n else None,
        breakeven_win_rate=round(be, 3), breakeven_win_rate_gross=round(1 / (1 + g["rr"]), 3),
        avg_r_net=round(avg, 3), avg_r_gross=round(sum(rg) / n, 3) if n else 0.0,
        expectancy_r_per_fill=round(avg, 3),
        expectancy_r_per_order=round(sum(rs) / len(trades), 3) if trades else 0.0,
        total_r_net=round(sum(rs), 2), max_losing_streak=mx, max_drawdown_r=round(dd, 2), t_stat=round(tstat, 2),
        first_half_avg_r=round(h1, 3) if h1 is not None else None,
        second_half_avg_r=round(h2, 3) if h2 is not None else None,
        sl_then_tp=len(swept), sl_then_tp_pct_of_losses=round(len(swept) / sl, 3) if sl else None,
        sl_then_tp_pct_of_fills=round(len(swept) / n, 3) if n else None,
        shallow_sweeps=len(shallow), shallow_sweep_pct_of_losses=round(len(shallow) / sl, 3) if sl else None,
        median_extra_stop_atr_for_swept=extra[len(extra) // 2] if extra else None,
        verdict=verdict)


def fmt_ts(ms):
    return time.strftime("%d %b %H:%M", time.gmtime(ms / 1000 + 3600)) + " WAT"


def text_report(out):
    g, s, m = out["setup"], out["result"], out["market"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"
    L = [f"Setup test: {g['side'].upper()} {m['market_id']}  ({out['data']['days']}d of {out['data']['interval']} MFP candles, "
         f"{fmt_ts(out['data']['from_ms'])} to {fmt_ts(out['data']['to_ms'])})",
         f"Setup: entry {g['entry']:g} ({g['entry_type']}), TP {g['tp']:g}, SL {g['sl']:g}, R:R 1:{g['rr']:.2f}. "
         f"Price now {g['ref']:g}, 4h ATR {g['atr']:.4g}.",
         f"Replayed as: entry {g['entry_atr']:+.2f} ATR from price, stop {g['risk_atr']:.2f} ATR ({g['risk_pct']:.2f}%), "
         f"target {g['reward_atr']:.2f} ATR ({g['reward_pct']:.2f}%); scaled by {out['params']['geometry']}. "
         f"New order at each 4h close when flat, expiry {out['params']['expiry']}"
         + (f", time stop {out['params']['max_hold']}" if str(out['params']['max_hold']).lower() != "none" else "")
         + (", only when 4h EMA50 slope agrees" if out['params']['trend_filter'] else "") + ".",
         "",
         f"Orders {s['placed']}: filled {s['fills']}, unfilled {s['unfilled']}, still open at end {s['open_at_end']}.",
         f"Closed {s['closed']}: TP {s['tp']}, SL {s['sl']}" + (f", time exits {s['time_exits']}" if s['time_exits'] else "") + ".",
         f"Win rate {pct(s['win_rate'])} vs break-even {pct(s['breakeven_win_rate'])} (after fees; {pct(s['breakeven_win_rate_gross'])} before).",
         f"Average R {s['avg_r_net']:+.2f} net ({s['avg_r_gross']:+.2f} gross). Total {s['total_r_net']:+.1f}R. "
         f"Per order placed {s['expectancy_r_per_order']:+.2f}R.",
         f"Worst losing streak {s['max_losing_streak']}. Worst drawdown {s['max_drawdown_r']:.1f}R. "
         f"First half {s['first_half_avg_r'] if s['first_half_avg_r'] is not None else 'n/a'}R, second half {s['second_half_avg_r'] if s['second_half_avg_r'] is not None else 'n/a'}R avg.",
         f"Stop check: {s['sl_then_tp']} of {s['sl']} stop-outs ({pct(s['sl_then_tp_pct_of_losses'])}) still reached TP within "
         f"{out['params']['sweep_window']} of the fill; {s['shallow_sweeps']} of them ({pct(s['shallow_sweep_pct_of_losses'])}) "
         f"only poked {out['params']['sweep_atr']} ATR or less past the stop first."]
    if s["shallow_sweep_pct_of_losses"] and s["sl"] >= 5 and s["shallow_sweep_pct_of_losses"] >= 0.2:
        L.append("  -> Stop looks too tight: a good share of losers were wicks through the stop before the move.")
    if out["params"]["trend_filter"]:
        L.append(f"Trend filter skipped {out['skipped_by_trend']} eligible 4h closes. Trend now: {out['trend_now']}.")
    if abs(g["entry_atr"]) > 3:
        L.append(f"Warning: entry is {abs(g['entry_atr']):.1f} ATR from current price, so most replays will not fill. Check the levels.")
    L += ["", f"Verdict: {s['verdict']}.",
          f"Fees used: {out['fees']['commission_pct_per_fill']}% per fill + {out['fees']['swap_pct_per_day']}%/day swap ({out['fees']['asset_class']}, MFP standard).",
          "Backtest on past candles only. Not a prediction, not advice."]
    return "\n".join(L)


def parse_dur(s):
    s = s.strip().lower()
    mult = {"m": 60_000, "h": 3600_000, "d": 86400_000}
    return int(float(s[:-1]) * mult[s[-1]])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a setup's geometry over recent MFP candles (read-only).")
    ap.add_argument("market")
    ap.add_argument("side", choices=["long", "short"])
    ap.add_argument("--entry", required=True, help="price, or +-x%% from current price")
    ap.add_argument("--tp", required=True, help="price, +-x%% from entry, or NR")
    ap.add_argument("--sl", required=True, help="price or +-x%% from entry")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--interval", default="15m", choices=["5m", "15m", "30m", "1h"])
    ap.add_argument("--expiry", default="72h", help="cancel unfilled order after (e.g. 24h, 3d)")
    ap.add_argument("--max-hold", default="7d", help="time stop after fill, exit at market (default 7d; 'none' to hold until TP/SL)")
    ap.add_argument("--geometry", choices=["atr", "pct"], default="atr", help="scale distances by 4h ATR (default) or fixed %%")
    ap.add_argument("--trend-filter", action="store_true", help="only place when 4h EMA50 slope agrees with side")
    ap.add_argument("--sweep-window", default=None, help="window after fill for the stop check (default = expiry)")
    ap.add_argument("--sweep-atr", type=float, default=1.0, help="max ATR past the stop that still counts as a wick sweep")
    ap.add_argument("--ref", type=float, help="override current price")
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="ignore candle cache")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--json-out", help="also write JSON (with trade list) to this path")
    a = ap.parse_args(argv)

    m = resolve_market(a.market, load_markets())
    step = STEP[a.interval]
    now = int(time.time() * 1000)
    end_ms = now - now % step
    warm = 14 * 86400_000  # 4h EMA50 + ATR warm-up (84 4h bars)
    start_ms = end_ms - a.days * 86400_000
    base = fetch_candles(m, a.interval, start_ms - warm, end_ms, refresh=a.refresh)
    if len(base) < 200:
        raise SystemExit(f"not enough MFP history for {m['market_id']} ({len(base)} bars)")
    h4 = resample(base, H4)
    h4c = [b for b in h4 if b[5] >= b[0] + H4 - step]
    atr_now = atr_series(h4c)[-1]
    ema = ema_series([b[4] for b in h4c])
    ref = a.ref or base[-1][4]
    e = parse_level(a.entry, ref)[1]
    s_ = parse_level(a.sl, e)[1]
    tpk, tpv = parse_level(a.tp, e, risk=True)
    tp = e + (1 if a.side == "long" else -1) * tpv * abs(e - s_) if tpk == "R" else tpv
    g = build_geometry(a.side, e, tp, s_, ref, atr_now)
    cls = asset_class(m)
    fee, swap = (0.0, 0.0) if a.no_fees else FEES[cls]
    expiry = parse_dur(a.expiry)
    trades, skipped = replay(base, h4, g, mode=a.geometry, expiry_ms=expiry, trend_filter=a.trend_filter,
                             fee_pct=fee, swap_pct_day=swap, start_ms=start_ms, sweep_atr=a.sweep_atr,
                             sweep_window_ms=parse_dur(a.sweep_window) if a.sweep_window else None,
                             max_hold_ms=None if str(a.max_hold).lower() == "none" else parse_dur(a.max_hold))
    s = summarize(trades, g)
    first_real = trades[0]["placed"] if trades else start_ms
    trend_now = None
    if ema[-1] is not None and ema[-6] is not None:
        trend_now = "up" if ema[-1] > ema[-6] else "down"
    out = dict(
        tool="mfp-setup-tester", generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        market=dict(market_id=m["market_id"], coin=m["coin"], provider=m["provider"]),
        setup={k: (round(v, 6) if isinstance(v, float) else v) for k, v in g.items()},
        params=dict(geometry=a.geometry, expiry=a.expiry, sweep_window=a.sweep_window or a.expiry, sweep_atr=a.sweep_atr, max_hold=a.max_hold, trend_filter=a.trend_filter,
                    placement="every 4h close when flat (one order/trade at a time)",
                    tie_rule="TP and SL in same bar = SL; TP not counted on the fill bar; gaps through SL fill at open"),
        data=dict(source=WS_URL, interval=a.interval, days=a.days, bars=len(base),
                  from_ms=first_real, to_ms=end_ms, first_bar_ms=base[0][0]),
        fees=dict(asset_class=cls, commission_pct_per_fill=fee, swap_pct_per_day=swap),
        skipped_by_trend=skipped, trend_now=trend_now, result=s)
    if a.json_out:
        json.dump(dict(out, trades=trades), open(a.json_out, "w"), indent=1)
    print(json.dumps(out, indent=1) if a.json else text_report(out))
    return out


if __name__ == "__main__":
    main()
