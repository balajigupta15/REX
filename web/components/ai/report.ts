import { get, post, ApiError } from "@/lib/api";
import { errText, pc } from "./lib";
import type { Agent, Report, ReportSection, Tool } from "./agents";
import { assetLabel } from "./agents";

/** Report run for non-trading agents: calls the endpoints Rex already has (price, news, research/backtest, memory) for the agent's asset, one section per tool the agent may use.
 *  Facts only: nothing is scored, nothing is recommended, and no order or deployment is possible from here. */
export type ToolState = "idle" | "working" | "done" | "failed";
export interface ReportProgress { states: Record<string, ToolState>; feed: { key: string; from: string; text: string; at: number }[] }

const num = (x: unknown, d = 2) => (typeof x === "number" && Number.isFinite(x) ? x.toLocaleString(undefined, { maximumFractionDigits: d }) : "n/a");
const pct = (x: unknown, d = 1) => (typeof x === "number" && Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${x.toFixed(d)}%` : "n/a");
const day = (iso?: string | null) => (iso ? iso.slice(0, 10) : "");

async function price(a: Agent): Promise<ReportSection> {
  const q = a.market === "crypto" ? await get(`/crypto/price?symbol=${encodeURIComponent(a.symbol)}`) : await get(`/assets/quote?symbol=${encodeURIComponent(a.symbol)}`);
  const cur = a.market === "nse" ? "INR " : "$";
  const lines = a.market === "crypto"
    ? [`${assetLabel(a)} ${cur}${num(q.price)} (${q.live ? "live" : "not live"}, ${q.provider}, ${q.time_utc}).`, `Market: ${q.market}.`]
    : [`${assetLabel(a)} ${cur}${num(q.price)} (${q.source}, ${q.price_time}).`, `Market state: ${q.state}${q.next_open ? `, next open ${q.next_open}` : ""}.${q.live ? "" : " Prices from NSE are delayed or the last close."}`];
  return { tool: "price", title: "Price", status: "ok", lines };
}

async function news(a: Agent): Promise<ReportSection> {
  const r = await get(`/news?symbol=${encodeURIComponent(a.symbol)}&limit=8`);
  const items = (r.items || []).map((i: any) => ({ text: i.title as string, url: i.url as string, meta: `${i.publisher || "source"}${i.published ? `, ${day(i.published)}` : ""}` }));
  const errs = (r.errors || []).length ? ` Some sources failed: ${(r.errors as any[]).map((e) => (typeof e === "string" ? e : e.source || "source")).join(", ")}.` : "";
  return { tool: "news", title: "Headlines", status: "ok", lines: [items.length ? `${items.length} recent headline${items.length > 1 ? "s" : ""}, listed as published. No sentiment is scored.${errs}` : `No headlines found right now.${errs}`], items };
}

async function history(a: Agent): Promise<ReportSection> {
  if (a.market === "nse") {
    const r = await get(`/research/${encodeURIComponent(a.symbol.replace(/\.NS$/, ""))}`);
    const t = r.trend || {}, v = r.volatility || {};
    return { tool: "backtest", title: "History and risk", status: "ok",
      lines: [`Trend: ${t.label}. 1 / 3 / 6 / 12 months: ${pct(t.ret_1m)} / ${pct(t.ret_3m)} / ${pct(t.ret_6m)} / ${pct(t.ret_12m)}.`,
        `Volatility ${num(v.annualised_pct, 1)}% a year; last 20 days ${num(v.last_20d_pct, 1)}%. Worst fall ${pct(v.max_drawdown_pct)}; now ${pct(v.current_drawdown_pct)} from the peak.`,
        `${num(r.bars, 0)} daily bars, ${day(r.data?.first)} to ${day(r.data?.last)}.`] };
  }
  const r = await post("/crypto/backtest", { symbol: a.symbol, interval: "1d", template: "ma_cross", remember: false });
  const L = r.result?.locked, m = L?.metrics, b = L?.benchmark;
  return { tool: "backtest", title: "History and risk", status: "ok",
    lines: [`Simple 10/50 moving-average crossover on daily candles, after costs, on the locked final period ${L?.start} to ${L?.end}.`,
      `Strategy: ${pc(m?.total_return)} return, max drawdown ${pc(m?.max_drawdown)}, ${m?.trades} trades. Buy and hold: ${pc(b?.total_return)} return, max drawdown ${pc(b?.max_drawdown)}.`,
      "This is a yardstick for risk, not a recommendation. It was not saved to memory."] };
}

async function memory(a: Agent): Promise<ReportSection> {
  const r = a.market === "crypto" ? await get(`/crypto/memory/strategy-runs?symbol=${encodeURIComponent(a.symbol)}&limit=5`) : await get(`/memory/strategy-runs?ticker=${encodeURIComponent(a.symbol)}&limit=5`);
  const lt = r.last_time;
  const lines = r.count ? [`${r.count} earlier run${r.count > 1 ? "s" : ""} on ${assetLabel(a)} in memory.`, ...(lt ? [`Last time: ${lt.lesson || "no lesson recorded"}`] : [])] : [`Nothing in memory for ${assetLabel(a)} yet. Run a trading agent on it to start the record.`];
  return { tool: "memory", title: "Memory", status: "ok", lines };
}

const RUN: Record<Tool, (a: Agent) => Promise<ReportSection>> = { price, news, backtest: history, memory };
const WHO: Record<Tool, string> = { price: "Data Analyst", news: "News Analyst", backtest: "Backtester", memory: "Researcher" };
const ORDER: Tool[] = ["price", "news", "backtest", "memory"];
export const toolOwner = (t: Tool) => WHO[t];

export async function runReport(a: Agent, onProgress: (p: ReportProgress) => void): Promise<Report> {
  const tools = ORDER.filter((t) => a.tools.includes(t));
  const t0 = Date.now();
  const p: ReportProgress = { states: Object.fromEntries(ORDER.map((t) => [t, tools.includes(t) ? "working" : "idle"])), feed: [{ key: "go", from: "You", text: a.role || `Report on ${assetLabel(a)}`, at: 0 }] };
  const emit = () => onProgress({ states: { ...p.states }, feed: [...p.feed] });
  emit();
  const sections: ReportSection[] = await Promise.all(tools.map(async (t): Promise<ReportSection> => {
    let s: ReportSection;
    try { s = await RUN[t](a); p.states[t] = "done"; p.feed.push({ key: t, from: WHO[t], text: s.lines[0], at: Math.round((Date.now() - t0) / 100) / 10 }); }
    catch (e) {
      const msg = e instanceof ApiError || e instanceof Error ? errText(e) : String(e);
      s = { tool: t, title: { price: "Price", news: "Headlines", backtest: "History and risk", memory: "Memory" }[t], status: "error", lines: [], error: msg };
      p.states[t] = "failed"; p.feed.push({ key: t, from: WHO[t], text: `Failed: ${msg}`, at: Math.round((Date.now() - t0) / 100) / 10 });
    }
    emit(); return s;
  }));
  sections.sort((x, y) => ORDER.indexOf(x.tool) - ORDER.indexOf(y.tool));
  const ok = sections.filter((s) => s.status === "ok");
  const summary = ok.map((s) => s.lines[0]).slice(0, 4);
  if (!ok.length) summary.push("No tool returned data, so there is nothing to report. See the errors below.");
  return { at: Date.now(), asset: assetLabel(a), summary, sections };
}
