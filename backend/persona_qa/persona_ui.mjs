import puppeteer from "puppeteer-core";
const BASE = process.env.BASE || "http://localhost:3000";
const OUT = process.env.OUT || "/workspace/rexlite/docs/screenshots";
const browser = await puppeteer.launch({ executablePath: "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
const page = await browser.newPage(); await page.setViewport({ width: 1360, height: 900 });
const problems = [];
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`[console.${m.type()}] ${page.url()} ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`[pageerror] ${page.url()} ${e.message}`));
page.on("requestfailed", (r) => problems.push(`[requestfailed] ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`); });
const log = (s) => console.log(s);
const shot = async (n) => { const h = await page.evaluate(() => document.documentElement.scrollHeight); await page.setViewport({ width: 1360, height: Math.min(h, 7000) }); await new Promise((r) => setTimeout(r, 1500)); await page.screenshot({ path: `${OUT}/${n}.png` }); await page.setViewport({ width: 1360, height: 900 }); log(`shot ${n}`); };
const clickText = async (sel, text) => { const h = await page.evaluateHandle((sel, text) => [...document.querySelectorAll(sel)].find((e) => e.textContent.includes(text)), sel, text); const el = h.asElement(); if (!el) throw new Error("no " + text); await el.click(); };
const visible = async (text, t = 60000) => page.waitForFunction((x) => document.body.innerText.toLowerCase().includes(x.toLowerCase()), { timeout: t }, text);

const go = async (p) => page.goto(BASE + p, { waitUntil: "networkidle0" });
const setVal = (sel, v) => page.$eval(sel, (e, v) => { const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), "value").set; set.call(e, v); e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); }, v);
const typeNL = async (txt) => { await page.click('[data-testid="nl-text"]', { clickCount: 3 }); await page.keyboard.type(txt); await page.click('[data-testid="nl-parse"]'); };
const parsed = async (needle) => page.waitForFunction((n) => document.querySelector('[data-testid="parsed-summary"]')?.textContent.includes(n), { timeout: 120000 }, needle);
const lastTime = async () => { await new Promise((r) => setTimeout(r, 3000)); return { head: await page.$eval('[data-testid="last-time-headline"]', (e) => e.innerText).catch(() => null), lesson: await page.$eval('[data-testid="last-time-lesson"]', (e) => e.innerText).catch(() => null) }; };
const out = {};
const who = process.env.WHO ? process.env.WHO.split(",") : ["A", "B", "D"];

async function trackFlow(tag, variantLabel, price, qty, date, exitPrice, journal) {
  await page.waitForSelector('[data-testid="track-card"]');
  if (variantLabel === "refined") { await page.select('[data-testid="track-variant"]', "1"); }
  await page.click('[data-testid="track-toggle"]'); await page.waitForSelector('[data-testid="trade-form"]');
  await setVal('[data-testid="trade-price"]', String(price)); await setVal('[data-testid="trade-qty"]', String(qty)); await setVal('[data-testid="trade-date"]', date);
  await page.click('[data-testid="track-logged"]'); await page.waitForSelector('[data-testid="paper-detail"]', { timeout: 30000 }); await page.waitForSelector('[data-testid="track-chart"] svg');
  const tiles = await page.$eval('[data-testid="paper-detail"]', (e) => e.innerText.replace(/\n+/g, " | ").slice(0, 600));
  const alert = await page.$('[data-testid="exit-alert"]') ? await page.$eval('[data-testid="exit-alert"]', (e) => e.innerText.replace(/\n/g, " ")) : null;
  await shot(`persona-${tag}-tracker`);
  if (exitPrice) await setVal('[data-testid="close-price"]', String(exitPrice));
  if (!journal) { const cb = await page.$('[data-testid="close-journal"]'); if (cb && await cb.evaluate((e) => e.checked)) await cb.click(); }
  await page.click('[data-testid="close-btn"]'); await page.waitForSelector('[data-testid="closed-card"]', { timeout: 30000 });
  const verdict = await page.$eval('[data-testid="closed-verdict"]', (e) => e.innerText), lesson = await page.$eval('[data-testid="closed-lesson"]', (e) => e.innerText);
  await shot(`persona-${tag}-closed`);
  return { tiles, alert, verdict, lesson };
}

if (who.includes("A")) {   // beginner: vague idea -> Rex says what it cannot do -> works with suggested wording
  await go("/build"); await page.waitForSelector('[data-testid="nl-text"]');
  await typeNL("I want to buy Reliance when it is going up a lot and sell when I make some profit or if I am losing too much");
  await page.waitForSelector('[data-testid="parsed-unsupported"]', { timeout: 120000 });
  out.A_vague = await page.$eval('[data-testid="parsed-unsupported"]', (e) => e.innerText); await shot("persona-A1-vague");
  await typeNL("Buy Reliance when it breaks above the 20 day high, hold 10 days, 5% stop loss, 10% profit target"); await parsed("RELIANCE");
  out.A_parsed = await page.$eval('[data-testid="parsed-card"]', (e) => e.innerText.replace(/\n+/g, " | ").slice(0, 400));
  await page.waitForSelector('[data-testid="memory-card"] .rounded-lg', { timeout: 20000 }); out.A_lasttime_before = await lastTime();
  await clickText("button", "Run backtest"); await page.waitForFunction(() => location.pathname === "/results", { timeout: 60000 }); await page.waitForSelector('[data-testid="equity-chart"] svg'); await shot("persona-A2-results");
  out.A_results = await page.evaluate(() => document.body.innerText.replace(/\n+/g, " | ").slice(0, 700));
  await clickText("button", "Refine this strategy"); await page.waitForFunction(() => location.pathname === "/refine", { timeout: 120000 }); await visible("Locked-year verdict");
  out.A_refine = await page.$eval('[data-testid="verdict"]', (e) => e.dataset.flag + " :: " + e.innerText.replace(/\n+/g, " | ").slice(0, 300)); await shot("persona-A3-refine");
  out.A_track = await trackFlow("A", "original", 1195, 10, "2026-10-27", null, false);
  await go("/build"); await typeNL("Buy Reliance when it breaks above the 25 day high, hold 10 days, 5% stop loss, 10% profit target"); await parsed("RELIANCE");
  await page.waitForSelector('[data-testid="last-time-headline"]', { timeout: 20000 }); out.A_lasttime_after = await lastTime(); await shot("persona-A4-last-time");
}
if (who.includes("B")) {   // swing trader: MA crossover, follows Rex's refined rules when it held up
  await go("/build"); await typeNL("Buy TCS when the 20 day average crosses above the 50 day average, 4% stop loss, 8% take profit, hold at most 15 days"); await parsed("TCS");
  await page.waitForSelector('[data-testid="memory-card"] .rounded-lg', { timeout: 20000 });
  await clickText("button", "Run backtest"); await page.waitForFunction(() => location.pathname === "/results", { timeout: 60000 }); await page.waitForSelector('[data-testid="equity-chart"] svg');
  await clickText("button", "Refine this strategy"); await page.waitForFunction(() => location.pathname === "/refine", { timeout: 120000 }); await visible("Locked-year verdict");
  out.B_refine = await page.$eval('[data-testid="verdict"]', (e) => e.dataset.flag + " :: " + e.innerText.replace(/\n+/g, " | ").slice(0, 400)); await shot("persona-B1-refine");
  out.B_track = await trackFlow("B", "refined", 2568, 5, "2026-10-23", null, true);
  await go("/build"); await typeNL("Buy TCS when the 15 day average crosses above the 50 day average, 4% stop loss, 8% take profit, hold at most 15 days"); await parsed("TCS");
  await page.waitForSelector('[data-testid="last-time-headline"]', { timeout: 20000 }); out.B_lasttime_after = await lastTime(); await shot("persona-B2-last-time");
}
if (who.includes("D")) {   // repeat-mistake trader uploads a real-price tradebook, then asks the pre-trade check
  await go("/journal"); await visible("Trade journal");
  const fi = await page.$('input[type="file"]'); await fi.uploadFile("/workspace/rexlite/backend/persona_qa/chintu_tradebook_uploaded.csv");
  await page.waitForSelector('[data-testid="import-result"]', { timeout: 60000 }); await visible("Top repeated mistakes"); await new Promise((r) => setTimeout(r, 1500));
  out.D_import = await page.$eval('[data-testid="import-result"]', (e) => e.innerText.replace(/\n+/g, " | ").slice(0, 400)); await shot("persona-D1-journal");
  out.D_mistakes = await page.evaluate(() => { const i = document.body.innerText.indexOf("Top repeated mistakes"); return document.body.innerText.slice(i, i + 700).replace(/\n+/g, " | "); });
  for (const [sym, date, tag] of [["HDFCBANK", "2025-12-24", "wait-case"], ["JIOFIN", "2025-06-10", "proceed-case"]]) {
    await go("/check?symbol=" + sym); await page.waitForSelector('[data-testid="recommendation"]', { timeout: 60000 });
    await setVal('input[type="date"]', date); await clickText("button", "Check"); await new Promise((r) => setTimeout(r, 4000)); await page.waitForSelector('[data-testid="recommendation"]');
    out["D_check_" + tag] = await page.$eval('[data-testid="recommendation"]', (e) => e.dataset.action + " :: " + e.innerText.replace(/\n+/g, " | ").slice(0, 500)); await shot("persona-D2-check-" + tag);
  }
}
console.log(JSON.stringify(out, null, 1));
log("PROBLEMS: " + (problems.length ? "\n" + [...new Set(problems)].join("\n") : "none"));
await browser.close();
