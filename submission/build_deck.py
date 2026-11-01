"""Builds pitch_deck.html (self-contained: crops of docs/screenshots are embedded as base64 JPEG).
Run: python3 build_deck.py   (needs Pillow). All numbers below are copied from RESULTS.md / README.md."""
import base64, io, os
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__)); SHOTS = os.path.join(HERE, "..", "docs", "screenshots")
CROPS = {  # name: (file, bottom_px)  -> crop (0,0,1360,bottom)
    "home": ("01-home.png", 880), "build": ("02-build.png", 1002), "results": ("03-results.png", 1000), "refine": ("04-refine.png", 1000),
    "journal": ("05-journal.png", 1000), "check": ("06-check.png", 1150), "memory": ("07-memory.png", 1000), "scout": ("08-scout.png", 1000),
    "research": ("09-research.png", 1000),
}
IMG = {}
for k, (f, b) in CROPS.items():
    im = Image.open(os.path.join(SHOTS, f)).convert("RGB").crop((0, 0, 1360, b)); im = im.resize((1000, round(b * 1000 / 1360)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=78, optimize=True); IMG[k] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    im.save(os.path.join(HERE, "assets", f"{k}.jpg"), quality=78)

def shot(k, cls=""): return f'<div class="win {cls}"><div class="bar"><i></i><i></i><i></i><span>localhost:3000</span></div><img src="{IMG[k]}" alt="Rex {k} page screenshot"></div>'

CSS = """
:root{--ink:#07090d;--panel:#0e121a;--line:#1d2431;--mute:#8b95a7;--text:#e8ecf3;--rex:#ffb020;--gain:#34d399;--loss:#fb7185;--sky:#38bdf8}
*{box-sizing:border-box;margin:0;padding:0}html,body{height:100%;background:#000;color:var(--text);font-family:Inter,"Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;overflow:hidden}
#stage{position:absolute;left:50%;top:50%;width:1280px;height:720px;transform-origin:center;}
.slide{position:absolute;inset:0;padding:56px 72px;background:radial-gradient(900px 500px at 92% -10%,rgba(255,176,32,.13),transparent 60%),var(--ink);opacity:0;pointer-events:none;transition:opacity .25s;overflow:hidden}
.slide.on{opacity:1;pointer-events:auto}
.tag{display:inline-block;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--rex);font-weight:700;margin-bottom:14px}
h1{font-size:64px;line-height:1.05;letter-spacing:-.02em;font-weight:700}h2{font-size:40px;line-height:1.12;letter-spacing:-.015em;font-weight:700;margin-bottom:22px}
h1 em,h2 em{font-style:normal;color:var(--rex)}p,li{font-size:19px;line-height:1.5;color:#c3cad6}li{margin:0 0 10px 20px}b,strong{color:var(--text)}
.sub{font-size:23px;color:var(--mute);margin-top:18px;max-width:640px;line-height:1.45}
.row{display:flex;gap:28px}.col{flex:1}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
.card h3{font-size:19px;margin-bottom:8px}.card p,.card li{font-size:16px}.k{font-size:13px;color:var(--mute);text-transform:uppercase;letter-spacing:.08em}
.num{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums}.g{color:var(--gain)}.l{color:var(--loss)}.r{color:var(--rex)}.m{color:var(--mute)}
.win{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#05070a;box-shadow:0 24px 60px rgba(0,0,0,.55)}
.win .bar{height:26px;background:#111722;display:flex;align-items:center;gap:6px;padding:0 10px;border-bottom:1px solid var(--line)}
.win .bar i{width:9px;height:9px;border-radius:50%;background:#2a3344}.win .bar span{margin-left:10px;font-size:11px;color:var(--mute)}
.win img{display:block;width:100%;height:auto}
.logo{position:absolute;top:30px;left:72px;display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px}.logo b{width:28px;height:28px;border-radius:7px;background:var(--rex);color:#111;display:grid;place-items:center;font-size:16px}
.foot{position:absolute;bottom:22px;left:72px;right:72px;display:flex;justify-content:space-between;font-size:12px;color:#5d6778}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;border:1px solid var(--line);font-size:12px;color:var(--mute);margin-right:6px}
.pill.s{color:var(--rex);border-color:rgba(255,176,32,.4)}.pill.ok{color:var(--gain);border-color:rgba(52,211,153,.4)}.pill.no{color:var(--loss);border-color:rgba(251,113,133,.4)}
.flow{display:flex;gap:14px;align-items:flex-start}.step{flex:1}.step .n{font:700 13px/1 inherit;color:var(--rex);margin-bottom:6px}.step h3{font-size:17px;margin:8px 0 4px}.step p{font-size:14px;line-height:1.4}
.box{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:14px 16px;text-align:center}.box small{display:block;color:var(--mute);font-size:12px;margin-top:4px}
.arrow{color:var(--rex);font-size:22px;align-self:center}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em;color:var(--rex)}
.bars{display:grid;grid-template-columns:140px 1fr;gap:10px 14px;align-items:center;font-size:14px}
.bar2{height:22px;border-radius:5px;display:flex;align-items:center;padding-left:8px;font-size:13px;font-weight:700;color:#06080b;font-variant-numeric:tabular-nums}
#hud{position:fixed;top:8px;right:14px;font-size:12px;color:#5d6778;z-index:9}#hud b{color:var(--rex)}
@media print{html,body{overflow:visible;height:auto;background:#000}#stage{position:static;transform:none!important;width:1280px;height:auto}.slide{position:relative;opacity:1;height:720px;page-break-after:always}#hud{display:none}}
"""

S = []
S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div>
<div class="row" style="margin-top:70px;align-items:center"><div style="flex:1.05"><span class="tag">HackCBS 9.0 · Agents That Remember</span>
<h1>Rex: the trading coach <em>with a memory</em></h1>
<p class="sub">It tests your idea honestly, learns from your own past trades and speaks up right before you repeat a mistake.</p>
<div style="margin-top:26px"><span class="pill s">Paper only</span><span class="pill">Educational</span><span class="pill ok">Meko memory over MCP</span><span class="pill">Works offline</span></div></div>
<div style="flex:.95">{shot('home')}</div></div>
<div class="foot"><span>Not investment advice. Rex places no orders.</span><span>1 / 10</span></div></section>""")

S.append("""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:50px"><span class="tag">The problem</span>
<h2>Traders repeat the same mistakes. <em>Backtests flatter them.</em></h2>
<div class="row"><div class="col"><div class="card" style="margin-bottom:18px"><h3>1. Nobody remembers your mistakes</h3>
<p>Every tool starts from zero when you close the tab. The pattern that hurt you last month is invisible at the moment you click buy again.</p></div>
<div class="card"><h3>2. Optimised strategies overfit</h3><p>Try 400 parameter sets, keep the best, and it looks great on the data you tuned on. Most apps stop there.</p></div></div>
<div class="col"><div class="card"><div class="k">What our own tool measured (RESULTS.md)</div>
<div style="margin:14px 0 6px"><span class="pill s">Refine, locked final year</span></div>
<p style="font-size:17px"><b>RELIANCE breakout:</b> Sharpe <span class="g">0.46</span> on training → <span class="l">0.14</span> on the locked year. <span class="pill no">OVERFIT</span></p>
<p style="font-size:17px;margin-top:10px"><b>TCS mean-reversion:</b> Sharpe <span class="g">0.80</span> on training → <span class="l">−1.25</span> on the locked year. <span class="pill no">OVERFIT</span></p>
<div style="margin:18px 0 6px"><span class="pill s">Sample journal (synthetic)</span></div>
<p style="font-size:17px">Losing trades held <b class="l">33.1 days</b> vs <b class="g">12.1 days</b> for winners.</p></div></div></div></div>
<div class="foot"><span>Real yfinance prices, 15 bp round-trip cost is an assumption. Journal is SAMPLE data.</span><span>2 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:50px"><span class="tag">The solution</span>
<h2>One coach, <em>three jobs</em></h2>
<div class="row" style="align-items:flex-start"><div style="flex:1.1">
<div class="card" style="margin-bottom:14px"><h3>🧪 Test an idea honestly</h3><p>Type your strategy in plain English or pick one of 4 templates, costs included. Refine on training years; the <b>last year stays locked</b> and grades the result: PASSED or OVERFIT.</p></div>
<div class="card" style="margin-bottom:14px"><h3>📒 Learn from your own trades</h3><p>Import a broker tradebook. Rex pairs fills into round trips, fingerprints each entry (trend, stretch, volume, volatility, gap) and finds the setups you keep losing in.</p></div>
<div class="card"><h3>🛡️ Check before you click</h3><p>Symbol, date, side → your most similar past trades → <b>proceed / reduce size / wait</b>, with reasons and sample size. A Challenger argues the other side and can veto.</p></div></div>
<div style="flex:.9">{shot('home')}</div></div></div>
<div class="foot"><span>Plus Scout (rule-based ranking) and Research (plain-English stock brief).</span><span>3 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:50px"><span class="tag">Live demo flow (about 3 minutes)</span>
<h2>From idea to a <em>warning with a memory</em></h2>
<div class="flow">
<div class="step"><div class="n">1 · BUILD</div>{shot('build')}<h3>Pick &amp; backtest</h3><p>Rex warns from memory before you run.</p></div>
<div class="step"><div class="n">2 · REFINE</div>{shot('refine')}<h3>Locked-year grade</h3><p>Overfit shows as a badge.</p></div>
<div class="step"><div class="n">3 · JOURNAL</div>{shot('journal')}<h3>Find repeat mistakes</h3><p>Load the SAMPLE journal.</p></div>
<div class="step"><div class="n">4 · CHECK</div>{shot('check')}<h3>Reduce / wait / veto</h3><p>Past trades recalled.</p></div></div></div>
<div class="foot"><span>Then Scout, Research and the Memory OFF vs ON proof. Script: docs/DEMO.md</span><span>4 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:44px"><div class="row" style="gap:44px">
<div style="flex:1;min-width:0"><span class="tag">The moment that matters</span><h2>The check, <em>with receipts</em></h2>
<p>Proposed: <b>INFY, buy</b> (sample journal, offline data to 2026-10-30).</p>
<ul style="margin-top:14px"><li><b>Reduce size ×0.5.</b> The 10 most similar past trades: win rate 60% vs 77% overall, avg return −2.48%.</li>
<li>Repeat mistake found: <i>above both averages + stressed volatility</i>, 15 trades, 60% win rate.</li>
<li><b>Challenger veto</b> (score 6, threshold 5): "Don't trade now". Final call: <b class="r">WAIT</b>.</li>
<li>Recall also pulled from <b>Meko</b> via <code>memory_search</code>.</li></ul>
<p class="m" style="font-size:15px;margin-top:10px">Small samples. The UI says: a hint, not proof.</p></div>
<div style="flex:1;min-width:0">{shot('check')}</div></div></div>
<div class="foot"><span>Rule-based and auditable. No LLM is needed for any decision.</span><span>5 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:44px"><span class="tag">Memory architecture</span>
<h2>SQLite is the truth. <em>Meko is the shared memory.</em></h2>
<div class="flow" style="margin:6px 0 26px"><div class="box" style="flex:1">Rex UI<small>Next.js · React · Recharts</small></div><div class="arrow">→</div>
<div class="box" style="flex:1">FastAPI<small>backtest · journal · check · challenger</small></div><div class="arrow">⇄</div>
<div class="box" style="flex:1;border-color:rgba(255,176,32,.5)">SQLite<small>source of truth, always works</small></div><div class="arrow">→</div>
<div class="box" style="flex:1.25;border-color:rgba(52,211,153,.5)">Meko MCP<small>mcp.mekodata.ai/mcp · agent_id "rexlite"</small></div></div>
<div class="row"><div class="col card"><h3>Written (background, best effort)</h3><ul><li>Backtest summaries and the lessons they produce</li><li>Patterns from an <b>imported real</b> journal</li><li>Sample data is <b>never</b> mirrored</li></ul>
<p class="m" style="font-size:14px">Tools: <code>conversation_create</code> → <code>memory_add</code></p></div>
<div class="col card"><h3>Recalled</h3><ul><li><code>memory_search</code> on the Check page</li><li>Results filtered to our agent id (search is cross-agent)</li><li>Fetched in the background: 7 to 18 s measured</li></ul></div>
<div class="col card"><h3>Verified, not assumed</h3><ul><li>Live server lists <b>42 tools</b>; write and recall tested 1 Nov 2026</li><li>Failures never break the app</li><li>Under the hood Meko runs on YugabyteDB (per Yugabyte)</li></ul></div></div></div>
<div class="foot"><span>We use Meko as a service; we do not run YugabyteDB ourselves.</span><span>6 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:44px"><span class="tag">Honest results · memory OFF vs ON</span>
<h2>Drawdown <em>−13%</em>. Total P&amp;L <span class="l">lower</span>. We show both.</h2>
<div class="row" style="align-items:flex-start"><div style="flex:1">
<div class="bars">
<div class="k">Max drawdown<br>OFF</div><div><div class="bar2" style="width:100%;background:var(--loss)">−55,495</div></div>
<div class="k">Max drawdown<br>ON</div><div><div class="bar2" style="width:86.5%;background:var(--gain)">−48,005</div></div>
<div class="k">Total P&amp;L<br>OFF</div><div><div class="bar2" style="width:100%;background:var(--sky)">+10,310</div></div>
<div class="k">Total P&amp;L<br>ON</div><div><div class="bar2" style="width:46.7%;background:var(--rex)">+4,813</div></div></div>
<ul style="margin-top:20px"><li>Walk-forward on 118 round trips: each trade checked with only earlier trades.</li>
<li>64 proceed · 36 reduce (half size) · 7 wait (skipped) · 11 warm-up.</li>
<li>The 7 skipped trades would have made <b class="g">+8,168</b>: that is why P&amp;L fell. Win rate 77.1% → 76.6%.</li>
<li>Journal is <b>synthetic behaviour on real prices</b>. A demo of the mechanism, <b>not proof of edge</b>.</li></ul></div>
<div style="flex:.85">{shot('memory')}</div></div></div>
<div class="foot"><span>Source: GET /memory/proof, RESULTS.md. 106 automated tests pass.</span><span>7 / 10</span></div></section>""")

S.append("""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:50px"><span class="tag">Why Rex fits “Agents That Remember”</span>
<h2>Memory that <em>changes a decision</em>, not a chat log</h2>
<div class="row"><div class="col"><div class="card" style="margin-bottom:16px"><h3>Memory has a job</h3><p>Past runs and trades are recalled <b>before</b> the next action and can downgrade proceed → reduce → wait.</p></div>
<div class="card"><h3>Real MCP integration</h3><p>Built against the live Meko server (42 tools), with the exact argument names it requires (<code>conversation_id</code>, JSON-string metadata). Not a mock.</p></div></div>
<div class="col"><div class="card" style="margin-bottom:16px"><h3>Evaluated, with a negative result</h3><p>A walk-forward OFF vs ON test, reported honestly: better drawdown, lower P&amp;L. Memory is judged, not just claimed.</p></div>
<div class="card"><h3>Engineered to degrade safely</h3><p>SQLite is the source of truth; Meko is best-effort. 106 tests, one-command start, offline demo, no API key required.</p></div></div></div></div>
<div class="foot"><span>Our view of the fit. We have not seen the judging rubric.</span><span>8 / 10</span></div></section>""")

S.append(f"""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:44px"><span class="tag">Roadmap · not built yet unless marked</span>
<h2>From one coach to a <em>5-agent desk</em></h2>
<div class="row" style="align-items:flex-start"><div style="flex:1.2">
<div class="row" style="gap:12px;margin-bottom:14px"><div class="card col"><span class="pill ok">built as rules</span><h3 style="margin-top:8px">Scout</h3><p>Ranks the watchlist</p></div><div class="card col"><span class="pill ok">built as rules</span><h3 style="margin-top:8px">Researcher</h3><p>Plain-English brief</p></div><div class="card col"><span class="pill ok">built as rules</span><h3 style="margin-top:8px">Challenger</h3><p>Argues against, can veto</p></div></div>
<div class="row" style="gap:12px;margin-bottom:18px"><div class="card col"><span class="pill s">next</span><h3 style="margin-top:8px">News agent</h3><p>Headlines → context</p></div><div class="card col"><span class="pill s">next</span><h3 style="margin-top:8px">Risk &amp; Coach</h3><p>Sizing + memory</p></div></div>
<ul><li><b>Shared memory:</b> promote lessons into Meko's shared layer so every agent learns from each other.</li><li><b>Paper trading</b> with live fills, then <b>crypto and prediction markets</b>.</li></ul></div>
<div style="flex:.8">{shot('scout')}</div></div></div>
<div class="foot"><span>Today Scout, Research and Challenger are deterministic rules, not LLM agents.</span><span>9 / 10</span></div></section>""")

S.append("""<section class="slide"><div class="logo"><b>R</b>Rex</div><div style="margin-top:60px;max-width:900px"><span class="tag">Team &amp; close</span>
<h1>Rex remembers, <em>so you do not repeat it.</em></h1>
<div class="row" style="margin-top:34px"><div class="card col"><h3>Team</h3><p><b>Vane</b>: builder and product owner<br><b>Grok Bot</b>: AI engineering partner (backend, frontend, tests, docs)<br><span class="m">[add team name, college and links before submitting]</span></p></div>
<div class="card col"><h3>Try it</h3><p><code>./start.sh</code> → UI :3000, API :8000<br>Offline demo bundled. MIT licensed.<br><span class="m">[add repo and demo-video URLs]</span></p></div>
<div class="card col"><h3>Built with</h3><p>Next.js 16 · React 19 · Tailwind v4 · Recharts · FastAPI · SQLite · yfinance · <b>Meko MCP</b></p></div></div>
<p class="m" style="font-size:14px;margin-top:26px">Educational research on historical data. Paper only. Not investment advice. Rex is a strategy and memory layer, not a broker.</p></div>
<div class="foot"><span>HackCBS 9.0</span><span>10 / 10</span></div></section>""")

JS = """
const slides=[...document.querySelectorAll('.slide')],st=document.getElementById('stage');let i=0;
function fit(){const s=Math.min(innerWidth/1280,innerHeight/720);st.style.transform='translate(-50%,-50%) scale('+s+')'}
function go(n){i=Math.max(0,Math.min(slides.length-1,n));slides.forEach((s,k)=>s.classList.toggle('on',k===i));document.getElementById('cur').textContent=i+1;history.replaceState(null,'','#'+(i+1))}
addEventListener('resize',fit);addEventListener('keydown',e=>{if(['ArrowRight','PageDown',' ','Enter'].includes(e.key)){e.preventDefault();go(i+1)}else if(['ArrowLeft','PageUp','Backspace'].includes(e.key)){e.preventDefault();go(i-1)}else if(e.key==='Home')go(0);else if(e.key==='End')go(99)});
document.addEventListener('click',e=>{if(e.clientX>innerWidth/2)go(i+1);else go(i-1)});
fit();go((parseInt(location.hash.slice(1))||1)-1);
"""
html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rex: the trading coach with a memory · HackCBS 9.0</title><style>{CSS}</style></head><body>
<div id="stage">{''.join(S)}</div><div id="hud"><b id="cur">1</b> / {len(S)} · ← → keys or click · print to PDF</div><script>{JS}</script></body></html>"""
open(os.path.join(HERE, "pitch_deck.html"), "w", encoding="utf-8").write(html)
print("pitch_deck.html", round(len(html) / 1e6, 2), "MB,", len(S), "slides")
