# CHRONOS web app

A small web front end for `../tester.py`. You pick an MFP market, a side, entry, TP and SL, and a mode: Every 4h close or Pattern only (fresh supply/demand zone retests, with a zone-timeframe picker). The page runs the engine and shows the verdict, stats, a cumulative-R equity curve and every simulated trade.

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
- `TESTER_TIMEOUT`: seconds per backtest, default 150
- `TESTER_MAX_PARALLEL`: concurrent backtests, default 2

Candles are cached in `../candle_cache/` (shared with the CLI). The first run on a market fetches about 74 days of 15m bars and takes a few seconds to a minute. Repeat runs take under a second.

## API

- `GET /api/markets?q=btc` returns the live MFP markets, filtered by `q`.
- `GET /api/price?market=BTC` returns the last closed 15m close on the MFP feed. This is the same reference price the engine uses, cached for 60s.
- `POST /api/test` takes this JSON body:
  ```json
  {"market": "BTC", "side": "short", "entry": 64780, "tp": 54209, "sl": 66350,
   "days": 60, "expiry": "72h", "trend_filter": false,
   "mode": "pattern", "zone_tf": "4h", "sl_buffer_atr": null}
  ```
  - Validation:
    - A long needs `sl < entry < tp`. A short needs `tp < entry < sl`.
    - `days` must be from 30 to 60.
    - `expiry` must be from 1h to 14d. It is only used in every-close mode.
    - `mode` is `every-close` (default) or `pattern`. `zone_tf` (30m, 1h or 4h) and `sl_buffer_atr` (0-10, or omit for auto) apply to pattern mode only.
  - Response: the engine's JSON (`market`, `setup`, `params`, `data`, `fees`, `result` with the verdict, plus `trades` and `elapsed_s`).
  - Errors come back as `{"error": "..."}`:
    - 400 for bad input
    - 422 for an engine error, such as not enough history
    - 429 when the server is busy
    - 504 on timeout

You can also prefill the page from the URL. Adding `run=1` runs the test straight away:

```
http://localhost:8080/?market=BTC&side=short&entry=64780&tp=54209&sl=66350&days=60&expiry=72h&run=1
```

## Limits (shown on the page)

- It tests the setup's shape at every 4h close, not only where your pattern (sweep, BOS, zone) appears.
- One order or trade runs at a time, so 60 days gives about 10 to 30 trades. Under 20 closed trades reads "too few samples".
- Backtest on past MFP candles only. Not a prediction, not advice. Past results do not guarantee future profit.

## Hosting (not included)

This repository is source only. No hosting, GitHub Pages, or deploy workflow has been set up. The `Dockerfile` here is an example only, with build context at the repository root (`docker build -f web/Dockerfile .`).
