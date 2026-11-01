import type { Run, Step } from "./lib";

/** Pure mapping from the streamed run (steps, debate, risk, final) to desk agents and hand-off messages. No backend changes: it only reads what /ai/runs/{id} already returns. */
export type AgentId = "data" | "news" | "researcher" | "coder" | "backtester" | "bull" | "bear" | "risk" | "pm";
export type AgentState = "idle" | "working" | "done" | "failed";
export interface DeskAgent { id: AgentId; name: string; duty: string; state: AgentState; task: string; snippet: string | null }
export interface DeskMsg { key: string; from: string; to: string | null; text: string; at: number | null }

const DEFS: { id: AgentId; name: string; duty: string }[] = [
  { id: "data", name: "Data Analyst", duty: "Real candles" },
  { id: "news", name: "News Analyst", duty: "Headlines" },
  { id: "researcher", name: "Researcher", duty: "Memory and hypotheses" },
  { id: "coder", name: "Coder", duty: "Writes strategy code" },
  { id: "backtester", name: "Backtester", duty: "Train, locked period, overfit check" },
  { id: "bull", name: "Bull", duty: "Case for the edge" },
  { id: "bear", name: "Bear", duty: "Case it is luck" },
  { id: "risk", name: "Risk Manager", duty: "Hard veto rules" },
  { id: "pm", name: "Portfolio Manager", duty: "Final call" },
];
const owners = (tool: string): AgentId[] => tool === "get_data" ? ["data"] : tool === "get_news" ? ["news"] : tool === "recall_memory" || tool === "plan" ? ["researcher"] : tool === "write_strategy" ? ["coder"]
  : tool === "backtest" ? ["backtester"] : tool === "debate" ? ["bull", "bear"] : tool === "risk_check" ? ["risk"] : tool === "decide" ? ["pm"] : [];
const NAME = Object.fromEntries(DEFS.map((d) => [d.id, d.name])) as Record<AgentId, string>;
const clip = (s: string | null | undefined, n = 150) => { const t = (s || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1).trimEnd() + "..." : t; };

function workingTask(id: AgentId, s: Step): string {
  const i = s.inputs || {};
  switch (s.tool) {
    case "get_data": return `Pulling real ${i.symbol ?? ""} ${i.interval ?? ""} candles`.replace(/\s+/g, " ");
    case "get_news": return `Reading recent headlines on ${i.symbol ?? "the asset"}`;
    case "recall_memory": return "Recalling lessons from earlier runs";
    case "plan": return "Drafting hypotheses to test";
    case "write_strategy": return `Round ${i.round ?? "?"}: writing code for "${clip(i.hypothesis, 48)}"`;
    case "backtest": return `Round ${i.round ?? "?"}: testing on train, then the locked period`;
    case "debate": return id === "bull" ? "Building the strongest case for the edge" : "Building the strongest case that it is luck";
    case "risk_check": return "Checking the evidence against the hard limits";
    case "decide": return "Writing the final call and the lesson";
    default: return "Working";
  }
}

