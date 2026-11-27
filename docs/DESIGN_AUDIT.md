# CHRONOS design audit and refinement plan

Branch `ui-refine`, 27 Nov 2026 (WAT). Scope: a visual refinement of the existing CHRONOS identity (name, infinity logo, dark green background, neon/lime accent). It is not a rebrand. All Phase 1-3 functions and data stay as they are.

## 1. What exists today

**Frontend structure.** One page (`web/static/index.html`, 645 lines) with plain JS modules: `app.js` (form, run, results, charts), `p2.js` (views, strategy comparison, trade replay), `p3.js` (Edge Score, analytics, strategy library), and `account.js` (header account and Copy trading tab). `auth.html` + `auth.js` serve sign-in, sign-up and reset. Charts are hand-drawn `<canvas>` (equity, R histogram, replay candles, comparison overlay, library trend). There is no build step, and every asset is self-hosted.

**Design system.** `style.css` (978 lines) is three theme layers stacked on top of each other: the base styles, then a "neon green theme + time motion" layer, then a later layer that tones the motion down. Measured on the current file:

| Item | Count |
| --- | --- |
| `:root` token blocks | 4 (tokens are redefined three times) |
| Distinct hex colours | 95, plus 152 distinct `rgba()` values |
| Distinct font sizes | 19 px values and 17 shorthand sizes (10 px to 34 px) |
| Border radii | 13 values (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 16, 18 and 999 px) |
| Keyframe animations | 20 (grid drift, 60 s conic sweep, timeline light, logo trace, glow pulse, title glow, dot blink, sheen, shimmer and more) |
| Fonts | Inter (UI), Space Grotesk (headings and numbers), Nunito 900 (wordmark and tagline), system monospace (data) |

## 2. Findings

**Brand and colour**
- The identity is strong and recognisable: the neon infinity mark, the green-black background and the lime accent should stay.
- Glow is used almost everywhere: logo, wordmark, tagline, panel titles, cards, buttons, price pill, badges, the verdict card and checkboxes. Glow on everything stops reading as an accent and starts to feel like a gaming UI.
- The greens are inconsistent: five or more accents (`#4dff1a`, `#39e61a`, `#39ff14`, `#5fd63a`, `#3ddc84`, `#4cc22c`) are used for the same meaning. Red is a saturated `#ff4d5e`/`#ff7583`, which shouts on a dark background.
- The background has continuous motion on desktop (drifting grid, rotating sweep, running timeline light, blinking dots). It is already off on phones, but it is still distracting on desktop.

**Typography**
- Four families with overlapping jobs. Headings are Space Grotesk, numbers mix Space Grotesk and the system monospace, and 38 rules use weight 600 and 10 use 700, so most text feels bold.
- Uppercase letter-spaced labels are used for nearly every field label, card title and table header. That adds noise and makes the long form look heavier.
- Numbers are not consistently tabular, and key statistics (24 px cards) are not much larger than labels.

**Layout and form**
- The setup form is one long column of roughly 25 controls with long help paragraphs between them. On desktop it is about 2.5 screens tall; on a phone, about 4.
- Primary inputs (market, side, levels) and rare settings (slippage unit, overlap, HTF confirmation, timeframe sweep) have the same visual weight. Nothing is collapsible except "How the test works".
- The view switch (Test / Compare / My strategies) sits inside the form panel. Backtester / Copy trading tabs sit in a separate top bar, so navigation is split across two places.

**Results**
- Results are a single column of about 15 stacked panels (verdict, Edge Score, warnings, 13 cards, why, conditions, timeframes, analytics, equity, histogram, outcomes, monthly, theoretical vs net, similar moments, trades, insights, other pairs, live scan, meta). The important numbers and the equity curve are far down. The page is about 8,700 px tall on a phone.
- All cards look the same: "Setups identified" has the same weight as "Net P/L" and "Profit factor".
- There is no drawdown chart. The equity curve has no time axis (trade index only), and the charts cannot zoom or pan.

**Charts**
- The replay candles are a custom canvas with fixed zoom. There is no wheel or pinch zoom and no pan, and prices are labelled only on the TP, SL and entry lines.
- The canvas charts use hard-coded colours that do not match the tokens.

**Mobile**
- There is no page-level horizontal overflow (existing tests check this), but tables scroll sideways with no fixed first column, so you lose which row you are reading.
- Several tap targets are under 44 px: small ghost buttons (`.btn-ghost.sm`, about 32 px), replay controls, checkbox rows, table sort headers, shortcut chips (36-40 px) and the "Replay" buttons in the trade table.
- The navigation is a small top bar that scrolls away with the hero. The results are below the whole form, with no quick way to jump to them.

**Accessibility and performance**
- `prefers-reduced-motion` is respected, but several animations are not transform or opacity based (filter drop-shadow pulse, text-shadow pulse, background-position shimmer, stroke-dashoffset trace).
- Contrast is good for primary text. Some muted text (`#5f7a66` placeholders) is low.

