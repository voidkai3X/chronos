#!/usr/bin/env python3
"""CHRONOS web app. Thin wrapper around ../tester.py (the CLI engine is unchanged).

Read-only: only MFP public keyless market data. Never places orders, uses no API keys.
"""
import asyncio, json, os, subprocess, sys, tempfile, threading, time

from flask import Flask, g, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.dirname(HERE)
sys.path.insert(0, ENGINE_DIR)
import tester  # noqa: E402  (engine: load_markets, resolve_market, _fetch_chunks)

RUN_TIMEOUT = int(os.environ.get("TESTER_TIMEOUT", "240"))  # first run of a market may backfill from OKX/Yahoo  # seconds per backtest
MAX_PARALLEL = int(os.environ.get("TESTER_MAX_PARALLEL", "2"))
RATE_HEAVY_PER_MIN = int(os.environ.get("RATE_HEAVY_PER_MIN", "6"))
RATE_HEAVY_CONCURRENT_PER_IP = int(os.environ.get("RATE_HEAVY_CONCURRENT_PER_IP", "1"))
_run_sem = threading.BoundedSemaphore(MAX_PARALLEL)
_market_locks, _ml_guard = {}, threading.Lock()  # one run per market at a time (shared candle cache file)
_price_cache = {}  # market_id -> (ts, payload)
_rate_guard = threading.Lock()
_heavy_hits = {}       # ip -> [timestamps] in the last minute
_heavy_inflight = {}   # ip -> current heavy runs

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
app.config["DEBUG"] = False
app.config["ENV"] = "production"


def err(msg, code=400):
    return jsonify(error=msg), code


def reset_rate_limits():
    """Test helper: clear per-IP counters."""
    with _rate_guard:
        _heavy_hits.clear()
        _heavy_inflight.clear()


def client_ip():
    """Prefer the Cloudflare connecting IP only when the request itself is from localhost (the tunnel)."""
    remote = (request.remote_addr or "").strip()
    cf = (request.headers.get("CF-Connecting-IP") or "").strip()
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if remote in ("127.0.0.1", "::1"):
        return cf or xff or remote or "unknown"
    if os.environ.get("TRUST_PROXY") == "1":
        # Behind Render's proxy: the right-most X-Forwarded-For entry is the one the proxy added (not spoofable).
        hops = [h.strip() for h in (request.headers.get("X-Forwarded-For") or "").split(",") if h.strip()]
        if hops:
            return hops[-1]
    return remote or "unknown"


def rate_limit_heavy():
    """Per-IP cap on /api/test, /api/compare and /api/scan. Returns a 429 response or None.

    Call rate_done_heavy() in a finally block if this returns None.
    """
    ip = client_ip()
    g.heavy_ip = ip
    now = time.time()
    with _rate_guard:
        # drop stale hits; also prune idle IPs so the maps cannot grow without bound
        stale = [k for k, ts in _heavy_hits.items() if not ts or now - ts[-1] >= 60]
        for k in stale:
            _heavy_hits.pop(k, None)
        hits = [t for t in _heavy_hits.get(ip, []) if now - t < 60]
        if len(hits) >= RATE_HEAVY_PER_MIN:
            return err("too many tests from your network, try again in a minute", 429)
        if _heavy_inflight.get(ip, 0) >= RATE_HEAVY_CONCURRENT_PER_IP:
            return err("a test from your network is already running, wait for it to finish", 429)
        hits.append(now)
        _heavy_hits[ip] = hits
        _heavy_inflight[ip] = _heavy_inflight.get(ip, 0) + 1
    return None


def rate_done_heavy():
    ip = getattr(g, "heavy_ip", None)
    if not ip:
        return
    with _rate_guard:
        n = _heavy_inflight.get(ip, 0) - 1
        if n <= 0:
            _heavy_inflight.pop(ip, None)
        else:
            _heavy_inflight[ip] = n


def market_lock(mid):
    with _ml_guard:
        return _market_locks.setdefault(mid, threading.Lock())


def resolve(name):
    try:
        return tester.resolve_market(name, tester.load_markets())
    except SystemExit as e:
        raise ValueError(str(e))