export function deriveDesk(run: Run | null): { agents: DeskAgent[]; feed: DeskMsg[]; phase: "waiting" | "working" | "complete" | "stopped"; activeCount: number } {
  const steps = run?.steps ?? [];
  const live = !!run && ["queued", "running", "interrupted"].includes(run.status);
  const agents: DeskAgent[] = DEFS.map((d) => {
    const mine = steps.filter((s) => owners(s.tool).includes(d.id));
    const running = [...mine].reverse().find((s) => s.status === "running");
    const lastOk = [...mine].reverse().find((s) => s.status === "ok");
    const lastErr = [...mine].reverse().find((s) => s.status === "error");
    const nOk = mine.filter((s) => s.status === "ok").length;
    let state: AgentState = "idle", task = live || !run ? "Waiting for a brief" : "Not needed for this run", snippet: string | null = null;
    if (!run) task = "Off shift until a brief arrives";
    else if (live && steps.length === 0 && d.id === "data") { state = "working"; task = "Starting up"; }
    else if (live && mine.length === 0) task = "Waiting for hand-off";
    if (running && live) { state = "working"; task = workingTask(d.id, running); }
    else if (running && !live) { state = "failed"; task = "Stopped before finishing"; }
    else if (lastErr && (!lastOk || lastErr.idx > lastOk.idx)) { state = "failed"; task = "Step failed"; snippet = clip(lastErr.error?.message || lastErr.summary); }
    else if (lastOk) {
      state = "done";
      const out = lastOk.output || {};
      switch (d.id) {
        case "data": task = "Candles loaded"; break;
        case "news": task = out.disabled ? "News is off here, skipped" : "Headlines read"; break;
        case "researcher": task = steps.some((s) => s.tool === "plan" && s.status === "ok") ? "Hypotheses ready" : "Memory recalled"; break;
        case "coder": task = `${nOk} strategy version${nOk > 1 ? "s" : ""} written`; break;
        case "backtester": task = `${nOk} test${nOk > 1 ? "s" : ""} run`; break;
        case "bull": task = "Case made"; break;
        case "bear": task = "Case made"; break;
        case "risk": task = run?.risk ? `Verdict: ${run?.risk.verdict}` : "Checked"; break;
        case "pm": task = run?.final ? `Call: ${run?.final.decision}` : "Call made"; break;
      }
    }
    if (state === "done" || (state === "working" && lastOk)) {
      if (d.id === "bull") snippet = clip(run?.debate?.bull?.[0]) || clip(lastOk?.summary);
      else if (d.id === "bear") snippet = clip(run?.debate?.bear?.[0]) || clip(lastOk?.summary);
      else if (d.id === "pm") snippet = clip(run?.final?.headline) || clip(lastOk?.summary);
      else if (d.id === "coder" && lastOk) snippet = clip(lastOk.output?.title ? `${lastOk.output.title} (${lastOk.output.source === "gemini" ? "Gemini" : "offline synthesizer"})` : lastOk.summary);
      else if (d.id === "backtester" && lastOk?.output?.experiment) snippet = clip(`${lastOk.output.experiment.status === "kept" ? "Kept" : "Dropped"}: ${lastOk.summary}`);
      else snippet = clip(lastOk?.summary);
    }
    return { ...d, state, task, snippet };
  });

  const t0 = steps.find((s) => s.started)?.started ?? null;
  const at = (x?: number | null) => (x && t0 ? Math.max(0, Math.round(x - t0)) : null);
  const feed: DeskMsg[] = [];
  const add = (key: string, from: AgentId | string, to: AgentId | string | null, text: string, ts?: number | null) => feed.push({ key, from: (NAME as any)[from] ?? from, to: to ? (NAME as any)[to] ?? to : null, text: clip(text, 170), at: at(ts) });
  if (run) add("brief", "You", "Researcher", run.goal, t0);
  for (const s of steps) {
    const i = s.inputs || {};
    if (s.tool === "write_strategy") add(`${s.idx}-in`, "researcher", "coder", `Round ${i.round ?? "?"}: ${clip(i.hypothesis, 90)}`, s.started);
    if (s.tool === "debate") add(`${s.idx}-in`, "researcher", "Bull and Bear", "Best candidate so far goes to debate", s.started);
    if (s.status === "error") { const o = owners(s.tool)[0]; add(`${s.idx}-err`, o, "researcher", `Failed: ${s.error?.message ?? s.summary ?? "unknown error"}`, s.ended ?? s.started); continue; }
    if (s.status !== "ok") continue;
    const out = s.output || {}; const sum = s.summary || "";
    switch (s.tool) {
      case "get_data": add(`${s.idx}-o`, "data", "researcher", sum, s.ended); break;
      case "get_news": add(`${s.idx}-o`, "news", "researcher", sum, s.ended); break;
      case "recall_memory": add(`${s.idx}-o`, "researcher", null, `Memory check: ${out.runs?.length ?? 0} earlier run(s) found for this asset`, s.ended); break;
      case "plan": add(`${s.idx}-o`, "researcher", "coder", sum, s.ended); break;
      case "write_strategy": add(`${s.idx}-o`, "coder", "backtester", sum, s.ended); break;
      case "backtest": add(`${s.idx}-o`, "backtester", "researcher", `${out.experiment ? (out.experiment.status === "kept" ? "Kept. " : "Dropped. ") : ""}${sum}`, s.ended); break;
      case "debate": add(`${s.idx}-o1`, "bull", "bear", run?.debate?.bull?.[0] ?? sum, s.ended); add(`${s.idx}-o2`, "bear", "risk", run?.debate?.bear?.[0] ?? sum, s.ended); break;
      case "risk_check": add(`${s.idx}-o`, "risk", "pm", sum, s.ended); break;
      case "decide": add(`${s.idx}-o`, "pm", "You", run?.final?.headline ?? sum, s.ended); break;
    }
  }
  const activeCount = agents.filter((a) => a.state === "working").length;
  const phase = !run ? "waiting" : live ? "working" : run.status === "done" ? "complete" : "stopped";
  return { agents, feed, phase, activeCount };
}
