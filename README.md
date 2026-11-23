# CHRONOS

AI-assisted trading research and historical backtesting on MyFundedPerps (MFP) market data.

CHRONOS replays one trade setup across recent MFP candles and reports how that same setup shape would have done. The backtest engine is deterministic and rule-based. A planned insights layer may later reword the engine's numbers; it will not invent results, place orders, or guarantee profit.

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

Output includes orders, fills, unfilled orders, TP, SL and time exits; win rate and break-even win rate before and after fees; average R (net and gross), expectancy, and total R; max losing streak and max drawdown in R; a first-half vs second-half average R stability check; and the stop check. `--json` prints this as JSON. `--json-out` also saves every simulated trade.

## Checks

`python tests/sanity.py` runs three checks (the look-ahead and coin-flip checks fetch live MFP candles):

- **Look-ahead.** Deleting future candles must not change any trade that had already finished: 5 markets, both sides, 3 cut points.
- **Tie rule.** A synthetic bar that touches both TP and SL must count as SL. This check is offline.
- **Coin-flip baseline.** A 1:1 market-entry setup with no fees across 10 markets should win about 50%. The last recorded run in the source tester documentation was 49.8% (762/1530).

## Limitations

- MFP history is currently about 60 days on Binance-sourced markets. Hyperliquid `xyz:` markets started around 2 Oct 2026, so those windows are shorter. A rolling 3-month window is on the roadmap and is not available yet.
- The tool tests the setup's shape at every 4h close, not only where a pattern (sweep, BOS, zone) appears. `--trend-filter` is a rough context filter only.
- One trade at a time gives about 10–30 trades in 60 days. Most single setups will read "too few samples". Treat any result under about 50 trades as a rough check, not proof.
- 15m candles cannot show the order of moves inside a bar. The tie rules always assume the worse case.
- Funding and slippage beyond bar gaps are not modelled yet.
- Results describe the past only. They are not a forecast and not advice.

## Disclaimer

Past results do not guarantee future profit. CHRONOS does not predict prices, does not promise an edge, and must never be read as a guarantee of profitability. Do not fabricate historical data or backtesting results; only numbers the engine produces from real candles should be reported.

## Roadmap

**Week 1**
- Extend coverage toward a 90-day window by merging additional public sources, with every bar labelled by source.
- Model slippage (beyond the current gap-through-SL-at-open rule).
- Add per-IP rate limits before any public hosting.

**Later**
- Funding costs.
- Walk-forward train/test split.
- Confidence ranges around reported metrics.
- Pattern-only mode (place the shape only where a stated pattern appears, not at every 4h close).
- A rule-based insights layer: AI may only reword numbers the engine already produced.

Hosting is intentionally not part of this push.
