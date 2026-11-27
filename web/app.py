#!/usr/bin/env python3
"""CHRONOS web app. Thin wrapper around ../tester.py (the CLI engine is unchanged).

Read-only market data: only MFP's public, keyless feed. Never places orders.
Screenshot reading is optional and uses OPENAI_API_KEY when set.
"""
import asyncio, json, os, subprocess, sys, tempfile, threading, time

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.dirname(HERE)
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, HERE)
import tester  # noqa: E402  (engine: load_markets, resolve_market, _fetch_chunks)
import screenshot as shot  # noqa: E402
import auth  # noqa: E402  (accounts: sign-up, sign-in, copy trading gate)
import library  # noqa: E402  (saved strategies + run snapshots; registers its tables before the engine starts)

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
_shot_hits = {}        # ip -> [timestamps] in the last 24h (screenshot daily cap)

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
app.config["DEBUG"] = False
app.config["ENV"] = "production"
app.config["MAX_CONTENT_LENGTH"] = shot.MAX_BYTES + 256 * 1024  # 5 MB image plus form overhead
auth.init_app(app)
app.register_blueprint(library.bp)


def err(msg, code=400):
    return jsonify(error=msg), code


def public_engine_error(last):
    """Map engine stderr to a public JSON error. Never return a raw Python traceback or WebSocket 429."""
    import sources
    text = str(last or "").strip()
    busy = sources.friendly_source_error(text)
    if busy:
        return err(busy, 503)
    low = text.lower()
    if "traceback" in low or "exceptions." in low or text.startswith("  File ") or "websocket" in low:
        return err(sources.BUSY_MSG, 503)
    return err(text or "engine failed", 422)


def reset_rate_limits():
    """Test helper: clear per-IP counters."""
    with _rate_guard:
        _heavy_hits.clear()
        _heavy_inflight.clear()
        _shot_hits.clear()


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


app.config["CLIENT_IP_FN"] = client_ip


def rate_limit_heavy():
    """Per-IP cap on /api/test, /api/compare, /api/scan and /api/screenshot. Returns a 429 response or None.

    Also enforces sign-in for these tools when REQUIRE_LOGIN is on (off by default).
    Call rate_done_heavy() in a finally block if this returns None.
    """
    gated = auth.gate_tools()
    if gated:
        return gated
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


def _screenshot_day_hits(ip, now=None):
    now = time.time() if now is None else now
    window = 86400
    with _rate_guard:
        stale = [k for k, ts in _shot_hits.items() if not ts or now - ts[-1] >= window]
        for k in stale:
            _shot_hits.pop(k, None)
        return [t for t in _shot_hits.get(ip, []) if now - t < window]


def rate_limit_screenshot_day():
    """Return a 429 if this IP is at the daily cap. Does not record a use.

    Failed OpenAI calls (quota, rate limit, timeouts) must not count. Record a
    use with screenshot_day_record() only after a billed vision response.
    """
    ip = getattr(g, "heavy_ip", None) or client_ip()
    if len(_screenshot_day_hits(ip)) >= shot.daily_limit():
        return err("daily screenshot limit reached, try again tomorrow", 429)
    return None


def screenshot_day_record():
    """Count one billed screenshot read (success or an unreadable-but-charged reply)."""
    ip = getattr(g, "heavy_ip", None) or client_ip()
    now = time.time()
    with _rate_guard:
        hits = [t for t in _shot_hits.get(ip, []) if now - t < 86400]
        hits.append(now)
        _shot_hits[ip] = hits


def pattern_from_body(d):
    if not isinstance(d, dict):
        return ""
    return shot.clean_pattern(d.get("pattern"))


def attach_pattern(resp, pattern):
    """Echo the trader's pattern label on a successful engine JSON response."""
    if not pattern:
        return resp
    body, code = resp if isinstance(resp, tuple) else (resp, 200)
    if code != 200:
        return resp
    try:
        data = body.get_json()
    except Exception:
        return resp
    if not isinstance(data, dict) or data.get("error"):
        return resp
    data["pattern"] = pattern
    return jsonify(data)


