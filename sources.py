#!/usr/bin/env python3
"""CHRONOS data sources: MFP first, then backups, merged into one 15m (or 5m/30m/1h) OHLC series in UTC.

Bars everywhere are tuples (openTime_ms_utc, open, high, low, close). openTime is a multiple of the interval.

Precedence (user rules): MFP public feed > OKX perp > Bybit perp > (FX / indices / commodities with no perp only)
Yahoo chart API > Dukascopy public datafeed. Never spot. Backups only BACKFILL bars older than the earliest bar of
the higher-precedence data; they never replace or fill holes inside MFP data. No bars are invented: holes stay holes
and are reported.

Each splice is checked: closes of the backup vs the data it extends are compared on their common bars (first 7 days
after the splice point). If the median abs % diff is above the threshold (crypto/tradfi 0.15%, FX 0.05%), the backup is
scaled by the median close ratio (a constant basis adjustment) when that brings it under the threshold and the ratio
is within 5% of 1 (or a known contract multiplier); otherwise the backup is rejected and not used.

  python3 sources.py coverage [--days 90]     # map every MFP market to its backups, probe live, write data_sources.json
  python3 sources.py show EURUSD [--days 90]  # merged provenance for one market
"""
import asyncio, bisect, json, lzma, os, pickle, random, re, statistics, struct, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "candle_cache")
CACHE_VERSION = 2
STEP = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
DAY = 86_400_000
UA = {"User-Agent": "Mozilla/5.0 (CHRONOS backtester; read-only)"}
OVERLAP_CHECK_MS = 7 * DAY
THRESHOLD = {"crypto": 0.0015, "tradfi": 0.0015, "forex": 0.0005}
MAX_BASIS = 0.05
MIN_OVERLAP_BARS = 20
SOURCE_LABEL = {"mfp": "MFP feed", "okx": "OKX perp", "bybit": "Bybit perp", "hyperliquid": "Hyperliquid perp",
                "yahoo": "Yahoo Finance (not a perp)", "dukascopy": "Dukascopy CFD feed (not a perp)"}
BUSY_MSG = "Price data source is busy, try again in a minute"
MFP_CONNECT_TRIES = 5
MFP_MAX_INFLIGHT = 2  # was 4; softer on shared egress IPs (e.g. Render)
RECENT_TTL_S = int(os.environ.get("CHRONOS_CACHE_TTL_S", "180"))  # skip re-fetch of recent bars for a few minutes


class SourceBusy(RuntimeError):
    """The market-data source refused or could not be reached after retries. Safe to show as BUSY_MSG."""


def _exc_status(exc):
    for obj in (exc, getattr(exc, "response", None)):
        if obj is None:
            continue
        for attr in ("status_code", "status"):
            v = getattr(obj, attr, None)
            if isinstance(v, int):
                return v
    m = re.search(r"HTTP\s+(\d{3})", str(exc))
    return int(m.group(1)) if m else None


def _retry_after_header(exc):
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def is_retryable_mfp(exc):
    if not isinstance(exc, BaseException):
        exc = RuntimeError(str(exc))
    if isinstance(exc, SourceBusy):
        return True
    code = _exc_status(exc)
    if code in (408, 425, 429, 500, 502, 503, 504):
        return True
    if code is not None and 400 <= code < 500:
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError, EOFError)):
        return True
    name = type(exc).__name__
    if name in {"InvalidStatus", "InvalidHandshake", "ConnectionClosed", "ConnectionClosedError",
                "PayloadTooBig"}:
        return True
    s = str(exc).lower()
    return any(x in s for x in ("http 429", "rejected websocket", "rate limit", "too many requests",
                                "temporarily unavailable", "connection reset"))


def friendly_source_error(exc_or_text):
    """If this looks like a refused/rate-limited/down market-data source, return the public message; else None."""
    if isinstance(exc_or_text, SourceBusy):
        return BUSY_MSG
    s = str(exc_or_text or "")
    if is_retryable_mfp(exc_or_text if isinstance(exc_or_text, BaseException) else RuntimeError(s)):
        return BUSY_MSG
    low = s.lower()
    if any(x in low for x in ("invalidstatus", "http 429", "rejected websocket", "rate limit",
                              "too many requests", "price data source is busy")):
        return BUSY_MSG
    return None


def backoff_seconds(exc, attempt):
    """Exponential backoff with jitter. Respects Retry-After when the error carries it. attempt is 0-based."""
    delay = min(8.0, 0.6 * (2 ** attempt))
    ra = _retry_after_header(exc)
    if ra is not None:
        delay = max(delay, min(ra, 20.0))
    return delay + random.uniform(0.05, 0.4)


