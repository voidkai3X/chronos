#!/usr/bin/env python3
"""CHRONOS: replay one setup's geometry across recent MyFundedPerps candles.

Read-only. Uses only the public, keyless MFP market-data WebSocket. Never places orders.

  python tester.py BTC long --entry 81000 --tp 83500 --sl 80200 [--days 60] [--interval 15m] [--expiry 72h]
  python tester.py XAUUSDT short --entry +0.5% --tp 3R --sl +1.2%      (percent / R form)
  python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m [--basket fx]

Modes: every-close (default) places the setup's ATR shape at every 4h close when flat.
       pattern only trades retests of fresh supply/demand zones found by rule (see detect_zones).

Levels: --entry  price | +-x% from current price
        --sl     price | +-x% from entry
        --tp     price | +-x% from entry | NR (N times the risk)
"""
import argparse, asyncio, bisect, json, math, os, pickle, statistics, sys, time, urllib.request

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
        tmp = f"{path}.{os.getpid()}.tmp"  # atomic: insights/basket runs may read other markets' caches in parallel
        with open(tmp, "wb") as fh:
            pickle.dump(have, fh)
        os.replace(tmp, path)
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
def simulate_order(base, j, t, entry, sl, tp, sgn, entry_type, expiry_ms, atr_t, risk=None, fee_pct=0.0,
                   swap_pct_day=0.0, max_hold_ms=None, sweep_window_ms=None, sweep_atr=1.0, tr=None):
    """Simulate one order placed at time t (base[j] is the first bar with openTime >= t).
    Returns (trade dict, next_free_time or None if the data ran out). Shared by every mode.
    Conservative: SL wins any same-bar tie, TP not counted on the fill bar, gaps through SL exit at the open."""
    risk = abs(entry - sl) if risk is None else risk
    sweep_window_ms = sweep_window_ms or expiry_ms
    tr = dict(tr or {}, placed=t, entry=entry, sl=sl, tp=tp, status="unfilled")
    # ---- fill
    fill_j = fill_px = None
    k = j
    while k < len(base) and base[k][0] < t + expiry_ms:
        _, o, h, l, c = base[k]
        if entry_type == "market":
            fill_j, fill_px = k, o
        elif entry_type == "limit":
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
            return tr, None
        tr["status"] = "unfilled"
        return tr, t + expiry_ms
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
        return tr, None
    exit_t = base[exit_j][0] + (base[1][0] - base[0][0])
    hours = max(0, (exit_t - tr["filled"]) / 3600_000)
    gross = (exit_px - fill_px) * sgn / risk
    cost = (fee_pct / 100 * (fill_px + exit_px) + swap_pct_day / 100 / 24 * fill_px * math.floor(hours)) / risk
    tr.update(status=res, exit=exit_t, exit_px=exit_px, hours=round(hours, 2),
              r_gross=round(gross, 4), r_net=round(gross - cost, 4))
    # ---- stop-too-tight check: after SL, did price reach TP within window (from fill)?
    # (this check deliberately looks past the exit; it never changes the trade itself)
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
            tr["extra_stop_needed_atr"] = round(mae / atr_t, 3)
            # shallow sweep: price went past SL by no more than sweep_atr ATR, then hit TP
            tr["shallow_sweep"] = mae / atr_t <= sweep_atr
    return tr, exit_t


def replay(base, h4, g, mode="atr", expiry_ms=72 * 3600_000, step_ms=H4, trend_filter=False,
           sweep_window_ms=None, sweep_atr=1.0, fee_pct=0.0, swap_pct_day=0.0, start_ms=None, max_hold_ms=None):
    """Every-close mode. Walk forward through history. At each eligible 4h close (t) with no open order/trade,
    place the setup using only info known at t (last closed 4h close as reference, ATR/EMA from closed 4h bars),
    then simulate it on base bars with openTime >= t. Conservative fills: SL wins any same-bar tie."""
    sgn = g["sgn"]
    bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51]))
    h4c = [b for b in h4 if b[5] >= b[0] + H4 - bstep]  # complete 4h bars only
    atr = atr_series(h4c)
    ema = ema_series([b[4] for b in h4c])
    close_t = [b[0] + H4 for b in h4c]  # time the 4h bar is closed/known
    bt = [b[0] for b in base]
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
        tr, nxt = simulate_order(base, j, t, entry, sl, tp, sgn, g["entry_type"], expiry_ms, risk / g["risk_atr"],
                                 risk=risk, fee_pct=fee_pct, swap_pct_day=swap_pct_day, max_hold_ms=max_hold_ms,
                                 sweep_window_ms=sweep_window_ms, sweep_atr=sweep_atr, tr=dict(ref=ref))
        trades.append(tr)
        if nxt is None:
            break
        busy_until = nxt
    return trades, skipped_trend


# ---------------------------------------------------------------- pattern mode (supply / demand zone retests)
ZONE_DEFAULTS = dict(max_base=6, max_leg=3, base_body_atr=0.5, leg_body_frac=0.5, impulse_atr=1.0, max_zone_atr=3.0)


def complete_bars(bars, step_ms, bstep):
    return [b for b in bars if b[5] >= b[0] + step_ms - bstep]