def attach_library_run(resp, uid, p, d):
    """Save a result snapshot (metrics, params, provenance; no candles) for a saved strategy's run."""
    body, code = resp if isinstance(resp, tuple) else (resp, 200)
    if code != 200:
        return resp
    try:
        data = body.get_json()
    except Exception:
        return resp
    if not isinstance(data, dict) or data.get("error"):
        return resp
    try:
        rid = library.save_run(uid, p["strategy_id"], data, dict(entry=p["entry"], tp=p["tp"], sl=p["sl"]))
        data["library_run"] = dict(saved=bool(rid), id=rid, strategy_id=p["strategy_id"])
    except Exception:
        data["library_run"] = dict(saved=False, error="The result could not be saved to your library.")
    return jsonify(data)


@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def too_large(_e):
    return err("That image is too large. Use a PNG, JPG or WebP under 5 MB.", 413)


def market_lock(mid):
    with _ml_guard:
        return _market_locks.setdefault(mid, threading.Lock())


def resolve(name):
    try:
        return tester.resolve_market(name, tester.load_markets())
    except SystemExit as e:
        raise ValueError(str(e))


def _strategy_id(d):
    v = d.get("strategy_id")
    if v in (None, ""):
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError("strategy_id must be a number")
    if v <= 0:
        raise ValueError("strategy_id must be a number")
    return v


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
    if mode not in ("every-close", "pattern", "similar", "rule"):
        raise ValueError("mode must be 'similar', 'every-close', 'pattern' or 'rule'")
    rule, rule_params = None, None
    if mode == "rule":
        import strategies as R
        rule = str(d.get("rule") or "")
        if rule not in R.RULES:
            raise ValueError("rule must be one of " + ", ".join(R.RULES))
        rule_params = R.clean_params(rule, d.get("rule_params") or {})
    max_hold = str(d.get("max_hold") or "7d").strip().lower()
    if max_hold != "none":
        try:
            mh_ms = tester.parse_dur(max_hold)
        except Exception:
            raise ValueError("max_hold (time stop) must look like 24h, 3d or none")
        if not 3600_000 <= mh_ms <= 30 * 86400_000:
            raise ValueError("max_hold (time stop) must be between 1h and 30d, or none")
    TFS = tester.SETUP_TFS
    setup_tf = str(d.get("tf") or "").lower() or None  # one setup timeframe for any mode (Phase 2)
    if setup_tf is not None and setup_tf not in TFS:
        raise ValueError("tf must be 5m, 15m, 30m, 1h, 4h or 1d")
    similar_tf = str((setup_tf if mode == "similar" else None) or d.get("similar_tf") or "1h").lower()
    if similar_tf not in TFS:
        raise ValueError("similar_tf must be 5m, 15m, 30m, 1h, 4h or 1d")
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
    zone_tf = str((setup_tf if mode == "pattern" else None) or d.get("zone_tf") or "4h").lower()
    if zone_tf not in TFS:
        raise ValueError("zone_tf must be 5m, 15m, 30m, 1h, 4h or 1d")
    close_tf = str((setup_tf if mode in ("every-close", "rule") else None) or ("1h" if mode == "rule" else "4h"))
    eff_tf = {"similar": similar_tf, "pattern": zone_tf}.get(mode, close_tf)
    interval = tester.exec_interval_for(eff_tf)
    htf = str(d.get("htf_confirm") or "off").lower()
    if htf not in ("off",) + tester.HTF_CHOICES:
        raise ValueError("htf_confirm must be off, 1h, 4h or 1d")
    if htf != "off" and tester.STEP[htf] <= tester.STEP[eff_tf]:
        raise ValueError(f"higher-timeframe confirmation ({htf}) must be a higher timeframe than the setup ({eff_tf})")
    sweep = d.get("tf_sweep")
    if sweep in (True, "1", 1, "true", "all"):
        sweep = list(TFS)
    elif isinstance(sweep, list):
        sweep = [str(x).lower() for x in sweep]
        if not all(x in TFS for x in sweep) or not 2 <= len(set(sweep)) <= len(TFS):
            raise ValueError("tf_sweep must list 2 to 6 of 5m, 15m, 30m, 1h, 4h, 1d")
    else:
        sweep = None
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
    slip_unit = str(d.get("slippage_unit") or "bps").lower()
    if slip_unit not in ("bps", "ticks"):
        raise ValueError("slippage_unit must be 'bps' or 'ticks'")
    slip_raw = d.get("slippage")
    if slip_raw is None:
        slip_raw = d.get("slippage_bps")
    if slip_raw in (None, ""):
        slip = tester.DEFAULT_SLIPPAGE_BPS if slip_unit == "bps" else 0.0
    else:
        try:
            slip = float(slip_raw)
        except (TypeError, ValueError):
            raise ValueError("slippage must be a number")
        if slip != slip or slip < 0 or slip > (100 if slip_unit == "bps" else 1000):
            raise ValueError("slippage must be 0 to 100 bps, or 0 to 1000 ticks")
    allow_overlap = bool(d.get("allow_overlap"))
    if allow_overlap and mode == "pattern":
        raise ValueError("'Allow overlapping trades' works with Similar moments and Every 4h close, not Pattern only")
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
                want_insights=want_insights, slb=slb, extras=extras,
                slip_unit=slip_unit, slip=slip, allow_overlap=allow_overlap,
                setup_tf=eff_tf, close_tf=close_tf, interval=interval, htf=htf, tf_sweep=sweep,
                rule=rule, rule_params=rule_params, max_hold=max_hold, strategy_id=_strategy_id(d))