# ---------------------------------------------------------------- http
def http(url, data=None, tries=5, timeout=30, raw=False):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=dict(UA, **({"Content-Type": "application/json"} if data else {})))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return b"" if raw else None
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        time.sleep(min(8, 0.7 * 2 ** i))
    raise RuntimeError(f"{url[:90]}: {last}")


def _closed(t, step, now_ms):
    return t + step <= now_ms


# ---------------------------------------------------------------- adapters: fetch(symbol, interval, start, end) -> {t: bar}
def fetch_mfp(symbol, interval, start, end):
    """symbol = 'provider|coin'. Public keyless WebSocket, 900-bar chunks, one connection, few in flight."""
    provider, coin = symbol.split("|", 1)
    return asyncio.run(_mfp_chunks(provider, coin, interval, _mfp_windows(start, end, interval)))


def _mfp_windows(start, end, interval):
    step, windows, c = STEP[interval], [], start
    while c < end:
        e = min(end, c + 900 * step)
        windows.append((c, e))
        c = e
    return windows


MFP_WS = "wss://api-stream.myfundedperpetuals.com/v1/market-data"


def _mfp_connect_cm(connect_factory=None):
    import websockets
    if connect_factory is not None:
        return connect_factory()
    return websockets.connect(MFP_WS, compression="deflate", open_timeout=15, max_size=2 ** 25)


async def _mfp_chunks_on(ws, provider, coin, interval, windows):
    """Send every history window on one already-open socket. Never opens a second connection."""
    out, sem, pending = {}, asyncio.Semaphore(MFP_MAX_INFLIGHT), {}

    async def reader():
        while True:
            f = json.loads(await asyncio.wait_for(ws.recv(), 30))
            fut = pending.pop(f.get("id"), None)
            if fut and not fut.done():
                fut.set_result(f)

    async def one(i, s, e):
        async with sem:
            fut = asyncio.get_running_loop().create_future()
            pending[i] = fut
            await ws.send(json.dumps({"op": "req", "id": i, "method": "candles.history",
                                      "payload": {"provider": provider, "symbol": coin, "interval": interval,
                                                  "limit": 1000, "startTime": s, "endTime": e}}))
            f = await asyncio.wait_for(fut, 40)
            if f.get("op") == "err":
                raise RuntimeError(f"MFP error: {f}")
            for x in f.get("result") or []:
                t = int(x["openTime"])
                if x.get("isFinal", True) and s <= t < e:
                    out[t] = (t, float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]))

    rd = asyncio.create_task(reader())
    try:  # hard cap so a stalled socket can't hang a run
        await asyncio.wait_for(asyncio.gather(*(one(i, s, e) for i, (s, e) in enumerate(windows, 1))),
                               60 + 15 * len(windows))
    finally:
        rd.cancel()
        try:
            await rd
        except (asyncio.CancelledError, Exception):
            pass
    return out


async def _mfp_chunks(provider, coin, interval, windows, connect_factory=None):
    """Open the MFP socket (retry 429/connect errors with backoff) and fetch every chunk on that one connection."""
    if not windows:
        return {}
    last = None
    for attempt in range(MFP_CONNECT_TRIES):
        cm = _mfp_connect_cm(connect_factory)
        entered = False
        try:
            ws = await cm.__aenter__()
            entered = True
            return await _mfp_chunks_on(ws, provider, coin, interval, windows)
        except Exception as e:
            last = e
            if not is_retryable_mfp(e) or attempt == MFP_CONNECT_TRIES - 1:
                raise SourceBusy(BUSY_MSG) from e
            await asyncio.sleep(backoff_seconds(e, attempt))
        finally:
            if entered:
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass
    raise SourceBusy(BUSY_MSG) from last


OKX_BAR = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H"}


