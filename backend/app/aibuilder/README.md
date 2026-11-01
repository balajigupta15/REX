# AI Strategy Builder and research agent

`/ai` in the web app, `/ai/*` in the API. You describe a goal, the agent writes its own `signals(df)` strategy code, tests it on the real data loaders with a locked final period, argues both sides, applies a deterministic risk veto, and reports evidence (not advice).

## Patterns this is modelled on (re-implemented natively, no code copied, nothing installed)
Links were opened and checked on 1 Nov 2026 (all returned HTTP 200 with the expected titles).
- **Microsoft RD-Agent**: https://github.com/microsoft/RD-Agent . Paper on the finance variant: R&D-Agent-Quant, https://arxiv.org/abs/2505.15155 . What we took: the loop hypothesis -> code -> evaluate -> feedback -> next hypothesis, repeated for N rounds, with an experiment log of every hypothesis and whether it was kept or dropped.
- **TradingAgents**: https://github.com/TauricResearch/TradingAgents . Paper: https://arxiv.org/abs/2412.20138 . What we took: specialist roles (data/technical analyst, news analyst, bull and bear researchers who debate, a risk manager, a portfolio manager).

Differences, stated plainly: our Risk Manager veto is a deterministic function of measured numbers, never an LLM call. Keep/drop decisions use the train period only. The LLM is called a handful of times per run (1 plan, 1 per round to write code, 1 debate).

## Layout
- `agent.py` the loop and roles; `service.py` strategy authoring, evaluation, memory, risk review, live paper signal; `codegen.py` Gemini prompts; `gemini.py` REST client with SQLite cache, model fallback and quota cooldown.
- `synth.py` **offline mode**: when Gemini is unavailable (or "Offline mode" is ticked) a local synthesizer composes indicator building blocks into new `signals()` programs and searches parameter and stop combinations on the train window. It is always labeled "offline mode"; it is not a template picker.
- `ast_guard.py` AST allowlist; `sandbox.py` + `_worker.py` isolated subprocess; `engine.py` independent position-series backtester (reuses the existing metrics, costs and windows); `store.py` SQLite persistence; `api.py` routes; `models.py` pydantic types.

## Safety of generated code
Never `exec`'d in the API process. AST allowlist (no imports except pandas/numpy, no dunder access, no `open`/`eval`/`exec`, no `while`), checked again inside the worker; the worker runs `python -I` with an empty environment (no `GEMINI_API_KEY`), a temp cwd, no network (`unshare -rn` plus patched sockets), restricted builtins, memory/CPU/file-size limits, a 5 s timer and a parent hard kill. A lookahead check re-runs the code on truncated data at sampled cut points and rejects any strategy whose past positions change (this catches `shift(-1)`). On failure the error is sent back to Gemini once.

## Honest evidence
Train period for choosing, last year locked for checking. Deflated Sharpe uses the true number of trials (every experiment and every offline combination). The 4-block stability check is **not** a clean out-of-sample test. Decision labels: DO NOT TRADE, WEAK EVIDENCE, EVIDENCE HELD UP, NO RESULT, always with a disclaimer.

## Memory
- Recall at the start: Meko `memory_search` when `MEKO_TOKEN` is set (timeline shows "source: Meko"), plus the local strategy-run memory. Without a token, or if Meko fails, the source is SQLite.
- Lesson at the end: written to the local `lessons` table and, when `MEKO_TOKEN` is set, to Meko with `memory_add` (best effort, 25 s cap). The final card says where it was written.

## Persistence and runs
Runs and steps are SQLite rows with run ids (`r_...`); the page polls `GET /ai/runs/{id}` and keeps the id in the URL, so a refresh resumes the view. Steps are replayed by key on resume, so finished steps cost no new LLM calls. A stale `running` run is reported as `interrupted` and the page resumes it. Each step has its own timeout, structured errors (`code`, `message`, `detail`, `retryable`) and logging; runs are claimed atomically so two workers cannot run the same id. `store.py` is the only module that touches the database and opens every connection through `app/db.py` (YugabyteDB when `REX_DB_URL` is reachable, otherwise SQLite). The Yugabyte path uses the translation layer in `db.py` and has not been exercised against a live Yugabyte here.

## Not done
Pine Script export. "Track this live" is this builder's own paper watch (re-runs the code on the latest closed candles; no orders); it is not the template-based `crypto_tracker`.
