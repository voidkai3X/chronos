# CHRONOS blueprint: gap audit

Audit date: 27 Nov 2026 (WAT). Baseline: `main` at 8947e7d (the live site). Branch with new work: `blueprint-p1p2`.

## Summary in plain English

**What CHRONOS already does well (live today).** The backtester is solid: it replays a setup on real MFP candles
(with OKX/Yahoo backfill for older history), counts fees, swap and slippage, refuses to look ahead, treats a candle that
touches both stop and target as a loss, handles gaps, and shows where every candle came from. It has a full results
dashboard, a trade table with CSV export, a candle-by-candle replay of each backtested trade, multi-timeframe testing,
market-condition splits, side-by-side comparison of 2 to 6 strategies, a personal strategy library with run history,
the Edge Score, and earlier-vs-later (out-of-sample) checks with bootstrap and Monte Carlo ranges.

**What this branch adds (Phase 1 gaps and Phase 2).**
- Backtester: recovery factor, maximum concurrent positions, position sizing and money P/L from your own account size
  and risk %, and a reproducibility record (engine version, every setting, and a fingerprint of the exact candles used)
  that you can download with each result.
- **Blind Replay**: practise on a random real window. The server keeps the future candles and only sends one when you
  advance, so the chart can never show what happens next. Practice, Strategy and Challenge modes, position sizing,
  skip, speed control, and daily/weekly practice statistics saved to your account.
- **Trading Journal**: log real trades (all the blueprint fields except screenshot uploads), R-multiple, planned vs
  actual R:R, sessions, calendar, search and filters, weekly and monthly reports, CSV export, and evidence-based
  observations that only appear when your own records contain enough trades, always with the counts.
- **Risk calculator**: position size, risk, stop distance, potential profit/loss and R:R for crypto, forex, metals,
  indices and stocks, with the contract assumptions shown.

**What is next (not built yet).** Strategy Builder with AND/OR rules, natural-language rules (needs paid AI credit),
the Edge Discovery Engine, Edge Monitor and alerts (needs a scheduled worker), economic-event analysis (needs a data
source), benchmarking, PDF reports, the community library, a dashboard home page and journal screenshot uploads
(needs file storage).

**Decisions and costs only you can make** (prices are Render's and providers' published list prices; check before buying):
1. **Your database expires on 27 Dec 2026.** The accounts database is on Render's free Postgres plan, which Render
   deletes 30 days after creation. Upgrading to Basic-256mb (about $6/month) keeps accounts, strategies, journals and
   practice history. This is the most urgent item.
2. **Web service plan.** The app is on Render's free plan: it sleeps after 15 minutes idle (first visit then takes
   about a minute) and loses its candle cache on every restart. Starter (about $7/month) keeps it awake; a persistent
   disk (about $0.25/GB/month, paid plans only) keeps the candle cache.
3. **Edge Monitor** needs a scheduled job that checks saved strategies on new data: a Render cron job (billed per
   minute of run time, typically $1 to $5/month for a few checks a day) or a background worker (about $7/month), plus
   an email service for alerts (free tiers exist, e.g. Resend or Brevo).
4. **Natural-language strategy rules** need an AI model. The OpenAI account linked to CHRONOS is out of credit, so
   screenshot reading and any AI parsing stop until it is topped up (pay-as-you-go; a few dollars covers thousands of
   short rule translations). Every AI translation would be shown to you as plain rules to confirm before any test.
5. **Journal screenshots** need object storage (Cloudflare R2 has 10 GB free, then about $0.015/GB/month; AWS S3 is
   similar). Until then the journal accepts a link to a chart image (for example a TradingView snapshot link).
6. **Economic events** need a reliable timestamped calendar. Free calendars are not licensed for redistribution and
   often lack exact times; paid APIs cost roughly $20 to $100/month. Not started until a source is chosen.
7. **PDF reports** can be built without extra cost (server-side library), but they make the free instance slower;
   decide whether they matter before Phase 4.

## Legend
- **Live**: on the live site (`main`), with where it lives.
- **Added on this branch**: built in `blueprint-p1p2`, not deployed.
- **Partial**: what exists and what is missing.
- **Missing**: not started; can be built on the current stack.
- **Needs infrastructure**: cannot be built honestly without something that does not exist yet (named).

## 2. Backtester (foundation)

