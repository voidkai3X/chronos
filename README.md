# CHRONOS

AI-assisted trading research and historical backtesting on MyFundedPerps (MFP) market data.

CHRONOS replays one trade setup across recent MFP candles and reports how that setup would have done. It has two modes:

- **Every 4h close** (`--mode every-close`, the default): the setup's shape (entry, stop, target in ATR) is placed at every 4h close. This tests the distances, not the reason for the trade.
- **Pattern only** (`--mode pattern`): the tester finds supply zones (for shorts) or demand zones (for longs) by rule and only places the trade when price comes back to a fresh zone. This tests the zone-retest idea itself.

The backtest engine is deterministic and rule-based. A planned insights layer may later reword the engine's numbers; it will not invent results, place orders, or guarantee profit.

Read-only. It uses only MFP's public, keyless market-data WebSocket (`wss://api-stream.myfundedperpetuals.com/v1/market-data`) and the public `/v1/markets` list. It needs no API key and never places an order.

This repository is source only. Hosting, GitHub Pages, and deploy workflows are not included.

## Install

Python 3.10+ is required.

CLI:

```
pip install websockets
```

Web app (includes the CLI dependency):

```
pip install -r web/requirements.txt
```

## Run the CLI

From the repository root:

```
python tester.py BTC long --entry 60400 --tp 65500 --sl 58700
python tester.py XAUUSDT short --entry 3690 --tp 3555 --sl 3735 --days 60
python tester.py AAPL long --entry 349 --tp 365.5 --sl 343.5 --trend-filter
python tester.py BTC long --entry=-1.2% --sl=-2.8% --tp 3R          # percent / R form
python tester.py ETH short --entry=+0.8% --sl=+2% --tp 3R --json     # JSON to stdout
python tester.py SOL long --entry=-1% --sl=-3% --tp 3R --json-out sol.json   # also saves every simulated trade

# pattern mode: only trade retests of fresh supply/demand zones
python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m
python tester.py EURUSD short --entry 1.11993 --tp 1.08353 --sl 1.12886 --mode pattern --zone-tf 4h --basket fx
python tester.py EURUSD short --entry 1.11993 --tp 1.08353 --sl 1.12886 --mode pattern --symbols EURUSD,GBPUSD --sl-buffer-atr 0.5
```

Market names resolve against `/v1/markets`, so `BTC`, `BTCUSDT`, `binance|BTCUSDT`, `AAPL` and `xyz:AAPL` all work.

Level formats:
- `--entry`: a price, or `+/-x%` from the current price.
- `--sl`: a price, or `+/-x%` from the entry.
- `--tp`: a price, `+/-x%` from the entry, or `NR` (N times the risk).
- For negative values, use `=`, as in `--entry=-1%`.

| Option | Default | Meaning |
|---|---|---|
| `--days` | 60 | Test window (30–60 makes sense; MFP history on some markets is shorter) |
| `--interval` | 15m | Candles used to simulate fills (5m, 15m, 30m, 1h) |
| `--expiry` | 72h | Unfilled order is cancelled after this |
| `--max-hold` | 7d | Time stop after fill, exits at market. `none` = hold until TP or SL |
| `--geometry` | atr | Scale the setup by 4h ATR (`atr`) or keep fixed percentages (`pct`) |
| `--trend-filter` | off | Only place when the 4h EMA50 slope (vs 5 bars back) agrees with the side |
| `--sweep-window` | = expiry | Window after the fill for the stop check |
| `--sweep-atr` | 1.0 | How far past the stop still counts as a "wick" sweep |
| `--no-fees` | off | Ignore commissions and swap |
| `--refresh` | off | Ignore the candle cache |
| `--ref` | last close | Override the current price |
| `--mode` | every-close | `every-close` or `pattern` |
| `--basket` / `--symbols` | none | Also run on these markets and pool the results: `fx` (all MFP FX pairs) and/or a comma list. Works in both modes |

Pattern-mode options:

| Option | Default | Meaning |
|---|---|---|
| `--zone-tf` | 4h | Timeframe zones are found on: 30m, 1h or 4h |
| `--sl-buffer-atr` | derived | Stop distance beyond the zone's far edge, in zone-timeframe ATR. If not set, it is taken from your own SL (see below) |
| `--max-touches` | 1 | A zone is usable until price has come back to it this many times. 1 = fresh zones only |
| `--zone-max-age` | 30d | Zones older than this are dropped |
| `--expiry` | 30d | Longest time a limit rests on one zone (every-close mode default stays 72h) |
| `--impulse-atr` | 1.0 | Minimum move away from the base, in ATR |
| `--max-base` | 6 | Most base candles in a zone (1-6) |
| `--base-body-atr` | 0.5 | Largest body a base candle may have, in ATR |
| `--trend-filter` | off | Only place when the 4h EMA50 slope agrees with the side (same rule as every-close mode) |