def detect_zones(zb, atr, side, step_ms, max_base=6, max_leg=3, base_body_atr=0.5, leg_body_frac=0.5,
                 impulse_atr=1.0, max_zone_atr=3.0):
    """Rule-based supply/demand zones on complete zone-timeframe bars zb[(open,o,h,l,c,...)].

    A zone is confirmed at the close of bar i only from bars 0..i (causal):
      - leg: 1..max_leg consecutive candles ending at i, all closing in the trade direction (down for a short),
        each with body >= leg_body_frac of its range; the first leg candle is not itself a small candle;
      - base: the 1..max_base small candles (body <= base_body_atr * ATR) right before the leg;
      - displacement: close of bar i is beyond the base by >= impulse_atr * ATR;
      - zone height (proximal to distal) <= max_zone_atr * ATR.
    Short (supply): proximal = lowest low of the base, distal = highest high of base + first leg candle.
    Long (demand):  proximal = highest high of the base, distal = lowest low of base + first leg candle.
    Each base is used once (first bar at which the leg qualifies)."""
    sgn = 1 if side == "long" else -1
    zones, used = [], set()
    for i in range(len(zb)):
        a = atr[i]
        if a is None:
            continue
        for L in range(1, max_leg + 1):
            s = i - L + 1
            if s < 1:
                break
            leg = zb[s:i + 1]
            if not all((b[4] - b[1]) * sgn > 0 and abs(b[4] - b[1]) >= leg_body_frac * (b[2] - b[3]) > 0 for b in leg):
                break  # longer legs would contain this failing candle too
            if abs(leg[0][4] - leg[0][1]) <= base_body_atr * a:
                continue
            be = s - 1
            if be in used:
                break
            bs = be
            while bs >= 0 and be - bs < max_base and abs(zb[bs][4] - zb[bs][1]) <= base_body_atr * a:
                bs -= 1
            bs += 1
            if bs > be:
                break  # candle before the leg is not a base candle
            base = zb[bs:be + 1]
            if sgn == -1:
                prox = min(b[3] for b in base)
                dist = max(max(b[2] for b in base), leg[0][2])
                disp = prox - zb[i][4]
            else:
                prox = max(b[2] for b in base)
                dist = min(min(b[3] for b in base), leg[0][3])
                disp = zb[i][4] - prox
            if disp < impulse_atr * a or abs(dist - prox) > max_zone_atr * a or dist == prox:
                continue
            used.add(be)
            zones.append(dict(side=side, base_from=zb[bs][0], base_to=zb[be][0], idx=i, known=zb[i][0] + step_ms,
                              proximal=prox, distal=dist, atr=a, height_atr=round(abs(dist - prox) / a, 3),
                              impulse_atr=round(disp / a, 3), base_len=len(base), leg_len=L))
            break
    return zones


def zone_lifecycle(z, base, bt, zb_close, step_ms, max_age_ms, max_touches):
    """Fill in when the zone was touched / invalidated, using bars after it was confirmed.
    touches: base-bar times where price came back into the zone after being outside it.
    dead: time a zone-tf close went through the distal edge (known at that bar's close), or max age."""
    sgn = 1 if z["side"] == "long" else -1
    prox, dist = z["proximal"], z["distal"]
    touches, inside = [], False
    end = z["known"] + max_age_ms
    for k in range(bisect.bisect_left(bt, z["known"]), len(base)):
        t, o, h, l, c = base[k]
        if t >= end:
            break
        hit = (l <= prox) if sgn == 1 else (h >= prox)
        if hit and not inside:
            touches.append(t)
            if len(touches) > max_touches:
                break
        inside = hit
    dead = end
    for t_close, c in zb_close:
        if t_close <= z["known"]:
            continue
        if t_close >= dead:
            break
        if (c < dist) if sgn == 1 else (c > dist):
            dead = t_close
            break
    z["touches"], z["dead"] = touches, dead
    return z


