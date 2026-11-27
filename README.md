# CHRONOS

AI-assisted trading research and historical backtesting on MyFundedPerps (MFP) market data.

CHRONOS replays one trade setup across recent MFP candles and reports how that setup would have done. The core idea is to take whatever setup you picture — order flow, ICT, SMC, a zone retest, or just a shape — and test it on past moments with very similar price action.

- **Similar moments** (`--mode similar`): fingerprints the last N candles at the setup moment (scale-free path plus trend, volatility, swings, sweeps and impulse), searches the same market for the closest non-overlapping windows, and places the same ATR-scaled trade. This is the default in the web app.
- **Every 4h close** (`--mode every-close`, the CLI default): the setup's shape (entry, stop, target in ATR) is placed at every 4h close. This tests the distances, not the reason for the trade.
- **Pattern only** (`--mode pattern`): optional. The tester finds supply or demand areas by rule and only places the trade on a fresh retest. This is not the headline mode.

The backtest engine is deterministic and rule-based. A planned insights layer may later reword the engine's numbers; it will not invent results, place orders, or guarantee profit.

Read-only. Candles come first from MFP's public, keyless market-data WebSocket (`wss://api-stream.myfundedperpetuals.com/v1/market-data`), with the public `/v1/markets` list for symbols. Where MFP history is shorter than the 90-day window, older bars are backfilled from public perp feeds (OKX, then Bybit) or, for indices and commodities with no perp, Yahoo Finance and Dukascopy. Every result says which source covered which dates. Market data needs no API key and it never places an order. Optional screenshot reading in the web app uses `OPENAI_API_KEY` when set. See [Data sources](#data-sources) and [Run the web app](#run-the-web-app).

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

# similar mode: replay on past windows with similar price action
python tester.py HYPE long --entry 56.65 --tp 69.81 --sl 53.86 --mode similar --days 90
python tester.py USDJPY short --entry 159.50 --tp 156.83 --sl 160.465 --mode similar --similar-tf 1h --similar-bars 24
python tester.py HYPE long --entry 56.65 --tp 69.81 --sl 53.86 --mode similar --scan
python tester.py USDJPY short --entry 159.50 --tp 156.83 --sl 160.465 --mode similar --force-scan --scan-scope family

# pattern mode (optional): only trade retests of fresh supply/demand areas
python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m
python tester.py EURUSD short --entry 1.11993 --tp 1.08353 --sl 1.12886 --mode pattern --zone-tf 4h --basket fx
python tester.py EURUSD short --entry 1.11993 --tp 1.08353 --sl 1.12886 --compare GBPUSD,AUDUSD,USDJPY,USDCAD,USDCHF
python tester.py HYPE long --entry 56.65 --tp 69.81 --sl 53.86 --compare BTC,ETH,SOL,XRP,BNB
python tester.py EURUSD short --entry 1.11993 --tp 1.08353 --sl 1.12886 --mode pattern --symbols EURUSD,GBPUSD --sl-buffer-atr 0.5

