# Rex: the trading coach with a memory

*HackCBS 9.0 · Track: Agents That Remember (Meko by YugabyteDB)*
*Fill in before submitting: team name, college, repo URL, demo video URL.*

**Tagline:** Test an idea honestly, learn from your own past trades, and get a proceed / reduce / wait check before you repeat a mistake.

## Inspiration
Two things keep retail traders poor. They repeat the same mistakes, and the tools they use forget everything the moment the tab closes. And when a tool does optimise a strategy, it usually tunes it on the same data it then grades itself on, so the result looks better than it is. We wanted a coach that remembers what happened to *you* and refuses to flatter the numbers.

## What it does
Rex is a consumer web app for Indian equities (NSE) with these parts:
- **Build, Results, Refine.** Describe your strategy in plain English (parsed into editable rules; whatever Rex cannot test is listed, never faked) or pick one of 4 strategy templates, backtest it with costs, then refine it with a grid search on the training years. The **last year of data stays locked** and grades the refined version: PASSED or OVERFIT. Both example refinements in our results (RELIANCE breakout, TCS mean-reversion) came out OVERFIT, and Rex says so.
- **Live Strategy Tracker.** Log the trade you took in your own broker app (or just follow a strategy) and Rex tracks the live price against your entry: P&L, distance to stop and target, and a "Strategy says exit now" alert from the same exit engine as the backtest. Rex never places orders. Closing the session saves the outcome and how price moved to memory.
- **Strategy-run memory.** Every backtest, refine and tracker session is remembered with the real 5 and 20 day price move that followed. Before you run a similar strategy, a "Last time" panel and a one-line lesson show what happened.
- **Journal.** Import a broker tradebook CSV (or load a clearly labelled SAMPLE journal). Rex pairs fills into round trips, fingerprints each entry (trend vs the 20/50-day averages, stretch, volume, volatility regime, gap) and surfaces the setups you keep losing in.
- **Pre-Trade Check.** Symbol, date, side. Rex recalls your most similar past trades and returns **proceed / reduce size / wait** with reasons and sample size. A **Challenger** card argues the other side and can veto the trade.
- **Scout and Research.** A transparent rule-based ranking of the cached stocks, and a plain-English brief per stock (5-year trend, biggest moves, volatility, support and resistance, earnings reactions where data exists).
- **Memory.** Backend status (Meko live vs SQLite), the lessons Rex has learned, and a walk-forward **memory OFF vs ON** test.

## How we built it
- **Frontend:** Next.js 16 (App Router), React 19, Tailwind v4, shadcn-style components, Recharts.
- **Backend:** FastAPI + SQLite. Prices are real yfinance daily bars, cached so the demo runs fully offline.
- **Memory:** SQLite is the source of truth. With a `MEKO_TOKEN`, Rex also mirrors backtest summaries, lessons and journal patterns to **Meko over MCP** (`conversation_create`, `memory_add`) and recalls them with `memory_search` on the Check page. We tested this against the live Meko server (42 tools) on 1 Nov 2026. Sample and replayed data are never mirrored into the real memory store.
- **Decisions are rule-based and auditable.** No LLM is needed for any recommendation. An optional "Explain" button uses a Groq or Gemini key if you provide one.
- **Quality:** 151 automated tests (hand-built fixtures for exit logic, fill pairing and the proceed/reduce/wait rules), TypeScript check, production build, and a headless-browser click-through of every page with zero console errors.

## Challenges
- **Honest evaluation.** The memory-ON walk-forward test cut max drawdown from −55,495 to −48,005 (about −13%) but total P&L fell from +10,310 to +4,813, because the 7 trades Rex told you to skip would have made +8,168. We kept that result on the home page instead of tuning the rules until the chart looked better.
- **Real MCP, real quirks.** Meko requires a `conversation_id` on every memory call, takes metadata as a JSON *string*, and `memory_search` is cross-agent, so we filter to our own agent id. Recall took 7 to 18 seconds in our tests, so the UI fetches it in the background.
- **Sample size.** Locked-year backtests have 2 to 8 trades and the journal has 118. We label every small-sample result as a hint, not proof.
- **No look-ahead.** The proof test and the check only use trades that closed before the decision date.

## Accomplishments we're proud of
- A refine step that catches its own overfitting, and a memory proof that reports a mixed result instead of a flattering one.
- A real, verified Meko MCP integration that degrades safely: if Meko is down or has no token the app still works from SQLite.
- One-command start (`./start.sh`), offline demo, 151 passing tests, and a UI where every number links back to something measured.

## What we learned
- Memory is only useful if it changes a decision at the right moment, so we put recall on the page where you are about to click buy.
- An honest negative result is more persuasive than a clean one, and it forced us to explain *why* P&L fell.
- Agent memory APIs have sharp edges (required conversation ids, cross-agent search, latency). Building against the live server early saved us from designing around a mock.

## What's next
A 5-agent desk: **Scout, Researcher and Challenger** exist today as deterministic rules; next are a **News agent** and a **Risk & Coach agent**, with lessons promoted into Meko's shared memory so every agent learns from the others. Then **crypto and prediction markets**, and broker-import so tracked trades log themselves.

## Built with
Next.js · React · TypeScript · Tailwind CSS · Recharts · Radix UI · FastAPI · Python · SQLite · pandas · yfinance · **Meko (MCP)** · Groq / Gemini (optional, off by default)

## Try it
`./start.sh` then open http://localhost:3000 (API on :8000). Runs offline with the bundled prices. Educational research on historical data. Paper only. Not investment advice; Rex places no orders.