## 3. Plan (implemented incrementally on `ui-refine`)

1. **One token set.** A single `:root` holding:
   - **Backgrounds:** almost-black green (`#050c08`), raised surfaces and deep forest green.
   - **Lines:** hairline borders at low-alpha green.
   - **Text:** off-white primary, soft grey secondary.
   - **Accent:** one neon lime (`#6dff3c`).
   - **Results:** one positive green and one muted red. A muted amber is kept only for caution states (too few samples, data warnings), which already existed.
   - **Shape and depth:** 3 radii (8, 12 and 16 px) and one shadow scale.
2. **Typography.**
   - **Families:** Geist (UI) and Geist Mono (all numbers and data, tabular), self-hosted under the SIL OFL. Nunito stays only for the CHRONOS wordmark in the logo lockup.
   - **Type scale:** 12 / 13 / 14 / 16 / 20 / 28 / 40 px.
   - **Weights:** mostly 400-500, with 600 for headings only.
   - **Labels:** sentence case. Uppercase micro labels only for table headers and eyebrows.
3. **Navigation.**
   - **Desktop:** one sticky top bar with the logo, Backtester, Compare, My strategies and Copy trading, plus an Account menu (email, plan, free-until date, sign out, delete account).
   - **Phones:** a compact top bar (logo and account) and a fixed bottom bar: Backtest, Results, Compare, Library, Copy.
   - **Pages:** no new pages.
4. **Form.**
   - **A · Strategy setup:** pattern, screenshot upload, market, direction, entry, TP and SL with live R:R.
   - **B · Backtest configuration:** method, setup timeframe, candles in the fingerprint, test window, order expiry and trend filter.
   - **Advanced (collapsible):** higher-timeframe confirmation, time stop, run across timeframes, slippage, overlapping trades and insights.
   - **Run test:** stays prominent and sticky at the bottom of the form.
5. **Results dashboard.**
   - **Always visible:** a result header (verdict, setup and data provenance), the Edge Score, then seven primary stat cards (win rate, total trades, net P/L, profit factor, max drawdown, expectancy and average R:R). Secondary numbers sit under "More numbers".
   - **Tabs:**
     - **Overview:** equity and drawdown, and why it worked or failed.
     - **Performance:** wins vs losses, monthly, distribution, and theoretical vs after costs.
     - **Trades:** trade table and closest past moments.
     - **Analysis:** market conditions, timeframes, advanced analytics and insights.
     - **Other markets:** test on other pairs and live scan.
   - **What stays:** every Phase 1-3 number. Nothing is removed.
6. **Charts.**
   - **Library:** self-hosted TradingView Lightweight Charts v5.3 (Apache-2.0) for the equity and drawdown chart (two panes, real exit times, WAT axis) and the trade replay candles. The replay shows green and red candles, entry, TP and SL price lines, and signal, fill and exit markers.
   - **Controls:** wheel and pinch zoom, drag to pan, and zoom-in, zoom-out and reset buttons.
   - **Attribution:** the TradingView link required by the licence is shown on the charts and in the footer.
   - **Other charts:** the distribution, monthly, outcomes, comparison overlay and library trend stay on canvas or HTML, restyled to the tokens.
   - **Data:** charts only ever draw the test's own trades and stored candles. There are no placeholders.
7. **Effects.**
   - **Background:** a very faint static grid and one soft green ambient light at the top.
   - **Panels:** hairline borders and a soft shadow. Glow only on the logo, the primary button and the Edge Score.
   - **Motion:** hover and tab transitions use opacity and transform only. There is no background motion anywhere, and reduced motion is respected.
8. **Brand.**
   - **Logo:** the same infinity mark in the nav, footer, empty state, auth pages and account menu.
   - **Loader:** a new infinity loader (a light travelling along the mark, using motion path and opacity) replaces the grey shimmer skeleton.
9. **Mobile.**
   - **Layout:** a single column that reflows vertically.
   - **Tap targets:** 44 px minimum on every control.
   - **Inputs:** 16 px text (no iOS zoom).
   - **Tables:** scroll inside their own frame with a sticky first column and sticky header.
   - **Charts:** sized to the screen.
   - **Screenshot upload:** the Phase 1 file-picker fix (`1d7acf4`) is kept unchanged: the file input is still an opacity-0 input stretched over "Choose file", never `display:none`.
10. **QA.** `tests/design_qa.cjs` checks every view (home, advanced open, results tabs, compare, library, Copy trading, account, sign-in and replay) at 360, 390, 430, 768, 1280 and 1440 px. It checks for page-level horizontal overflow and for tap targets under 44 px on touch widths. All Python tests and the browser suites (`auth_e2e`, `p1`, `p2` and `p3` shots, and the screenshot picker) must still pass.

## 4. Before

Reference screenshots of the current design (before this branch) are kept outside the repo, in `/workspace/chronos-notes/before/`.