def fetch_okx(symbol, interval, start, end):
    """OKX /market/history-candles, 100 bars per page, paging backwards with `after` (= older than ts)."""
    step, now, out, after = STEP[interval], int(time.time() * 1000), {}, end
    while after > start:
        d = http(f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar={OKX_BAR[interval]}"
                 f"&limit=100&after={after}")
        if not d or d.get("code") != "0":
            raise RuntimeError(f"OKX {symbol}: {d}")
        rows = d["data"]
        if not rows:
            break
        for r in rows:
            t = int(r[0])
            if start <= t < end and r[8] == "1" and _closed(t, step, now):
                out[t] = (t, float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        oldest = min(int(r[0]) for r in rows)
        if oldest >= after:
            break
        after = oldest
        time.sleep(0.11)  # OKX: 20 requests / 2 s
    return out


def fetch_bybit(symbol, interval, start, end):
    """Bybit v5 linear kline, 1000 bars per page, newest first; page by moving `end` back."""
    step, now, out = STEP[interval], int(time.time() * 1000), {}
    iv = str(step // 60_000)
    e = end - 1
    while e >= start:
        d = http(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={iv}"
                 f"&start={start}&end={e}&limit=1000")
        if not d or d.get("retCode") != 0:
            raise RuntimeError(f"Bybit {symbol}: {d}")
        rows = d["result"].get("list") or []
        if not rows:
            break
        for r in rows:
            t = int(r[0])
            if start <= t < end and _closed(t, step, now):
                out[t] = (t, float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        oldest = min(int(r[0]) for r in rows)
        if oldest - 1 >= e:
            break
        e = oldest - 1
        time.sleep(0.1)
    return out


def fetch_hyperliquid(symbol, interval, start, end):
    """Hyperliquid info candleSnapshot (xyz: builder-dex markets too). Server keeps only the latest 5000 candles."""
    step, now, out, s = STEP[interval], int(time.time() * 1000), {}, start
    while s < end:
        d = http("https://api.hyperliquid.xyz/info", data=json.dumps(
            {"type": "candleSnapshot", "req": {"coin": symbol, "interval": interval, "startTime": s, "endTime": end - 1}}).encode())
        if not d:
            break
        for x in d:
            t = int(x["t"])
            if start <= t < end and _closed(t, step, now):
                out[t] = (t, float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]))
        nxt = max(int(x["t"]) for x in d) + step
        if nxt <= s:
            break
        s = nxt
    return out


YAHOO_IV = {"5m": ("5m", 59), "15m": ("15m", 59), "30m": ("30m", 59), "1h": ("60m", 729)}


def fetch_yahoo(symbol, interval, start, end):
    """Yahoo chart API. Intraday limit checked live 2026-11-24: 5m/15m/30m only for the last 60 days (older -> HTTP 422)."""
    iv, max_days = YAHOO_IV[interval]
    step, now = STEP[interval], int(time.time() * 1000)
    lo = max(start, now - max_days * DAY)
    if lo >= end:
        return {}
    d = http(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?interval={iv}"
             f"&period1={lo // 1000}&period2={min(end, now) // 1000}")
    r = (d or {}).get("chart", {}).get("result") or []
    if not r or "timestamp" not in r[0]:
        return {}
    q = r[0]["indicators"]["quote"][0]
    out = {}
    for i, ts in enumerate(r[0]["timestamp"]):
        t = ts * 1000
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or t % step or not (start <= t < end) or not _closed(t, step, now):
            continue  # misaligned or in-progress bars are dropped, never shifted
        out[t] = (t, float(o), float(h), float(l), float(c))
    return out


DUKA_SCALE = {"USDJPY": 1e3}  # FX default 1e5; JPY pairs 1e3; index/commodity CFDs 1e3


def _duka_scale(symbol):
    if symbol in DUKA_SCALE:
        return DUKA_SCALE[symbol]
    if len(symbol) == 6 and symbol.isalpha():
        return 1e3 if symbol.endswith("JPY") else 1e5
    return 1e3


DUKA_BUDGET_S = float(os.environ.get("CHRONOS_DUKA_BUDGET_S", "90"))


def _duka_day(symbol, day_ms):
    """One UTC day of 1-minute candles. Each finished day is cached on its own, so a slow run can resume."""
    dpath = os.path.join(CACHE_DIR, "dukascopy", "days", symbol, f"{day_ms}.pkl")
    try:
        with open(dpath, "rb") as fh:
            return pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError):
        pass
    g = time.gmtime(day_ms / 1000)
    url = (f"https://datafeed.dukascopy.com/datafeed/{symbol}/{g.tm_year}/{g.tm_mon - 1:02d}/{g.tm_mday:02d}/"
           "BID_candles_min_1.bi5")
    raw = http(url, raw=True, tries=3, timeout=20)
    rows = []
    if raw:
        b = lzma.decompress(raw)
        sc = _duka_scale(symbol)
        for i in range(0, len(b) - 23, 24):
            sec, o, c, lo, hi, v = struct.unpack(">iiiiif", b[i:i + 24])
            if v > 0:  # Dukascopy pads closed minutes with flat zero-volume candles: drop them (no invented bars)
                rows.append((day_ms + sec * 1000, o / sc, hi / sc, lo / sc, c / sc))
    _write_cache(dpath, rows)
    return rows


def fetch_dukascopy(symbol, interval, start, end):
    """Dukascopy public datafeed: 1-minute BID candles per UTC day (LZMA), aggregated to the interval.
    Slow and rate-limited from this box (429 / 503 / TLS resets seen 2026-11-24), so each call has a time budget;
    finished days are kept, and the next run continues where this one stopped."""
    step, now = STEP[interval], int(time.time() * 1000)
    days = list(range(start - start % DAY, min(end, now - DAY), DAY))  # whole past days only
    mins, t0 = [], time.time()
    for n, d in enumerate(days):
        if time.time() - t0 > DUKA_BUDGET_S:
            raise RuntimeError(f"Dukascopy {symbol}: time budget {DUKA_BUDGET_S:.0f}s used after {n}/{len(days)} days "
                               "(finished days are cached; run again to continue)")
        mins.extend(_duka_day(symbol, d))  # a day that still fails raises -> range not cached, retried next run
    return aggregate(mins, step, start, end, now)


