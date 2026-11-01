# Rex Lite accuracy audit

Date: 1 Nov 2026 (IST). Data: real yfinance NSE daily bars from the bundled cache, last bar 2026-10-30. Everything here is reproducible:
`backend/audit/*.py` (needs the API on :8003 for three of them) and `backend/tests/test_audit_accuracy.py` (68 tests, run in the normal `pytest`).

## Method

1. **Independent reference** `backend/audit/ref_backtest.py`: a separate minimal pandas/numpy backtester that imports nothing from `app`. It re-implements the
   rules (breakout, ma_cross, rsi, mean_reversion), the entry/exit engine and the metrics from the written spec only.
2. **Compare** the app's `/backtest` with the reference on 80 runs = 10 tickers x 4 templates x (default params at 15 bps, plus a varied set: alternative params, six stop/tp/hold variants
   including no stop, hold 1 and hold 120, and costs of 0/15/100/500 bps). Compared per window (train / locked year): trade count, every entry date, exit date, exit reason, trade return, total return, Sharpe, max drawdown,
   win rate, and buy-and-hold.
3. **Validate the audit itself** by mutation: with a deliberately wrong reference cost the same script reports 2,230 mismatches, so it can fail.
4. Run `/strategies/parse` -> `/backtest` -> `/refine` on plain-English text for all 10 tickers and 4 templates, plus edge cases.
5. Lookahead, locked-year, tracker and memory checks (below).

## Results

| Check | Result | Numbers |
|---|---|---|
| Engine vs independent reference (trades, dates, returns, Sharpe, drawdown, win rate, buy&hold) | **PASS** | 80 runs, 2,172 trades, 1,120 metric comparisons, **0 mismatches** |
| Costs applied (half at entry, half at exit) | **PASS** | 0, 15, 100 and 500 bps all match the reference; wrong-cost mutation gives 2,230 mismatches |
| Stop / take-profit / hold exits, gap fills, stop-before-tp inside a bar | **PASS** | every exit date and reason matches the reference; tracker `evaluate_exit` reproduces the same exit on the same bar on 4 templates |
| Buy & hold benchmark | **PASS** | matches reference on every window |
| No lookahead: signals use only past bars | **PASS** | prefix-truncation: 240/240 signal series identical; future prices scrambled x0.3-3: 240/240 identical signals; 240/240 identical closed trades |
| Entries at the NEXT bar's open | **PASS** | 794/794 entries equal the open of the bar after the signal bar |
| Locked year never touches tuning | **PASS** | locked year multiplied x3 + noise: the chosen parameters, top candidates and trial count were identical on 5/5 (audit) and 2/2 (tests) combos |
| Refine picks the best training Sharpe | **PASS** | 40 combos, 0 mismatches; top candidates re-scored by the reference; counts consistent ("Best of N eligible (M combinations tried)") |
| Parser: numbers come only from the text | **PASS** | 560/560 generated English+Hinglish cases correct; 27/27 edge cases; 20,000 random-fragment fuzz run during the audit (0 crashes) and 3,000 in pytest; 0 numbers not present in the text |
| Tracker P&L math (long, short, costs) | **PASS** | `net = dir x (exit/entry - 1) x 100 - cost_bps/100`; 5 parametrised cases incl. short, 0/15/40 bps, abs P&L |
| Memory "Last time" correctness | **PASS** | shipped DB: 80/80 replayed runs re-derived with the reference using only data up to their date: 800 stored fields + 160 forward 5/20-day moves compared, **0 mismatches**; headline numbers equal stored numbers |
| Full UI run (`ui.mjs`) on a fresh DB | **PASS** | PROBLEMS: none |

## Bugs found and fixed

| # | Bug | Fix | Test |
|---|---|---|---|
| 1 | Refine page showed "Best of 203 candidates" in the verdict and "Best of 400 candidates" under What changed (grid size vs candidates that qualified) | Both now come from the same search numbers: "Best of {eligible} eligible candidates ({trials} combinations tried)" | `test_candidate_counts_are_consistent_everywhere` |
| 2 | When no nearby candidate had enough training trades (e.g. JIOFIN ma_cross 10/50, 0 of 400 eligible), Refine said the rules were "already the best of the nearby grid" | Flag `inconclusive`, honest headline ("none of the 400 nearby combinations had 10+ training trades, so your rules were kept") | `test_no_eligible_candidates_is_not_called_best_of_grid` |
| 3 | Research page could show the wrong ticker after quick navigation (a slow earlier response overwrote the newer one; seen as `/research?t=JIOFIN` showing RELIANCE) | `useGet` ignores any response that is not the latest request | covered by `ui.mjs` (research page) |
| 4 | Parser raised HTTP 500 on some Hinglish text ("RSI 25 se neeche") | `_fmt` handles strings; Hinglish layer maps words only, never invents numbers, and says so in the notes | `test_parser_edge_cases`, `test_parse_endpoint_hinglish_is_200_not_500` |
| 5 | "stop loss 5% take profit 3%" (and reversed orderings) read take-profit as 5 | stop/tp matching picks the number tied to its own keyword | `test_parser_edge_cases` |
| 6 | "SBIN 70 se upar RSI" was read as oversold 30 | word-order rule (upar = above = overbought) | `test_parser_edge_cases` |

The backtest engine itself needed no changes: 0 mismatches against the independent reference.

## Conventions worth knowing (documented, not bugs)

* **Hold exit**: a trade entered at the open of day e with `hold_days = h` exits at the close of bar e + h, i.e. h + 1 sessions including the entry day.
  Decided to document rather than change, because the reference, the tracker and the memory replays all share this convention.
* **Tracker vs backtest cost**: the tracker subtracts the round-trip cost from the percent move; the backtest compounds half the cost on each side.
  They differ only at second order (about 0.015 percentage points for a 10% move at 15 bps).
* **Forward returns** for live runs anchored at the latest bar are empty until new data arrives (shown as "not enough trading days yet").
* The honest overfit check is strict: 36 of 40 default ticker/template combos are flagged `overfit` (3 `ok`, 1 `inconclusive`). That is the product being
  honest about tuned parameters, not an error.

## Limits of this audit

* The optional Gemini parser path was not exercised offline (it is re-validated by the same rules, but not audited here).
* The reference shares the spec with the app (same rule definitions); it proves the engine implements that spec exactly, not that the rules make money.
* Past results do not predict future results; Rex tracks and tests, it never places orders.

## Re-run

```
cd backend
env -u MEKO_TOKEN -u GEMINI_API_KEY python -m pytest -q
REX_OFFLINE=1 REX_DB=/tmp/audit.sqlite python -m uvicorn app.main:app --port 8003 &
python audit/audit_backtest.py http://127.0.0.1:8003 ; python audit/audit_refine.py http://127.0.0.1:8003
python audit/audit_parser.py http://127.0.0.1:8003 ; python audit/audit_lookahead.py ; python audit/audit_memory.py
```