# entry styles on zone taps, and coaching
python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m --entry-style sweep
python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m --entry-style confirm --confirm-tf 15m
python tester.py AUDUSD short --entry 0.66629 --tp 0.65881 --sl 0.66833 --mode pattern --zone-tf 30m --insights
```

Market names resolve against `/v1/markets`, so `BTC`, `BTCUSDT`, `binance|BTCUSDT`, `AAPL` and `xyz:AAPL` all work.

Level formats:
- `--entry`: a price, or `+/-x%` from the current price.
- `--sl`: a price, or `+/-x%` from the entry.
- `--tp`: a price, `+/-x%` from the entry, or `NR` (N times the risk).
- For negative values, use `=`, as in `--entry=-1%`.

| Option | Default | Meaning |
|---|---|---|
| `--days` | 90 | Test window. 14 more days are loaded before it as indicator warm-up. If a market has less history, the test runs on what exists and reports the real number of days |
| `--no-backups` | off | MFP feed only: no backfill from OKX, Bybit, Yahoo or Dukascopy |
| `--interval` | 15m | Candles used to simulate fills (5m, 15m, 30m, 1h) |
| `--setup-tf` | 4h | every-close and rule modes: the timeframe whose closes decide setups (5m, 15m, 30m, 1h, 4h, 1d). Must not be finer than `--interval` |
| `--htf-confirm` | off | Higher-timeframe confirmation (1h, 4h, 1d, above the setup timeframe): only take a setup when the last closed HTF candle closed above a rising EMA20 (EMA higher than 3 candles earlier) for a long, below a falling one for a short. Not enough HTF history = no confirmation |
| `--tf-sweep` | off | Rerun the same setup for each setup timeframe (`all` or a list like `15m,1h,4h`) and add a comparison table. 5m setups fill on 5m candles, the rest on 15m |
| `--mode rule` | - | Mechanical rule instead of your levels' location: `--rule ema_cross`, `breakout`, `liquidity_sweep` or `fvg`, with `--rule-params '{"fast":20}'`. The target is your levels' R:R |
| `--end-ms` | now | Fix the window end (epoch ms), so several runs use exactly the same window |
| `--expiry` | 72h | Unfilled order is cancelled after this |
| `--max-hold` | 7d | Time stop after fill, exits at market. `none` = hold until TP or SL |
| `--geometry` | atr | Scale the setup by 4h ATR (`atr`) or keep fixed percentages (`pct`) |
| `--trend-filter` | off | Only place when the 4h EMA50 slope (vs 5 bars back) agrees with the side |
| `--sweep-window` | = expiry | Window after the fill for the stop check |
| `--sweep-atr` | 1.0 | How far past the stop still counts as a "wick" sweep |
| `--no-fees` | off | Ignore commissions and swap |
| `--slippage-bps` | 1 | Slippage on market entries, stop entries, stop-loss and time exits, in basis points (1 bps = 0.01% of price). Limit entries and take-profit fills get none. 0-100 |
| `--slippage-ticks` | off | Slippage in ticks of the market's tick size instead of bps (0-1000) |
| `--allow-overlap` | off | Let a new trade open while another is still running. Off by default: one trade at a time. Not available in pattern mode |
| `--refresh` | off | Ignore the candle cache |
| `--ref` | last close | Override the current price |
| `--mode` | every-close | `similar`, `every-close` (CLI default) or `pattern`. The web app defaults to `similar` |
| `--basket` / `--symbols` | none | Also run on these markets and pool the results: `fx` (all MFP FX pairs) and/or a comma list. Works in every mode |
| `--compare` | none | Rerun the same setup (ATR-scaled) on up to 5 other markets and print a comparison table plus a pooled row. Example: `--compare GBPUSD,AUDUSD,USDJPY`. Cannot be combined with `--basket` |
| `--similar-tf` | 1h | similar: timeframe of the fingerprint path (15m, 30m, 1h, 4h) |
| `--similar-bars` | 24 | similar: candles in the fingerprint (8-96) |
| `--similar-k` | 30 | similar: max non-overlapping historical matches per market |
| `--similar-at` | last visit to `--entry` | similar: setup moment as UTC ms, or `YYYY-MM-DD` / `YYYY-MM-DDTHH:MM` in WAT. Default is the last time price traded at your entry (or now if it is still there) |
| `--scan` | off | After a passing backtest (edge or possible edge), scan live markets for similar price action |
| `--force-scan` | off | Run the live scan even if the backtest did not pass; the report is labelled FORCED |
| `--scan-scope` | family | Live scan universe: `family` (FX/index/commodity family, or a liquid crypto core), `all`, `passed`, or a comma list of markets |

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
| `--entry-style` | limit | `limit` (blind limit at the zone's near edge), `sweep` or `confirm` (see "Entry styles" below) |
| `--confirm-tf` | 15m | sweep/confirm: timeframe whose closes are read for the trigger. Between `--interval` and `--zone-tf` |
| `--trigger-window` | 12 zone-tf bars | sweep/confirm: give up this long after the tap |
| `--sweep-min-atr` | 0.1 | sweep: how far past the far edge price must trade, in zone-tf ATR |
| `--sweep-max-atr` | 1.5 | sweep: further than this past the far edge is a breakout, not a sweep, so the setup is cancelled |
| `--skip-strong-atr` | off | sweep/confirm: cancel if a zone-tf candle with a body at least this many ATR closes against the trade on the way in |

Coaching:

| Option | Default | Meaning |
|---|---|---|
| `--insights` | off | Print plain-English findings, a what-if comparison (limit / wider SL / sweep / confirm, on the market and on a basket) and suggested fixes. Included in `--json` output under `insights` |

## Data sources

CHRONOS loads 15m bars (5m, 30m and 1h also work) in UTC from several public, keyless sources, in this order of preference:

1. **MFP feed** (MyFundedPerps public market-data WebSocket). Always used where it has bars.
2. **OKX perp** (`/api/v5/market/history-candles`, paged back 100 bars at a time).
3. **Bybit perp** (`/v5/market/kline`, category linear) where OKX has no perp.
4. **Yahoo Finance** (chart API) only for FX, indices and commodities with no perp. Labelled "not a perp". Yahoo gives 5m, 15m and 30m bars for the **last 60 days only**; older requests return HTTP 422.
5. **Dukascopy** public datafeed (1-minute CFD candles, aggregated) as a last resort for the same markets. Labelled "not a perp". From this box it often answered 429, 503 or dropped the TLS connection (24 Nov 2026), so each call has a 90 s budget (`CHRONOS_DUKA_BUDGET_S`); finished days are cached and the next run continues.

Spot data is never used. Binance futures answered from the box on 24 Nov 2026, but MFP's Binance markets already come from Binance, so it is not called directly. The Hyperliquid `candleSnapshot` adapter exists, but Hyperliquid keeps only the latest 5000 candles (about 52 days of 15m), the same history MFP already serves for `xyz:` markets.

**Merge rules** (`sources.py`, tested offline in `tests/test_merge.py`)
- MFP bars always win. Backups only **backfill bars older than** the earliest bar already merged. They never replace MFP bars or fill holes inside MFP data.
- No bars are invented. Holes stay holes and are reported as gaps. Weekend closures on FX and index markets are counted separately. Dukascopy's zero-volume filler minutes are dropped.
- Every bar's open time must be a multiple of the interval in UTC. Misaligned bars are dropped, never shifted.
- **Overlap check at each splice:** closes of the backup and the newer data are compared on their common bars in the 7 days after the splice point. If the median absolute difference is above 0.15% (crypto, stocks, indices, commodities) or 0.05% (FX), the backup is scaled by the median close ratio, which is a constant basis adjustment for futures vs perp. That only happens if the ratio is within 5% of 1 and the scaled difference falls under the threshold. Otherwise the backup is **rejected** and not used.
- Each result (text, JSON `data.provenance`, and the web page) shows the sources with their date ranges, the days actually tested, the days loaded including warm-up, gaps, and the overlap check.
- The cache is in `candle_cache/<source>/<symbol>_<interval>.pkl`. It is written atomically (temp file, fsync, rename) and remembers which ranges were fetched, so only missing ranges are requested. Empty answers are not remembered and are asked again next time.

`python3 sources.py coverage` maps every MFP market to its backups, loads 90 + 14 days, and writes `data_sources.json`. It also writes a 14-day QA comparison of MFP against each backup for FX, indices, `xyz:` and a few crypto markets. `python3 sources.py show EURUSD` prints one market's provenance. `python3 sources.py qa EURUSD` runs the QA comparison.

**Coverage on 24 Nov 2026** (15m bars, 104 days requested = 90 tested + 14 warm-up):

| Markets | Source used | Loaded | Tested |
|---|---|---|---|
| 96 Binance-listed (crypto, stocks, metals, oil) | MFP only | 104 d | 90 d |
| 6 FX (`veranta`), DJI (`ostium`) | MFP only | 104 d | 90 d |
| `xyz:AAPL`, `xyz:AMZN`, `xyz:MSFT` | OKX perp to 3 Oct, then MFP | 104 d | 90 d |
| `xyz:JP225` | OKX perp from 9 Sep (listing date), then MFP | 76.8 d | ~63 d |
| `xyz:SP500`, `xyz:XYZ100` | Yahoo ES=F / NQ=F (basis-adjusted) 27 Sep to 2 Oct, then MFP | 58 d | ~44 d |
| `xyz:BRENTOIL` | MFP only (Yahoo BZ=F rejected: 0.37% diff, the ratio could not fix it) | 52.2 d | ~38 d |

Overlap at the splice points: AAPL 0.087%, AMZN 0.071%, MSFT 0.084%, JP225 0.090% (all OK); ES=F 0.695% and NQ=F 0.796% before adjustment, 0.012% and 0.022% after; BZ=F 0.374% (rejected). Recent 14-day QA, MFP vs backup median close diff: BTC and ETH 0.005% (OKX and Bybit), XAU 0.01%; FX vs Yahoo 0.004% (GBP, JPY, CAD, CHF), 0.028% (EUR) and 0.034% (AUD); FX vs Bybit FX perps 0.09–0.12%; DJI vs YM=F 0.106%. Dukascopy was skipped in QA because it is slow. These numbers come from `data_sources.json` and will drift.

## Run the web app

From `chronos/web/`:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                 # serves http://0.0.0.0:8080
```

