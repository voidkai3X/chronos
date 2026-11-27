# CHRONOS Edge Score

The Edge Score is a number from 0 to 100 that summarises how much statistical evidence one backtest gives for an edge. It is computed only from that test's own closed trades, after fees, swap and slippage. Code: `edge_score.py` (version 1). Tests: `tests/test_edge_score.py`.

**It is a historical statistical assessment of this backtest only. It is not a prediction, not financial advice and not a guarantee of future results.** The site shows this disclaimer next to every score.

## In plain words

A setup scores well only when:
- it made money per trade after costs (measured in R, not win rate),
- that average is clearly larger than the trade-to-trade noise,
- there are enough trades for the average to mean something,
- the profit held up across different stretches of time and different market conditions,
- the drawdown was small compared with what it earned, and
- the profit survives doubled costs.

Win rate on its own earns nothing. A 90% win rate with small wins and big losses has a negative average R and scores at most 15.

## Components

Each component is scored from 0 to 1, then multiplied by its weight. The weights add up to 100. The site's "How this is calculated" panel lists every component with this test's value, weight and points.

| Component | Weight | Full marks at | Zero at |
| --- | --- | --- | --- |
| Statistical confidence: t-statistic of average R after costs (mean / (std / sqrt n)) | 25 | t of 3 or more | t of 0 or less, or average R of 0 or less |
| Average R after costs (expectancy) | 15 | +0.30R per trade | 0R or less |
| Sample size: closed trades | 15 | 100 or more | 15 or fewer, or average R of 0 or less (a big losing sample is evidence against an edge) |
| Profit factor after costs | 10 | 2.0 or more | 1.0 or less |
| Drawdown vs return: net R / 95th-percentile Monte Carlo drawdown | 10 | 3x or more | net R of 0 or less |
| Consistency over time: share of chronological segments with a positive average R (4 segments from 40 trades, 3 from 15) | 10 | every segment positive | none positive, or average R of 0 or less |
| Consistency across market conditions: share of condition groups (trend/range, high/low volatility, bullish/bearish/mixed, tagged from closed 4h candles at the signal) with 5+ trades and a positive average R | 10 | every group positive | fewer than 3 groups with 5+ trades, or average R of 0 or less |
| Survives double costs: average R with every trade's own costs doubled, divided by the average R | 5 | no edge lost | edge gone |

## Caps

The score cannot go above these, whatever the components add up to:

| Condition | Highest possible score |
| --- | --- |
| Fewer than 15 closed trades | 15 |
| Average R after costs is zero or negative | 15 |
| Fewer than 30 closed trades | 40 |
| The 90% bootstrap range of average R includes zero | 60 |
| The edge disappears when costs are doubled | 60 |

So a test needs at least 30 trades to reach Promising, and a 90% bootstrap range of average R that is entirely above zero (plus survival at double costs) to reach Strong. The panel lists any cap that was applied.

## Bands

| Score | Band | Engine verdict |
| --- | --- | --- |
| 0-19 | No evidence | "no edge" (average R of 0 or less), "too few samples" (under the minimum), or "no reliable edge" |
| 20-44 | Weak | "no reliable edge" |
| 45-69 | Promising | "possible edge, not proven" |
| 70-100 | Strong | "edge on this sample; past results only" |

The verdict text and the live-scan gate use the same score, so the old, more generous "possible edge" rule (a positive average and a t-stat threshold) no longer applies. The live scan in similar mode only opens for Promising or Strong.

## Worked example (from the tests)

- **90% win rate, negative expectancy.** 200 trades: 180 wins of +0.1R and 20 losses of -1.2R. Average R = -0.03. Score **0**, "No evidence", verdict "no edge". The sample-size component gives 0 points because a big losing sample is evidence against an edge.
- **40% win rate, positive expectancy.** 150 trades of +2R / -1R at a 40% win rate. Average R = +0.20, positive in all 4 time segments. Raw score 71.6, but the 90% bootstrap range of average R still touches zero (t = 1.67), so it is capped at **60, Promising**.
- **The same edge over 400 trades.** The bootstrap range is now entirely above zero, so the score reaches **85, Strong**.
- **Same expectancy, different win rates.** A 90% and a 20% win-rate setup with the same +0.10R average get the same expectancy points. Win rate is not a component.

## Advanced analytics (shown beside the score)

All three use the real closed trades only. Nothing is simulated from made-up prices.

- **Earlier vs later trades (out-of-sample check).** The first two thirds of the trades (in time order) against the last third: trades, win rate, expectancy, profit factor, net R, max drawdown and t-stat for each part. CHRONOS does not fit anything on the first part, because it uses the trader's own levels, so this shows whether later trades behaved like earlier ones. It needs 10 or more trades, and it warns when the later part has fewer than 15.
- **Bootstrap ranges.** 2,000 resamples (with replacement) of the real trades give a 90% range (5th to 95th percentile) for average R and win rate, plus the share of resamples with a positive average.
- **Monte Carlo drawdown.** 1,000 random orderings of the same trades give the median, 95th-percentile and worst max drawdown in R, compared with the drawdown in the real order.

The random seed is fixed (20261127), so the same trades always give the same numbers.

## Limits

- The score describes one test window on one market. A rolling 90-day window moves, so scores change from run to run. The run history in My strategies shows this.
- Bootstrap and Monte Carlo assume trades are independent. Trades that cluster in time can make the ranges look narrower than they are.
- Condition groups overlap (each trade is in one trend, one volatility and one direction group), so they are not independent tests.
- Thresholds (t of 3, +0.30R, 100 trades, PF of 2, 3x recovery) were set before looking at any results and were not tuned to make any setup look good.