def engine_cmd(p, json_path, compare=False):
    m = p["market"]
    cmd = [sys.executable, os.path.join(ENGINE_DIR, "tester.py"), m["market_id"], p["side"],
           f"--entry={p['entry']!r}", f"--tp={p['tp']!r}", f"--sl={p['sl']!r}", "--days", str(p["days"]),
           "--json", "--json-out", json_path, "--mode", p["mode"]] + (["--trend-filter"] if p["trend"] else [])
    cmd += ["--interval", p.get("interval") or "15m"]
    if p.get("htf") and p["htf"] != "off":
        cmd += ["--htf-confirm", p["htf"]]
    if p.get("tf_sweep"):
        cmd += ["--tf-sweep", ",".join(p["tf_sweep"])]
    if p["mode"] == "pattern":
        cmd += ["--zone-tf", p["zone_tf"], "--entry-style", p["entry_style"]]
        if p["slb"] is not None:
            cmd += [f"--sl-buffer-atr={p['slb']!r}"]
    elif p["mode"] == "similar":
        cmd += ["--expiry", p["expiry"], "--similar-tf", p["similar_tf"],
                "--similar-bars", str(p["similar_bars"]), "--similar-k", str(p["similar_k"])]
    else:
        cmd += ["--expiry", p["expiry"], "--setup-tf", p.get("close_tf") or "4h"]
    if p["mode"] == "rule":
        cmd += ["--rule", p["rule"], "--rule-params", json.dumps(p["rule_params"] or {})]
    cmd += ["--max-hold", p.get("max_hold") or "7d"]
    if p.get("slip_unit") == "ticks" and p.get("slip"):
        cmd += [f"--slippage-ticks={p['slip']!r}"]
    else:
        cmd += [f"--slippage-bps={p.get('slip', tester.DEFAULT_SLIPPAGE_BPS)!r}"]
    if p.get("allow_overlap"):
        cmd += ["--allow-overlap"]
    if compare:
        cmd += ["--compare", ",".join(x["market_id"] for x in p["extras"])]
    elif p["want_insights"]:
        cmd += ["--insights"]
    return cmd


