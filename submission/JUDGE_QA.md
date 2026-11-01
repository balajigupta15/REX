# Judge Q&A: 15 tough questions, honest answers

Numbers come from `RESULTS.md` (sample journal: 118 synthetic round trips on real prices; offline data to 2026-10-30).

**1. Isn't this just another backtester?**
The backtester is the plain part. The point is what surrounds it: a locked final year that grades every refinement (PASSED / OVERFIT), a journal that learns *your* repeat mistakes, and a check that recalls them right before you click buy. Both example refinements came out OVERFIT.

**2. Memory OFF vs ON: total P&L got worse. So does it work?**
It reduced risk, not return, on this journal. Max drawdown improved from −55,495 to −48,005 (about 13%), but P&L fell from +10,310 to +4,813 because the 7 skipped trades would have made +8,168. We report it as is. One small synthetic journal demonstrates the mechanism; it does not prove edge.

**3. Is the journal fake?**
Yes, deliberately, and it is labelled SAMPLE DATA everywhere. It is synthetic trader behaviour on real yfinance prices, so the demo is reproducible. Rex also imports real broker tradebook CSVs (that path is tested with hand-built fills), but we have not validated it on a real trader's history.

**4. What does Meko actually do here that SQLite doesn't?**
Honest answer: the proceed / reduce / wait decision comes from rules over the SQLite journal. Meko holds a mirrored copy of backtest summaries, lessons and journal patterns, and `memory_search` recalls them on the Check page, so the memory lives outside the app and can be shared. Today Meko recall informs the user; it does not change the rule output. Making shared memory drive the agents is the roadmap.

**5. Which Meko features do you not use?**
Datapacks, promotion to shared knowledge, knowledgebase and decision traces. We use `conversation_create`, `memory_add` and `memory_search` (the live server exposes 42 tools). The 5-agent desk is where promotion and traces would matter.

**6. Where does YugabyteDB come in?**
Per Yugabyte, Meko's stateful layer runs on YugabyteDB. We call Meko over MCP and do not run YugabyteDB ourselves, so we use it indirectly.

**7. Meko recall took 7 to 18 seconds. Is that usable?**
For a pre-trade check it is slow, so the UI never waits on it: the check renders from SQLite immediately and the Meko recall box fills in afterwards. We would not claim real-time recall.

**8. The recalled Meko box shows near-duplicate entries.**
True. Re-running similar backtests mirrors similar summaries and we do not de-duplicate yet. It is on the fix list (dedupe by run id before recall, or use `memory_update`).

**9. Does an LLM make the trade calls?**
No. Every recommendation, the Challenger and the Scout ranking are deterministic, tested rules, and the app needs no API key. An optional Explain button can use a Groq or Gemini key, and it is hidden without one.

**10. Is the Challenger a real agent?**
Not in the LLM sense. It is a weighted rule set that collects arguments against a trade (repeat-mistake pattern, recent form, stressed volatility, chasing, thin evidence) and vetoes at a score of 5 or more. It overrides the final action to "wait" but never edits the underlying recommendation. On 60 offline checks on the sample journal it vetoed 5.

**11. How do you stop overfitting in Refine?**
Grid search runs on training years only (best of 400 trials by training Sharpe). The last year is untouched until grading. We also say the obvious: with 2 to 8 locked-year trades, any verdict is a hint, and picking the best of many candidates inflates the training numbers.

**12. What are the data and cost assumptions?**
Daily bars from yfinance (auto-adjusted), a bundled cache for 10 NSE stocks, and a 15 bp round-trip cost, which is an assumption. No slippage model, no survivorship-bias correction, and earnings dates come from yfinance and are not verified against exchange filings.

**13. Can this make money? Is it investment advice?**
We make no such claim. It is educational, paper only, and places no orders. Past performance does not predict future results, and the UI says so on every page.

**14. What exists today versus the roadmap?**
Built: Build/Results/Refine, Journal, Pre-Trade Check, Challenger, Scout, Research, Memory proof, Meko mirror and recall, plain-English strategy input (Gemini optional, deterministic fallback), 106 tests, one-command offline start. Not built: a News agent, a Risk & Coach agent, paper trading, crypto and prediction markets, and shared-memory promotion between agents.

**15. How was it built, and by whom?**
By a two-person team: Vane and an AI engineering partner (Grok Bot), and we say so openly. The code is MIT licensed and reproducible: `./start.sh`, 106 passing tests, a type-checked production build and a headless click-through of every page. `[confirm build window and any pre-existing code before submitting]`
