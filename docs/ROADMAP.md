# CHRONOS roadmap

Status as of 27 Nov 2026 (WAT). Phases 1 and 2 are built and tested. Phase 3 has not been started.

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
| 4 | Strategy comparison: two or more setups on the same data, side by side | 2 | Done. 2 to 6 strategies under identical conditions with a table and equity overlay. Testable today: the trader's own levels, every candle close, EMA crossover, breakout, liquidity sweep, fair value gap, and zone retest/sweep/break-of-structure. Order block and SMC are approximations through the zone rules. Saved on the device only; account saving comes with the Phase 3 library |
| 7 | Strategy library: save, name and re-run a trader's own setups | 3 | Not started. Accounts exist |
| 10 | Edge Score: one honest summary of sample size, consistency, costs and robustness | 3 | Not started. Will replace the generous "possible edge" wording |
| - | Advanced analytics: walk-forward split, confidence ranges, funding costs | 3 | Not started |

## Rules for every phase
- Keep the brand: dark green background, green accents, infinity logo, Nunito, minimal layout.
- No heavy animation, and no background motion on phones.
- Never fabricate trades or statistics. When data is short, say so.
- Never promise profit.