def derive_sl_buffer(zones_all, side, entry, sl, atr_now):
    """User rule: stops beyond the zone, not at the edge. Find the latest detected zone that contains the user's entry
    (entry between the zone edges, or within 0.25 ATR outside the near edge) and measure how far the user's SL sits
    past its far edge, in zone-tf ATR. If no zone matches: the user's risk in ATR minus the median detected zone height
    (so the replayed stop is about as far from the entry as the user's). Only reads the user's own setup."""
    sgn = 1 if side == "long" else -1
    for z in reversed(zones_all):
        lo, hi = min(z["proximal"], z["distal"]), max(z["proximal"], z["distal"])
        lo, hi = (lo - 0.25 * atr_now, hi) if sgn == -1 else (lo, hi + 0.25 * atr_now)
        if lo <= entry <= hi:
            past = (z["distal"] - sl) * sgn  # >0 when SL is beyond the far edge
            return max(0.0, past / atr_now), dict(
                how=f"your SL is {past / atr_now:.2f} ATR past the far edge of the detected zone your entry sits in",
                zone=dict(proximal=z["proximal"], distal=z["distal"], base_from=z["base_from"], known=z["known"]))
    risk_atr = abs(entry - sl) / atr_now
    hs = sorted(z["height_atr"] for z in zones_all) or [0.0]
    med = hs[len(hs) // 2]
    return max(0.0, risk_atr - med), dict(
        how=f"no detected zone contains your entry; your risk {risk_atr:.2f} ATR minus median zone height {med:.2f} ATR")


def replay_pattern(base, g, zone_tf="4h", expiry_ms=30 * 86400_000, max_touches=1, sl_buffer_atr=0.3,
                   trend_filter=False, sweep_window_ms=None, sweep_atr=1.0, fee_pct=0.0, swap_pct_day=0.0,
                   start_ms=None, max_hold_ms=None, zone_max_age_ms=30 * 86400_000, zone_params=None):
    """Pattern mode. At each zone-tf close t when flat, look only at zones confirmed at or before t that are
    still alive at t and have been touched fewer than max_touches times before t. Pick the one nearest to the
    current price (price must still be on the far side of its proximal edge) and rest a limit at its proximal edge:
    SL = distal edge + sl_buffer_atr * ATR (beyond the zone), TP = entry + rr * risk. The limit is re-checked at
    every zone-tf close: it moves if a nearer fresh zone appears, is cancelled if its zone dies or is older than
    expiry_ms. Same fill/exit engine as every-close mode."""
    sgn, side, rr = g["sgn"], g["side"], g["rr"]
    zstep = STEP[zone_tf]
    bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51]))
    if zstep < bstep:
        raise SystemExit(f"--zone-tf {zone_tf} is finer than the candle interval")
    zb = complete_bars(resample(base, zstep), zstep, bstep)
    zatr = atr_series(zb)
    h4c = complete_bars(resample(base, H4), H4, bstep)
    ema = ema_series([b[4] for b in h4c])
    h4_close_t = [b[0] + H4 for b in h4c]
    bt = [b[0] for b in base]
    zones = detect_zones(zb, zatr, side, zstep, **(zone_params or {}))
    zb_close = [(b[0] + zstep, b[4]) for b in zb]
    for z in zones:
        zone_lifecycle(z, base, bt, zb_close, zstep, zone_max_age_ms, max_touches)
    sweep_window_ms = sweep_window_ms or 7 * 86400_000
    trades, busy_until, skipped_trend, traded, dropped = [], 0, 0, set(), set()
    armed = None  # (zone index, time armed, sl, tp, risk, atr) for the resting limit
    def disarm(t_cancel):
        n, t0, sl, tp, risk, a = armed
        z = zones[n]
        trades.append(dict(ref=None, zone=n, zone_known=z["known"], zone_proximal=z["proximal"], zone_distal=z["distal"],
                           atr=a, placed=t0, entry=z["proximal"], sl=sl, tp=tp, status="unfilled", cancelled=t_cancel))
    for i, b in enumerate(zb):
        t = b[0] + zstep  # this bar is closed and known at t
        if (start_ms and t < start_ms) or t < busy_until or zatr[i] is None:
            continue
        ref = b[4]
        if armed and t - armed[1] >= expiry_ms:
            dropped.add(armed[0])
        cands = []
        for n, z in enumerate(zones):
            if z["known"] > t:
                break  # zones are in confirmation order; nothing later is known yet
            if n in dropped or z["dead"] <= t or sum(1 for x in z["touches"] if x < t) >= max_touches:
                continue
            if (ref - z["proximal"]) * sgn <= 0:
                continue  # price is not on the far side of the zone
            cands.append((abs(ref - z["proximal"]), n))
        ok_trend = True
        if cands and trend_filter:
            h = bisect.bisect_right(h4_close_t, t) - 1  # last 4h bar closed at or before t
            ok_trend = h >= 5 and ema[h] is not None and ema[h - 5] is not None and (ema[h] - ema[h - 5]) * sgn > 0
            if not ok_trend:
                skipped_trend += 1
        n = min(cands)[1] if cands and ok_trend else None
        if armed and armed[0] != n:
            disarm(t)
            armed = None
        if n is None:
            continue
        z = zones[n]
        if armed is None:  # SL / TP fixed when the limit is first placed on this zone
            a = zatr[i]
            sl = z["distal"] - sgn * sl_buffer_atr * a
            risk = (z["proximal"] - sl) * sgn
            armed = (n, t, sl, z["proximal"] + sgn * rr * risk, risk, a)
        _, t0, sl, tp, risk, a = armed
        j = bisect.bisect_left(bt, t)
        if j >= len(base):
            break
        # the limit rests until the next zone-tf close; then the choice of zone is re-checked
        tr, nxt = simulate_order(base, j, t, z["proximal"], sl, tp, sgn, "limit", zstep, a, risk=risk, fee_pct=fee_pct,
                                 swap_pct_day=swap_pct_day, max_hold_ms=max_hold_ms, sweep_window_ms=sweep_window_ms,
                                 sweep_atr=sweep_atr, tr=dict(ref=ref, zone=n, zone_known=z["known"],
                                                              zone_proximal=z["proximal"], zone_distal=z["distal"], atr=a))
        if tr["status"] == "unfilled":
            continue
        tr["placed"] = t0
        trades.append(tr)
        if "filled" in tr:
            traded.add(n)
        armed = None
        if nxt is None:
            break
        busy_until = nxt
    if armed:
        disarm(None)
        trades[-1]["status"] = "pending_at_end"
    in_window = [z for z in zones if not start_ms or z["known"] >= start_ms]
    return trades, skipped_trend, zones, dict(zones_found=len(in_window), zones_found_incl_warmup=len(zones),
                                              zones_traded=len(traded),
                                              zones_touched=sum(1 for z in in_window if z["touches"]),
                                              zones_invalidated=sum(1 for z in in_window if z["dead"] < z["known"] + zone_max_age_ms))


# ---------------------------------------------------------------- triggered entries (sweep / confirm) on zone taps
ENTRY_STYLES = ("limit", "sweep", "confirm")