def aggregate(mins, step, start, end, now):
    out = {}
    for t, o, h, l, c in sorted(mins):
        k = t - t % step
        if not (start <= k < end) or not _closed(k, step, now):
            continue
        b = out.get(k)
        out[k] = (k, o, h, l, c) if b is None else (k, b[1], max(b[2], h), min(b[3], l), c)
    return out


FETCHERS = {"mfp": fetch_mfp, "okx": fetch_okx, "bybit": fetch_bybit, "hyperliquid": fetch_hyperliquid,
            "yahoo": fetch_yahoo, "dukascopy": fetch_dukascopy}


# ---------------------------------------------------------------- cache (atomic, keyed by source)
_locks = {}
_locks_guard = threading.Lock()
_mem_cache = {}  # (source, symbol, interval, start, end) -> (expires_at, bars)
_mem_guard = threading.Lock()


def reset_mem_cache():
    """Test helper."""
    with _mem_guard:
        _mem_cache.clear()


def _mem_get(key):
    with _mem_guard:
        hit = _mem_cache.get(key)
        if hit and hit[0] > time.time():
            return dict(hit[1])
        if hit:
            _mem_cache.pop(key, None)
    return None


def _mem_put(key, bars):
    with _mem_guard:
        _mem_cache[key] = (time.time() + RECENT_TTL_S, dict(bars))
        if len(_mem_cache) > 256:
            now = time.time()
            for k, v in list(_mem_cache.items()):
                if v[0] <= now:
                    _mem_cache.pop(k, None)


def cache_path(source, symbol, interval):
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in symbol)
    return os.path.join(CACHE_DIR, source, f"{safe}_{interval}.pkl")


def _read_cache(path):
    try:
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        if isinstance(d, dict) and d.get("v") == CACHE_VERSION:
            return d
    except (OSError, EOFError, pickle.UnpicklingError, ValueError):
        pass
    return {"v": CACHE_VERSION, "bars": {}, "covered": []}


