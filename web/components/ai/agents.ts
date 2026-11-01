"use client";
import { useCallback, useEffect, useState } from "react";
import type { Run } from "./lib";

/** Agents the user hired or built. Stored in this browser only (localStorage "rex.agents.v1"; entries saved by the earlier desk version load as trading agents).
 *  kind "trading": runs the research swarm (POST /ai/runs) and can be deployed as a PAPER watch (signals only, no orders).
 *  kind "analyst": any non-trading job; a run produces a report from the existing price / news / history / memory endpoints and has no deploy option. */
export type Kind = "trading" | "analyst";
export type Tool = "price" | "news" | "backtest" | "memory";
export type Style = "any" | "momentum" | "mean-reversion";
export const TOOLS: { id: Tool; label: string; hint: string }[] = [
  { id: "price", label: "Price data", hint: "Live or last-close quote" },
  { id: "news", label: "News", hint: "Recent headlines" },
  { id: "backtest", label: "Backtest / history", hint: "Trend, volatility, drawdown, a simple test vs buy and hold" },
  { id: "memory", label: "Memory", hint: "What Rex learned from earlier runs on this asset" },
];
export const ALL_TOOLS: Tool[] = TOOLS.map((t) => t.id);

export interface Alert_ { at: number; text: string }
export interface Deploy {
  status: "live" | "stopped"; pending?: boolean; watchId?: string; versionId: string; runId: string; title: string; symbol: string; startedAt: number; stoppedAt?: number;
  entryPrice?: number | null; error?: string | null;
  last?: { position: string; price: number | null; paperPct: number | null; trades: number; bar: string; at: number };
  alerts: Alert_[];
}
export interface ReportSection { tool: Tool; title: string; status: "ok" | "error"; lines: string[]; items?: { text: string; url?: string; meta?: string }[]; error?: string }
export interface Report { at: number; asset: string; summary: string[]; sections: ReportSection[] }
export interface Agent {
  id: string; name: string; kind: Kind; role: string; preset?: string; tools: Tool[];
  market: "crypto" | "nse"; symbol: string; interval: "1h" | "4h" | "1d"; style: Style; maxDd: number; goal: string; created: number;
  lastRunId?: string; last?: { status: "done" | "error"; decision?: string; headline?: string; dd?: number | null; versionId?: string | null; title?: string; at: number };
  report?: Report; deploy?: Deploy;
}
export type Hire = Agent;

export interface Preset { id: string; label: string; kind: Kind; blurb: string; role: string; tools: Tool[]; market: "crypto" | "nse"; symbol: string; goal: string; name: string }
export const PRESETS: Preset[] = [
  { id: "trader", label: "Trading Agent", kind: "trading", name: "Trading Agent", market: "crypto", symbol: "BTCUSDT", tools: ALL_TOOLS,
    role: "Researches a strategy on real candles, tests it on a locked final period, and can be deployed as a paper watch.", goal: "Find a BTC strategy on 1h candles that won't overfit",
    blurb: "Runs the full swarm: data, code, backtest, debate, risk veto. Can be deployed as paper signals." },
  { id: "research", label: "Research Analyst", kind: "analyst", name: "Research Analyst", market: "crypto", symbol: "BTCUSDT", tools: ["price", "news", "backtest", "memory"],
    role: "One-page brief on an asset: price, history, headlines, and what Rex learned last time.", goal: "",
    blurb: "A one-page brief from price, history, headlines and memory. No trading." },
  { id: "news", label: "News Analyst", kind: "analyst", name: "News Analyst", market: "crypto", symbol: "BTCUSDT", tools: ["news", "price"],
    role: "Reads the latest headlines on an asset and lists them with the current price.", goal: "",
    blurb: "Latest headlines with sources and links, next to the current price." },
  { id: "risk", label: "Risk Manager", kind: "analyst", name: "Risk Manager", market: "crypto", symbol: "BTCUSDT", tools: ["price", "backtest", "memory"],
    role: "Reports drawdown, volatility and how a simple strategy did against buy and hold.", goal: "",
    blurb: "Drawdown, volatility and a simple strategy against buy and hold." },
];