def replay_pattern_triggered(base, g, entry_style, zone_tf="4h", confirm_tf="15m", sl_buffer_atr=0.3, max_touches=1,
                             trigger_window_ms=None, sweep_min_atr=0.1, sweep_max_atr=1.5, skip_strong_atr=None,
                             swing_n=2, trend_filter=False, sweep_window_ms=None, sweep_atr=1.0, fee_pct=0.0,
                             swap_pct_day=0.0, start_ms=None, max_hold_ms=None, zone_max_age_ms=30 * 86400_000,
                             zone_params=None):
    """Pattern mode with a trigger instead of a blind limit. Same zones, same 15m fill/exit engine, same fees.

    Watch starts when price first taps a fresh zone's near edge (the tap is a base-bar time, known when it happens).
    From then on only confirm-tf candles that have CLOSED are read, one at a time, until a trigger, a cancel, or
    trigger_window_ms after the tap:
      sweep   (short): price trades above the zone's far edge (the swing high the base + leg made, where buy stops
              sit) by >= sweep_min_atr x zone-tf ATR, then a confirm-tf candle closes back below that edge.
              Cancelled if it runs more than sweep_max_atr ATR past the edge (that is a breakout, not a sweep).
      confirm (short): after the tap, a bearish confirm-tf candle closes below the last confirmed confirm-tf swing low
              (a low with swing_n higher lows on each side; confirmed swing_n candles later) = break of structure.
      Long is the mirror image. Entry: market at the open of the first base bar after the trigger candle closes.
      SL: beyond the most extreme price since the tap AND beyond the zone's far edge, plus sl_buffer_atr x ATR.
      TP: entry +- rr x risk (the user's R:R). Cancelled if a zone-tf candle closes through the far edge (zone dead),
      and, with skip_strong_atr, if a zone-tf candle with a body >= skip_strong_atr ATR closes hard against the trade
      between zone confirmation and the trigger. One setup at a time, taps handled in time order."""
    if entry_style not in ("sweep", "confirm"):
        raise ValueError(entry_style)
    sgn, rr = g["sgn"], g["rr"]
    zstep, cstep = STEP[zone_tf], STEP[confirm_tf]
    bstep = min(b2[0] - b1[0] for b1, b2 in zip(base[:50], base[1:51]))
    if zstep < bstep:
        raise SystemExit(f"--zone-tf {zone_tf} is finer than the candle interval")
    if cstep < bstep or cstep > zstep:
        raise SystemExit(f"--confirm-tf {confirm_tf} must be between the candle interval and --zone-tf {zone_tf}")
    zb = complete_bars(resample(base, zstep), zstep, bstep)
    zatr = atr_series(zb)
    zb_close_t = [b[0] + zstep for b in zb]
    cb = complete_bars(resample(base, cstep), cstep, bstep)
    cb_close_t = [b[0] + cstep for b in cb]
    h4c = complete_bars(resample(base, H4), H4, bstep)
    ema = ema_series([b[4] for b in h4c])
    h4_close_t = [b[0] + H4 for b in h4c]
    bt = [b[0] for b in base]
    zones = detect_zones(zb, zatr, g["side"], zstep, **(zone_params or {}))
    zb_close = [(b[0] + zstep, b[4]) for b in zb]
    for z in zones:
        zone_lifecycle(z, base, bt, zb_close, zstep, zone_max_age_ms, max_touches)
    trigger_window_ms = trigger_window_ms or 12 * zstep
    sweep_window_ms = sweep_window_ms or 7 * 86400_000
    cands = sorted((tt, n) for n, z in enumerate(zones) for tt in z["touches"][:max_touches]
                   if tt < z["dead"] and (not start_ms or tt >= start_ms))
    trades, busy_until, skipped_trend, traded = [], 0, 0, set()

    def against(b):  # body size against the trade (bullish body for a short)
        return max(0.0, (b[1] - b[4]) * sgn)

    for tt, n in cands:
        if tt < busy_until or n in traded:
            continue
        z = zones[n]
        h = bisect.bisect_right(zb_close_t, tt) - 1  # last zone-tf bar closed at or before the tap
        if h < 0 or zatr[h] is None:
            continue
        a = zatr[h]
        info = dict(ref=None, zone=n, zone_known=z["known"], zone_proximal=z["proximal"], zone_distal=z["distal"],
                    atr=a, placed=tt, tap=tt, entry_style=entry_style, entry=None, sl=None, tp=None, status="unfilled")
        if trend_filter:
            k4 = bisect.bisect_right(h4_close_t, tt) - 1
            if not (k4 >= 5 and ema[k4] is not None and ema[k4 - 5] is not None and (ema[k4] - ema[k4 - 5]) * sgn > 0):
                skipped_trend += 1
                continue
        # strong candle against the order between zone confirmation and the tap (closed zone-tf bars only)
        if skip_strong_atr:
            k0, k1 = bisect.bisect_right(zb_close_t, z["known"]), bisect.bisect_right(zb_close_t, tt)
            if any(against(zb[k]) >= skip_strong_atr * a for k in range(k0, k1)):
                trades.append(dict(info, reason="strong candle against the trade on the way into the zone"))
                continue
        level = z["distal"]
        ext = None  # most extreme price against the trade since the tap
        swing = None  # last confirmed confirm-tf swing (low for a short, high for a long): (index, price)
        ci = bisect.bisect_right(cb_close_t, tt)  # first confirm-tf candle still open at the tap
        if entry_style == "confirm":  # swings confirmed before the tap candle closes (lookback 4 x swing_n x 5)
            for k in range(max(swing_n, ci - 20 * swing_n), ci):
                p = k - swing_n
                if p - swing_n < 0:
                    continue
                v = cb[p][3] if sgn == -1 else cb[p][2]
                nb = [cb[q][3] if sgn == -1 else cb[q][2] for q in range(p - swing_n, p + swing_n + 1) if q != p]
                if all((v < x) if sgn == -1 else (v > x) for x in nb):
                    swing = (p, v)
        trig = reason = None
        zk = bisect.bisect_right(zb_close_t, tt)  # next zone-tf close to check for a strong candle
        k = ci
        while k < len(cb) and cb_close_t[k] <= tt + trigger_window_ms:
            tclose = cb_close_t[k]
            if tclose >= z["dead"]:
                reason = "zone closed through its far edge before a trigger"
                break
            if skip_strong_atr:
                while zk < len(zb) and zb_close_t[zk] <= tclose:
                    if against(zb[zk]) >= skip_strong_atr * a:
                        reason = "strong candle closed against the trade before a trigger"
                    zk += 1
                if reason:
                    break
            _, o, hi, lo, c = cb[k][:5]
            ext = (hi if ext is None else max(ext, hi)) if sgn == -1 else (lo if ext is None else min(ext, lo))
            if entry_style == "sweep":
                beyond = (ext - level) * -sgn
                if beyond > sweep_max_atr * a:
                    reason = f"ran more than {sweep_max_atr:g} ATR past the far edge (breakout, not a sweep)"
                    break
                if beyond >= sweep_min_atr * a and (c - level) * sgn > 0:  # closed back inside (below the edge for a short)
                    trig = (k, c)
                    break
            else:
                p = k - swing_n  # does this close confirm a new swing?
                if p - swing_n >= 0:
                    v = cb[p][3] if sgn == -1 else cb[p][2]
                    nb = [cb[q][3] if sgn == -1 else cb[q][2] for q in range(p - swing_n, p + swing_n + 1) if q != p]
                    if all((v < x) if sgn == -1 else (v > x) for x in nb):
                        swing = (p, v)
                if swing and swing[0] < k and (c - swing[1]) * sgn > 0 and (c - o) * sgn > 0:
                    trig = (k, c)
                    break
            k += 1
        if trig is None:
            if reason is None and k >= len(cb):
                trades.append(dict(info, status="pending_at_end", reason="data ended while waiting for a trigger"))
                break
            trades.append(dict(info, reason=reason or "no trigger within the trigger window"))
            continue
        k, entry = trig
        te = cb_close_t[k]
        far = max(ext, level) if sgn == -1 else min(ext, level)
        sl = far - sgn * sl_buffer_atr * a
        risk = (entry - sl) * sgn
        if risk <= 0:
            trades.append(dict(info, reason="no room between trigger close and stop"))
            continue
        tp = entry + sgn * rr * risk
        j = bisect.bisect_left(bt, te)
        if j >= len(base):
            trades.append(dict(info, status="pending_at_end", trigger=te, reason="data ended at the trigger"))
            break
        tr, nxt = simulate_order(base, j, te, entry, sl, tp, sgn, "market", bstep, a, risk=risk, fee_pct=fee_pct,
                                 swap_pct_day=swap_pct_day, max_hold_ms=max_hold_ms, sweep_window_ms=sweep_window_ms,
                                 sweep_atr=sweep_atr, tr=dict(info, trigger=te, tap_extreme=ext,
                                                              swing_level=swing[1] if swing and entry_style == "confirm" else None))
        tr["placed"] = tt
        trades.append(tr)
        traded.add(n)
        if nxt is None:
            break
        busy_until = nxt
    in_window = [z for z in zones if not start_ms or z["known"] >= start_ms]
    return trades, skipped_trend, zones, dict(zones_found=len(in_window), zones_found_incl_warmup=len(zones),
                                              zones_traded=len(traded),
                                              zones_touched=sum(1 for z in in_window if z["touches"]),
                                              zones_invalidated=sum(1 for z in in_window if z["dead"] < z["known"] + zone_max_age_ms))


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


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def result_lines(s, p):
    pct = _pct
    L = [f"Orders {s['placed']}: filled {s['fills']}, unfilled {s['unfilled']}, still open at end {s['open_at_end']}.",
         f"Closed {s['closed']}: TP {s['tp']}, SL {s['sl']}" + (f", time exits {s['time_exits']}" if s['time_exits'] else "") + ".",
         f"Win rate {pct(s['win_rate'])} vs break-even {pct(s['breakeven_win_rate'])} (after fees; {pct(s['breakeven_win_rate_gross'])} before).",
         f"Average R {s['avg_r_net']:+.2f} net ({s['avg_r_gross']:+.2f} gross). Total {s['total_r_net']:+.1f}R. "
         f"Per order placed {s['expectancy_r_per_order']:+.2f}R.",
         f"Worst losing streak {s['max_losing_streak']}. Worst drawdown {s['max_drawdown_r']:.1f}R. "
         f"First half {s['first_half_avg_r'] if s['first_half_avg_r'] is not None else 'n/a'}R, second half {s['second_half_avg_r'] if s['second_half_avg_r'] is not None else 'n/a'}R avg.",
         f"Stop check: {s['sl_then_tp']} of {s['sl']} stop-outs ({pct(s['sl_then_tp_pct_of_losses'])}) still reached TP within "
         f"{p['sweep_window']} of the fill; {s['shallow_sweeps']} of them ({pct(s['shallow_sweep_pct_of_losses'])}) "
         f"only poked {p['sweep_atr']} ATR or less past the stop first."]
    if s["shallow_sweep_pct_of_losses"] and s["sl"] >= 5 and s["shallow_sweep_pct_of_losses"] >= 0.2:
        L.append("  -> Stop looks too tight: a good share of losers were wicks through the stop before the move.")
    return L


