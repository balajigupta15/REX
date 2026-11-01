# Rex: 3 minute demo script

Start: `./start.sh`, open http://localhost:3000. Everything below works offline with the bundled prices. The journal is SAMPLE data and the UI says so.

**0:00 Pitch (Home).** "Most trading tools forget you the moment you close the tab. Rex is a trading coach with a memory." Point at the live counters (runs, lessons, journal trades) and the backend chip (Meko live when `MEKO_TOKEN` is set, otherwise local SQLite).

**0:30 Test an idea honestly (Build → Results → Refine).** Build: click the "MA crossover" example chip under "Describe your strategy" and press Parse (say: "you type your own strategy; Rex shows what it understood, what it assumed and what it cannot test"), or just pick RELIANCE, breakout. Note the "Rex remembers" card warning before you run. Run backtest: equity vs buy and hold, costs included. Click "Refine this strategy": grid search on training years, graded on the locked final year. Say: "It looks great on training and the locked year says OVERFIT. Rex shows that instead of hiding it."

**1:00 Track it live (Tracker).** On Build the "Last time" panel shows what happened last time you ran something similar and what price did next. After the backtest, on Results press "I took this trade in my broker" (say: "I trade in Groww or Zerodha. Rex never places orders, it tracks."), enter entry price and qty. The Tracker page shows live P&L, distance to stop and target, and the red **Strategy says exit now** alert when the exit rule fires. Press "Mark closed and save to memory": verdict plus lesson are stored. The market is shown as closed on weekends; Rex then uses the last close and says so. "Track strategy (no trade)" follows the strategy alone.

**1:15 Find your repeat mistakes (Journal).** "Load sample journal" (SAMPLE DATA badge). Show win rate, the hold-time chart (losers held 33 days vs 12 for winners) and the top repeated mistakes: buying stretched in stressed volatility.

**1:50 Check before you click (Pre-Trade Check).** INFY, buy. Rex recalls the most similar past trades, then says REDUCE SIZE with reasons and sample size. Point at the Challenger card: "A second, sceptical rule set argues against the trade and can veto it." Try SBIN for a different answer. Point at "Memory recalled" (and the Meko recall box when connected).

**2:25 Where to look (Scout, Research).** Scout ranks the watchlist with reasons. Open one stock in Research: plain-English summary, trend chart, support and resistance, biggest days, earnings reactions (JIOFIN shows an honest "not available").

**2:50 Does memory help? (Memory).** The walk-forward OFF vs ON chart. Say the honest line: "Drawdown improved from -55k to -48k, total P&L is lower on this one synthetic journal. It is a demo of the mechanism, not proof of edge." Close: "Educational, paper only, not a broker."

Fallbacks: no network → `REX_OFFLINE=1` is the default. No Meko → chip says local SQLite, nothing breaks. No LLM key → the optional Explain buttons are simply hidden.

## Bonus: Crypto section (about 60 s)
Open **Crypto**. The strip shows live BTC / ETH / SOL with "updated Ns ago" (24/7, real-time). Build "buy BTC when RSI drops below 30", run it, Refine (locked final period), then Track it: log the trade you took on your exchange and watch live P&L, stop/target distance and the exit alert. Rex never places orders. Screenshots: `docs/screenshots/12-crypto-*.png`.