const STYLE_TEXT: Record<Style, string> = { any: "", momentum: " Style: momentum / trend-following ideas only.", "mean-reversion": " Style: mean-reversion ideas only." };
export const hireGoal = (h: Pick<Agent, "goal" | "style" | "maxDd">) => `${h.goal.trim()}${STYLE_TEXT[h.style]} Keep the maximum drawdown under ${h.maxDd}%.`.slice(0, 600);
export const hireTarget = (h: Agent) => ({ market: h.market, symbol: h.symbol, interval: h.market === "nse" ? "1d" : h.interval });
export const isLive = (r: Run | null) => !!r && ["queued", "running", "interrupted"].includes(r.status);
/** Normalize the API's terminal state before persisting the agent card. */
export function completedResult(run: Run): Agent["last"] | null {
  if (run.status !== "done" && run.status !== "failed") return null;
  const dd = run.final?.metrics?.locked?.max_drawdown;
  return { status: run.status === "failed" ? "error" : "done", decision: run.final?.decision,
    headline: run.final?.headline ?? run.error?.message, dd: dd == null ? null : Math.abs(dd) * 100,
    versionId: run.final?.version_id ?? null, title: run.final?.title, at: Date.now() };
}
export const mkId = () => `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
export const assetLabel = (a: Pick<Agent, "market" | "symbol">) => a.symbol.replace(/\.NS$/, "").replace(/USDT$/, "");

const KEY = "rex.agents.v1", SEL = "rex.agents.sel";
function migrate(x: any): Agent | null {
  if (!x || typeof x !== "object" || typeof x.id !== "string" || typeof x.name !== "string") return null;
  const kind: Kind = x.kind === "analyst" ? "analyst" : "trading";
  const deploy = x.deploy && typeof x.deploy === "object" && !(x.deploy.pending && !x.deploy.watchId) ? { ...x.deploy, error: null, alerts: Array.isArray(x.deploy.alerts) ? x.deploy.alerts : [] } : undefined;   // a deploy that never reached the server (page closed mid-request) is dropped
  return { ...x, deploy, kind, role: typeof x.role === "string" ? x.role : "", tools: Array.isArray(x.tools) ? x.tools.filter((t: any) => ALL_TOOLS.includes(t)) : ALL_TOOLS,
    market: x.market === "nse" ? "nse" : "crypto", symbol: String(x.symbol || "BTCUSDT"), interval: x.interval || "1h", style: x.style || "any", maxDd: Number(x.maxDd) || 20, goal: String(x.goal || ""), created: Number(x.created) || 0 };
}
const load = (): Agent[] => { try { const x = JSON.parse(localStorage.getItem(KEY) || "[]"); return Array.isArray(x) ? x.map(migrate).filter((a): a is Agent => !!a) : []; } catch { return []; } };
const save = (a: Agent[]) => { try { localStorage.setItem(KEY, JSON.stringify(a)); } catch { /* storage full or blocked: agents stay in memory */ } };

export interface Store {
  agents: Agent[]; ready: boolean; selId: string | null; selected: Agent | null;
  update: (fn: (a: Agent[]) => Agent[]) => void; patch: (id: string, p: Partial<Agent> | ((a: Agent) => Partial<Agent>)) => void;
  select: (id: string | null) => void; add: (a: Agent) => void; remove: (id: string) => void;
}
export function useAgents(): Store {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [ready, setReady] = useState(false);
  const [selId, setSel] = useState<string | null>(null);
  useEffect(() => { const a = load(); setAgents(a); try { const s = localStorage.getItem(SEL); if (s && a.some((x) => x.id === s)) setSel(s); } catch { /* ignore */ } setReady(true); }, []);
  const update = useCallback((fn: (a: Agent[]) => Agent[]) => setAgents((a) => { const n = fn(a); save(n); return n; }), []);
  const patch = useCallback((id: string, p: Partial<Agent> | ((a: Agent) => Partial<Agent>)) => update((a) => a.map((x) => (x.id === id ? { ...x, ...(typeof p === "function" ? p(x) : p) } : x))), [update]);
  const select = useCallback((id: string | null) => { setSel(id); try { id ? localStorage.setItem(SEL, id) : localStorage.removeItem(SEL); } catch { /* ignore */ } }, []);
  const add = useCallback((a: Agent) => { update((x) => [a, ...x]); select(a.id); }, [update, select]);
  const remove = useCallback((id: string) => { update((x) => x.filter((a) => a.id !== id)); setSel((s) => { if (s === id) { try { localStorage.removeItem(SEL); } catch { /* ignore */ } return null; } return s; }); }, [update]);
  return { agents, ready, selId, selected: agents.find((a) => a.id === selId) ?? null, update, patch, select, add, remove };
}