def replayed_as(g, p, hold=""):
    kind = "supply" if g["side"] == "short" else "demand"
    tail = (f"; zone usable until touched {p['max_touches']}x, closed through, or {p['zone_max_age']} old"
            + (", only when 4h EMA50 slope agrees" if p["trend_filter"] else "") + ".")
    style = p.get("entry_style", "limit")
    if style == "limit":
        return (f"Replayed as: a limit at the near edge of each fresh {kind} zone on "
                f"{p['zone_tf']} candles, SL {p['sl_buffer_atr']:.2f} x {p['zone_tf']} ATR beyond the far edge "
                f"({p['sl_buffer_source']['how']}), TP {g['rr']:.2f}R. Checked at every {p['zone_tf']} close when flat, "
                f"expiry {p['expiry']}{hold}" + tail)
    far, dirn = ("high", "above") if g["side"] == "short" else ("low", "below")
    if style == "sweep":
        how = (f"after price taps a fresh {kind} zone on {p['zone_tf']} candles, wait for it to trade {dirn} the zone's far "
               f"edge by at least {p['sweep_min_atr']:g} ATR (a stop-run) and then for a {p['confirm_tf']} candle to close back "
               f"inside; cancel if it runs more than {p['sweep_max_atr']:g} ATR past the edge")
    else:
        sw = "swing low" if g["side"] == "short" else "swing high"
        how = (f"after price taps a fresh {kind} zone on {p['zone_tf']} candles, wait for a {p['confirm_tf']} candle body to "
               f"close through the last {p['confirm_tf']} {sw} (break of structure)")
    return (f"Replayed as: {how}; enter at market on the next bar. SL {p['sl_buffer_atr']:.2f} x {p['zone_tf']} ATR beyond "
            f"the move's extreme {far} (and beyond the zone), TP {g['rr']:.2f}R. Give up {p['trigger_window']} after the tap"
            + (f"; skip if a {p['zone_tf']} candle body >= {p['skip_strong_atr']:g} ATR closes against the trade"
               if p.get("skip_strong_atr") else "") + f"{hold}" + tail)


