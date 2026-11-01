# MASTER PROMPT: build Rex Lite's web app on a TESTED backend

Paste this whole file into Codex, with the unzipped `rexlite/` folder as the workspace.

## What Rex Lite is
A trader gives a strategy (form and dropdowns). Rex Lite backtests it on real NSE daily history, shows whether it would have made money after costs, then **refines** it: it searches nearby parameters on the training years only, and validates the winner on a **locked final year** that is never used for tuning. If the result collapses there, the UI says OVERFIT, loudly. A memory layer stores every run and the losing-trade setups, warns the trader before the next run, and shows lessons.

**Rex is a strategy and memory layer, not a broker.** The trader executes elsewhere (Zerodha, Upstox, anything). Rex never places, routes or simulates orders. Instead the trader **imports the broker's tradebook CSV**; Rex pairs fills into round trips, tags the market context at each entry, and remembers it. Before the next trade, the trader types the idea (symbol, date, buy/sell) into **Pre-Trade Check**; Rex finds the most similar past trades in that trader's own journal, shows how they went, names the repeated mistake, and recommends proceed / reduce size / wait, by plain rules with reasons.

There is **no LLM and no API key**. Everything is plain code. Educational research on historical data. Paper only. Not investment advice.

## Hard rules
1. **Do not rewrite `backend/`.** It is built and tested (64 pytest tests). Keep it as is. Only add files if you must. If you think it has a bug, write a failing test first, then fix it.
2. **No mock data anywhere.** Every number on screen comes from the API. If the API fails, show the error. Never invent a fallback result.
3. Be honest in the UI: show the 15 bp cost as an editable assumption, show train and locked results separately, and always show the buy-and-hold benchmark next to the strategy.
4. A permanent footer on every page: "Educational research on historical data. Paper only. Not investment advice. Past performance does not predict future results."
5. Meko (optional memory mirror) is off unless `MEKO_TOKEN` is set; verified live 1 Nov 2026 (see RESULTS.md). Show it as the live backend only when `/memory/status` says connected. SQLite is the source of truth.
6. Do not add auth, payments, order placement, broker connections or other asset classes. No buy/sell buttons anywhere.
7. **Sample data is always labelled.** `/trades/sample` and the seeded journal are SYNTHETIC trader behaviour on REAL prices. Every screen showing them must carry a visible `SAMPLE DATA` badge (the API returns `is_sample` and `label`). Never present them as a real trader's results. Show `confidence` and `n` beside every recommendation.

## Layout to produce
```
rexlite/
  backend/            (given, do not rewrite; has data_cache/ CSVs, seeded sqlite, tests, examples/ real API JSON)
  web/                (you build: Next.js App Router, TypeScript, Tailwind, shadcn/ui, Recharts)
  Makefile            (given: make setup | test | seed | api | web | dev)
  RESULTS.md          (real example numbers)
```
The web app reads `NEXT_PUBLIC_API` (default `http://localhost:8000`). Put the base URL and typed fetch helpers in `web/lib/api.ts`. Types in `web/lib/types.ts`, taken from `backend/examples/*.json`, which are real responses. Look at them before coding.

## API contract (CORS is open)
- `GET /health` -> `{ok, paper_only, memory}`
- `GET /strategies/templates` -> `{templates, exits, tickers, cost_bps_default, cost_note, locked_days, tag_groups}`.
  - `templates` is a map keyed by `breakout | ma_cross | rsi | mean_reversion`. Each has `{label, description, params:[{name,label,type:int|float|choice,default,min,max,step,choices?}]}`.
  - `exits` is a list of `{name,label,type,default,min,max,step,optional?}` for `hold_days`, `stop_pct`, `tp_pct`. Optional ones can be switched off (send null).
  - **Build the form from this metadata. Hard-code no parameter names.**
- `POST /backtest` body `{ticker, template, params, exits, cost_bps, remember}` -> `{spec, data, result:{train,locked,full}, notes}`.
  - Each window has `{start,end,metrics,benchmark,trades,equity,benchmark_equity,dates}`.
  - `metrics`: `days, trades, total_return, cagr, win_rate, avg_trade, avg_trade_ci95 (null if under 5 trades), profit_factor, max_drawdown, sharpe, exposure`. All are **fractions** (0.05 = 5%).
  - `benchmark`: `total_return, max_drawdown, sharpe, cagr`.
  - `equity` and `benchmark_equity` start at 1.0 and align with `dates`.
  - A trade has `window, entry_date, exit_date, entry, exit, return_pct (already in %), reason (stop|tp|hold|end), bars, tags:{trend,volume,volatility,momentum}`.
  - `remember` (default true) stores the run in memory.
