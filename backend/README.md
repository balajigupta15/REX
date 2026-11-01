# Rex Lite backend (tested, do not rewrite)

Educational research tool. Historical backtests only, paper only, not investment advice. No LLM, no API key.

    pip install -r requirements.txt
    python3 -m pytest -q tests                      # full suite
    REX_OFFLINE=1 python3 -m uvicorn app.main:app --port 8000   # bundled data_cache, no network
    python3 -m uvicorn app.main:app --port 8000      # live yfinance (falls back to cache if it fails)
    python3 scripts/seed_demo.py --reset             # rebuild demo memory (54 runs, ~1k trades, ~90 lessons)

Env: GEMINI_API_KEY and GEMINI_MODEL (optional; plain-English parser, see `app/nlparse.py`; the deterministic parser is used without them), REX_OFFLINE=1, REX_DB=<sqlite path>, MEKO_TOKEN (optional, enables the Meko mirror; verified live 1 Nov 2026), MEKO_DATAPACK_ID.
`POST /strategies/parse {text}` turns plain English into a `/backtest` strategy (`strategy` is `null` if no supported entry rule is found; `unsupported[]` lists what cannot be tested).
SQLite is always the source of truth. Without MEKO_TOKEN, memory is SQLite only.
`examples/` holds real API responses recorded 1 Nov 2026. See RESULTS.md at the repo root.

## Trade journal (strategy + memory layer; Rex is NOT a broker and places no orders)
    curl -X POST localhost:8000/trades/import -F file=@my_tradebook.csv      # broker tradebook CSV (tolerant column mapping)
    curl -X POST localhost:8000/trades/import-sample                          # bundled SAMPLE (synthetic behaviour, real prices)
    curl -X POST localhost:8000/trades/check -H 'content-type: application/json' -d '{"symbol":"INFY","date":"2026-10-15","side":"buy"}'
Fills are paired into round trips (average-cost, scale-ins/partial exits stay in one trip, open positions excluded), each entry is
tagged from the PRIOR close (trend vs 20/50 DMA, extension, volume spike, volatility regime) plus the entry-bar gap, and stored in SQLite
(Meko mirror optional). `/trades/check` ranks past trades by fingerprint similarity (>=3 of 5 groups) and applies fixed rules. No LLM.
The sample file is `sample/SAMPLE_tradebook_synthetic_journal_real_prices.csv` (rebuild: `python3 scripts/make_sample_tradebook.py`).