def text_report(out):
    g, s, m, p = out["setup"], out["result"], out["market"], out["params"]
    hold = f", time stop {p['max_hold']}" if str(p['max_hold']).lower() != "none" else ""
    head = (f"({out['data']['days']}d of {out['data']['interval']} MFP candles, "
            f"{fmt_ts(out['data']['from_ms'])} to {fmt_ts(out['data']['to_ms'])})")
    if p.get("mode") == "pattern":
        z = out["zones"]
        L = [f"Setup test (PATTERN mode, {p.get('entry_style', 'limit')} entry): {g['side'].upper()} {m['market_id']}  {head}",
             f"Your setup: entry {g['entry']:g}, TP {g['tp']:g}, SL {g['sl']:g}, R:R 1:{g['rr']:.2f}. Price now {g['ref']:g}.",
             replayed_as(g, p, hold),
             f"Zones found in window: {z['zones_found']} (touched {z['zones_touched']}, invalidated {z['zones_invalidated']}); "
             f"zones traded (filled): {z['zones_traded']}.", ""]
    else:
        L = [f"Setup test: {g['side'].upper()} {m['market_id']}  {head}",
             f"Setup: entry {g['entry']:g} ({g['entry_type']}), TP {g['tp']:g}, SL {g['sl']:g}, R:R 1:{g['rr']:.2f}. "
             f"Price now {g['ref']:g}, 4h ATR {g['atr']:.4g}.",
             f"Replayed as: entry {g['entry_atr']:+.2f} ATR from price, stop {g['risk_atr']:.2f} ATR ({g['risk_pct']:.2f}%), "
             f"target {g['reward_atr']:.2f} ATR ({g['reward_pct']:.2f}%); scaled by {p['geometry']}. "
             f"New order at each 4h close when flat, expiry {p['expiry']}" + hold
             + (", only when 4h EMA50 slope agrees" if p['trend_filter'] else "") + ".", ""]
    L += result_lines(s, p)
    if p["trend_filter"]:
        L.append(f"Trend filter skipped {out['skipped_by_trend']} eligible closes. Trend now: {out['trend_now']}.")
    if p.get("mode") != "pattern" and abs(g["entry_atr"]) > 3:
        L.append(f"Warning: entry is {abs(g['entry_atr']):.1f} ATR from current price, so most replays will not fill. Check the levels.")
    L += ["", f"Verdict: {s['verdict']}.",
          f"Fees used: {out['fees']['commission_pct_per_fill']}% per fill + {out['fees']['swap_pct_per_day']}%/day swap ({out['fees']['asset_class']}, MFP standard).",
          "Backtest on past candles only. Not a prediction, not advice."]
    return "\n".join(L)


def basket_report(out):
    p, s = out["params"], out["result"]
    L = [f"BASKET test ({p['mode']} mode" + (f", {p.get('entry_style', 'limit')} entry" if p['mode'] == 'pattern' else "") + f"), {out['setup']['side'].upper()}, {len(out['per_market'])} markets, levels taken from "
         f"{out['market']['market_id']}" + (f", zones on {p['zone_tf']}, SL {p['sl_buffer_atr']:.2f} ATR past the zone, TP {out['setup']['rr']:.2f}R"
                                           if p['mode'] == 'pattern' else "") + ".", ""]
    for r in out["per_market"]:
        x = r["result"]
        zs = f"zones {r['zones']['zones_found']:>3}, " if r.get("zones") else ""
        L.append(f"  {r['market']['market_id']:<18} {zs}orders {x['placed']:>3}, fills {x['fills']:>3}, TP {x['tp']:>2}, SL {x['sl']:>2}, "
                 f"time {x['time_exits']:>2}, win {_pct(x['win_rate']):>4}, avg {x['avg_r_net']:+.2f}R, total {x['total_r_net']:+.1f}R")
    L += ["", "POOLED (all markets, trades in exit-time order):"] + (
        [f"Zones found in window: {out['zones']['zones_found']}, traded (filled) {out['zones']['zones_traded']}."] if out.get("zones") else [])
    L += result_lines(s, p)
    L += ["", f"Verdict (pooled): {s['verdict']}.", "Backtest on past candles only. Not a prediction, not advice."]
    return "\n".join(L)


def parse_dur(s):
    s = s.strip().lower()
    mult = {"m": 60_000, "h": 3600_000, "d": 86400_000}
    return int(float(s[:-1]) * mult[s[-1]])


BASKETS = {"fx": lambda ms: [m for m in ms if m["provider"] == "veranta"]}


def parse_basket(spec, markets):
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        out += BASKETS[part.lower()](markets) if part.lower() in BASKETS else [resolve_market(part, markets)]
    return out


