#!/usr/bin/env python3
"""CHRONOS web app. Thin wrapper around ../tester.py (the CLI engine is unchanged).

Read-only: only MFP public keyless market data. Never places orders, uses no API keys.
"""
import asyncio, json, os, subprocess, sys, tempfile, threading, time

from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.dirname(HERE)
sys.path.insert(0, ENGINE_DIR)
import tester  # noqa: E402  (engine: load_markets, resolve_market, _fetch_chunks)

RUN_TIMEOUT = int(os.environ.get("TESTER_TIMEOUT", "240"))  # first run of a market may backfill from OKX/Yahoo  # seconds per backtest
MAX_PARALLEL = int(os.environ.get("TESTER_MAX_PARALLEL", "2"))
_run_sem = threading.BoundedSemaphore(MAX_PARALLEL)
_market_locks, _ml_guard = {}, threading.Lock()  # one run per market at a time (shared candle cache file)
_price_cache = {}  # market_id -> (ts, payload)

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")


def err(msg, code=400):
    return jsonify(error=msg), code


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
    mode = str(d.get("mode") or "every-close").lower()
    if mode not in ("every-close", "pattern"):
        raise ValueError("mode must be 'every-close' or 'pattern'")
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
    d = request.get_json(silent=True)
    try:
        p = parse_test_body(d, require_compare=False)
    except ValueError as e:
        return err(str(e))
    return run_engine(p, compare=False, timeout=RUN_TIMEOUT)


@app.post("/api/compare")
def run_compare():
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


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve
    print(f"CHRONOS on http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8)
