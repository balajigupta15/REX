# Rex Lite: real example output (recorded 1 Nov 2026, IST)

Data: real yfinance daily bars (auto_adjust) through 2026-10-30. 15 bp round-trip cost is an ASSUMPTION. Locked year = 2025-10-31 to 2026-10-30. Raw JSON in `backend/examples/`.

## Backtest: RELIANCE.NS breakout n=20, hold 10 / stop 5% / tp 10%
- Train: 25 trades, win 36%, return -12.3%, Sharpe -0.28, maxDD -18.1%, B&H +26.0%
- Locked: 2 trades, win 50%, return -2.3%, Sharpe -0.35, maxDD -8.6%, B&H -21.4%

## Backtest: JIOFIN.NS RSI oversold (14, <30), same exits
- Train: 9 trades, win 56%, return +1.8%, Sharpe 0.14, maxDD -17.3%, B&H +21.7%
- Locked: 8 trades, win 12%, return -20.6%, Sharpe -1.58, maxDD -22.2%, B&H -33.8%

## Refine: RELIANCE.NS breakout
- Original {'n': 20} {'hold_days': 10, 'stop_pct': 5.0, 'tp_pct': 10.0}
  - Train: 25 trades, win 36%, return -12.3%, Sharpe -0.28, maxDD -18.1%, B&H +26.0%
  - Locked: 2 trades, win 50%, return -2.3%, Sharpe -0.35, maxDD -8.6%, B&H -21.4%
- Refined {'n': 16} {'hold_days': 15, 'stop_pct': 5.0, 'tp_pct': 5.0} (best of 400 trials on TRAIN Sharpe)
  - Train: 29 trades, win 59%, return +20.2%, Sharpe 0.46, maxDD -21.0%, B&H +26.0%
  - Locked: 3 trades, win 67%, return +0.7%, Sharpe 0.14, maxDD -5.3%, B&H -21.4%
- Verdict: **overfit**. Refined Sharpe fell from 0.46 (train) to 0.14 (locked year): the tuned parameters did not hold up. On the locked year the refined version returned +0.7% vs -2.3% for your original (small samples can flatter either version). Best of 400 candidates tried on training data; picking the best of many looks better than it is (multiple testing).

## Refine: TCS.NS mean_reversion (20, z 1.5)
- Original {'n': 20, 'z': 1.5} {'hold_days': 10, 'stop_pct': 5.0, 'tp_pct': 10.0}
  - Train: 31 trades, win 48%, return -16.8%, Sharpe -0.37, maxDD -26.5%, B&H -9.5%
  - Locked: 9 trades, win 22%, return -18.7%, Sharpe -1.25, maxDD -22.3%, B&H -14.0%
- Refined {'n': 30, 'z': 2.25} {'hold_days': 10, 'stop_pct': 10.0, 'tp_pct': 7.5} (best of 400 trials on TRAIN Sharpe)
  - Train: 12 trades, win 67%, return +22.3%, Sharpe 0.80, maxDD -9.0%, B&H -9.5%
  - Locked: 4 trades, win 50%, return -13.6%, Sharpe -1.25, maxDD -17.8%, B&H -14.0%
- Verdict: **overfit**. Refined Sharpe fell from 0.80 (train) to -1.25 (locked year): the tuned parameters did not hold up. On the locked year the refined version returned -13.6% vs -18.7% for your original (small samples can flatter either version). Best of 400 candidates tried on training data; picking the best of many looks better than it is (multiple testing).

Both refinements flagged OVERFIT: the tool reports that honestly rather than hiding it. Small locked-year samples (2 to 8 trades) mean every number here is a hint, not proof.

## Test results
`python3 -m pytest -q tests` -> 151 passed with `REX_OFFLINE=1` (core + Live Strategy Tracker + strategy-run memory + journal + Meko bad-token fallback + research / scout / challenger + 42 plain-English parser tests, one of which calls Gemini live and is skipped when `GEMINI_API_KEY` is absent). Frontend: `tsc --noEmit` clean, `next build` OK, headless Chrome click-through of every page with zero console errors or warnings. Synthetic fixtures: hand-built OHLC for exit logic, hand-built fills for pairing and hand-built journal rows for the wait/reduce/proceed rules. Real yfinance cache for fingerprints, the sample journal and API tests.

## Plain-English strategy input (`POST /strategies/parse`)
Verified 1 Nov 2026 (IST). Rules parser: 21 phrasings, every produced strategy is accepted by `/backtest`. Gemini path called live with `gemini-3.6-flash` (about 5 s per call; `gemini-3.1-flash-lite` about 1.4 s as fallback; `gemini-3.8-flash` was slow and timed out under load so it is not the default). The model's output is re-validated, so it cannot add numbers that are not in the text. Cached repeats return in ~0 ms. Unsupported ideas (short selling, volume, trailing stop, other stocks) are listed back to the user, not approximated.

## Trade journal and pre-trade check (SAMPLE DATA)
The journal below is **synthetic trader behaviour on REAL yfinance prices** (`backend/sample/SAMPLE_tradebook_synthetic_journal_real_prices.csv`, generator seeded 42). It is NOT a real trader's record. It shows the mechanics only.

