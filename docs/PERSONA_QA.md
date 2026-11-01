# Persona QA: did following Rex actually help?

Four simulated traders used Rex end to end through the real API and the real UI (Chrome via puppeteer), on the bundled real NSE data (yfinance daily bars, last bar 2026-10-30; the market was closed, so live tracking used the last close). Nothing here is advice. Every number below is produced by scripts in `backend/persona_qa/` and can be re-run.

How to re-run: `persona_flows.py` (journeys A/B/C, API on :8002), `persona_ui.mjs` (UI journeys A/B/D, API :8000 + web :3000), `journal_persona.py [--sweep]` (persona D), `oos_refine_study.py` + `summarize_oos.py` (out-of-sample study). Raw outputs are the `*_results.json` files and `persona_ui_log.txt`. Screenshots: `docs/screenshots/persona-*.png`.

## Journeys (what Rex said, what the trader did)

| Persona | Strategy | Backtest (train / locked year) | Refine verdict | Trader's choice | Logged trade and tracking | Close and memory |
|---|---|---|---|---|---|---|
| A. Beginner Bina | Vague idea: "buy Reliance when it is going up a lot...". Rex parsed nothing, invented nothing, and suggested wording. Re-typed: RELIANCE 20-day-high breakout, hold 10, 5% stop, 10% target | train -12.3%, locked -2.3% (buy and hold locked -21.4%) | OVERFIT (tuned: train +20.2%, locked +0.65%) | Followed Rex: kept the original rules | Entry 1195 x 10 on 27 Oct; live 1164.9 = -2.67% (-Rs 319); no exit alert (stop 2.5% away) | Closed LOST; "Last time" on the next RELIANCE backtest shows the tracked trade and a lesson. Before her first run it already warned: 11 of 12 breakout backtests lost, 3 of 3 refinements overfit |
| B. Swing Sameer | TCS 20/50 MA cross, hold 15, 4% stop, 8% target | train +7.3%, locked -1.9% (buy and hold -14.0%) | PASSED (tuned 16/30, 2% stop, 12% target: train +31.2%, locked +7.1%) | Followed Rex: used the refined rules | Entry 2568 x 5 on 23 Oct; live 2502.6 = -2.70% (-Rs 346); exit alert fired: stop 2516.64 hit on 26 Oct | Closed LOST; lesson "Price went past your stop... honour the stop". Next TCS check shows it |
| C. Optimizer Omkar | JIOFIN RSI 14/30, hold 8, 4% stop, 8% target | train -7.3%, locked -15.0% | OVERFIT (tuned: train +17.1%, locked -15.2%) | Ignored Rex: used the tuned rules | Entry 211.05 x 50; live 204.5 = -3.25%; alert "max hold of 4 days reached (29 Oct)" | Closed LOST; lesson "exit fired but you held longer, cost about 1.2%". A similar RSI level 35 backtest: locked -25.9% |
| D. Chaser Chintu | Uploaded tradebook CSV (Zerodha style, 154 rows, 77 round trips) | n/a | n/a | n/a | Pre-trade check on 29 later entries | Journal flagged "gap up at the open: 6 trades, win rate 17% vs 67%". UI check: HDFCBANK 2025-12-24 -> WAIT; JIOFIN 2025-06-10 -> PROCEED |

## Did following Rex improve results? (real data, out of sample)

There is no data after 2026-10-30, so tests are as-of: Rex's tuning and its locked year end at a cut date, and the strategy is judged only on bars after the cut.

**1. Refine verdicts (80 cases: 10 tickers x 4 templates x cuts 2024-04-30 and 2025-04-30; 76 valid, 59 where tuning changed the rules).** "Follow Rex" = switch to tuned rules only if the verdict is `ok`; "ignore Rex" = always switch.

| Group | Cases | Original rules | Tuned rules, always | Follow Rex | Buy and hold |
|---|---|---|---|---|---|
| All | 76 | -4.39% | -8.30% | -4.68% | -3.45% |
| Rules changed | 59 | -4.47% | -9.50% | -4.84% | -2.23% |
| Flagged OVERFIT | 37 | -6.69% | -14.13% | -6.69% | -8.44% |
| Flagged ok | 22 | -0.73% | -1.71% | -1.71% | +8.22% |

(Average forward return over the unseen period.) Average max drawdown: original -14.98%, tuned-always -19.84%, follow Rex -16.31%. Among the 37 OVERFIT cases, tuned rules were worse than the original in 27. The OVERFIT warning was valuable: ignoring it cost about 7.4 points of forward return. The `ok` verdict was **not** reliable: tuned rules were worse than the original in 10 of 22 `ok` cases. Rex now says so next to every "held up" verdict.

**2. Journal check (persona D, 12 simulated repeat-mistake traders, seeds 1-12, trade prices from real opens/closes, Rs 50,000 per trade, 15 bps costs).** Policy: skip on `wait`, scale size on `reduce_size`. Tradebook before 2025-06-01 trains the journal; later entries are checked one by one as of their date.

| | Ignore Rex | Follow Rex |
|---|---|---|
| Sum of test P&L over 12 traders | -Rs 117,181 | -Rs 94,370 |
| Sum of worst drawdowns | -Rs 282,048 | -Rs 215,600 |
| Traders better off on P&L | | 7 of 12 |
| Traders better off on drawdown | | 12 of 12 |

Seed 7 (the one shown in the UI): ignore P&L -4,961 / drawdown -22,958 over 29 trades; follow P&L -8,973 / drawdown -20,369 over 19 trades. The trades Rex told it to skip would have made +4,711 there. In the 12-trader sweep the skipped `wait` trades summed to -Rs 10,810 if taken.

**Bottom line.** Following Rex made these traders lose less (smaller drawdowns every time, smaller total loss), but nobody in the simulation was *profitable* out of sample: all versions lost money on the unseen period, and plain buy and hold was also negative in these windows. Rex reduced the damage and did not create an edge.

## Bugs and confusing UX found and fixed

- Parser flagged "a lot" as position sizing, so a beginner's vague idea got a misleading reason. Fixed; now the message says nothing was guessed and gives a working example sentence (test added).
- Tracker page crashed for a session with no trade ("Cannot read properties of null (reading 'fired')"). Fixed with a nullable type.
- Strategy-run counts stopped growing in the UI because `limit` capped `count`. API now returns `total` (test added).
- "Held up" refinements looked like a green light. Added a caveat citing the out-of-sample result above.
- Close lesson said "up +0.8% at its best ... consider a trailing exit" for a trade that never really went up, and Rex does not support trailing exits. Now needs at least +2% at best and suggests taking profit at the strategy's target.
- When the stop was already passed, "Distance to stop -0.6%" was confusing. Now shows "Stop level passed, price is 0.6% beyond it".

## Limitations

- Small samples. 12 simulated journal traders and 76 refine cases are hints, not proof; only two cut dates.
- Persona D's behaviour is synthetic (a chase-the-gap-up rule on real prices), so it is a test of the mechanics, not of real people. Its P&L numbers come from a Rs 50,000 notional per trade.
- Personas A to C each had one logged trade; the market was closed, so live tracking used the 30 Oct close and "Last time" could not yet show how price moved afterwards.
- JIOFIN lacks history for the 2024 cut; 4 of 80 study cases were skipped.
- Daily bars only; no intraday, no live feed in offline mode; stops are checked on daily bars.
- Rex only tracks and alerts. It never places or changes orders in any broker.