| Item | Status | Where / what is missing |
| --- | --- | --- |
| Total setups identified | Live | `metrics.compute` `setups_identified`; "More numbers" card |
| Total executed trades | Live | `trades_executed` card |
| Winning / losing trades | Live | "Wins / losses" card |
| Win rate | Live | card + break-even win rate |
| Net PnL | Live | in R and % of account at 1% risk. **Added**: money P/L at your own account size and risk % |
| Profit factor | Live | card |
| Maximum drawdown | Live | card + drawdown chart; Monte Carlo range in Analysis |
| Average R:R | Live | "Avg R:R achieved" |
| Expected value per trade | Live | "Expectancy" |
| Average winning / losing trade | Live | "Avg win / avg loss" |
| Max consecutive wins / losses | Live | "Longest streaks" |
| Recovery factor | **Added** | net R / max drawdown R (`metrics.block`) |
| Average trade duration | Live | "Avg time in trade" (winners and losers separately) |
| Instrument, timeframe, period, side | Live | form (100+ MFP markets; 5m to daily; 30 to 90 days) |
| Entry conditions | Partial | levels, similar moments, every close, pattern/zone rules, EMA cross, breakout, sweep, FVG. Free-form conditions need the Strategy Builder (Phase 3) |
| Stop-loss / take-profit | Live | price levels, ATR-scaled to each past setup |
| Fees | Live | MFP real fees and swap per asset class (not user-editable by design: they are the venue's fees) |
| Slippage | Live | configurable bps or ticks |
| Position sizing / risk per trade | **Added** | account size and risk % in Advanced settings: position size for your levels, money P/L and drawdown. Results stay in R, so sizing never changes the statistics |
| Maximum concurrent positions | Partial, **added** stat | one-at-a-time or unlimited overlap is configurable; the result now reports the maximum that were open at once. A numeric cap (e.g. at most 3) needs an engine change: planned |
| Trade records (entry/exit time and price, SL, TP, outcome, R) | Live | trade table, CSV, per-trade candle replay with integrity check |
| Traceable to the data used | Live | provenance per segment and source. **Added**: SHA-256 fingerprint of the exact candles and the engine version |
| Reproducible results | **Added** | "Download config" saves every setting + engine version + data fingerprint; library runs keep the fingerprint |
| In-sample / out-of-sample | Live | earlier-vs-later split in Analysis; Edge Score uses it |
| Walk-forward | Partial | one split today; rolling walk-forward windows planned with the Edge Discovery Engine |
| Conservative same-candle rule | Live | TP+SL in one candle counts as SL; TP on the fill candle ignored |
| Gaps / missing candles | Live | gap-through-stop exits at the open; gaps reported, never filled in |
| Timezone handling | Live | engine in UTC; UI shows WAT (UTC+1) with labels |

## 3. Edge Discovery Engine — Missing (Phase 3)
Building blocks exist (sweep, FVG, zone/BOS rules, HTF confirmation, condition tags, Edge Score, out-of-sample split).
Missing: a condition catalogue with written detection rules, combination runner (A, A+B, A+B+C on identical data),
multiple-testing control (fewer combinations, out-of-sample confirmation, penalty in the score), results table.
No new infrastructure; heavier runs will be slow on the free instance (see decision 2).

## 4. Blind Replay — **Added on this branch**
| Item | Status |
| --- | --- |
| Select instrument and timeframe | Added (any MFP market; 15m, 1h, 4h) |
| Historical candles, future hidden | Added: the window is stored server-side; the client only ever receives candles up to the replay cursor (tested) |
| Enter or skip; long/short; entry, SL, TP | Added: market or limit orders, validated levels |
| Advance candle by candle; speed control | Added: step, play/pause with 1x to 8x |
| Outcome reveal | Added: fills and exits computed on the server with the conservative same-candle rule; the full window is revealed only when the session ends |
| Position sizing and risk | Added: virtual account and risk % per trade; units and money P/L shown |
| Session tracking, trade history, stats | Added: per-account sessions and trades, daily and weekly practice stats, by mode |
| Practice mode | Added (random real window) |
| Strategy mode | Added: practise one of your saved strategies; its rules are shown and its stop/R:R prefill the order; trades are tagged |
| Challenge mode | Added: fixed rules (start 10,000 virtual, max 1% risk per trade, fail at -5%, pass at +6%, at most 10 trades, longer window) |
| Leaderboards / shared challenges | Missing (Phase 5, community) |

## 5. Strategy Builder — Partial / Missing (Phase 3)
Partial: saved strategies with setup type, rule parameters and free-text entry/exit notes. Missing: visual IF/THEN
builder with AND/OR, condition catalogue, exit rules beyond fixed R:R and time stop. Natural-language rules:
**needs infrastructure** (AI credit, decision 4), with mandatory confirmation of the parsed rules and explicit
"ambiguous" flags.

## 6. Trading Journal — **Added on this branch** (except uploads)
| Item | Status |
| --- | --- |
| Instrument, entry, SL, TP, exit, size, direction, strategy, P/L, R-multiple, session, notes | Added |
| Entry / exit screenshots | **Needs infrastructure** (object storage, decision 5). A chart-image link field is provided instead |
| Behaviour analysis | Added as rule-based, evidence-gated observations (planned vs realised R:R, early exits, stop moves, overtrading days, risk consistency, session differences, strategies with most losses, trades after a loss). Each needs a minimum sample and shows its counts. No AI and no psychological labels |
| Weekly and monthly reports | Added |
| Strategy-specific journals | Added (filter by strategy, per-strategy figures) |
| Calendar view | Added (daily net R and count) |
| Screenshot gallery | Needs infrastructure (decision 5) |
| Search and filtering | Added (text, market, strategy, side, outcome, session, date range) |
| CSV export | Added |

## 7. Edge Monitor — **Needs infrastructure** (Phase 4)
Needs a scheduled job (cron or worker, decision 3) to rerun saved strategies on new data, a table for monitor
snapshots, rolling-window statistics, and an alert channel (email). The statistics (rolling expectancy, baseline vs
recent comparison with a significance test) can reuse the Edge Score maths.

## 8. Strategy Lab — Partial (Phase 3)
Live: compare 2 to 6 strategies on identical data (`/api/strategies`), timeframe sweep, condition splits, equity
overlay, library compare. Missing: one Lab page that groups builder, discovery and comparison; consistency and
condition-sensitivity columns in the comparison; same strategy across timeframes in one comparison view.

## 9. Edge Score — Live
`edge_score.py`, `docs/EDGE_SCORE.md`: expectancy, t-stat, sample size, profit factor, Monte Carlo drawdown, time and
condition consistency, double-cost survival, caps (Strong needs 50+ trades and t >= 2). Explanation panel and
disclaimer live. Missing: an explicit "market condition sensitivity" line in the panel (data exists in Analysis).

## 10. Community library — Missing (Phase 5)
Needs: publish flow, public pages, reproduction runs, version history table, moderation and abuse controls, clear
"not verified by CHRONOS" labelling. No new paid infrastructure, but needs moderation decisions.

## 11. Additional features
| Item | Status |
| --- | --- |
| A. Risk calculator | **Added** (crypto perps, forex lots with pip values, metals, indices, stocks; conversion rate for non-USD quotes) |
| B. Economic events | **Needs infrastructure** (timestamped event data, decision 6) |
| C. Strategy documentation | Partial: description and entry/exit notes per saved strategy. A personal knowledge base page is missing |
| D. Benchmarking | Missing: buy-and-hold and random-entry baselines on identical windows can be built on current data (Phase 4) |
| E. Reports | Partial: CSV of trades (live), config JSON (added). PDF needs a generator; see decision 7 |

## 12. Data accuracy rules
All live: no look-ahead, no future candles (backtester and the new replay), no fabricated data, fees, slippage,
conservative same-candle rule, gap handling, timezone handling, provenance. Added: data fingerprint and config record
for reproducibility. Note: on the free plan the candle cache is lost on restart, so a re-run may re-download the
same history; the fingerprint shows whether the candles were identical.

## 13. UI
Live: v2 design (dark green, lime accent, Geist, calm motion, mobile bottom bar, 44 px targets). Added: navigation
for Replay, Journal and Calculator with a "More" sheet on phones.

## 14. Product structure
| Section | Status |
| --- | --- |
| Dashboard | Missing (a summary page once Journal and Replay have data) |
| Backtester | Live |
| Replay | Added |
| Strategy Lab | Partial (Compare + My strategies) |
| Journal | Added |
| Edge Monitor | Needs infrastructure |
| Community | Missing |
| Settings | Partial (account menu: plan, dates, sign out, delete). Preferences page missing |

## Existing architecture (for reference)
- Engine: `tester.py` (CLI, run as a subprocess per test), `metrics.py`, `similar.py`, `conditions.py`, `strategies.py`,
  `strategy_compare.py`, `edge_score.py`, `insights.py`; data: `sources.py` (MFP feed + OKX/Yahoo backfill, pickle cache).
- Web: Flask + waitress (`web/app.py`), accounts (`web/auth.py`), library (`web/library.py`), screenshot reader
  (`web/screenshot.py`, OpenAI). Static front end, no build step.
- Database: SQLAlchemy Core; Postgres on Render (`DATABASE_URL`), SQLite locally. Tables are created additively with
  `metadata.create_all` (existing tables are never altered). Tables: users, strategies, strategy_runs; this branch adds
  practice_sessions, practice_trades, journal_trades.
- Hosting: one free Render web service (Frankfurt), free Render Postgres (expires 27 Dec 2026).