Import: 282 fills read, 0 rejected, **118 round trips**, 3 open positions (ITC, SBIN, LT, excluded). Re-import adds 0 (idempotent).
- All 118 trades: win rate 77.1%, avg return +0.08%, avg win +2.46%, avg loss -7.94%, total PnL +10,310 (gross, no costs), avg hold 16.9 days.
- You hold losing trades about 33.1 calendar days vs 12.1 for winners (27 losers, 91 winners). Losers run longer than winners: a pre-set stop would address that.
- BAD pattern: When you bought with price above both the 20 and 50 DMA and stressed volatility regime (20-day vol over 1.2x its 1-year level): 15 trades, win rate 60% (your average is 77%), avg return -2.2%. This is a repeat mistake pattern. Small sample: a hint, not proof.
- BAD pattern: When you bought with price stretched more than 5% above its 20 DMA (extended) and stressed volatility regime (20-day vol over 1.2x its 1-year level): 13 trades, win rate 62% (your average is 77%), avg return -2.5%. This is a repeat mistake pattern. Small sample: a hint, not proof.
- good pattern: When you bought with price more than 5% below its 20 DMA (deep pullback) and gap up at the open (1% or more): 9 trades, win rate 100% (your average is 77%), avg return +2.2%. This looks like your better setup. Small sample: a hint, not proof.
- good pattern: When you bought with volume spike (prior session at least 1.5x its 20-day average) and flat open: 8 trades, win rate 100% (your average is 77%), avg return +2.1%. This looks like your better setup. Small sample: a hint, not proof.

Pre-trade checks (`examples/trades_check_*.json`), long entries, sample journal:

| Proposed | Fingerprint | Similar n | Win rate (overall 77%) | Avg return | Recommendation |
|---|---|---|---|---|---|
| INFY 2026-10-15 buy | bounce_in_downtrend, stretched, spike, normal, gap_up | 7 | 57% | -1.29% | reduce_size (medium) |
| RELIANCE 2026-10-30 buy | below_both, near, normal, stressed, flat_open | 10 | 80% | +0.98% | proceed (high) |
| TCS (latest close) buy | above_both, stretched, dry, normal | 10 | 80% | +0.84% | proceed (high) |

The `wait` rule path is covered by a test on a hand-built journal; none of the real-price sample examples trigger it.

## Memory OFF vs ON proof (`GET /memory/proof`, SAMPLE journal, 118 round trips)
Walk-forward, no look-ahead: each trade is checked using only trades that closed before it. The first 10 per direction are warm-up (taken at full size).
- Decisions: 64 proceed, 36 reduce size, 7 wait, 11 warm-up.
- Memory OFF (take everything): P&L +10,310, max drawdown -55,495, win rate 77.1%, 118 trades.
- Memory ON (wait = skip, reduce = half size): P&L +4,813, max drawdown -48,005, win rate 76.6%, 111 trades.
- The 7 skipped trades would have made +8,168; the 36 reduced trades lost -5,342 at full size (-2,671 at half size).
- Honest reading: following the check cut drawdown by about 13% and halved the damage on flagged trades, but total P&L is LOWER on this journal because the skipped trades happened to win. One small synthetic journal is a demo of the mechanism, not evidence that it adds return. The UI states this plainly.

## Meko MCP (verified live 1 Nov 2026, IST)
Endpoint `https://mcp.mekodata.ai/mcp` (streamable HTTP, Bearer token from `MEKO_TOKEN`, never printed or committed). 42 tools listed, including `memory_add`, `memory_search`, `memory_get_all`, `memory_get_by_id`, `memory_update`, `memory_delete_by_id`, `memory_delete_all`, `memory_promote`, `context_search`, `knowledgebase_search`, `conversation_create/add_message/get/list/update/delete`, `datapack_*`, `artifact_put/get`, `track_token_usage`, `amp_*`.
- Used by Rex: `conversation_create(agent_id, title, ...)` -> `memory_add(text, conversation_id, agent_id, metadata=<JSON string>)` -> `memory_search(query, conversation_id, agent_id, limit)` (search is cross-agent, so Rex filters results to agent_id `rexlite`) and `memory_delete_by_id`.
- Verified end to end: a lesson was written and found again by a natural-language query (text stored verbatim, metadata round-trips as a dict). `memory_delete_by_id` is wired in `app/meko.py`; its use against the live server was not separately re-verified in this pass.
- Caveats: `memory_search` took about 7 to 18 seconds, so the Check page fetches recall in the background. Seeding and the sample journal are never mirrored (no flooding); only lessons from your own runs and patterns from an imported real journal are. SQLite stays the source of truth and the app works with Meko off.

## Scout, Research, Challenger (rule-based, no LLM; offline cache through 2026-10-30)
- Scout ranking of the 10 cached stocks (score 0-100): ICICIBANK 72.5 strong, TCS 57.9, BHARTIARTL 57.4, INFY 48.1, LT 42.7, ITC 32.0, SBIN 23.6, HDFCBANK 22.7, RELIANCE 19.1, JIOFIN 15.4. Rules are printed on the page.
- Research brief: 5y trend (50/200-day averages), 5 best and worst days with volume ratio, volatility and drawdown, swing-pivot support / resistance, and earnings reactions. Earnings dates and EPS come from yfinance, cached in `backend/data_cache/earnings_*.json` for 9 of 10 stocks; JIOFIN has none and the page says so. Reaction is a 2-day window because the timing of results within the day is not in the data. Dates are not verified against exchange filings.
- Challenger on `/trades/check`: weighted arguments against the trade (repeat-mistake pattern, same-symbol record, recent form, stressed volatility, chasing, hold-losers behaviour, worst similar loss, thin evidence). Weight 5 or more vetoes ("don't trade") and the `final_action` becomes wait. Across 60 offline checks on the sample journal it vetoed 5; it never changes `recommendation`, which stays the tested rule set.
