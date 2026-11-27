# CHRONOS roadmap

Status as of 27 Nov 2026 (WAT). Phase 1 is built and tested. Phases 2 and 3 have not been started.

| # | Area | Phase | Status |
| --- | --- | --- | --- |
| 9 | Accuracy and data integrity: no look-ahead, fees, slippage, same-candle rule, no overlap, gaps, traceability | 1 | Done |
| 1 | Comprehensive results dashboard: counts, R and %, profit factor, drawdown, expectancy, streaks, time in trade, gross vs net, charts, "why" | 1 | Done |
| 1b | Trade records: sortable table, CSV export, candle-window reference for replay | 1 | Done (replay view itself is Phase 2) |
| 6 | Screenshot recognition: fields with confidence, editable review, uncertain fields left blank | 1 | Done (reader tested with mocks; the live reader needs OpenAI credit) |
| 8 | Mobile UX: spacing, help text, loading stages, empty state, clear errors, easy navigation | 1 | Done |
| 2 | Trade replay: step through each trade on its candles | 2 | Not started. Each trade already stores its candle window |
| 3 | Multi-timeframe confirmation: test the idea with a higher-timeframe filter | 2 | Not started |
| 5 | Market conditions: split results by trend, range, volatility and session | 2 | Not started. Session split already exists in the stats |
| 4 | Strategy comparison: two or more setups on the same data, side by side | 2 | Partly there: markets can be compared, but setups cannot yet |
| 7 | Strategy library: save, name and re-run a trader's own setups | 3 | Not started. Accounts exist |
| 10 | Edge Score: one honest summary of sample size, consistency, costs and robustness | 3 | Not started. Will replace the generous "possible edge" wording |
| - | Advanced analytics: walk-forward split, confidence ranges, funding costs | 3 | Not started |

## Rules for every phase
- Keep the brand: dark green background, green accents, infinity logo, Nunito, minimal layout.
- No heavy animation, and no background motion on phones.
- Never fabricate trades or statistics. When data is short, say so.
- Never promise profit.
