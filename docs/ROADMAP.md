# CHRONOS roadmap

Status as of 27 Nov 2026 (WAT). Phases 1, 2 and 3 are built and tested. The full product blueprint is mapped item by item in [BLUEPRINT_GAP.md](BLUEPRINT_GAP.md); its Phase 1 gaps and Phase 2 (Blind Replay, Trading Journal, Risk Calculator, weekly and monthly reports) are built on the `blueprint-p1p2` branch.

| # | Area | Phase | Status |
| --- | --- | --- | --- |
| 9 | Accuracy and data integrity: no look-ahead, fees, slippage, same-candle rule, no overlap, gaps, traceability | 1 | Done |
| 1 | Comprehensive results dashboard: counts, R and %, profit factor, drawdown, expectancy, streaks, time in trade, gross vs net, charts, "why" | 1 | Done |
| 1b | Trade records: sortable table, CSV export, candle-window reference for replay | 1 | Done |
| 6 | Screenshot recognition: fields with confidence, editable review, uncertain fields left blank | 1 | Done (reader tested with mocks; the live reader needs OpenAI credit) |
| 8 | Mobile UX: spacing, help text, loading stages, empty state, clear errors, easy navigation | 1 | Done |
| 2 | Trade replay: step through each trade on its candles | 2 | Done. Replay button per trade: stored candles only (reloaded from the exact range the test loaded and checked against the fill and exit candles), entry, SL and TP lines, play/pause/step/speed/scrubber, exit reason and duration |
| 3 | Multi-timeframe: setup timeframe 5m, 15m, 30m, 1h, 4h or daily; optional higher-timeframe confirmation; run across timeframes | 2 | Done. HTF rule: last closed HTF candle above a rising EMA20 for longs (below a falling one for shorts). 5m history is about 104 days on MFP and 60 days on Yahoo; daily setups give few trades in 90 days |
| 5 | Market conditions: split results by trend, range, volatility and direction | 2 | Done. Tags from closed 4h candles at the signal: efficiency ratio (trend/range), ATR percentile (volatility), EMA50 position and slope (direction). Sample-size warnings and a plain summary. Session split stays in the stats |
| 4 | Strategy comparison: two or more setups on the same data, side by side | 2 | Done. 2 to 6 strategies under identical conditions with a table and equity overlay. Testable today: the trader's own levels, every candle close, EMA crossover, breakout, liquidity sweep, fair value gap, and zone retest/sweep/break-of-structure. Order block and SMC are approximations through the zone rules. Saved on the device when signed out; signed-in users can import them into My strategies (Phase 3) |
| 7 | Strategy library: save, name and re-run a trader's own setups | 3 | Done. "My strategies" for signed-in users: name, description, setup type and rule settings, pattern name, side, up to 5 markets, timeframe, HTF filter, entry offset, stop, R:R, order expiry, time stop, window, slippage, trend filter, overlap, plus free-text entry and exit notes (kept, not tested). Edit, duplicate, delete with confirm, run, compare 2 to 6 saved strategies, import device-saved comparisons. Every run saves a snapshot (key numbers, Edge Score, settings, data sources and time; no candles or trades) with a history table and trend chart. Limits: 50 strategies per user, 200 runs per strategy (oldest removed) |
| 10 | Edge Score: one honest summary of sample size, consistency, costs and robustness | 3 | Done. 0-100 with a "How this is calculated" panel; method in docs/EDGE_SCORE.md. The engine verdict and live-scan gate now follow the score |
| - | Advanced analytics: walk-forward split, confidence ranges, funding costs | 3 | Done for earlier-vs-later (out-of-sample) split, bootstrap ranges for expectancy and win rate, and Monte Carlo drawdown range. Funding is still not modelled |
| B1 | Blueprint Phase 1 gaps: recovery factor, max concurrent positions, position sizing display, reproducibility (engine version, data fingerprint, config download) | B1 | Built on `blueprint-p1p2` |
| B2 | Blueprint Phase 2: Blind Replay (Practice, Strategy, Challenge), Trading Journal with reports and evidence-gated observations, Risk Calculator | B2 | Built on `blueprint-p1p2` (journal screenshots wait for file storage) |
| B3-B5 | Strategy Builder, Edge Discovery Engine, Strategy Lab page, Edge Monitor and alerts, economic events, benchmarking, PDF reports, community library | B3-B5 | Not started; see BLUEPRINT_GAP.md for the infrastructure each needs |

## Rules for every phase
- Keep the brand: dark green background, green accents, infinity logo, Nunito, minimal layout.
- No heavy animation, and no background motion on phones.
- Never fabricate trades or statistics. When data is short, say so.
- Never promise profit.