Environment variables: `PORT` (default 8080), `HOST` (default 0.0.0.0), account settings below, `TESTER_TIMEOUT` (seconds per backtest, default 150), `TESTER_MAX_PARALLEL` (concurrent backtests, default 2).

The app is a thin wrapper around `tester.py`. It calls the engine with `--json --json-out` and shows the verdict, stats, a cumulative-R equity curve, and every simulated trade. See `web/README.md` for the HTTP API.

You can also upload a chart screenshot (PNG, JPG or WebP, up to 5 MB) and name the pattern (SMC, ICT, order block, and so on). If `OPENAI_API_KEY` is set, CHRONOS sends the image to OpenAI vision (`OPENAI_VISION_MODEL`, default `gpt-4o-mini`) and fills the market, side, timeframe and levels for you to review. It never starts a backtest on its own. Missing prices stay blank; it does not invent levels. If the key is not set, the panel says "Screenshot reading isn't enabled yet" and the rest of the app works as before. The screenshot endpoint uses the same per-IP heavy rate limit as a test, plus a daily cap (`RATE_SCREENSHOT_PER_DAY`, default 20) so a public link cannot run up an unexpected OpenAI bill.

`web/app.py` finds `tester.py` in the repository root (the parent of `web/`).

### Accounts (sign up, sign in) and Copy trading

