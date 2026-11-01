# Rex: 2 minute demo video script

Target length 2:00 (about 270 spoken words). Record at 1280×720 or larger, dark browser window, `./start.sh` already running, offline data. Reset to a clean state before recording. The journal is SAMPLE data, so keep the SAMPLE DATA badge in frame. Never show the MEKO_TOKEN or the terminal env.

| Time | On screen | Voice-over |
|---|---|---|
| 0:00 to 0:10 | Home (`/`): hero "Rex: the trading coach with a memory", Meko live chip | "Most trading tools forget you the moment you close the tab, and they flatter your backtests. Rex is a trading coach with a memory." |
| 0:10 to 0:35 | Build: RELIANCE, breakout → Run backtest → Results equity curve vs buy and hold. Click Refine this strategy → OVERFIT badge | "I backtest an idea with costs. Then Rex refines it on the training years and grades it on a locked final year it never touched. Sharpe 0.46 on training, 0.14 on the locked year: overfit. Rex tells me instead of hiding it." |
| 0:35 | Cut | |
| 0:35 to 0:55 | Journal: click Load sample journal → SAMPLE DATA badge → hold-time chart and repeat-mistake list | "Now my trades. I load a clearly labelled sample journal: 118 round trips. Rex finds that I hold losers 33 days against 12 for winners, and the setup I keep losing in: buying stretched in stressed volatility." |
| 0:55 to 1:25 | Check: INFY, buy → REDUCE SIZE → Challenger veto card → Memory recalled table → Meko recall box | "Before I click buy, I ask Rex. It recalls my ten most similar trades: 60 percent win rate against my usual 77. Reduce size. The Challenger argues the other side and vetoes: wait. And the lessons are recalled from Meko over MCP, so this memory lives outside the app." |
| 1:25 to 1:40 | Scout then Research on one stock (e.g. ICICIBANK) | "Scout ranks the watchlist with reasons, and Research explains a stock in plain English. Rules, no black box." |
| 1:40 to 1:55 | Memory: OFF vs ON chart, numbers visible | "Does memory help? A walk-forward test: drawdown down 13 percent, from minus 55 thousand to minus 48. But total P and L is lower, because the trades it skipped would have won. We show that, because honest beats flattering." |
| 1:55 to 2:00 | Home with the roadmap or title card | "Rex: it remembers, so you do not repeat it. Built for Agents That Remember." |

## Recording notes
- Say "sample data" once out loud during the journal step; judges notice.
- The Meko recall box fills in after 7 to 18 seconds. Start the Check run early, narrate the Challenger while it loads, then show the box. If it has not loaded, say "recall loads in the background" and move on. Never fake it.
- If Meko is not connected (no token), the chip reads SQLite and the app still works. Do not claim Meko in that case.
- Keep the disclaimer footer visible at least once: educational, paper only, not investment advice.
- Add on-screen end card with repo URL and team names: `[fill in]`.