## Run the web app

From `chronos/web/`:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                 # serves http://0.0.0.0:8080
```

Environment variables: `PORT` (default 8080), `HOST` (default 0.0.0.0), `TESTER_TIMEOUT` (seconds per backtest, default 150), `TESTER_MAX_PARALLEL` (concurrent backtests, default 2).

The app is a thin wrapper around `tester.py`. It calls the engine with `--json --json-out` and shows the verdict, stats, a cumulative-R equity curve, and every simulated trade. See `web/README.md` for the HTTP API.

`web/app.py` finds `tester.py` in the repository root (the parent of `web/`).

## Methodology

### Every 4h close mode (default)

1. Your entry, stop and target are turned into a shape measured in 4h ATR(14). For example: entry 0.5 ATR under price, stop 1.1 ATR under entry, target 3.35 ATR over entry.
2. The shape is placed at every 4h close when flat (no open order or trade). Placement uses only data known at that moment: the last closed 4h close as price, and ATR and EMA from closed 4h bars. There is no look-ahead.
3. Fills are simulated on 15m candles:
   - **Limit entry** (below price for a long, above for a short): fills when price trades through it, at the open if the bar gaps past the level.
   - **Stop entry** (on the other side of price): fills when price breaks the level.
   - Unfilled orders cancel at the 72h expiry (configurable).
   - After a fill, a 7d time stop exits at market if TP or SL has not hit (`--max-hold none` disables this).
   - **Conservative tie rules:**
     - If TP and SL are in the same bar, it counts as SL.
     - SL can hit in the fill bar, but TP is not counted there.
     - A gap through the SL exits at the bar open.
   - Only one order or trade exists at a time, with no overlap.
4. Fees follow MFP's published standard rates (docs.myfundedperpetuals.com/guides/commissions-and-fees). Swap is charged hourly. Elite discounts are not applied.

   | Asset class | Commission per fill | Swap per day |
   |---|---|---|
   | Crypto | 0.03% | 0.03% |
   | Stocks, commodities and indices | 0.005% | 0.015% |
   | Forex | 0.0025% | 0.005% |

5. **Stop-sweep check.** For each stop-out, the engine checks whether price still reached the TP within the window, and how far past the stop it went first. If at least 20% of losers went no more than 1 ATR past the stop and then hit TP, the tool flags "stop too tight".
6. A verdict needs at least 20 closed trades. Below that, the result is `too few samples`. Other verdicts: `no edge` (expectancy at or below 0 after fees), `possible edge, not proven` (t < 2), `edge on this sample`.

### Pattern mode

**How a zone is defined (rule-based, on `--zone-tf` candles, closed candles only):**

1. **Base:** 1 to 6 small candles in a row. "Small" means a body no bigger than 0.5 x ATR(14).
2. **Leg out:** right after the base, 1 to 3 candles that all close in the trade direction (down for supply, up for demand), each with a body at least half of its range. The first leg candle must not itself be small.
3. **Displacement:** by the close of the leg, price is at least 1 x ATR beyond the base (`--impulse-atr`).
4. **Zone edges:**
   - Supply (shorts): near edge = lowest low of the base; far edge = highest high of the base and the first leg candle.
   - Demand (longs): near edge = highest high of the base; far edge = lowest low of the base and the first leg candle.
   - Zones taller than 3 ATR are skipped.
5. The zone only exists from the close of the last leg candle. Nothing before that moment can use it.
6. **Life of a zone:** it is fresh until price comes back to its near edge (`--max-touches`, default 1 = first retest only). It dies when a zone-timeframe candle closes beyond the far edge, or after 30 days.

**How a trade is placed:**

- At each zone-timeframe close with no open trade, the tester takes the fresh zones that already exist and picks the one nearest to price (price must still be on the outside of it).
- It rests a limit at the zone's near edge.
  - **Stop:** beyond the far edge by `--sl-buffer-atr` x ATR, so the stop is past the whole zone, not at its edge.
  - **Target:** your R:R. For example, 1:3.67 for the AUD/USD example.
- The limit is re-checked at every close. It moves to a nearer fresh zone if one appears, and is cancelled when its zone dies or after `--expiry`.
- **Fills and exits** use the same engine as every-close mode: 15m candles, SL-first ties, no TP on the fill bar, gaps through the SL exit at the open, a 7d time stop, and MFP fees.

**Default stop buffer (taken from your setup):** the tester looks for the latest detected zone that contains your entry, then measures how far your SL sits past that zone's far edge, in ATR. If no detected zone contains your entry, it uses your risk in ATR minus the median zone height. Either way, the replayed stop is as far beyond the zone as yours. The report says which rule it used.

**Report:** zones found in the window, how many were touched, broken and traded, plus the same stats, stop-sweep check and 20-trade verdict as every-close mode. With `--basket`, it lists each market and then a pooled result over all trades. Other markets reuse your R:R and stop buffer in ATR, so nothing depends on their price level.

### Output

Output includes orders, fills, unfilled orders, TP, SL and time exits; win rate and break-even win rate before and after fees; average R (net and gross), expectancy, and total R; max losing streak and max drawdown in R; a first-half vs second-half average R stability check; and the stop check. `--json` prints this as JSON. `--json-out` also saves every simulated trade.

## Checks

`python tests/test_pattern.py` runs offline pattern-mode checks on synthetic candles:

- **Zone detection has no look-ahead.** Zones found on data cut at time c are exactly the zones the full run had confirmed by c. This runs for 30m, 1h and 4h, both sides, and 4 cut points.
- **Orders never use future zones.** Every order's zone was confirmed at or before the order time, and it was untouched before then.
- **Truncation.** Cutting off future candles does not change any pattern trade that had already finished.
- **Hand-built retest.** A base, then a drop, then a rally back to the zone low. The order fills at the zone low with the stop above the zone, and it hits TP for exactly +2R. With the data cut before the drop candle closes, there is no zone and no order.

`python tests/sanity.py` runs three checks (the look-ahead and coin-flip checks fetch live MFP candles):

- **Look-ahead.** Deleting future candles must not change any trade that had already finished: 5 markets, both sides, 3 cut points.
- **Tie rule.** A synthetic bar that touches both TP and SL must count as SL. This check is offline.
- **Coin-flip baseline.** A 1:1 market-entry setup with no fees across 10 markets should win about 50%. The last recorded run in the source tester documentation was 49.8% (762/1530).

## Limitations

- MFP history is currently about 60 days on Binance-sourced markets. Hyperliquid `xyz:` markets started around 2 Oct 2026, so those windows are shorter. A rolling 3-month window is on the roadmap and is not available yet.
- Every-close mode tests the setup's shape at every 4h close, not only where a pattern appears. `--trend-filter` is a rough context filter only.
- Pattern mode's zones are a **rule-based approximation** of the zones a trader draws by eye. It does not see liquidity sweeps, BOS confirmation, equal highs and lows, efficiency or imbalance, or higher-timeframe context. It may miss zones you would take and take zones you would skip. Your own chart zone may not match any detected zone; see "Default stop buffer" above.
- Pattern mode trades the first retest blind with a resting limit. The course's preferred entry waits for confirmation on a lower timeframe, and that is not modelled yet.
- With about 60 days of data, a single market gives only a handful of fresh zones (about 5 on 4h, about 25 on 30m). Use `--basket` to reach 20+ trades, but pooling assumes the pattern behaves the same across pairs.
- The zone rule's defaults were chosen before looking at results and were not optimised. Changing them and keeping whichever looks best would be curve-fitting on a tiny sample.
- One trade at a time gives about 10–30 trades in 60 days. Most single setups will read "too few samples". Treat any result under about 50 trades as a rough check, not proof.
- 15m candles cannot show the order of moves inside a bar. The tie rules always assume the worse case.
- Funding and slippage beyond bar gaps are not modelled yet.
- Results describe the past only. They are not a forecast and not advice.

## Disclaimer

Past results do not guarantee future profit. CHRONOS does not predict prices, does not promise an edge, and must never be read as a guarantee of profitability. Do not fabricate historical data or backtesting results; only numbers the engine produces from real candles should be reported.

## Example results

`examples/` holds the text reports for the two example setups (AUD/USD short and EUR/USD short) in pattern mode, on one market and on the FX basket, plus every-close runs for comparison. The run time is in `examples/RUN_TIME.txt`. The numbers move as the 60-day window rolls forward.

## Roadmap

**Week 1**
- Extend coverage toward a 90-day window by merging additional public sources, with every bar labelled by source.
- Model slippage (beyond the current gap-through-SL-at-open rule).
- Add per-IP rate limits before any public hosting.

**Later**
- Funding costs.
- Walk-forward train/test split.
- Confidence ranges around reported metrics.
- Pattern mode v2: lower-timeframe confirmation entry, liquidity-sweep and equal-high/low filters, efficiency-based stop choice.
- A rule-based insights layer: AI may only reword numbers the engine already produced.

Hosting is intentionally not part of this push.
