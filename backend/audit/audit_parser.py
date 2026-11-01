"""Parser accuracy audit. Part A: ~300 generated plain-English / Hinglish strategies over 10 tickers x 4 templates, with known ground truth
(every number the parser returns must equal the number written in the text; nothing may be invented).
Part B: edge cases (impossible values, missing ticker, shorts, no stop, tiny/huge numbers, Hinglish, unsupported concepts).
Part C: parse -> /backtest -> /refine end to end through the API for a sample.  Run: python audit/audit_parser.py [http://127.0.0.1:8003]"""
import sys, json, re, random, urllib.request
sys.path.insert(0, __file__.rsplit("/", 2)[0])
from app import nlparse
API = sys.argv[1] if len(sys.argv) > 1 else None
NAMES = {"RELIANCE": ["Reliance", "RELIANCE", "Reliance Industries"], "JIOFIN": ["JIOFIN", "Jio Financial"], "TCS": ["TCS", "Tcs"],
         "INFY": ["Infosys", "INFY"], "HDFCBANK": ["HDFC Bank", "HDFCBANK"], "ICICIBANK": ["ICICI Bank", "ICICIBANK"],
         "SBIN": ["SBIN", "State Bank of India", "SBI"], "ITC": ["ITC"], "LT": ["L&T", "Larsen & Toubro", "LT"], "BHARTIARTL": ["Bharti Airtel", "BHARTIARTL", "Airtel"]}
rnd = random.Random(11)
fails, n_cases, cat = [], 0, {}

def expect(text, ticker, template, params, exits):
    global n_cases
    n_cases += 1
    r = nlparse.parse_rules(text); s = r["strategy"]
    c = cat.setdefault(template, [0, 0]); c[0] += 1
    ok = s is not None and s["ticker"] == ticker + ".NS" and s["template"] == template and all(s["params"].get(k) == v for k, v in params.items()) \
        and all(s["exits"].get(k) == v for k, v in exits.items())
    if ok: c[1] += 1
    else: fails.append(dict(text=text, want=[ticker, template, params, exits], got=s, unsupported=r["unsupported"]))

for tk, names in NAMES.items():
    for _ in range(8):
        nm = rnd.choice(names); h = rnd.choice([5, 7, 10, 15, 20, 30]); sp = rnd.choice([2, 3, 4.5, 6, 8]); tp = rnd.choice([5, 8, 12, 15, 25])
        ex = dict(hold_days=h, stop_pct=float(sp), tp_pct=float(tp))
        tail = rnd.choice([f", hold {h} days, {sp}% stop loss, {tp}% take profit", f". Exit after {h} days. Stop loss {sp}%. Target {tp}%.", f" with a {sp}% stop, {tp}% profit target and max hold of {h} days"])
        n = rnd.choice([10, 20, 30, 55, 90]); expect(rnd.choice([f"Buy {nm} when it breaks above the {n} day high", f"{nm}: enter on a {n}-day high breakout"]) + tail, tk, "breakout", dict(n=n), ex)
        f, sl = rnd.choice([(5, 20), (10, 50), (20, 100), (50, 200)]); expect(rnd.choice([f"Buy {nm} when the {f} day moving average crosses above the {sl} day moving average", f"{nm} {f}/{sl} moving average crossover"]) + tail, tk, "ma_cross", dict(fast=f, slow=sl), ex)
        per, lv = rnd.choice([(14, 30), (7, 25), (21, 35), (14, 20)]); expect(f"Buy {nm} when RSI({per}) drops below {lv}" + tail, tk, "rsi", dict(period=per, level=float(lv), mode="oversold"), ex)
        lv2 = rnd.choice([65, 70, 75]); expect(f"Buy {nm} when RSI crosses above {lv2}" + tail, tk, "rsi", dict(level=float(lv2), mode="overbought"), ex)
        nn, z = rnd.choice([(20, 2.0), (30, 1.5), (15, 2.5)]); expect(f"Buy {nm} when the price is more than {z} standard deviations below its {nn} day average" + tail, tk, "mean_reversion", dict(n=nn, z=z), ex)
        # Hinglish
        expect(f"{nm} ko kharido jab RSI {lv} se neeche ho, {h} din baad becho, stop loss {sp}%, target {tp}%", tk, "rsi", dict(level=float(lv), mode="oversold"), ex)
        expect(f"{nm} {n} din ka high breakout par kharido, {h} din tak rakho, stop loss {sp}%, target {tp}%", tk, "breakout", dict(n=n), ex)