def parse_test_body(d, require_compare=False):
    """Validate a /api/test or /api/compare JSON body. Returns (market_row, side, entry, tp, sl, days, expiry, trend, mode, zone_tf, entry_style, want_insights, slb, compare_list)."""
    if not isinstance(d, dict):
        raise ValueError("send a JSON body: {market, side, entry, tp, sl, days, expiry, trend_filter, mode, zone_tf, sl_buffer_atr, entry_style, insights, compare}")
    market = str(d.get("market") or "").strip()
    if not market:
        raise ValueError("market is required")
    side = str(d.get("side") or "").lower()
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    entry, tp, sl = _num(d, "entry"), _num(d, "tp"), _num(d, "sl")
    if side == "long" and not (sl < entry < tp):
        raise ValueError("for a long, SL must be below entry and TP above entry (sl < entry < tp)")
    if side == "short" and not (tp < entry < sl):
        raise ValueError("for a short, TP must be below entry and SL above entry (tp < entry < sl)")
    days = int(d.get("days") or 90)
    if not 30 <= days <= 90:
        raise ValueError("days must be between 30 and 90")
    expiry = str(d.get("expiry") or "72h").strip().lower()
    try:
        exp_ms = tester.parse_dur(expiry)
    except Exception:
        raise ValueError("expiry must look like 24h, 72h or 3d")
    if not 3600_000 <= exp_ms <= 14 * 86400_000:
        raise ValueError("expiry must be between 1h and 14d")
    trend = bool(d.get("trend_filter"))
    mode = str(d.get("mode") or "similar").lower()
    if mode not in ("every-close", "pattern", "similar"):
        raise ValueError("mode must be 'similar', 'every-close' or 'pattern'")
    similar_tf = str(d.get("similar_tf") or "1h").lower()
    if similar_tf not in ("15m", "30m", "1h", "4h"):
        raise ValueError("similar_tf must be 15m, 30m, 1h or 4h")
    try:
        similar_bars = int(d.get("similar_bars") or 24)
    except (TypeError, ValueError):
        raise ValueError("similar_bars must be a number")
    if not 8 <= similar_bars <= 96:
        raise ValueError("similar_bars must be between 8 and 96")
    try:
        similar_k = int(d.get("similar_k") or 30)
    except (TypeError, ValueError):
        raise ValueError("similar_k must be a number")
    if not 5 <= similar_k <= 80:
        raise ValueError("similar_k must be between 5 and 80")
    zone_tf = str(d.get("zone_tf") or "4h").lower()
    if zone_tf not in ("30m", "1h", "4h"):
        raise ValueError("zone_tf must be 30m, 1h or 4h")
    entry_style = str(d.get("entry_style") or "limit").lower()
    if entry_style not in tester.ENTRY_STYLES:
        raise ValueError("entry_style must be limit, sweep or confirm")
    if mode != "pattern" and entry_style != "limit":
        raise ValueError("entry_style sweep/confirm needs mode 'pattern'")
    want_insights = bool(d.get("insights", True if not require_compare else False))
    slb = d.get("sl_buffer_atr")
    if slb not in (None, ""):
        try:
            slb = float(slb)
        except (TypeError, ValueError):
            raise ValueError("sl_buffer_atr must be a number")
        if not 0 <= slb <= 10:
            raise ValueError("sl_buffer_atr must be between 0 and 10")
    else:
        slb = None
    m = resolve(market)
    compare = d.get("compare")
    extras = []
    if require_compare or compare not in (None, "", []):
        if isinstance(compare, str):
            spec = compare
        elif isinstance(compare, list):
            if len(compare) > tester.COMPARE_LIMIT:
                raise ValueError(f"compare accepts at most {tester.COMPARE_LIMIT} other markets (got {len(compare)})")
            spec = ",".join(str(x).strip() for x in compare if str(x).strip())
        else:
            raise ValueError("compare must be a list of up to 5 market names")
        try:
            extras = tester.parse_compare(spec, tester.load_markets(), m)
        except ValueError:
            raise
    if require_compare and not extras:
        raise ValueError("compare must list 1 to 5 other markets")
    return dict(market=m, side=side, entry=entry, tp=tp, sl=sl, days=days, expiry=expiry,
                trend=trend, mode=mode, zone_tf=zone_tf, entry_style=entry_style,
                similar_tf=similar_tf, similar_bars=similar_bars, similar_k=similar_k,
                want_insights=want_insights, slb=slb, extras=extras)


def engine_cmd(p, json_path, compare=False):
    m = p["market"]
    cmd = [sys.executable, os.path.join(ENGINE_DIR, "tester.py"), m["market_id"], p["side"],
           f"--entry={p['entry']!r}", f"--tp={p['tp']!r}", f"--sl={p['sl']!r}", "--days", str(p["days"]),
           "--json", "--json-out", json_path, "--mode", p["mode"]] + (["--trend-filter"] if p["trend"] else [])
    if p["mode"] == "pattern":
        cmd += ["--zone-tf", p["zone_tf"], "--entry-style", p["entry_style"]]
        if p["slb"] is not None:
            cmd += [f"--sl-buffer-atr={p['slb']!r}"]
    elif p["mode"] == "similar":
        cmd += ["--expiry", p["expiry"], "--similar-tf", p["similar_tf"],
                "--similar-bars", str(p["similar_bars"]), "--similar-k", str(p["similar_k"])]
    else:
        cmd += ["--expiry", p["expiry"]]
    if compare:
        cmd += ["--compare", ",".join(x["market_id"] for x in p["extras"])]
    elif p["want_insights"]:
        cmd += ["--insights"]
    return cmd