def run_market(m, a, g_user, mode, end_ms, start_ms, warm, expiry, sl_buffer, sl_src, zone_params):
    """Run one market. g_user is the user's setup; other markets get the same shape in ATR units."""
    base = fetch_candles(m, a.interval, start_ms - warm, end_ms, refresh=a.refresh)
    if len(base) < 200:
        raise SystemExit(f"not enough MFP history for {m['market_id']} ({len(base)} bars)")
    step = STEP[a.interval]
    h4 = resample(base, H4)
    h4c = [b for b in h4 if b[5] >= b[0] + H4 - step]
    atr_now = atr_series(h4c)[-1]
    ema = ema_series([b[4] for b in h4c])
    ref = base[-1][4]
    if m["market_id"] == g_user["market_id"]:
        g = dict(g_user)
    else:  # same geometry in 4h-ATR units from this market's own price
        e = ref + g_user["entry_atr"] * atr_now
        g = build_geometry(g_user["side"], e, e + g_user["sgn"] * g_user["reward_atr"] * atr_now,
                           e - g_user["sgn"] * g_user["risk_atr"] * atr_now, ref, atr_now)
        g["market_id"] = m["market_id"]
        if a.ref is not None:
            g["ref"] = ref
    cls = asset_class(m)
    fee, swap = (0.0, 0.0) if a.no_fees else FEES[cls]
    common = dict(fee_pct=fee, swap_pct_day=swap, start_ms=start_ms, sweep_atr=a.sweep_atr,
                  sweep_window_ms=parse_dur(a.sweep_window) if a.sweep_window else None,
                  max_hold_ms=None if str(a.max_hold).lower() == "none" else parse_dur(a.max_hold))
    zstats = zlist = None
    if mode == "pattern":
        style = getattr(a, "entry_style", "limit") or "limit"
        if style == "limit":
            trades, skipped, zones, zstats = replay_pattern(
                base, g, zone_tf=a.zone_tf, expiry_ms=expiry, max_touches=a.max_touches, sl_buffer_atr=sl_buffer,
                trend_filter=a.trend_filter, zone_max_age_ms=parse_dur(a.zone_max_age), zone_params=zone_params, **common)
        else:
            trades, skipped, zones, zstats = replay_pattern_triggered(
                base, g, style, zone_tf=a.zone_tf, confirm_tf=a.confirm_tf, sl_buffer_atr=sl_buffer,
                max_touches=a.max_touches, trigger_window_ms=parse_dur(a.trigger_window) if a.trigger_window else None,
                sweep_min_atr=a.sweep_min_atr, sweep_max_atr=a.sweep_max_atr, skip_strong_atr=a.skip_strong_atr,
                trend_filter=a.trend_filter, zone_max_age_ms=parse_dur(a.zone_max_age), zone_params=zone_params, **common)
        zlist = [dict(known=z["known"], base_from=z["base_from"], proximal=z["proximal"], distal=z["distal"],
                      height_atr=z["height_atr"], impulse_atr=z["impulse_atr"],
                      first_touch=z["touches"][0] if z["touches"] else None,
                      dead=z["dead"]) for z in zones if z["known"] >= start_ms]
    else:
        trades, skipped = replay(base, h4, g, mode=a.geometry, expiry_ms=expiry, trend_filter=a.trend_filter, **common)
    for t in trades:
        t["market"] = m["market_id"]
    s = summarize(trades, g)
    trend_now = None
    if ema[-1] is not None and ema[-6] is not None:
        trend_now = "up" if ema[-1] > ema[-6] else "down"
    out = dict(market=dict(market_id=m["market_id"], coin=m["coin"], provider=m["provider"]),
               setup={k: (round(v, 6) if isinstance(v, float) else v) for k, v in g.items()},
               data=dict(source=WS_URL, interval=a.interval, days=a.days, bars=len(base),
                         from_ms=trades[0]["placed"] if trades else start_ms, to_ms=end_ms, first_bar_ms=base[0][0]),
               fees=dict(asset_class=cls, commission_pct_per_fill=fee, swap_pct_per_day=swap),
               skipped_by_trend=skipped, trend_now=trend_now, result=s)
    if zstats is not None:
        out["zones"] = dict(zstats, list=zlist)
    return out, trades


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a setup over recent MFP candles (read-only).")
    ap.add_argument("market", help="market your levels belong to")
    ap.add_argument("side", choices=["long", "short"])
    ap.add_argument("--entry", required=True, help="price, or +-x%% from current price")
    ap.add_argument("--tp", required=True, help="price, +-x%% from entry, or NR")
    ap.add_argument("--sl", required=True, help="price or +-x%% from entry")
    ap.add_argument("--mode", choices=["every-close", "pattern"], default="every-close",
                    help="every-close (default): the setup shape at every 4h close when flat. "
                         "pattern: only at fresh supply/demand zones found by rule on --zone-tf candles")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--interval", default="15m", choices=["5m", "15m", "30m", "1h"])
    ap.add_argument("--expiry", default=None, help="cancel unfilled order after (default 72h every-close; pattern: max time a limit rests on one zone, default 30d)")
    ap.add_argument("--max-hold", default="7d", help="time stop after fill, exit at market (default 7d; 'none' to hold until TP/SL)")
    ap.add_argument("--geometry", choices=["atr", "pct"], default="atr", help="every-close: scale by 4h ATR (default) or fixed %%")
    ap.add_argument("--trend-filter", action="store_true", help="only place when 4h EMA50 slope agrees with side")
    ap.add_argument("--sweep-window", default=None, help="window after fill for the stop check (default = expiry)")
    ap.add_argument("--sweep-atr", type=float, default=1.0, help="max ATR past the stop that still counts as a wick sweep")
    # pattern mode
    ap.add_argument("--zone-tf", default="4h", choices=["30m", "1h", "4h"], help="pattern: timeframe zones are found on")
    ap.add_argument("--sl-buffer-atr", type=float, default=None,
                    help="pattern: SL distance beyond the zone's far edge in zone-tf ATR (default: derived from your SL)")
    ap.add_argument("--max-touches", type=int, default=1, help="pattern: zone usable until touched this many times (1 = fresh only)")
    ap.add_argument("--zone-max-age", default="30d", help="pattern: drop zones older than this")
    ap.add_argument("--impulse-atr", type=float, default=ZONE_DEFAULTS["impulse_atr"], help="pattern: min move away from the base, in ATR")
    ap.add_argument("--max-base", type=int, default=ZONE_DEFAULTS["max_base"], help="pattern: max base candles (1-6)")
    ap.add_argument("--base-body-atr", type=float, default=ZONE_DEFAULTS["base_body_atr"], help="pattern: max base candle body, in ATR")
    ap.add_argument("--entry-style", choices=list(ENTRY_STYLES), default="limit",
                    help="pattern: limit (blind limit at the zone edge, default), sweep (wait for a stop-run past the "
                         "zone's far edge and a close back inside), confirm (wait for a lower-timeframe break of structure)")
    ap.add_argument("--confirm-tf", default="15m", choices=["5m", "15m", "30m", "1h"],
                    help="sweep/confirm: timeframe whose closes are read for the trigger (>= --interval, <= --zone-tf)")
    ap.add_argument("--trigger-window", default=None, help="sweep/confirm: give up this long after the tap (default 12 zone-tf bars)")
    ap.add_argument("--sweep-min-atr", type=float, default=0.1, help="sweep: min poke past the far edge, zone-tf ATR")
    ap.add_argument("--sweep-max-atr", type=float, default=1.5, help="sweep: more than this past the far edge = breakout, cancel")
    ap.add_argument("--skip-strong-atr", type=float, default=None,
                    help="sweep/confirm: cancel if a zone-tf candle closes against the trade with a body >= this many ATR")
    ap.add_argument("--insights", action="store_true",
                    help="explain the result in plain words and re-run limit / wider SL / sweep / confirm variants to compare")
    ap.add_argument("--basket", "--symbols", dest="basket", default=None,
                    help="also run on these markets and pool them: 'fx' (all MFP FX) and/or a comma list, e.g. fx or EURUSD,GBPUSD")
    ap.add_argument("--ref", type=float, help="override current price")
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="ignore candle cache")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--json-out", help="also write JSON (with trade list) to this path")
    a = ap.parse_args(argv)

    mode = a.mode
    expiry_s = a.expiry or ("30d" if mode == "pattern" else "72h")
    expiry = parse_dur(expiry_s)
    markets = load_markets()
    m = resolve_market(a.market, markets)
    step = STEP[a.interval]
    now = int(time.time() * 1000)
    end_ms = now - now % step
    warm = 14 * 86400_000  # 4h EMA50 + ATR warm-up (84 4h bars)
    start_ms = end_ms - a.days * 86400_000
    base = fetch_candles(m, a.interval, start_ms - warm, end_ms, refresh=a.refresh)
    if len(base) < 200:
        raise SystemExit(f"not enough MFP history for {m['market_id']} ({len(base)} bars)")
    h4c = [b for b in resample(base, H4) if b[5] >= b[0] + H4 - step]
    atr_now = atr_series(h4c)[-1]
    ref = a.ref or base[-1][4]
    e = parse_level(a.entry, ref)[1]
    s_ = parse_level(a.sl, e)[1]
    tpk, tpv = parse_level(a.tp, e, risk=True)
    tp = e + (1 if a.side == "long" else -1) * tpv * abs(e - s_) if tpk == "R" else tpv
    g = build_geometry(a.side, e, tp, s_, ref, atr_now)
    g["market_id"] = m["market_id"]

    zone_params = dict(ZONE_DEFAULTS, impulse_atr=a.impulse_atr, max_base=max(1, min(6, a.max_base)), base_body_atr=a.base_body_atr)
    if mode != "pattern" and a.entry_style != "limit":
        raise SystemExit("--entry-style sweep/confirm needs --mode pattern (they trigger on zone taps)")
    ctx = dict(markets=markets, m=m, g=g, base=base, e=e, sl_user=s_, end_ms=end_ms, start_ms=start_ms, warm=warm,
               zone_params=zone_params)
    sl_buffer, sl_src = None, None
    if mode == "pattern":
        sl_buffer, sl_src, zatr_now = pattern_sl_buffer(a, ctx)
        g["zone_tf_atr_now"] = zatr_now
    out, trades = run_setup(a, mode, ctx, sl_buffer, sl_src, a.basket)
    if a.insights:
        import insights
        out["insights"] = insights.build(a, mode, ctx, out, trades, sl_buffer, sl_src)
        txt = (text_report(out) if "per_market" not in out else basket_report(out)) + "\n\n" + insights.text(out["insights"])
    else:
        txt = text_report(out) if "per_market" not in out else basket_report(out)
    if a.json_out:
        json.dump(dict(out, trades=trades), open(a.json_out, "w"), indent=1)
    print(json.dumps(out, indent=1) if a.json else txt)
    return out