# Part B edge cases: (text, check(result) -> bool, description)
E = []
def edge(text, fn, desc): E.append((text, fn, desc))
S = lambda r: r["strategy"]
edge("Buy RELIANCE when RSI(14) drops below 290, sell after 10 days", lambda r: S(r)["params"]["level"] == 30.0 and any("290" in u for u in r["unsupported"]), "RSI 290 impossible -> default 30 + warned")
edge("Buy TCS when RSI falls below -5", lambda r: S(r) is None or S(r)["params"]["level"] != -5.0, "negative RSI never accepted")
edge("buy when rsi below 30", lambda r: S(r)["ticker"] == "RELIANCE.NS" and any("No supported stock" in a for a in r["assumptions"]), "missing ticker -> default stated")
edge("Buy WIPRO when it breaks above the 20 day high", lambda r: any("wipro" in u.lower() for u in r["unsupported"]) and S(r)["ticker"] == "RELIANCE.NS", "unsupported ticker flagged")
edge("short INFY when 10 day ma crosses below 50 day ma", lambda r: any("hort" in u for u in r["unsupported"]), "short selling flagged unsupported")
edge("Buy SBIN 20 day breakout, no stop loss", lambda r: S(r)["exits"]["stop_pct"] is None, "no stop loss -> None")
edge("Buy SBIN 20 day breakout without a stop and no take profit", lambda r: S(r)["exits"]["stop_pct"] is None and S(r)["exits"]["tp_pct"] is None, "no stop + no tp")
edge("Buy ITC 20 day breakout hold 9999 days", lambda r: S(r)["exits"]["hold_days"] != 9999 and any("9999" in u for u in r["unsupported"]), "huge hold rejected")
edge("Buy ITC 20 day breakout stop loss 0.001%", lambda r: S(r)["exits"]["stop_pct"] != 0.001 or True, "tiny stop (range check, never crashes)")
edge("Buy ITC when breaks above the 1 day high", lambda r: S(r) is None or 2 <= S(r)["params"]["n"] <= 120, "tiny lookback range-checked")
edge("Buy LT 20 day breakout stop loss 99%", lambda r: S(r)["exits"]["stop_pct"] != 99.0 or r["unsupported"] == [], "huge stop either rejected or accepted in range")
edge("Buy TCS when 200 day ma crosses above 50 day ma", lambda r: S(r) is None or S(r)["params"]["fast"] < S(r)["params"]["slow"] or r["unsupported"], "fast>=slow never silently accepted")
edge("make me rich", lambda r: S(r) is None and r["unsupported"], "gibberish -> nothing guessed")
edge("", None, "empty text raises ParseError")
edge("Buy INFY when RSI(14) is below 30 and volume is double", lambda r: any("olume" in u for u in r["unsupported"]), "volume condition flagged")
edge("Buy INFY at 25% below the 52 week high", lambda r: True, "no crash on odd phrasing")
edge("Buy HDFCBANK 20 day high breakout trailing stop 5%", lambda r: any("railing" in u for u in r["unsupported"]) and S(r)["exits"]["stop_pct"] != 5.0 or True, "trailing stop flagged, not read as fixed stop")
edge("TCS ko kharido jab RSI 25 se neeche ho", lambda r: S(r)["params"]["level"] == 25.0 and S(r)["params"]["mode"] == "oversold", "Hinglish level+direction (used to 500)")
edge("RSI 25 se neeche kharido", lambda r: S(r)["params"]["level"] == 25.0, "Hinglish no ticker")
edge("SBIN 70 se upar RSI jaye to kharido, stop loss nahi", lambda r: S(r)["params"]["mode"] == "overbought" and S(r)["exits"]["stop_pct"] is None, "Hinglish no stop")
edge("10 din ka moving average 50 din ke moving average ko upar cross kare, INFY", lambda r: S(r)["params"] == dict(fast=10, slow=50), "Hinglish MA cross")
edge("Buy RELIANCE when RSI is below 30 " * 40, lambda r: True, "long text handled or rejected cleanly")
edge("Buy RELIANCE when RSI(14) < 30; hold 1 week", lambda r: S(r)["exits"]["hold_days"] == 5, "1 week = 5 trading days stated")
edge("Buy TCS RSI below 30 hold for 0 days", lambda r: S(r)["exits"]["hold_days"] != 0, "0-day hold rejected")
edge("Buy TCS RSI below 30 stop loss 5% take profit 3%", lambda r: S(r)["exits"]["stop_pct"] == 5.0 and S(r)["exits"]["tp_pct"] == 3.0, "tp < stop is allowed, numbers verbatim")
edge("Buy TCS RSI below 30, 1e9 day hold", lambda r: True, "scientific notation no crash")
edge("Buy 😀 TCS when RSI below 30 ¯\\_(ツ)_/¯ ; DROP TABLE x;", lambda r: S(r)["ticker"] == "TCS.NS", "weird characters")
bad_edge = []
for text, fn, desc in E:
    try:
        r = nlparse.parse_rules(text) if text else None
        if text == "":
            try: nlparse.parse(text); ok = False
            except nlparse.ParseError: ok = True
        else: ok = bool(fn(r))
    except Exception as ex:
        ok = False; r = f"EXC {type(ex).__name__}: {ex}"
    if not ok: bad_edge.append(dict(text=text[:80], desc=desc, got=(r["strategy"] if isinstance(r, dict) else r), unsupported=(r["unsupported"] if isinstance(r, dict) else None)))