The web app has email + password accounts at `/signup`, `/signin` and `/forgot` (password reset by email is a "coming soon" stub; nothing is emailed yet). The header shows Sign in / Sign up, or your email and Sign out. A members-only **Copy trading** tab (`/copy-trading`) is a placeholder for now ("Strategies with a tested edge, coming soon") and always needs a signed-in account.

- Passwords are hashed with scrypt (werkzeug). They are never stored or logged in plain text.
- Sessions are signed cookies (HttpOnly, Secure, SameSite=Lax, 30 days) keyed by `SECRET_KEY`.
- Every account POST needs the CSRF token from `GET /api/auth/me` in an `X-CSRF-Token` header.
- Sign-in is rate limited per IP (`RATE_SIGNIN_PER_IP`, default 20 per 15 min) and per email (5 failed tries per 15 min). Sign-up is limited per IP (`RATE_SIGNUP_PER_IP`, default 5 per hour) and per email. A failed sign-in always says "Wrong email or password."
- Each user has `plan` (default `free`), `created_at`, `free_until` (sign-up + `FREE_DAYS`, default 365) and `is_admin`, ready for paid access later.
- `REQUIRE_LOGIN=1` makes the heavy tools (`/api/test`, `/api/compare`, `/api/scan`, `/api/screenshot`) need a signed-in account with access. It is off by default, so the backtester stays open to everyone.

