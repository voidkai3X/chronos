# CHRONOS web app

A small web front end for `../tester.py`. You pick an MFP market, a side, entry, TP and SL, and a mode: Every 4h close or Pattern only (fresh supply/demand zone retests, with a zone-timeframe picker). The page runs the engine and shows the verdict, stats, a cumulative-R equity curve and every simulated trade. After a single-pair result, a "Test on other pairs" section lets you pick up to 5 similar markets (suggestions pre-selected) and compare the same setup on them.

The CLI is unchanged. The web app calls `tester.py --json --json-out` in a subprocess and returns that JSON. `app.py` loads the engine from the repository root (the parent of this `web/` directory).

Read-only: it uses only MFP's public, keyless market data (`/v1/markets` and the market-data WebSocket). It has no API keys and never places orders.

## Run locally

From `chronos/web/`:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                 # serves http://0.0.0.0:8080
```

Environment variables:
- `PORT`: default 8080
- `HOST`: default 0.0.0.0
- `TESTER_TIMEOUT`: seconds per backtest, default 240
- `COMPARE_TIMEOUT`: seconds for a compare run (up to 6 markets); default is `max(TESTER_TIMEOUT, 120 * markets)`
- `TESTER_MAX_PARALLEL`: concurrent backtests, default 2

Candles are cached in `../candle_cache/` (shared with the CLI). The first run on a market fetches about 74 days of 15m bars and takes a few seconds to a minute. Repeat runs take under a second.

## API

- `GET /api/markets?q=btc` returns the live MFP markets, filtered by `q`.
- `GET /api/price?market=BTC` returns the last closed 15m close on the MFP feed. This is the same reference price the engine uses, cached for 60s.
- `POST /api/test` takes this JSON body:
  ```json
  {"market": "BTC", "side": "short", "entry": 64780, "tp": 54209, "sl": 66350,
   "days": 60, "expiry": "72h", "trend_filter": false,
   "mode": "pattern", "zone_tf": "4h", "sl_buffer_atr": null,
   "entry_style": "limit", "insights": true}
  ```
  - Validation:
    - A long needs `sl < entry < tp`. A short needs `tp < entry < sl`.
    - `days` must be from 30 to 90.
    - `expiry` must be from 1h to 14d. It is only used in every-close mode.
    - `mode` is `every-close` (default) or `pattern`. `zone_tf` (30m, 1h or 4h) and `sl_buffer_atr` (0-10, or omit for auto) apply to pattern mode only.
    - `entry_style` is `limit` (default), `sweep` or `confirm`, and works in pattern mode only.
    - `insights` (default true) adds `--insights`. This re-runs the setup as 4 variants on the market and, for FX, on all FX pairs, which takes a few seconds more. The response then has an `insights` object with `findings`, `comparison`, `best`, `fixes` and `disclaimer`.
  - Response: the engine's JSON (`market`, `setup`, `params`, `data`, `fees`, `result` with the verdict, plus `trades` and `elapsed_s`).
  - Errors come back as `{"error": "..."}`:
    - 400 for bad input
    - 422 for an engine error, such as not enough history
    - 429 when the server is busy
    - 504 on timeout

- `GET /api/compare-suggest?market=EURUSD` returns up to 5 suggested other markets in the same family (FX majors, crypto perps, indices, metals and energy, or equities), plus `family` and `limit` (5).
- `POST /api/compare` takes the same body as `/api/test`, plus `compare`: a list of 1 to 5 other market names. Insights are not run (too much extra work). The same setup is ATR-scaled onto each market, sequentially. Response is the engine's compare JSON: `compare_rows` (source + others), `compare_pooled`, `compare_summary` (for example `works on 1 of 5, pooled no edge`), `per_market`, `result`, `trades` and `elapsed_s`. More than 5 names returns 400.

You can also prefill the page from the URL. Adding `run=1` runs the test straight away:

```
http://localhost:8080/?market=BTC&side=short&entry=64780&tp=54209&sl=66350&days=60&expiry=72h&run=1
```

Pattern-mode URLs also accept `mode=pattern`, `zone_tf=30m`, `entry_style=sweep|confirm`, and `insights=0` to turn insights off.

## Limits (shown on the page)

- Every-close mode tests the setup's shape at every 4h close, not only where your pattern appears. Pattern mode with sweep or confirm entries is closer to how the setup is traded, but it is still a mechanical rule.
- Insights are rule-based and use only the engine's numbers. The "best variant" is the best of 8 tries on the same data, so it overstates the edge.
- One order or trade runs at a time, so 60 days gives about 10 to 30 trades. Under 20 closed trades reads "too few samples".
- Backtest on past MFP candles only. Not a prediction, not advice. Past results do not guarantee future profit.

## Hosting (not included)

This repository is source only. No hosting, GitHub Pages, or deploy workflow has been set up. The `Dockerfile` here is an example only, with build context at the repository root (`docker build -f web/Dockerfile .`).