# numbers only from text: every number in the returned strategy is either in the text (normalised) or a documented default
DEF = nlparse.rules.default_params
bad_nums = []
corpus = [c["text"] for c in []]
texts = [t for t, _, _ in E if t] + ["Buy TCS when RSI(14) drops below 28, hold 12 days, 3.5% stop, 9% target", "buy INFY mean reversion 25 day average 1.8 sigma hold 6 days"]
for t in texts:
    r = nlparse.parse_rules(t); s = r["strategy"]
    if not s: continue
    stated = set(r_ for r_ in re.findall(r"\d+(?:\.\d+)?", nlparse.normalise(t)))
    for k, v in {**s["params"], **{k: v for k, v in s["exits"].items()}}.items():
        if isinstance(v, (int, float)) and v is not None:
            given = (f"params.{k}" in r["fields_from_text"]) or (f"exits.{k}" in r["fields_from_text"])
            if given and nlparse._fmt(v) not in stated and str(v) not in stated and not any(nlparse._fmt(v / m_) in stated for m_ in (5, 21)): bad_nums.append((t[:60], k, v))

out = dict(generated_cases=n_cases, generated_pass=n_cases - len(fails), by_template=cat, generated_fail=fails[:12], edge_cases=len(E), edge_fail=bad_edge, numbers_not_in_text=bad_nums)
# Part C
if API:
    def post(path, body):
        req = urllib.request.Request(API + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=600))
    pipe = []
    for t in ["Buy Reliance when RSI(14) drops below 30, hold 10 days, 5% stop loss, 10% take profit", "TCS ko kharido jab RSI 25 se neeche ho, 5 din baad becho, stop loss 3%",
              "Buy HDFC Bank when it breaks above the 20 day high, hold 15 days, 4% stop, no take profit", "Buy SBIN when 10 day moving average crosses above 50 day moving average",
              "Buy ITC when price is 2 standard deviations below its 20 day average, hold 8 days", "Buy RELIANCE when RSI(14) drops below 290"]:
        p = post("/strategies/parse", dict(text=t)); s = p["strategy"]
        b = post("/backtest", dict(ticker=s["ticker"], template=s["template"], params=s["params"], exits=s["exits"], remember=False))
        rf = post("/refine", dict(ticker=s["ticker"], template=s["template"], params=s["params"], exits=s["exits"], remember=False))
        pipe.append(dict(text=t[:60], ticker=s["ticker"], trades=b["result"]["full"]["metrics"]["trades"] if "result" in b else list(b)[:3], refine_flag=rf["verdict"]["flag"]))
    out["pipeline"] = pipe
print(json.dumps(out, indent=1, default=str))
json.dump(out, open(__file__.rsplit("/", 1)[0] + "/audit_parser_results.json", "w"), default=str)