def run_engine(p, compare=False, timeout=None):
    timeout = timeout or RUN_TIMEOUT
    ids = [p["market"]["market_id"]] + [x["market_id"] for x in p.get("extras") or []]
    # lock markets in sorted order so two compare requests cannot deadlock
    locks = [market_lock(i) for i in sorted(set(ids))]
    if not _run_sem.acquire(timeout=5):
        return err("server busy with other tests, try again in a moment", 429)
    got = []
    try:
        for lk in locks:
            if not lk.acquire(timeout=5):
                return err("server busy with other tests, try again in a moment", 429)
            got.append(lk)
        with tempfile.TemporaryDirectory() as td:
            jp = os.path.join(td, "out.json")
            cmd = engine_cmd(p, jp, compare=compare)
            t0 = time.time()
            try:
                proc = subprocess.run(cmd, cwd=ENGINE_DIR, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return err(f"backtest timed out after {timeout}s (market data fetch was slow); try again, "
                           "the candle cache makes repeat runs fast", 504)
            if proc.returncode != 0 or not os.path.exists(jp):
                msg = (proc.stderr or proc.stdout or "engine failed").strip().splitlines()
                return err(msg[-1] if msg else "engine failed", 422)
            out = json.load(open(jp))
            out["elapsed_s"] = round(time.time() - t0, 1)
            return jsonify(out)
    finally:
        for lk in reversed(got):
            lk.release()
        _run_sem.release()


@app.get("/")
def index():
    return send_from_directory(os.path.join(HERE, "static"), "index.html")


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/markets")
def markets():
    try:
        ms = tester.load_markets()
    except Exception as e:  # network
        return err(f"could not load MFP markets list: {e}", 502)
    q = (request.args.get("q") or "").strip().upper()
    out = []
    for m in ms:
        row = dict(market_id=m["market_id"], symbol=m["symbol"], coin=m["coin"], provider=m["provider"],
                   asset_class=tester.asset_class(m), family=tester.market_family(m),
                   family_label=tester.FAMILY_LABEL[tester.market_family(m)],
                   default_venue=m.get("default_venue", True))
        if q and q not in " ".join((m["market_id"], m["symbol"], m["coin"])).upper():
            continue
        out.append(row)
    out.sort(key=lambda r: (not r["symbol"].upper().startswith(q) if q else False, r["symbol"]))
    return jsonify(count=len(out), total=len(ms), markets=out)


@app.get("/api/price")
def price():
    """Last closed 15m close on the MFP feed: the same reference price the engine uses."""
    name = request.args.get("market", "")
    try:
        m = resolve(name)
    except ValueError as e:
        return err(str(e), 404)
    hit = _price_cache.get(m["market_id"])
    if hit and time.time() - hit[0] < 60:
        return jsonify(hit[1])
    now = int(time.time() * 1000)
    step = tester.STEP["15m"]
    end = now - now % step
    try:
        bars = asyncio.run(tester._fetch_chunks(m["provider"], m["coin"], "15m", [(end - 12 * step, end)]))
    except Exception as e:
        return err(f"MFP price fetch failed: {e}", 502)
    if not bars:
        return err("no recent candles from MFP for this market", 502)
    t = max(bars)
    payload = dict(market_id=m["market_id"], symbol=m["symbol"], price=bars[t][4], bar_open_ms=t,
                   note="last closed 15m close on the MFP feed (the tester's reference price)")
    _price_cache[m["market_id"]] = (time.time(), payload)
    return jsonify(payload)


def _num(d, k):
    v = d.get(k)
    if v is None or v == "":
        raise ValueError(f"{k} is required")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{k} must be a number")
    if not (f > 0) or f != f or f == float("inf"):
        raise ValueError(f"{k} must be a positive number")
    return f


@app.get("/api/compare-suggest")
def compare_suggest():
    name = (request.args.get("market") or "").strip()
    if not name:
        return err("market is required")
    try:
        ms = tester.load_markets()
        m = tester.resolve_market(name, ms)
    except SystemExit as e:
        return err(str(e), 404)
    except Exception as e:
        return err(f"could not load MFP markets list: {e}", 502)
    fam = tester.market_family(m)
    sug = tester.suggest_compare(m, ms)
    def row(x):
        return dict(market_id=x["market_id"], symbol=x["symbol"], coin=x["coin"], provider=x["provider"],
                    asset_class=tester.asset_class(x), family=tester.market_family(x),
                    family_label=tester.FAMILY_LABEL[tester.market_family(x)])
    return jsonify(market=row(m), family=fam, family_label=tester.FAMILY_LABEL[fam],
                   limit=tester.COMPARE_LIMIT, suggestions=[row(x) for x in sug])


@app.post("/api/test")
def run_test():
    limited = rate_limit_heavy()
    if limited:
        return limited
    try:
        d = request.get_json(silent=True)
        try:
            p = parse_test_body(d, require_compare=False)
        except ValueError as e:
            return err(str(e))
        return run_engine(p, compare=False, timeout=RUN_TIMEOUT)
    finally:
        rate_done_heavy()


@app.post("/api/compare")
def run_compare():
    limited = rate_limit_heavy()
    if limited:
        return limited
    try:
        d = request.get_json(silent=True)
        try:
            p = parse_test_body(d, require_compare=True)
        except ValueError as e:
            return err(str(e))
        # no insights: each insights run multiplies work by ~8; compare already runs up to 6 markets
        p["want_insights"] = False
        n = 1 + len(p["extras"])
        timeout = int(os.environ.get("COMPARE_TIMEOUT", str(max(RUN_TIMEOUT, 120 * n))))
        return run_engine(p, compare=True, timeout=timeout)
    finally:
        rate_done_heavy()


SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "180"))