def run_engine(p, compare=False, timeout=None, cmd_fn=None):
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
            cmd = cmd_fn(jp, td) if cmd_fn else engine_cmd(p, jp, compare=compare)
            t0 = time.time()
            try:
                proc = subprocess.run(cmd, cwd=ENGINE_DIR, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return err(f"backtest timed out after {timeout}s (market data fetch was slow); try again, "
                           "the candle cache makes repeat runs fast", 504)
            if proc.returncode != 0 or not os.path.exists(jp):
                msg = (proc.stderr or proc.stdout or "engine failed").strip().splitlines()
                last = msg[-1] if msg else "engine failed"
                return public_engine_error(last)
            out = json.load(open(jp))
            out["elapsed_s"] = round(time.time() - t0, 1)
            return jsonify(out)
    finally:
        for lk in reversed(got):
            lk.release()
        _run_sem.release()


def asset_version():
    """Cache-bust query for app.js / style.css. Prefer the git commit, then file mtime."""
    for key in ("ASSET_VERSION", "RENDER_GIT_COMMIT", "RENDER_GIT_COMMIT_SHA"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v[:12]
    try:
        v = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ENGINE_DIR,
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip()
        if v:
            return v[:12]
    except Exception:
        pass
    try:
        return str(int(os.path.getmtime(os.path.join(HERE, "static", "app.js"))))
    except OSError:
        return "1"


_ASSET_V = asset_version()


def _html_page(name):
    path = os.path.join(HERE, "static", name)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    ver = _ASSET_V
    for asset in ("style.css", "app.js", "p2.js", "p3.js", "account.js", "auth.js", "ui.js",
                  "vendor/lightweight-charts.standalone.production.js"):
        attr = "href" if asset.endswith(".css") else "src"
        html = html.replace(f'{attr}="/static/{asset}"', f'{attr}="/static/{asset}?v={ver}"', 1)
    resp = app.response_class(html, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.get("/signin")
@app.get("/signup")
@app.get("/forgot")
def auth_page():
    return _html_page("auth.html")


@app.get("/copy-trading")
def copy_trading_page():
    return _html_page("index.html")


@app.get("/")
def index():
    return _html_page("index.html")


@app.get("/api/health")
def health():
    return jsonify(ok=True, screenshot=shot.enabled(), require_login=auth.require_login_enabled(),
                   db=auth.db_kind(), db_ok=auth.db_ok())


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
        import sources
        return err(sources.friendly_source_error(e) or "Price data source is busy, try again in a minute", 503)
    if not bars:
        return err("no recent candles from MFP for this market", 502)
    t = max(bars)
    payload = dict(market_id=m["market_id"], symbol=m["symbol"], price=bars[t][4], bar_open_ms=t,
                   note="last closed 15m close on the MFP feed (the tester's reference price)")
    _price_cache[m["market_id"]] = (time.time(), payload)
    return jsonify(payload)


@app.get("/api/screenshot")
def screenshot_status():
    out = dict(enabled=shot.enabled(), max_bytes=shot.MAX_BYTES,
               types=["image/png", "image/jpeg", "image/webp"],
               daily_limit=shot.daily_limit())
    if shot.enabled():
        out["model"] = shot.model_name()
    return jsonify(out)


@app.post("/api/screenshot")
def read_screenshot():
    """Read a chart image with OpenAI vision and return fields for the user to review.

    Never starts a backtest. Counts as a heavy request once the image is valid.
    The daily cap is charged only after a billed vision response (a successful
    extract, or an unreadable-chart 422). Quota, rate-limit and transport
    failures are not counted.
    """
    if not shot.enabled():
        return err("Screenshot reading isn't enabled yet", 503)
    f = request.files.get("image") or request.files.get("file") or request.files.get("screenshot")
    if f is None:
        return err("Please attach a chart screenshot.")
    pattern = shot.clean_pattern(request.form.get("pattern") or request.args.get("pattern"))
    try:
        data = f.read()
    except Exception:
        return err("Please attach a chart screenshot.")
    try:
        shot.validate_image(data, getattr(f, "filename", "") or "")
    except shot.ScreenshotError as e:
        return err(e.message, e.status)
    limited = rate_limit_heavy()
    if limited:
        return limited
    try:
        day = rate_limit_screenshot_day()
        if day:
            return day
        try:
            markets = tester.load_markets()
        except Exception as e:
            return err(f"could not load MFP markets list: {e}", 502)
        try:
            payload = shot.extract_setup(data, getattr(f, "filename", "") or "", pattern, markets)
        except shot.ScreenshotError as e:
            if e.status == 422:
                screenshot_day_record()
            return err(e.message, e.status)
        screenshot_day_record()
        return jsonify(payload)
    finally:
        rate_done_heavy()


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
        timeout = RUN_TIMEOUT + (len(p["tf_sweep"]) * SWEEP_PER_TF if p.get("tf_sweep") else 0)
        if p.get("tf_sweep"):
            p["want_insights"] = False  # a sweep already reruns the test up to 6 times
        uid = None
        if p.get("strategy_id"):  # running a saved strategy: the result snapshot goes into the owner's library
            u, bad = library._guard()
            if bad:
                return bad
            if not library.owned_strategy(u["id"], p["strategy_id"]):
                return err("Strategy not found.", 404)
            uid = u["id"]
        resp = attach_pattern(run_engine(p, compare=False, timeout=timeout), pattern_from_body(d))
        return attach_library_run(resp, uid, p, d) if uid else resp
    finally:
        rate_done_heavy()


SWEEP_PER_TF = int(os.environ.get("SWEEP_PER_TF_TIMEOUT", "90"))
STRATEGY_TIMEOUT = int(os.environ.get("STRATEGY_TIMEOUT", "90"))  # per strategy in a comparison


def parse_strategies_body(d):
    """Validate /api/strategies: the usual setup fields + 2-6 strategies. Returns (p, spec)."""
    import strategy_compare as SC
    if not isinstance(d, dict):
        raise ValueError("send a JSON body with market, side, entry, tp, sl and strategies")
    base = {k: v for k, v in d.items() if k not in ("strategies", "mode", "tf_sweep", "compare")}
    base["mode"] = "similar"
    p = parse_test_body(dict(base, tf=d.get("tf") or "1h"), require_compare=False)
    spec = dict(market=p["market"]["market_id"], side=p["side"], entry=p["entry"], tp=p["tp"], sl=p["sl"],
                days=p["days"], tf=p["setup_tf"], slippage=p["slip"], slippage_unit=p["slip_unit"],
                htf_confirm=p["htf"], expiry=p["expiry"], max_hold=p["max_hold"], strategies=d.get("strategies"))
    SC.validate(spec)
    return p, spec


@app.post("/api/strategies")
def run_strategies():
    """Strategy comparison: 2-6 setups under identical conditions (same market, window, costs, timeframe)."""
    limited = rate_limit_heavy()
    if limited:
        return limited
    try:
        d = request.get_json(silent=True)
        try:
            p, spec = parse_strategies_body(d)
        except ValueError as e:
            return err(str(e))

        def cmd(jp, td):
            sp = os.path.join(td, "spec.json")
            with open(sp, "w") as f:
                json.dump(spec, f)
            return [sys.executable, os.path.join(ENGINE_DIR, "strategy_compare.py"), sp, jp]
        timeout = RUN_TIMEOUT + STRATEGY_TIMEOUT * len(spec["strategies"])
        return run_engine(p, timeout=timeout, cmd_fn=cmd)
    finally:
        rate_done_heavy()


@app.get("/api/strategy-types")
def strategy_types():
    import strategy_compare as SC
    import strategies as R
    return jsonify(types={k: dict(label=v["label"], kind=v["kind"], note=v["note"],
                                  params=(R.RULES[v["rule"]]["params"] if v.get("rule") else {}),
                                  bounds=(R.RULES[v["rule"]]["bounds"] if v.get("rule") else {}))
                          for k, v in SC.TYPES.items()},
                   named={k: dict(type=v[0], how=v[1]) for k, v in SC.NAMED.items()},
                   min=SC.MIN_STRATEGIES, max=SC.MAX_STRATEGIES)


# ---------------------------------------------------------------- trade replay: the stored candles of one trade
REPLAY_MAX_BARS = 3000
RATE_CANDLES_PER_MIN = int(os.environ.get("RATE_CANDLES_PER_MIN", "40"))
_candle_hits = {}


def _int_arg(name):
    v = request.args.get(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number of milliseconds") from None


@app.get("/api/candles")
def replay_candles():
    """Candles for a trade replay. Loads the same range the backtest loaded (load_from..load_to) so the merged
    series is identical, then returns only from..to. Real stored candles only; nothing is filled in."""
    gated = auth.gate_tools()
    if gated:
        return gated
    ip, now = client_ip(), time.time()
    with _rate_guard:
        hits = [t for t in _candle_hits.get(ip, []) if now - t < 60]
        if len(hits) >= RATE_CANDLES_PER_MIN:
            return err("too many replay requests from your network, try again in a minute", 429)
        hits.append(now)
        _candle_hits[ip] = hits
    try:
        mid = str(request.args.get("market") or "")
        interval = str(request.args.get("interval") or "15m")
        if interval not in ("5m", "15m", "30m", "1h"):
            raise ValueError("interval must be 5m, 15m, 30m or 1h")
        lo, hi = _int_arg("from"), _int_arg("to")
        lf = _int_arg("load_from") if request.args.get("load_from") else lo
        lt = _int_arg("load_to") if request.args.get("load_to") else hi
        step = tester.STEP[interval]
        if not (lo < hi) or (hi - lo) // step > REPLAY_MAX_BARS:
            raise ValueError(f"the window must be 1 to {REPLAY_MAX_BARS} candles")
        if not (lf <= lo and lt >= hi - step) or lt - lf > 400 * 86400_000:
            raise ValueError("load range must contain the window and be at most 400 days")
        m = next((x for x in tester.load_markets() if x["market_id"] == mid), None)
        if m is None:
            m = resolve(mid)
    except ValueError as e:
        return err(str(e))
    import sources
    lk = market_lock(m["market_id"])
    if not lk.acquire(timeout=20):
        return err("server busy with other tests, try again in a moment", 429)
    try:
        bars, prov = sources.load(m, interval, lf, lt, kind=tester.asset_class(m), use_backups=True)
    except Exception as e:
        msg = sources.friendly_source_error(e)
        return err(msg or "Market data is unavailable right now, try again in a minute", 503)
    finally:
        lk.release()
    rows = [[b[0], b[1], b[2], b[3], b[4]] for b in bars if lo <= b[0] < hi]
    segs = [dict(source=sg["source"], label=sg.get("label"), from_ms=sg["from_ms"], to_ms=sg["to_ms"])
            for sg in prov.get("segments") or [] if sg["from_ms"] < hi and sg["to_ms"] > lo]
    resp = jsonify(market_id=m["market_id"], interval=interval, from_ms=lo, to_ms=hi, candles=rows, sources=segs)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


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
        return attach_pattern(run_engine(p, compare=True, timeout=timeout), pattern_from_body(d))
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
    if p["similar_tf"] not in ("15m", "30m", "1h", "4h"):
        return err("the live scan works with 15m, 30m, 1h or 4h fingerprints")
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
        except SystemExit as e:
            import sources
            return err(sources.friendly_source_error(e) or str(e), 503 if sources.friendly_source_error(e) else 422)
        except Exception as e:
            import sources
            msg = sources.friendly_source_error(e)
            if msg:
                return err(msg, 503)
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
