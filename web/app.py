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

RUN_TIMEOUT = int(os.environ.get("TESTER_TIMEOUT", "150"))  # seconds per backtest
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
                   asset_class=tester.asset_class(m), default_venue=m.get("default_venue", True))
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


@app.post("/api/test")
def run_test():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return err("send a JSON body: {market, side, entry, tp, sl, days, expiry, trend_filter, mode, zone_tf, sl_buffer_atr}")
    try:
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
        days = int(d.get("days") or 60)
        if not 30 <= days <= 60:
            raise ValueError("days must be between 30 and 60")
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
    except ValueError as e:
        return err(str(e))

    if not _run_sem.acquire(timeout=5):
        return err("server busy with other tests, try again in a moment", 429)
    try:
        with market_lock(m["market_id"]):
            with tempfile.TemporaryDirectory() as td:
                jp = os.path.join(td, "out.json")
                cmd = [sys.executable, os.path.join(ENGINE_DIR, "tester.py"), m["market_id"], side,
                       f"--entry={entry!r}", f"--tp={tp!r}", f"--sl={sl!r}", "--days", str(days),
                       "--json", "--json-out", jp, "--mode", mode] + (["--trend-filter"] if trend else [])
                if mode == "pattern":  # pattern mode: limit rests on a zone until touched/invalidated (engine default)
                    cmd += ["--zone-tf", zone_tf] + ([f"--sl-buffer-atr={slb!r}"] if slb is not None else [])
                else:
                    cmd += ["--expiry", expiry]
                t0 = time.time()
                try:
                    p = subprocess.run(cmd, cwd=ENGINE_DIR, capture_output=True, text=True, timeout=RUN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    return err(f"backtest timed out after {RUN_TIMEOUT}s (MFP data fetch was slow); try again, "
                               "the candle cache makes repeat runs fast", 504)
                if p.returncode != 0 or not os.path.exists(jp):
                    msg = (p.stderr or p.stdout or "engine failed").strip().splitlines()
                    return err(msg[-1] if msg else "engine failed", 422)
                out = json.load(open(jp))
                out["elapsed_s"] = round(time.time() - t0, 1)
                return jsonify(out)
    finally:
        _run_sem.release()


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    from waitress import serve
    print(f"CHRONOS on http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=8)