- `POST /refine`, same body -> `{spec, data, original:{params,exits,result}, refined:{params,exits,result,changed}, verdict:{flag: ok|overfit|inconclusive|no_change, overfit, headline, notes[]}, search:{trials, eligible, objective, tuned_on, locked, top_candidates[]}}`. Each `result` has the same `{train, locked, full}` shape as `/backtest`.
- `POST /memory/check`, same body (without `remember`) -> `{warnings:[{level:info|warn,text}], current_setup:{tags,as_of}, similar:{n,win_rate,avg_return,base_win_rate,examples[]}, recent_lessons[], memory_runs}`. This is the "before you run" panel.
- `GET /memory/runs?limit=`, `GET /memory/lessons?limit=` (-> `{lessons[], counts, refine_outcomes}`), `GET /memory/status`, `POST /memory/recall` (Meko search, may report disabled), `POST /memory/seed?reset=true`.
- **Journal** (all sample-aware; `source=auto` uses real imports if any exist, else the sample):
  - `POST /trades/import?cost_bps=0` takes multipart field `file`, a raw `text/csv` body, or JSON `{csv_text, filename, cost_bps}`. Columns are mapped tolerantly (Zerodha: symbol, trade_date, trade_type, quantity, price). It returns `{import_id, is_sample, label, column_mapping, rows_read, rows_rejected:[{row,reason}], round_trips, new_trades, duplicates_skipped, open_positions[], context_missing[], trades[], summary, stored_in, meko_mirror, cost_note}`. Re-importing is idempotent. PnL is gross unless `cost_bps` is set.
  - `GET /trades/sample` (CSV text), `POST /trades/import-sample`, `GET /trades?limit=&source=`, `GET /trades/summary?source=` -> `{source_used, overall, long, short, bad_patterns[], good_patterns[], hold_behaviour, notes[]}`, `DELETE /trades?sample_only=`.
  - `POST /trades/check` body `{symbol, date?, side: buy|sell, top_k=10, source=auto, price?, qty?}` -> `{fingerprint:{tags(trend_dma, extension, volume, volatility, gap), metrics, descriptions[], complete, gap_known, as_of}, journal:{n, source_used}, similar:{n, stats, neighbors:[{cosine, ticker, entry_date, exit_date, pnl, return_pct, hold_days, matching[], tags, is_sample}]}, mistake_pattern:{matched, other_bad_patterns[], hold_behaviour}, recommendation:{action: proceed|reduce_size|wait, size_factor, confidence, reasons[], wait_for, rules[]}, notes[]}`.
- Errors are HTTP 422 with `{detail}`. Show them as-is in an alert.

## Pages (dark, clean, trader-grade; one nav: Build, Results, Refine, Journal, Pre-Trade Check, Scout, Research, Memory; built and live in `web/`)

**1. Strategy Builder `/`**
- Ticker combobox (the `tickers` list plus free text; the backend validates `.NS`/`.BO`).
- Template dropdown with its description. Params rendered from metadata (number input plus slider). Exits with toggles for stop and take-profit. Cost bps input with the `cost_note`.
- A "Before you run" card calls `/memory/check` (debounced) and shows warnings and similar-setup stats.
- "Run backtest" -> `/backtest` -> navigate to Results. Keep the last result in React state or a context, and in sessionStorage.

**2. Results `/results`**
- Segmented control: Train | Locked final year | Full. Locked tab badge: "never used for tuning".
- Metric cards: total return, buy-and-hold return, win rate, max drawdown, Sharpe, trades, profit factor, exposure, avg trade with its CI (show "n too small" when null).
- Recharts line chart: strategy equity vs buy-and-hold, with a drawdown area underneath. Use a shared tooltip.
- Trade table (sortable): dates, entry, exit, return %, exit reason, tag chips. Losing rows are tinted.
- A "Refine this strategy" button -> `/refine`.
- Warn plainly when trades < 10: "too few trades to trust".

**3. Refine `/refine`**
- Loading state (the search takes about 1 to 3 s).
- Verdict banner: red for OVERFIT, amber for inconclusive, green for ok, neutral for no_change. Show `headline` and `notes`.
- Side by side: **Original** vs **Refined**, with params diff highlighted and train/locked metrics for each. Overlay the locked-year equity of both vs buy and hold.
- A table of `top_candidates` with train Sharpe. A sentence: "Chosen from N trials: expect it to look better than it is."
- Never imply the refined version is "the answer" when the flag is overfit.

**4. Memory `/memory`**
- Status chip (SQLite / Meko enabled or not, counts).
- Lessons list grouped by kind (setup_weak, setup_strong, stop_heavy, regime_change, lagged_bh, refine_overfit...), filterable by ticker.
- Runs table (ticker, template, train vs locked return, flag). A row click re-opens that run.
- A "Seed demo data" button (`POST /memory/seed?reset=true`) with a confirm dialog.

