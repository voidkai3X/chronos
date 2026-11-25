# CHRONOS web app

A small web front end for `../tester.py`. You pick an MFP market, a side, entry, TP and SL, and a mode. The default is **Similar moments**: the engine fingerprints the setup's recent candles and replays the same ATR-scaled trade on the closest past windows, then lists match dates and scores. **Every 4h close** and **Pattern only** (an optional rule-based supply/demand retest) are still there. After a single-pair result you can test the same setup on up to 5 other markets, and if the backtest passes (edge or possible edge) you can scan live markets whose current price action looks like the setup.

The CLI is unchanged. The web app calls `tester.py --json --json-out` in a subprocess and returns that JSON. `app.py` loads the engine from the repository root (the parent of this `web/` directory).

Read-only: it uses only MFP's public, keyless market data (`/v1/markets` and the market-data WebSocket). It has no API keys and never places orders.

## Run locally

From `chronos/web/`:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                 # serves http://127.0.0.1:8080
```

Environment variables:
- `PORT`: default 8080
- `HOST`: default 127.0.0.1 (localhost only; set `0.0.0.0` if you need LAN access)
- `TESTER_TIMEOUT`: seconds per backtest, default 240
- `COMPARE_TIMEOUT`: seconds for a compare run (up to 6 markets); default is `max(TESTER_TIMEOUT, 120 * markets)`
- `SCAN_TIMEOUT`: seconds budget note for a live scan, default 180
- `TESTER_MAX_PARALLEL`: global cap on concurrent engine runs, default 2
- `RATE_HEAVY_PER_MIN`: max `/api/test`, `/api/compare` and `/api/scan` calls per client IP per minute, default 6
- `RATE_HEAVY_CONCURRENT_PER_IP`: max concurrent heavy runs per client IP, default 1

A public or shared link can otherwise pile up engine runs on the box. Heavy endpoints return 429 when either per-IP cap is hit, or when the global engine cap is busy. The client IP is `request.remote_addr`, except that `CF-Connecting-IP` (or `X-Forwarded-For`) is trusted only when the request itself comes from localhost (the tunnel). Debug mode is off. There are no debug or admin endpoints and no API keys.

Candles are cached in `../candle_cache/` (shared with the CLI). The first run on a market fetches about 74 days of 15m bars and takes a few seconds to a minute. Repeat runs take under a second.

## API

- `GET /api/markets?q=btc` returns the live MFP markets, filtered by `q`.
- `GET /api/price?market=BTC` returns the last closed 15m close on the MFP feed. This is the same reference price the engine uses, cached for 60s.
- `POST /api/test` takes this JSON body:
  ```json
  {"market": "BTC", "side": "short", "entry": 64780, "tp": 54209, "sl": 66350,
   "days": 60, "expiry": "72h", "trend_filter": false,
   "mode": "similar", "similar_tf": "1h", "similar_bars": 24,
   "insights": false}
  ```
  - Validation:
    - A long needs `sl < entry < tp`. A short needs `tp < entry < sl`.
    - `days` must be from 30 to 90.
    - `expiry` must be from 1h to 14d. Used in similar and every-close modes (pattern uses a longer zone rest).
    - `mode` is `similar` (default), `every-close` or `pattern`. `similar_tf` (15m, 30m, 1h, 4h) and `similar_bars` (8-96, default 24) apply to similar mode. `zone_tf` (30m, 1h or 4h) and `sl_buffer_atr` (0-10, or omit for auto) apply to pattern mode only.
    - `entry_style` is `limit` (default), `sweep` or `confirm`, and works in pattern mode only.
    - `insights` (default true except in similar mode) adds `--insights`. This re-runs the setup as 4 variants on the market and, for FX, on all FX pairs, which takes a few seconds more. The response then has an `insights` object with `findings`, `comparison`, `best`, `fixes` and `disclaimer`.
  - Response: the engine's JSON (`market`, `setup`, `params`, `data`, `fees`, `result` with the verdict, plus `trades` and `elapsed_s`).
  - Errors come back as `{"error": "..."}`:
    - 400 for bad input
    - 422 for an engine error, such as not enough history
    - 429 when the server is busy
    - 504 on timeout

- `GET /api/compare-suggest?market=EURUSD` returns up to 5 suggested other markets in the same family (FX majors, crypto perps, indices, metals and energy, or equities), plus `family` and `limit` (5).
- `POST /api/compare` takes the same body as `/api/test`, plus `compare`: a list of 1 to 5 other market names. Insights are not run (too much extra work). The same setup is ATR-scaled onto each market, sequentially. Response is the engine's compare JSON: `compare_rows` (source + others), `compare_pooled`, `compare_summary` (for example `works on 1 of 5, pooled no edge`), `per_market`, `result`, `trades` and `elapsed_s`. More than 5 names returns 400.

- `POST /api/scan` takes the same body as `/api/test`, plus the last backtest `result` (or `verdict`), optional `query` from that backtest, optional `scope` (`family` default), and `force` / `force_scan`. If the backtest did not pass and `force` is not set, the response is `{gated: true, reason, rows: []}`. Otherwise it fingerprints each in-scope market's latest window and returns `rows` (side, pair, limit, single TP, SL, score, distance from price), `generated` (WAT), `forced`, and `skipped`. The page has no force button; `?force_scan=1` on the URL is only for labelled review screenshots.

You can also prefill the page from the URL. Adding `run=1` runs the test straight away:

```
http://localhost:8080/?market=BTC&side=short&entry=64780&tp=54209&sl=66350&days=60&expiry=72h&run=1
```

URLs also accept `mode=similar|every-close|pattern`, `similar_tf`, `similar_bars`, `zone_tf`, `entry_style=sweep|confirm`, `insights=0`, and `force_scan=1` (labelled forced live scan only; there is no force button in the normal UI).

## Limits (shown on the page)

- Similar mode finds past windows whose recent path looks like your setup. A high score is closeness, not a forecast. Every-close mode tests the setup's shape at every 4h close. Pattern mode is an optional rule-based approximation of a zone retest.
- Insights are rule-based and use only the engine's numbers. The "best variant" is the best of 8 tries on the same data, so it overstates the edge.
- One order or trade runs at a time, so 60 days gives about 10 to 30 trades. Under 20 closed trades reads "too few samples".
- Backtest on past MFP candles only. Not a prediction, not advice. Past results do not guarantee future profit.

## Hosting (not included)

This repository is source only. No hosting, GitHub Pages, or deploy workflow has been set up. The `Dockerfile` here is an example only, with build context at the repository root (`docker build -f web/Dockerfile .`).