Storage: set `DATABASE_URL` to a Postgres URL (`postgres://` and `postgresql://` both work; psycopg 3 via SQLAlchemy). Without it, accounts go in a local SQLite file (`data/chronos.db`, or `SQLITE_PATH`). That is fine for local use, but on a host with a temporary disk (such as Render's free web service) SQLite accounts are wiped on every deploy or restart.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs session cookies. Required in production; without it a random key is used and everyone is signed out on restart. |
| `DATABASE_URL` | Postgres for accounts. Falls back to SQLite. |
| `REQUIRE_LOGIN` | `1` to require sign-in for the heavy tools. Default off. |
| `FREE_DAYS` | Free period for new accounts, default 365. |
| `COOKIE_INSECURE` | `1` only for local plain-http testing (drops the Secure cookie flag). |

`GET /api/health` reports `db` (`postgres` or `sqlite`) and `db_ok`, never the URL. Account endpoints: `GET /api/auth/me`, `POST /api/auth/signup`, `POST /api/auth/signin`, `POST /api/auth/signout`, `POST /api/auth/forgot` (501, coming soon), `POST /api/auth/delete` (needs the password again), `GET /api/copy-trading` (members only).


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
6. A verdict needs at least 15 closed trades. Below that, the result is `too few samples`. Other verdicts: `no edge` (expectancy at or below 0 after fees), `possible edge, not proven` (t < 2), `edge on this sample`.

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

### Compare on other pairs (`--compare`)

After testing a setup on one market, `--compare M1,M2,...` reruns the same side, mode, entry style, R:R and stop/target (scaled by each market's own 4h ATR, the same transfer `--basket` uses) on up to 5 other markets. More than 5 names is rejected. The report lists the source market and each other pair (closed trades, win rate vs break-even, average R, total R, TP/SL/time exits, verdict, data source and days), a pooled row over all of them, and a one-line summary such as `works on 1 of 5, pooled no edge`. "Works" here means the engine's verdict was a possible edge or an edge on that sample (at least 15 closed trades and positive average R after fees). Suggestions in the web app default to the same family: FX majors for FX, other crypto perps for crypto, indices for indices, metals and energy for commodities (equities otherwise).

### Entry styles (pattern mode)

All three use the same zones, the same 15m fill and exit engine, the same fees and your R:R for the target. Sweep and confirm only start watching when price first taps a fresh zone's near edge, then read **closed** `--confirm-tf` candles one at a time. Both enter at market on the next bar after the trigger candle closes.

- **limit** (default): a resting limit at the zone's near edge. This is the blind first-retest entry.
- **sweep**: in plain words, *wait for price to run the stops sitting just past the zone, then come back*. For a short, price has to trade above the zone's far edge (the high made by the base and the first leg candle, where buy stops sit) by at least `--sweep-min-atr` ATR, and then a `--confirm-tf` candle has to close back below that edge. If price runs more than `--sweep-max-atr` ATR past the edge first, it counts as a breakout and the setup is cancelled. Stop: beyond the sweep's extreme plus the buffer. Longs are the mirror image.
- **confirm**: in plain words, *wait for the lower timeframe to turn in your direction after the tap*. For a short, after price taps the zone, a bearish `--confirm-tf` candle has to close below the last confirmed `--confirm-tf` swing low (a low with 2 higher lows on each side, which is only known 2 candles later). That is a break of structure. Stop: beyond the highest price since the tap **and** beyond the zone's far edge, plus the buffer. That follows the "stops well beyond the zone" rule.
- Both styles cancel if a zone-timeframe candle closes through the zone's far edge, or if nothing triggers within `--trigger-window`. With `--skip-strong-atr`, they also skip a zone when a big candle closes hard against the trade on the way in.
- In the report, "orders" for sweep and confirm means zone taps that were watched. "Unfilled" means no trigger happened.

### Insights (`--insights`)

`insights.py` is rule-based. It reads only the engine's numbers for this run. Every finding states the window, the market or basket, and how many closed trades it rests on. Rules:

| Rule | Fires when | Says |
|---|---|---|
| sample | fewer than 15 closed trades | too few to judge; use a basket or a longer window |
| swept | at least 30% of 3+ stop-outs later reached TP within the window | stops are getting swept; wait for the sweep or put the SL beyond liquidity |
| fills | fill rate under 40% | entry rarely reached (limit too far, or zone rarely retested, or trigger rarely seen) |
| far | every-close entry more than 3 x 4h ATR from price | far limit = bet on a retest; test in pattern mode |
| time | 30% or more of trades end on the time limit (or 40%+ across the basket variant runs) | target too far for the window, or stop very wide |
| below_be | win rate below break-even with blind limits | try a confirmation entry (shows the confirm rows as evidence) |
| markets | basket market with -3R or worse | names the losing and winning markets |
| decay | first half positive and second half at least 0.3R worse | it got weaker over time |
| edge_stop | SL less than 0.5 x zone ATR beyond the zone's far edge | warns against zone-edge stops (your own rule) |
| streak / rr | long losing streak; R:R below your 1:2.8 rule | information only. R:R is reported, never enforced |

**What-if comparison.** The setup is re-run, on the same window and in pattern mode, as four variants: blind limit, limit with the SL 0.5 ATR further beyond the zone, sweep entry, and confirm entry. Each runs on the single market and on a basket (`--basket` if given, otherwise all FX pairs for an FX market) to get more trades. A variant is only named as "best" if it has at least 15 closed trades, a win rate above break-even and a positive average R after fees. Even then, the text warns that the best of 8 tries on the same data overstates its edge (multiple comparisons). Otherwise it says the evidence is insufficient. The insights never say a setup will win.

The web app shows this as an **Insights** panel under the result cards: findings, the comparison table, the best-variant line, **Suggested fixes** with the tested numbers behind each fix, and a disclaimer. It is on by default and can be switched off with the "Insights" checkbox.

### Output

Output includes orders, fills, unfilled orders, TP, SL and time exits; win rate and break-even win rate before and after fees; average R (net and gross), expectancy, and total R; max losing streak and max drawdown in R; a first-half vs second-half average R stability check; and the stop check. `--json` prints this as JSON. `--json-out` also saves every simulated trade.

### Phase 2 tools (web)

- **Trade replay.** Every trade has a Replay button. `GET /api/candles?market&interval&from&to&load_from&load_to` reloads the same range the test loaded and returns only stored candles; the page checks the fill and exit candles against the ones saved with the trade (`bar_check`) and says whether they match.
- **Timeframes.** One "Setup timeframe" control (5m to daily) for every mode, optional higher-timeframe confirmation, and "Run across timeframes" (`tf`, `htf_confirm`, `tf_sweep` in the `/api/test` body).
- **Market conditions.** Each trade is tagged at its signal from closed 4h candles: trending or ranging (efficiency ratio over 20 candles, 0.30 or more = trending), high or low volatility (ATR14 percentile against the previous 90 candles), bullish, bearish or mixed (close vs EMA50 and its 5-candle slope). Groups under 15 trades are flagged as small, under 5 are not judged.
- **Strategy comparison.** `POST /api/strategies` runs 2 to 6 strategies on the same market, window, fees, slippage, timeframe and filters (`python strategy_compare.py spec.json` from the CLI). `GET /api/strategy-types` lists what can be tested and which named patterns are approximations.

### Phase 3 tools (web)

- **My strategies (signed-in users).** Save a setup with its name, description, setup type and rule settings, side, markets, timeframe, HTF filter, entry offset, stop, R:R, order expiry, time stop, window, slippage and free-text entry/exit notes (notes are kept but not tested). Edit, duplicate, delete (with confirm), run, and compare 2 to 6 saved strategies under identical conditions. Signed-out users can still test and compare; only saving needs an account. Device-saved Phase 2 comparisons can be imported. Limits: 50 strategies per user, 200 runs per strategy (oldest removed first).
- **Run history.** Each run of a saved strategy stores a snapshot on the server: key metrics, Edge Score, settings, data sources and time. Candles and trades are not stored. The history table and trend chart show how results moved as the window rolled forward.
- **Endpoints.** All need a sign-in and the `X-CSRF-Token` header, GETs included; another user's ids return 404. `GET/POST /api/library/strategies`, `GET/PUT/DELETE /api/library/strategies/<id>`, `POST /api/library/strategies/<id>/duplicate`, `GET /api/library/strategies/<id>/runs`, `DELETE /api/library/runs/<id>`, `POST /api/library/import`. `POST /api/test` accepts `strategy_id` (checked for ownership before the engine runs), `max_hold` and `mode: "rule"`.
- **Edge Score (0-100).** Shown with every result, in the comparison table and in run history, with a "How this is calculated" panel. It is built from expectancy in R after costs, statistical confidence, sample size, profit factor, drawdown, consistency over time and across market conditions, and survival at double costs, with caps for small or uncertain samples. Win rate alone earns nothing. The engine verdict follows the score. Method: [docs/EDGE_SCORE.md](docs/EDGE_SCORE.md).
- **Advanced analytics.** Earlier vs later trades (2/3 vs 1/3, out-of-sample check), 90% bootstrap ranges for expectancy and win rate (2,000 resamples), and the Monte Carlo drawdown range (1,000 random orders). All are computed from the test's real trades only.
- **Database.** Two new tables, `strategies` and `strategy_runs`, are added on startup by `create_all`, which only creates missing tables and does not touch `users`. This works on SQLite and Postgres. Deleting an account deletes its strategies and runs.

## Checks

`python tests/test_pattern.py` runs offline pattern-mode checks on synthetic candles:

- **Zone detection has no look-ahead.** Zones found on data cut at time c are exactly the zones the full run had confirmed by c. This runs for 30m, 1h and 4h, both sides, and 4 cut points.
- **Orders never use future zones.** Every order's zone was confirmed at or before the order time, and it was untouched before then.
- **Truncation.** Cutting off future candles does not change any pattern trade that had already finished.
- **Hand-built retest.** A base, then a drop, then a rally back to the zone low. The order fills at the zone low with the stop above the zone, and it hits TP for exactly +2R. With the data cut before the drop candle closes, there is no zone and no order.

`python tests/test_similar.py` runs offline checks for similar mode: the path is scale-free (a 2x-scaled copy stays close); fingerprints are causal (later bars cannot change an earlier window); overlapping matches are dropped; the query window itself is not traded; results sort by distance; live levels keep the same ATR shape and R:R; and the live-scan gate only opens for a possible or proven edge.

`python tests/test_edge_score.py` runs offline Edge Score checks: the worked example (a 90% win rate with negative expectancy scores 0; a 40% win rate at +0.2R scores 60 Promising at 150 trades and Strong at 400); win rate alone is not rewarded; every cap; each component; bootstrap, Monte Carlo and walk-forward on known trade lists; and the engine output and verdict.

`python tests/test_library.py` runs offline library checks: signed-out and missing-CSRF requests are refused; another user's strategies and runs return 404, including through `/api/test` with `strategy_id`; create, read, update, duplicate and delete; spec validation; the 50 and 200 limits; import; snapshots without candles or trades; account deletion removes the user's strategies; and the additive migration on an existing SQLite database (and on Postgres when `CHRONOS_PG_TEST_URL` is set). `node tests/p3_shots.cjs <base_url> web/screenshots` drives the Phase 3 UI against the mock server and saves `web/screenshots/p3-*.png`.

`python tests/test_rate_limit.py` runs offline checks for the web rate limiter: 6 heavy requests per IP per minute then 429; compare/scan share that budget; a second concurrent heavy run from the same IP is rejected; the slot is released afterwards; `CF-Connecting-IP` is trusted only from localhost; and `/` and `/api/health` are not limited.

`python tests/test_phase2.py` runs offline Phase 2 checks: resampling 5m/15m into 15m-to-daily bars (OHLC, UTC alignment, unfinished bars dropped); higher-timeframe confirmation and market-condition tags give the same answer when every later candle is deleted or replaced; rule signals never read later candles; `/api/candles` returns exactly the stored candles the trade's fill and exit used; and request validation for timeframes, HTF confirmation, sweeps and strategy comparisons. `node tests/p2_shots.cjs` drives the Phase 2 UI against the mock server and saves `web/screenshots/p2-*.png`.

`python tests/test_screenshot.py` runs offline checks for screenshot reading (OpenAI is mocked): image type and size; fuzzy mapping of symbols such as `USD/JPY` and `BTC` onto MFP markets; missing prices stay blank; the review payload flags low-confidence and missing fields; the endpoint is disabled without `OPENAI_API_KEY`; friendly errors for a bad image or an unreadable chart; the screenshot route shares the heavy per-IP budget; and the daily cap is enforced.

`python tests/test_shot_upload.py` checks the screenshot file picker: the file input is not nested inside the drop-zone label, both `input` and `change` are handled, empty MIME types are sniffed from magic bytes, and a Playwright Pixel run clicks the label and the Choose file control and shows a preview.

`python tests/test_auth.py` runs offline account checks on a temporary SQLite file: sign-up creates a free user with a 365-day `free_until`; passwords are hashed and never logged; cookie flags; CSRF is required; generic sign-in errors; per-IP and per-email rate limits; Copy trading needs a sign-in and an active free period or paid plan; account deletion; the `REQUIRE_LOGIN` switch; and `DATABASE_URL` normalisation. `node tests/auth_e2e.cjs <base_url> [screenshot_dir]` runs the whole flow in a Pixel 7 browser against a running site and deletes its throwaway account afterwards.

### Interface (v2 visual refinement)

The interface keeps the CHRONOS name, infinity mark, green-black background and lime accent; `docs/DESIGN_AUDIT.md` lists what was changed and why. Fonts are self-hosted (Geist for UI text, Geist Mono for numbers, Nunito for the wordmark; SIL OFL). The equity/drawdown chart and the trade replay use TradingView Lightweight Charts v5.3.0, vendored under `web/static/vendor/` with its Apache-2.0 licence and NOTICE (attribution link in the page footer and the chart logo); if the library fails to load, the older canvas charts are drawn instead. Charts only ever show the test's own trades and stored candles.

`node tests/design_qa.cjs <base_url> [screenshot_dir]` walks every view (form, advanced settings, loading, each results tab, replay, compare, library, Copy trading, account menu, sign-in) at 360, 390, 430, 768, 1280 and 1440 px and fails on page-level horizontal overflow or, at touch widths, any control smaller than 44 x 44 px; with a directory it also saves `ui2-desktop-*.png` and `ui2-mobile-*.png`. `node tests/ui2_markets.cjs <base_url>` runs real BTC, XAU and EURUSD tests through the form and checks the v2 results, charts and replay.

`python tests/test_compare.py` runs offline checks for `--compare`: more than 5 other markets is rejected; transferred levels keep the same ATR shape and R:R at a different price and ATR; pooled closed trades, TP/SL/time exits and total R match the concatenated trade list; and the one-line summary counts only the other pairs.

`python tests/test_entries.py` runs offline checks for the sweep and confirm entries:

- **Truncation.** Cutting off future candles never changes a sweep or confirm trade that had already finished. This runs for both styles, both sides, 30m and 1h zones, and 3 cut points.
- **Future changes don't move a trigger.** Changing every candle after a trade's trigger leaves that trade's tap, trigger, entry, SL and TP unchanged.
- **Event order and stops.** zone confirmed <= tap <= trigger <= fill. The SL sits beyond both the zone's far edge and the tap/sweep extreme. A sweep trade needs price beyond the far edge and an entry back inside it. A confirm entry closes through the swing.
- **Hand-built sweep.** Price taps a supply zone, wicks 0.5 above its far edge, and closes back inside. The short is entered on the first 15m close back under the edge, never before, with the SL above the sweep high, and it hits TP at +2R. With the data cut before that close, there is no trade. A plain retest that never trades past the far edge gives no sweep trade.
- **Hand-built confirm.** Tap, then a 15m swing low at 98.9, a push up, and a bearish close at 98.7. The short is entered there, with the SL beyond the zone's far edge.

`python tests/test_merge.py` runs offline checks for the multi-source merge: no duplicate bars and UTC-aligned open times; MFP precedence on overlap (backups only add older bars); holes inside MFP or a backup are not filled and are reported; basis adjustment and rejection at the overlap check; a chain of backups with misaligned bars dropped; weekend closures not counted as gaps; a window that starts on a weekend needs no backfill; and an atomic, source-keyed cache that only fetches missing ranges.

`python tests/sanity.py` runs three checks (the look-ahead and coin-flip checks fetch live MFP candles):

- **Look-ahead.** Deleting future candles must not change any trade that had already finished: 5 markets, both sides, 3 cut points.
- **Tie rule.** A synthetic bar that touches both TP and SL must count as SL. This check is offline.
- **Coin-flip baseline.** A 1:1 market-entry setup with no fees across 10 markets should win about 50%. The last recorded run in the source tester documentation was 49.8% (762/1530).

## Limitations

- The default window is a rolling 90 days. MFP serves more than 104 days of 15m history on Binance, FX and DJI markets. `xyz:` markets on MFP start around 2–3 Oct 2026; OKX perps backfill AAPL, AMZN and MSFT to the full 90 days, but JP225, SP500, XYZ100 and BRENTOIL stay shorter (see Data sources). Short markets run on what exists, and the result shows the real number of days.
- Backfilled bars come from a different venue. OKX and Bybit stock perps differ from MFP by about 0.07–0.15%. Yahoo index futures carry a basis, which is removed with one constant ratio, but futures rolls inside the backfill are not modelled. Dukascopy is unreliable from this box.
- Every-close mode tests the setup's shape at every 4h close, not only where a pattern appears. `--trend-filter` is a rough context filter only.
- Pattern mode's zones are a **rule-based approximation** of the zones a trader draws by eye. It does not see liquidity sweeps, BOS confirmation, equal highs and lows, efficiency or imbalance, or higher-timeframe context. It may miss zones you would take and take zones you would skip. Your own chart zone may not match any detected zone; see "Default stop buffer" above.
- The sweep and confirm entries are mechanical rules: the "liquidity" is the zone's far edge, and the swing is a 2-bar fractal on 15m. They do not see equal highs and lows, session highs, or higher-timeframe context, and confirm-tf candles finer than the 15m data (5m) need `--interval 5m`.
- Even with 90 days of data, sweep and confirm on one market give only a handful of trades. The what-if comparison tries 8 variants on the same data, so any "best" one is partly luck. It is evidence to forward-test, not a fix.
- With 90 days of data, a single market still gives only a handful of fresh zones (AUD/USD: 33 on 30m; EUR/USD: 9 on 4h). Use `--basket` to reach 15+ trades, but pooling assumes the pattern behaves the same across pairs.
- The zone rule's defaults were chosen before looking at results and were not optimised. Changing them and keeping whichever looks best would be curve-fitting on a tiny sample.
- One trade at a time gives about 15–45 trades in 90 days. Most single setups will read "too few samples". Treat any result under about 50 trades as a rough check, not proof.
- 15m candles cannot show the order of moves inside a bar. The tie rules always assume the worse case.
- Slippage is a fixed assumption (default 1 bps), not measured from order books. Funding is not modelled yet.
- The Edge Score summarises one past window. It is a statistical assessment, not a forecast, and it moves as the window rolls forward. Bootstrap and Monte Carlo ranges assume trades are independent.
- A saved strategy with several markets runs on its first market; comparing saved strategies uses the first picked strategy's market, levels, timeframe and costs. Fees are always MFP's real fees for the market.
- Results describe the past only. They are not a forecast and not advice.

## Disclaimer

Past results do not guarantee future profit. CHRONOS does not predict prices, does not promise an edge, and must never be read as a guarantee of profitability. Do not fabricate historical data or backtesting results; only numbers the engine produces from real candles should be reported.

## Example results

`examples/` holds the text reports for the two example setups (AUD/USD short and EUR/USD short) in pattern mode, on one market and on the FX basket, plus every-close runs for comparison. The run time is in `examples/RUN_TIME.txt`. The `*_insights_*` files are the same setups with `--insights` (findings, what-if comparison, suggested fixes), with JSON for the single-market runs; their run time is in `examples/RUN_TIME_INSIGHTS.txt`. `web/screenshot-insights.png` shows the Insights panel. The numbers move as the window rolls forward.

The `*_90d.*` files are the 90-day reruns with insights, made 24 Nov 2026 23:40 WAT (`examples/RUN_TIME_90D.txt`). The `*_60d_now.*` files are the same setups at the same time with `--days 60`, for a like-for-like comparison. The every-close geometry is scaled to the current price and ATR, so older 60-day files are not directly comparable.

## Roadmap

The full plan is in `docs/ROADMAP.md`, and the Phase 1 audit is in `docs/AUDIT.md`.


**Week 1**
- ~~Extend coverage to a 90-day window by merging public sources, with every bar labelled by source.~~ Done.
- ~~Model slippage (beyond the current gap-through-SL-at-open rule).~~ Done in Phase 1 (`--slippage-bps`, `--slippage-ticks`).
- Add per-IP rate limits before any public hosting.

**Later**
- Funding costs.
- ~~Walk-forward train/test split.~~ Done in Phase 3 (earlier vs later trades).
- ~~Confidence ranges around reported metrics.~~ Done in Phase 3 (bootstrap and Monte Carlo).
- Pattern mode v2: equal-high/low and session-high liquidity, efficiency-based stop choice, higher-timeframe context.
- Insights: optional AI rewording, limited to numbers the engine already produced. The rule-based layer is done.


## License

Copyright (c) 2026 VOIDKAI. All rights reserved. The code is public for review only; see [LICENSE](LICENSE). Copying, hosting or reusing it requires written permission.
