# CHRONOS audit (Phase 1)

Written 27 Nov 2026 (WAT). Plain-English review of what CHRONOS did before Phase 1, what was wrong or missing, and what Phase 1 changed. Mission: every trader has an edge worth discovering. CHRONOS tests a trader's own idea on real history. It is not a signal service and it never promises profit.

## How a test works

1. The site sends the setup (market, side, entry, stop, target, mode) to `web/app.py`, which runs `tester.py` as a separate process.
2. `sources.py` loads 15-minute candles: the MFP feed first, then OKX, Bybit, Yahoo or Dukascopy to fill older history. Every stretch of candles is labelled with where it came from, overlapping sources are cross-checked, and missing candles are counted (weekend closures do not count as missing).
3. The engine replays the setup in one of three modes:
   - **similar**: finds past moments whose recent price path looks like the setup moment, and places the trade there.
   - **every-close**: places the setup's shape (scaled by 4h ATR) at every 4h close while flat.
   - **pattern**: places it only at supply/demand zones found by a fixed rule.
4. `simulate_order` (shared by all modes) walks candle by candle to see whether the order fills, then whether the stop, the target or the time limit is hit first.

## What was already right (kept)

- **No look-ahead.** Decisions use only closed candles. Existing tests cut the future off and check that no finished trade changes. Similar mode's fingerprints only look backwards.
- **Conservative fills.** If the stop and the target are both inside one candle, the stop is counted. A target touched on the fill candle is not counted. A gap through the stop exits at the open, not at the stop price.
- **Fees.** Commission per fill and daily swap per asset class are already in every net result.
- **One trade at a time.** Every-close, similar and pattern modes wait for the last trade to finish.
- **Honest data handling.** Sources are labelled, merges are checked, and a busy feed gives a clear message instead of made-up numbers.

## What was wrong or missing

| Area | Before Phase 1 | Risk |
| --- | --- | --- |
| Slippage | Not modelled, except the gap-through-stop rule | Net results were slightly too good on market and stop fills |
| Trade traceability | A trade had times and R, but not the theoretical vs real fill price, the exit reason, the candle source or a link to the candles | Hard to check a single trade by hand |
| Gaps | Counted for the whole test only | A trade that ran across missing candles looked normal |
| Insufficient data | One message for "no candles" | A short or patchy history was not explained |
| Overlap | Always one at a time, with no switch and no check | Fine, but not stated or verifiable |
| Results | Win rate, total R, expectancy, drawdown, a t-statistic | No profit factor, average win/loss, streaks, time in trade, gross vs net split, monthly view or histogram |
| "Why" | Insights panel in some modes only | No plain, data-backed explanation for every result |
| Trade list | Short table, no sorting, partial CSV | Not enough to review trades |
| Screenshot reading | Market, side, timeframe and levels, with confidence; low-confidence values were still filled in | A wrong reading could be run without being noticed. No pattern field |
| Mobile | Worked, but little help text, a bare spinner, a blank results area before the first test and a generic error | Hard for a new trader to follow |

## Things to know (not changed in Phase 1)

- **Similar mode's query is the setup moment.** If the trader picks a moment in the past, the matches are searched over the whole window, so some matches fall after that moment. That is part of how the pattern is defined, not look-ahead in the outcomes: every replayed trade still only uses candles up to its own fill.
- **Pattern mode's stop buffer** is taken from the trader's current setup (distance from entry to stop, in ATR). That is a modelling choice, documented in the README.
- **The verdict wording is generous.** "Possible edge" can show with a low t-statistic. The per-result "Why" list and the data quality box now give the sober reading next to it. A stricter Edge Score is planned for Phase 3.
- **Tick sizes.** Some markets report odd tick sizes (BTC shows 1e-08), so tick-based slippage can be tiny there. The results show the tick size used, and bps is the default.
- **Fixed slippage.** Slippage is an assumption, not measured from order books.

## What Phase 1 changed

### Accuracy and data integrity
- **Slippage**, set in bps (default 1 bps = 0.01%) or ticks. It applies to market entries, stop entries, stop-loss exits and time exits, always against the trader. Limit entries and take-profit fills get none, because they fill at the stated price or better. Fees are worked out on the slipped prices. Results show the slippage used.
- **Gross vs net.** Each trade keeps its theoretical R (`r_gross`, before costs), the slippage cost (`r_slip`), the fee cost (`r_fee`) and the result after costs (`r_net`).
- **Same-candle rule** documented on the page and in each result. Same-candle ties, gap exits and ignored fill-candle targets are counted in an "integrity" block.
- **Overlap.** One trade at a time stays the default. An "allow overlapping trades" option is added for every-close and similar modes (not pattern). Overlaps are counted, so the setting can be checked.
- **Gaps** are reported per trade (`gap_bars_in_trade`), and the test gets a data quality status (ok, warn or insufficient) with plain messages: short history, more than 1% missing candles, unverified source overlap, or fewer than 15 closed trades.
- **Traceability.** Every trade carries the signal time, entry and exit times, theoretical and real prices, exit reason (tp, sl, sl_gap_open, time_stop), candle source, and a candle window (`from_ms`, `to_ms`) for a future replay view.

### Results dashboard
- **Counts:** setups found, trades executed, unfilled, wins, losses, win rate.
- **Results:** net P/L in R and %, profit factor, max drawdown, average R:R achieved, expectancy, average win and loss, longest win and loss streaks, average and median time in trade.
- **Gross vs net** side by side, with fees and slippage in R and as a share of gross profit.
- **Charts:** equity (net filled vs gross dashed), R histogram, outcome breakdown (TP, SL, time exits, unfilled) and R by month.
- **"Why it worked or failed":** findings computed from the trades only, covering sample size, fill rate, whether costs flip the result, payoff vs win rate, fast losses, durations, time exits, concentration in a few trades, first half vs second half, trading sessions, streaks, same-candle ties and gaps.

### Trade records
Sortable table of every trade, with closed-only filter and a CSV export of every field above.

### Screenshot recognition
- Reads instrument, timeframe, pattern, direction, entry, stop and target, each with a confidence.
- **Review panel:** every field shows what was read, how sure CHRONOS is, and what it did with it.
- **Confidence rules:**
  - below 70% the field is filled but highlighted
  - below 40% it is left blank and only offered as a "Use" suggestion
  - levels that contradict the side (for example, a long with the stop above entry) are all highlighted
- The test will not run until highlighted fields are edited or confirmed.
- The out-of-credit message is unchanged. Tests use a mocked reader.

### Mobile and UX
- Help text under each section, a loading indicator with stage text and elapsed time, an empty state explaining what a test returns (with an example button), and clear errors for unavailable market data, short history and rate limits.
- Sticky top bar, so Backtester and Copy trading stay one tap away.
- The earlier mobile file-picker fix (1d7acf4) still passes its real-click test.

## Effect on results

With the default 1 bps, net results are a little lower than before on market and stop fills. Gross results are unchanged. Example (real candles, 60 days, every-close mode, run 27 Nov 2026):

| Market | Closed trades | Gross R | Net R | Fees R | Slippage R |
| --- | --- | --- | --- | --- | --- |
| BTC long | 109 | -25.0 | -39.0 | 12.6 | 1.4 |
| XAU short | 102 | -6.0 | -13.7 | 4.9 | 2.8 |
| EUR/USD long | 28 | -12.0 | -14.2 | 1.2 | 1.1 |

These are tests of arbitrary example geometries, not strategies, and they lose. That is expected and is reported as it is.