**5. Journal Import `/journal`**
- Drop zone for a tradebook CSV (and a "Use SAMPLE tradebook" button, badge `SAMPLE DATA`). Optional cost bps input (default 0, labelled "gross PnL").
- After import: show the detected **column mapping** (editable view only), rows read, **rejected rows with reasons**, duplicates skipped, and any `context_missing`.
- Round-trip table (symbol, direction, entry/exit date, qty, avg entry/exit, PnL, return, hold days, fills, tag chips). Open positions in their own card, marked "excluded from statistics".
- Summary: win rate, avg win vs avg loss, total PnL, `hold_behaviour` text, **bad patterns** (red) and **good patterns** (green) with n.

**6. Pre-Trade Check `/check`**
- Form: symbol, date (default today), side (Buy = long, Sell = short). No quantity needed; no order button.
- Fingerprint chips from `fingerprint.descriptions`. If `gap_known` is false, say "gap unknown for a future date".
- Recommendation card (green proceed / amber reduce size 50% / red wait) with `reasons`, `wait_for` and `confidence`. Show the `rules` in a "How this is decided" disclosure.
- Neighbours table (similarity, matching groups, symbol, dates, PnL, hold days) plus the stats row (n, win rate vs overall, avg return). The matched mistake pattern is highlighted. If `journal.n` is under 10, show "not enough history: low confidence".
- `SAMPLE DATA` badge whenever `journal.source_used` is `sample`.

## Milestones and acceptance tests (do them in order; run each test before moving on)
- **M0 Setup.** `make test` shows 64 passed. `make api` serves, and `curl localhost:8000/strategies/templates` returns 4 templates. Scaffold `web/` with shadcn (button, card, tabs, select, input, slider, table, badge, alert, dialog, switch, tooltip).
- **M1 Builder.** The form is generated purely from metadata. Test: change the template and the param fields change. Out-of-range input is blocked client-side. A backend 422 shows an alert.
- **M2 Results.** Test: run RELIANCE.NS breakout n=20 defaults (hold 10, stop 5, tp 10, 15 bp). It must match `RESULTS.md`: train 25 trades, return -12.3%, B&H +26.0%; locked 2 trades, -2.3%, B&H -21.4% (if live data has moved on, the numbers may differ slightly; say so, don't fake them). Equity charts start at 1.0. The trade count in the table equals the metric.
- **M3 Refine.** Test: RELIANCE.NS breakout refine shows OVERFIT with the Original/Refined side by side. Test: the locked-year chart is labelled as never tuned on.
- **M4 Memory.** Test: after 2 runs with `remember=true`, `/memory/runs` grows by 2 and the Memory page shows them. The "before you run" card shows the seeded warning for RELIANCE breakout.
- **M4b Journal.** Test: import the sample CSV. Must show 118 round trips, 0 rejected, 3 open positions, win rate 77.1%, total PnL +10,310, and hold-time losers 33.1 vs winners 12.1 days. Importing it twice adds no trades. A bad CSV shows the 422 text.
- **M4c Pre-Trade Check.** Test with the sample journal: INFY 2026-10-15 buy -> 7 similar trades, win rate 57%, RECOMMEND reduce size (medium). RELIANCE 2026-10-30 buy -> 10 similar, win rate 80%, proceed (high). Side `hold` is rejected. Sample badge visible. Both come from the API.
- **M5 Polish.** Loading skeletons, empty states, error alerts, a responsive layout, the footer disclaimer on all pages, no console errors.
- **M6 Run and demo.** `make setup && make dev` runs API plus web in one command. The seeded demo works offline (`REX_OFFLINE=1` is the default in `make api`). Playwright smoke tests if time allows; otherwise list exactly what you verified by hand.

## Demo script the app must support (90 seconds)
1. Builder: JIOFIN.NS, RSI oversold. The memory card shows its warnings.
2. Run. Show the locked-year tab: the strategy loses, and buy and hold loses too. Be honest about both.
3. Refine RELIANCE.NS breakout. The tool flags OVERFIT instead of selling a pretty curve.
4. Memory page: losing-setup lessons the tool learned from earlier runs.
5. Journal: import the tradebook, see repeated mistakes. Pre-Trade Check INFY: "your similar trades won 57% vs 77% overall: reduce size". Say aloud that it is sample data and Rex places no orders.

## Definition of done
`make test` is green; `make dev` brings up everything; all six pages work against the real API with no mock data; you have written a short `web/README.md`, and your final message lists what you verified (and how) and what you did NOT verify.