def pattern_sl_buffer(a, ctx):
    """SL distance beyond the zone's far edge (zone-tf ATR): --sl-buffer-atr, or derived from the user's own SL."""
    step = STEP[a.interval]
    zstep = STEP[a.zone_tf]
    zb = complete_bars(resample(ctx["base"], zstep), zstep, step)
    zatr = atr_series(zb)
    if a.sl_buffer_atr is not None:
        return a.sl_buffer_atr, dict(how="set with --sl-buffer-atr"), zatr[-1]
    buf, src = derive_sl_buffer(detect_zones(zb, zatr, a.side, zstep, **ctx["zone_params"]), a.side, ctx["e"],
                                ctx["sl_user"], zatr[-1])
    src["how"] = f"derived from your SL: {src['how']}"
    return buf, src, zatr[-1]


def run_setup(a, mode, ctx, sl_buffer, sl_src, basket):
    """Run the setup on its market (+ basket) with the options in a. Returns (out, trades)."""
    m, g, markets = ctx["m"], ctx["g"], ctx["markets"]
    expiry_s = a.expiry or ("30d" if mode == "pattern" else "72h")
    expiry = parse_dur(expiry_s)
    style = a.entry_style if mode == "pattern" else "limit"
    targets = [m] + [x for x in parse_basket(basket, markets) if x["market_id"] != m["market_id"]]
    runs = []
    for mk in targets:
        try:
            runs.append(run_market(mk, a, g, mode, ctx["end_ms"], ctx["start_ms"], ctx["warm"], expiry, sl_buffer,
                                   sl_src, ctx["zone_params"]))
        except SystemExit as ex:
            if mk is m:
                raise
            print(f"skipped {mk['market_id']}: {ex}", file=sys.stderr)
    params = dict(mode=mode, geometry=a.geometry, expiry=expiry_s,
                  sweep_window=a.sweep_window or (expiry_s if mode != "pattern" else "7d"), sweep_atr=a.sweep_atr,
                  max_hold=a.max_hold, trend_filter=a.trend_filter,
                  tie_rule="TP and SL in same bar = SL; TP not counted on the fill bar; gaps through SL fill at open")
    if mode == "pattern":
        params.update(placement=(f"at each {a.zone_tf} close when flat, limit at the near edge of the nearest fresh zone "
                                 f"(one order/trade at a time)") if style == "limit" else
                      (f"watch starts when price taps a fresh zone; enter at market after a {a.confirm_tf} "
                       + ("sweep of the zone's far edge and a close back inside" if style == "sweep"
                          else "body close through the last swing (break of structure)") + " (one trade at a time)"),
                      entry_style=style, zone_tf=a.zone_tf, sl_buffer_atr=round(sl_buffer, 4), sl_buffer_source=sl_src,
                      max_touches=a.max_touches, zone_max_age=a.zone_max_age, zone_rules=ctx["zone_params"])
        if style != "limit":
            params.update(confirm_tf=a.confirm_tf, trigger_window=a.trigger_window or f"12 x {a.zone_tf}",
                          sweep_min_atr=a.sweep_min_atr, sweep_max_atr=a.sweep_max_atr, skip_strong_atr=a.skip_strong_atr)
    else:
        params.update(placement="every 4h close when flat (one order/trade at a time)")
    head = dict(tool="mfp-setup-tester", generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), params=params)
    if len(runs) == 1:
        out, trades = runs[0]
        out = dict(head, **out)
        out["setup"]["side"] = a.side
    else:
        trades = sorted((t for _, tr in runs for t in tr), key=lambda t: t.get("exit") or t["placed"])
        pooled = summarize(trades, g)
        out = dict(head, market=runs[0][0]["market"], setup=runs[0][0]["setup"], data=runs[0][0]["data"],
                   basket=[r[0]["market"]["market_id"] for r in runs], per_market=[r[0] for r in runs], result=pooled)
        if mode == "pattern":
            out["zones"] = {k: sum(r[0]["zones"][k] for r in runs) for k in ("zones_found", "zones_traded", "zones_touched", "zones_invalidated")}
    return out, trades

if __name__ == "__main__":
    main()