@app.post("/api/scan")
def run_scan():
    """Fingerprint each market's latest window against the setup moment. Gated unless force=true."""
    limited = rate_limit_heavy()
    if limited:
        return limited
    try:
        return _run_scan()
    finally:
        rate_done_heavy()


def _run_scan():
    import similar as S
    d = request.get_json(silent=True) or {}
    try:
        p = parse_test_body(d, require_compare=False)
    except ValueError as e:
        return err(str(e))
    result = d.get("result") if isinstance(d.get("result"), dict) else None
    if result is None and isinstance(d.get("verdict"), str):
        result = dict(verdict=d["verdict"])
    forced = bool(d.get("force") or d.get("force_scan"))
    reason = S.gate_reason(result)
    if reason and not forced:
        return jsonify(gated=True, reason=reason, forced=False, rows=[],
                       generated=tester.fmt_ts(int(time.time() * 1000)))
    m = p["market"]
    query_in = d.get("query") if isinstance(d.get("query"), dict) and d.get("query").get("path") else None
    try:
        markets = tester.load_markets()
        scope, kind = S.resolve_scope(str(d.get("scope") or d.get("scan_scope") or "family"), m, markets, None)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"could not load MFP markets list: {e}", 502)
    ids = sorted({x["market_id"] for x in ([m] + list(scope))})
    if not _run_sem.acquire(timeout=5):
        return err("server busy with other tests, try again in a moment", 429)
    got = []
    try:
        for mid in ids:
            lk = market_lock(mid)
            if not lk.acquire(timeout=5):
                return err("server busy with other tests, try again in a moment", 429)
            got.append(lk)
        now = int(time.time() * 1000)
        step = tester.STEP["15m"]
        end_ms = now - now % step
        warm = 14 * 86400_000
        hist = max(40, int(S.ATR_PCT_LOOKBACK * tester.STEP[p["similar_tf"]] / 86400_000) + 2) * 86400_000
        start = end_ms - hist - warm
        try:
            base = tester.fetch_candles(m, "15m", start, end_ms, refresh=False)
        except Exception as e:
            return err(f"could not load candles for {m['market_id']}: {e}", 502)
        if len(base) < 200:
            return err(f"not enough history for {m['market_id']}", 422)
        h4c = [b for b in tester.resample(base, tester.H4) if b[5] >= b[0] + tester.H4 - step]
        atr_now = tester.atr_series(h4c)[-1]
        ref = base[-1][4]
        g = tester.build_geometry(p["side"], p["entry"], p["tp"], p["sl"], ref, atr_now)
        g["market_id"] = m["market_id"]
        q = query_in
        if q is None:
            q, *_ = S.query_fingerprint(base, tf=p["similar_tf"], n=p["similar_bars"],
                                        end_ms=end_ms, interval="15m", entry=p["entry"])
        if q is None:
            return err("could not fingerprint the setup moment", 422)
        t0 = time.time()
        scan = S.scan_live(q, g, scope, interval="15m", tf=p["similar_tf"], n=p["similar_bars"],
                           trend_filter=p["trend"], refresh=False, end_ms=end_ms)
        scan["forced"] = forced
        scan["gate_reason"] = reason
        scan["scope"] = kind
        scan["query"] = S.public_query(q)
        scan["elapsed_s"] = round(time.time() - t0, 1)
        return jsonify(scan)
    finally:
        for lk in reversed(got):
            lk.release()
        _run_sem.release()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve
    print(f"CHRONOS on http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8, ident="CHRONOS")