def _write_cache(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(d, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # readers see the old file or the new one, never half a file


def _merge_ranges(rs):
    out = []
    for s, e in sorted(rs):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _missing(covered, start, end):
    gaps, c = [], start
    for s, e in covered:
        if e <= c or s >= end:
            continue
        if s > c:
            gaps.append((c, s))
        c = max(c, e)
    if c < end:
        gaps.append((c, end))
    return gaps


def _fetch_todos(source, symbol, interval, todo):
    """Fetch every missing range. MFP uses one WebSocket for all of them (no reconnect per gap)."""
    if source == "mfp":
        provider, coin = symbol.split("|", 1)
        windows = []
        for s, e in todo:
            windows.extend(_mfp_windows(s, e, interval))
        if not windows:
            return {}
        return asyncio.run(_mfp_chunks(provider, coin, interval, windows))
    out = {}
    for s, e in todo:
        out.update(FETCHERS[source](symbol, interval, s, e))
    return out


def cached_fetch(source, symbol, interval, start, end, refresh=False):
    """Bars in [start, end) from one source. Requested ranges are remembered (also empty ones) so they are not
    re-requested; ranges near 'now' are never marked covered for long, so the latest bars stay fresh.
    A short-lived in-memory + on-disk TTL (RECENT_TTL_S) serves recent bars without hammering the source
    on repeat runs, compare, or scan."""
    key = (source, symbol, interval, start, end)
    if not refresh:
        hit = _mem_get(key)
        if hit is not None:
            return hit
    path = cache_path(source, symbol, interval)
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        if not refresh:
            hit = _mem_get(key)
            if hit is not None:
                return hit
        d = {"v": CACHE_VERSION, "bars": {}, "covered": []} if refresh else _read_cache(path)
        step, now = STEP[interval], int(time.time() * 1000)
        todo = _missing(d["covered"], start, end)
        # Within the TTL, reuse bars already on disk even for the uncovered 'near now' tail.
        if todo and not refresh and d.get("fresh_until", 0) > time.time():
            have = {t: b for t, b in d["bars"].items() if start <= t < end}
            last_closed = now - now % step - step
            if have and max(have) >= min(end, last_closed) - 2 * step:
                todo = []
        if todo:
            stable = now - now % step - 2 * step
            got = _fetch_todos(source, symbol, interval, todo)
            d["bars"].update(got)
            if got:  # an empty answer may be a hiccup (seen on Yahoo): don't remember it, ask again next run
                new_cov = []
                for s, e in todo:
                    if min(e, stable) > s:
                        new_cov.append([s, min(e, stable)])
                d["covered"] = _merge_ranges(d["covered"] + new_cov)
                d["fresh_until"] = time.time() + RECENT_TTL_S
                _write_cache(path, d)
        result = {t: b for t, b in d["bars"].items() if start <= t < end}
        if result or not todo:
            _mem_put(key, result)
        return result


# ---------------------------------------------------------------- mapping
FX = {"EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"}
# Explicit maps where the ticker alone is ambiguous or no perp exists. Verified live 2026-11-24.
# NOTE: OKX/Bybit "SPX" is the SPX6900 meme coin, not the S&P 500 - never map SP500 to it.
OVERRIDES = {
    "hyperliquid|xyz:AAPL": [("okx", "AAPL-USDT-SWAP"), ("bybit", "AAPLUSDT")],
    "hyperliquid|xyz:AMZN": [("okx", "AMZN-USDT-SWAP"), ("bybit", "AMZNUSDT")],
    "hyperliquid|xyz:MSFT": [("okx", "MSFT-USDT-SWAP"), ("bybit", "MSFTUSDT")],
    "hyperliquid|xyz:JP225": [("okx", "JP225-USDT-SWAP"), ("yahoo", "NIY=F"), ("dukascopy", "JPNIDXJPY")],
    "hyperliquid|xyz:SP500": [("yahoo", "ES=F"), ("dukascopy", "USA500IDXUSD")],
    "hyperliquid|xyz:XYZ100": [("yahoo", "NQ=F"), ("dukascopy", "USATECHIDXUSD")],
    "hyperliquid|xyz:BRENTOIL": [("yahoo", "BZ=F"), ("dukascopy", "BRENTCMDUSD")],
    "ostium|DJI": [("yahoo", "YM=F"), ("dukascopy", "USA30IDXUSD")],
    "binance|COPPERUSDT": [("okx", "XCU-USDT-SWAP")],
    "binance|NATGASUSDT": [("yahoo", "NG=F")],
}
BYBIT_FX = {"EURUSD": "EURUSDUSDT", "GBPUSD": "GBPUSDUSDT", "USDJPY": "USDJPYUSDT"}  # listed 2026-09-08


def _listing(kind):
    path = os.path.join(CACHE_DIR, f"{kind}_instruments.json")
    try:
        if time.time() - os.path.getmtime(path) < 24 * 3600:
            return set(json.load(open(path)))
    except OSError:
        pass
    try:
        if kind == "okx":
            ids = [x["instId"] for x in http("https://www.okx.com/api/v5/public/instruments?instType=SWAP")["data"]]
        else:
            ids = [x["symbol"] for x in http("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")
                   ["result"]["list"] if x.get("contractType") == "LinearPerpetual"]
    except Exception:
        return set()
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    json.dump(ids, open(tmp, "w"))
    os.replace(tmp, path)
    return set(ids)


def backups_for(m, okx=None, bybit=None):
    """Ordered backup list [(source, symbol, multiplier)] for an MFP market dict."""
    mid, coin = m["market_id"], m["coin"]
    if mid in OVERRIDES:
        return [(s, sym, 1.0) for s, sym in OVERRIDES[mid]]
    if m["provider"] == "veranta" and coin in FX:
        out = [("bybit", BYBIT_FX[coin], 1.0)] if coin in BYBIT_FX else []
        return out + [("yahoo", f"{coin}=X", 1.0), ("dukascopy", coin, 1.0)]
    if m["provider"] == "binance" and coin.endswith("USDT"):
        okx = _listing("okx") if okx is None else okx
        bybit = _listing("bybit") if bybit is None else bybit
        base, mult = coin[:-4], 1.0
        if base.startswith("1000"):
            base, mult = base[4:], 1000.0
        out = []
        if f"{base}-USDT-SWAP" in okx:
            out.append(("okx", f"{base}-USDT-SWAP", mult))
        if coin in bybit:
            out.append(("bybit", coin, 1.0))
        return out
    return []


# ---------------------------------------------------------------- merge (pure, offline-testable)
def overlap_stats(ref, other, lo, hi):
    """Median abs % close diff and median close ratio (ref/other) on common bars in [lo, hi)."""
    common = [t for t in other if lo <= t < hi and t in ref]
    if not common:
        return dict(n=0, median_abs_pct=None, ratio=None)
    diffs = [abs(other[t][4] - ref[t][4]) / ref[t][4] for t in common]
    ratios = [ref[t][4] / other[t][4] for t in common]
    return dict(n=len(common), median_abs_pct=statistics.median(diffs), ratio=statistics.median(ratios))


def _scale(bars, k):
    return {t: (t, b[1] * k, b[2] * k, b[3] * k, b[4] * k) for t, b in bars.items()}


def merge_sources(primary, backups, step, start, end, threshold=0.0015, multipliers=None):
    """primary: (name, {t: bar}); backups: [(name, {t: bar}), ...] in precedence order.
    Returns (bars sorted list, segments, checks). Backups only add bars older than everything already merged."""
    multipliers = multipliers or {}
    name0, p = primary
    merged = {t: b for t, b in p.items() if start <= t < end and t % step == 0}
    checks = []
    contrib = {name0: set(merged)}
    for name, raw in backups:
        bars = {t: b for t, b in raw.items() if start <= t < end and t % step == 0}
        earliest = min(merged) if merged else end
        if earliest <= start:
            break
        chk = dict(source=name, splice_at=earliest if merged else None)
        mult = multipliers.get(name, 1.0)
        if mult != 1.0:
            bars = _scale(bars, mult)
            chk["multiplier"] = mult
        if merged and not any(t < earliest for t in bars):
            chk.update(status="nothing older to add (source history too short)", overlap_bars=0, bars_added=0)
            checks.append(chk)
            continue
        st = overlap_stats(merged, bars, earliest, earliest + OVERLAP_CHECK_MS)
        chk.update(overlap_bars=st["n"], median_abs_pct_diff=None if st["median_abs_pct"] is None else round(st["median_abs_pct"] * 100, 4))
        if st["n"] < MIN_OVERLAP_BARS:
            chk["status"] = "unverified: no overlap with the data it extends" if merged else "only source (nothing to compare)"
        elif st["median_abs_pct"] <= threshold:
            chk["status"] = "ok"
        else:
            k = st["ratio"]
            st2 = overlap_stats(merged, _scale(bars, k), earliest, earliest + OVERLAP_CHECK_MS)
            if abs(k - 1) <= MAX_BASIS and st2["median_abs_pct"] <= threshold:
                bars = _scale(bars, k)
                chk.update(status="basis-adjusted", ratio=round(k, 6),
                           median_abs_pct_diff_after=round(st2["median_abs_pct"] * 100, 4))
            else:
                chk.update(status="rejected: overlap mismatch", ratio=round(k, 6),
                           median_abs_pct_diff_after=round(st2["median_abs_pct"] * 100, 4))
                checks.append(chk)
                continue
        add = {t: b for t, b in bars.items() if t < earliest}
        merged.update(add)
        contrib[name] = set(add)
        chk["bars_added"] = len(add)
        checks.append(chk)
    segments = []
    for name, ts in contrib.items():
        if ts:
            segments.append(dict(source=name, from_ms=min(ts), to_ms=max(ts) + step, bars=len(ts)))
    segments.sort(key=lambda s: s["from_ms"])
    return [merged[t] for t in sorted(merged)], segments, checks


def find_gaps(bars, step, session=False):
    """Holes between consecutive bars. Session markets: holes touching the weekend (Fri 20:00 - Mon 00:00 UTC) are
    'closures', not gaps. Returns dict with counts and the longest unexpected gaps."""
    gaps, closures = [], 0
    for a, b in zip(bars, bars[1:]):
        if b[0] - a[0] > step:
            s, e = a[0] + step, b[0]
            if session and _weekendish(s, e):
                closures += 1
            else:
                gaps.append((s, e))
    gaps.sort(key=lambda g: g[0] - g[1])
    return dict(gaps=len(gaps), missing_bars=sum((e - s) // step for s, e in gaps), weekend_closures=closures,
                longest=[dict(from_ms=s, to_ms=e, bars=(e - s) // step) for s, e in gaps[:3]])


def _weekendish(s, e):
    for t in (s, e - 1):
        g = time.gmtime(t / 1000)
        wd, h = g.tm_wday, g.tm_hour  # Mon=0
        if wd == 5 or wd == 6 or (wd == 4 and h >= 20):
            return True
    return e - s >= 2 * DAY and any(time.gmtime(x / 1000).tm_wday == 5 for x in range(s, e, 3600_000))


# ---------------------------------------------------------------- public entry
def asset_kind(m):
    return "forex" if m["provider"] == "veranta" else ("crypto" if m["provider"] == "binance" else "tradfi")


def load(m, interval, start, end, refresh=False, kind=None, use_backups=True, log=None):
    """Merged bars for an MFP market + provenance dict."""
    step = STEP[interval]
    kind = kind or asset_kind(m)
    thr = THRESHOLD.get(kind, 0.0015)
    mfp_sym = f"{m['provider']}|{m['coin']}"
    errors, mfp_failed = [], None
    try:
        primary = cached_fetch("mfp", mfp_sym, interval, start, end, refresh)
    except Exception as ex:
        # After retries, fall back to backups for the whole window. Never invent bars.
        mfp_failed = ex
        errors.append(f"mfp: {friendly_source_error(ex) or str(ex)[:160]}")
        primary = {}
    blist, mult = [], {}
    session_mkt = m["provider"] in ("veranta", "ostium")

    def reaches_start(first):  # a window that starts on a weekend is covered by Monday's first bar
        return first <= start + step or (session_mkt and first - start <= 3 * DAY and _weekendish(start, first))

    need = not reaches_start(min(primary) if primary else end)
    if use_backups and need:
        for src, sym, k in backups_for(m):
            name = f"{src}:{sym}"
            cur = merge_sources((f"mfp:{mfp_sym}", primary), blist, step, start, end, thr, mult)[0]
            first = cur[0][0] if cur else end
            if reaches_start(first):
                break
            try:  # fetch only what is needed: backfill range + 7 days of overlap
                b = cached_fetch(src, sym, interval, start, min(end, first + OVERLAP_CHECK_MS), refresh)
            except Exception as ex:
                errors.append(f"{name}: {str(ex)[:160]}")
                continue
            blist.append((name, b))
            if k != 1.0:
                mult[name] = k
    bars, segments, checks = merge_sources((f"mfp:{mfp_sym}", primary), blist, step, start, end, thr, mult)
    session = m["provider"] in ("veranta", "ostium") or any(s["source"].startswith(("yahoo", "dukascopy")) for s in segments)
    gaps = find_gaps(bars, step, session=session)
    for s in segments:
        s["label"] = SOURCE_LABEL.get(s["source"].split(":", 1)[0], s["source"])
    prov = dict(interval=interval, requested_from_ms=start, requested_to_ms=end,
                first_bar_ms=bars[0][0] if bars else None, last_bar_ms=bars[-1][0] + step if bars else None,
                days_covered=round(((bars[-1][0] + step) - bars[0][0]) / DAY, 1) if bars else 0.0,
                bars=len(bars), segments=segments, overlap_checks=checks, threshold_pct=thr * 100,
                backfill_needed=need, gaps=gaps, errors=errors,
                mfp_unavailable=mfp_failed is not None)
    if not bars and mfp_failed is not None:
        raise SourceBusy(BUSY_MSG) from mfp_failed
    return bars, prov


def provenance_text(prov, days_tested=None):
    fmt = lambda ms: time.strftime("%d %b %Y", time.gmtime(ms / 1000))
    segs = "; ".join(f"{s['label']} [{s['source'].split(':', 1)[1]}] {fmt(s['from_ms'])} to {fmt(s['to_ms'])}"
                     for s in prov["segments"]) or "no data"
    chk = []
    for c in prov["overlap_checks"]:
        d = c.get("median_abs_pct_diff")
        part = f"{c['source']} vs newer data: " + (f"median close diff {d:.3f}% on {c['overlap_bars']} bars" if d is not None else "no overlap")
        if c.get("status") == "basis-adjusted":
            part += f", basis-adjusted x{c['ratio']:.5f} -> {c['median_abs_pct_diff_after']:.3f}%"
        elif c.get("status", "").startswith("rejected"):
            part += f" > {prov['threshold_pct']:.2f}% threshold, REJECTED (not used)"
        elif c.get("status", "").startswith("unverified"):
            part += " (unverified)"
        chk.append(part)
    g = prov["gaps"]
    overlap = ("; ".join(chk) if chk else
               ("MFP feed was unavailable; backup used for the whole window" if prov.get("mfp_unavailable")
                else ("n/a, MFP covered the whole window" if not prov["backfill_needed"] else "no backup used")))
    s = (f"Data: {segs}. " + (f"Tested {days_tested:g} days; " if days_tested is not None else "")
         + f"{prov['days_covered']:g} days of {prov['interval']} bars loaded incl. warm-up. "
         + f"Gaps: {g['gaps']} ({g['missing_bars']} missing bars)" + (f", plus {g['weekend_closures']} weekend closures" if g['weekend_closures'] else "")
         + ". Overlap: " + overlap + ".")
    if prov.get("mfp_unavailable"):
        s = "MFP feed was unavailable; used backup sources for the requested window. " + s
    if prov.get("errors"):
        s += " Source errors: " + "; ".join(prov["errors"]) + "."
    return s


def qa(m, interval="15m", days=14, with_dukascopy=False):
    """Compare MFP with every mapped backup on the last `days` (closed days only), even when no backfill is needed."""
    step, now = STEP[interval], int(time.time() * 1000)
    end = now - now % DAY
    start = end - days * DAY
    ref = cached_fetch("mfp", f"{m['provider']}|{m['coin']}", interval, start, end)
    out = []
    for src, sym, k in backups_for(m):
        if src == "dukascopy" and not with_dukascopy:
            out.append(dict(source=f"{src}:{sym}", skipped="slow source; checked only when it is used for backfill"))
            continue
        try:
            b = cached_fetch(src, sym, interval, start, end)
        except Exception as ex:
            out.append(dict(source=f"{src}:{sym}", error=str(ex)[:160]))
            continue
        if k != 1.0:
            b = _scale(b, k)
        st = overlap_stats(ref, b, start, end)
        out.append(dict(source=f"{src}:{sym}", common_bars=st["n"], backup_bars=len(b), mfp_bars=len(ref),
                        median_abs_pct_diff=None if st["median_abs_pct"] is None else round(st["median_abs_pct"] * 100, 4),
                        median_ratio_mfp_over_backup=None if st["ratio"] is None else round(st["ratio"], 6)))
    return dict(window_utc=[time.strftime("%Y-%m-%d", time.gmtime(start / 1000)), time.strftime("%Y-%m-%d", time.gmtime(end / 1000))],
                checks=out)


# ---------------------------------------------------------------- CLI
def _markets():
    sys.path.insert(0, HERE)
    import tester
    return tester, tester.load_markets()


def coverage(days=90, warm_days=14, interval="15m"):
    tester, ms = _markets()
    now = int(time.time() * 1000)
    step = STEP[interval]
    end = now - now % step
    start = end - (days + warm_days) * DAY
    okx, bybit = _listing("okx"), _listing("bybit")
    rows = []
    for m in ms:
        bl = backups_for(m, okx, bybit)
        row = dict(market_id=m["market_id"], asset_class=tester.asset_class(m),
                   backups=[dict(source=s, symbol=sym, **({"multiplier": k} if k != 1.0 else {})) for s, sym, k in bl])
        try:
            bars, prov = load(m, interval, start, end)
            row.update(days_covered=prov["days_covered"], first_bar=time.strftime("%Y-%m-%d %H:%M", time.gmtime(prov["first_bar_ms"] / 1000)) if bars else None,
                       segments=[dict(source=s["source"], bars=s["bars"], from_utc=time.strftime("%Y-%m-%d %H:%M", time.gmtime(s["from_ms"] / 1000)),
                                      to_utc=time.strftime("%Y-%m-%d %H:%M", time.gmtime(s["to_ms"] / 1000))) for s in prov["segments"]],
                       overlap_checks=prov["overlap_checks"], gaps=prov["gaps"]["gaps"], missing_bars=prov["gaps"]["missing_bars"],
                       weekend_closures=prov["gaps"]["weekend_closures"], errors=prov["errors"])
        except Exception as ex:
            row.update(error=str(ex)[:200])
        if m["provider"] in ("veranta", "ostium", "hyperliquid") or m["coin"] in ("BTCUSDT", "ETHUSDT", "XAUUSDT"):
            try:
                row["qa_last_14d"] = qa(m, interval)
            except Exception as ex:
                row["qa_last_14d"] = dict(error=str(ex)[:200])
        rows.append(row)
        print(f"{m['market_id']:<24} {row.get('days_covered', 'ERR')!s:>6}d  " +
              " + ".join(f"{s['source']}({s['bars']})" for s in row.get("segments", [])) +
              ("  " + "; ".join(f"{c['source']}:{c['status']}:{c.get('median_abs_pct_diff')}" for c in row.get("overlap_checks", [])) if row.get("overlap_checks") else "")
              + (f"  ERR {row['error']}" if "error" in row else ""), flush=True)
    doc = dict(generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), interval=interval,
               requested_days=days + warm_days, note=f"{days} test days + {warm_days} warm-up days requested", markets=rows)
    tmp = os.path.join(HERE, "data_sources.json.tmp")
    json.dump(doc, open(tmp, "w"), indent=1)
    os.replace(tmp, os.path.join(HERE, "data_sources.json"))
    return doc


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["coverage", "show", "qa"])
    ap.add_argument("market", nargs="?")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--interval", default="15m")
    a = ap.parse_args()
    if a.cmd == "coverage":
        coverage(a.days, interval=a.interval)
    elif a.cmd == "qa":
        tester, ms = _markets()
        print(json.dumps(qa(tester.resolve_market(a.market, ms), a.interval), indent=1))
    else:
        tester, ms = _markets()
        m = tester.resolve_market(a.market, ms)
        now = int(time.time() * 1000)
        end = now - now % STEP[a.interval]
        bars, prov = load(m, a.interval, end - (a.days + 14) * DAY, end)
        print(json.dumps(prov, indent=1))
        print(provenance_text(prov))
