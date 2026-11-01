"use client";
import { useEffect, useRef } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Run } from "./lib";
import { deriveDesk, type AgentState } from "./desk-model";

const STATE_LABEL: Record<AgentState, string> = { idle: "idle", working: "working", done: "done", failed: "failed" };
const dot: Record<AgentState, string> = { idle: "bg-line", working: "bg-sky motion-safe:animate-pulse", done: "bg-gain", failed: "bg-loss" };
const PHASE: Record<string, string> = { waiting: "Standing by", working: "Working", complete: "Shift complete", stopped: "Stopped" };

/** AI trading desk: Rex as an employee with a team of specialist agents. Read-only view over the streamed run; nothing here places orders. */
export function TradingDesk({ run, agentName }: { run: Run | null; agentName?: string }) {
  const { agents, feed, phase, activeCount } = deriveDesk(run);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [feed.length]);
  return (
    <Card data-testid="trading-desk">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <div><CardTitle>Rex desk on shift{agentName ? <span className="ml-2 align-middle"><Badge tone="sky" data-testid="desk-agent">{agentName}</Badge></span> : null}</CardTitle>
          <p className="text-xs text-mute">Rex is the AI trader here. It hands work to nine specialists and never places an order; the final call is evidence for you to act on in your own broker.</p></div>
        <div className="flex items-center gap-2 text-xs text-mute" aria-live="polite" data-testid="desk-phase">
          <span className={cn("h-2 w-2 rounded-full", phase === "working" ? "bg-sky motion-safe:animate-pulse" : phase === "complete" ? "bg-gain" : phase === "stopped" ? "bg-loss" : "bg-line")} />
          {PHASE[phase]}{phase === "working" ? `, ${activeCount} agent${activeCount === 1 ? "" : "s"} active` : ""}
        </div>
      </CardHeader>
      <CardBody className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" data-testid="desk-agents">
          {agents.map((a) => (
            <div key={a.id} data-agent={a.id} data-state={a.state}
              className={cn("rounded border bg-ink/40 px-3 py-2 transition-colors", a.state === "working" ? "border-sky/60" : a.state === "failed" ? "border-loss/50" : "border-line", a.state === "idle" && "opacity-70")}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{a.name}</span>
                <span className="flex items-center gap-1.5 text-[11px] text-mute"><span className={cn("h-1.5 w-1.5 rounded-full", dot[a.state])} />{STATE_LABEL[a.state]}</span>
              </div>
              <div className="text-[11px] text-mute">{a.duty}</div>
              <p className="mt-1.5 text-xs">{a.task}</p>
              {a.snippet && <p className="mt-1 line-clamp-3 border-l border-line pl-2 text-xs text-mute" title={a.snippet}>{a.snippet}</p>}
            </div>
          ))}
        </div>
        <div className="flex min-h-0 flex-col">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-mute">Hand-offs</div>
          <div ref={box} className="max-h-[26rem] min-h-40 flex-1 space-y-1.5 overflow-auto rounded border border-line bg-ink/40 p-2" data-testid="desk-feed">
            {feed.length === 0 && <p className="text-xs text-mute">Messages between agents appear here once a run starts.</p>}
            {feed.map((m) => (
              <div key={m.key} className="text-xs leading-snug">
                <span className="font-medium">{m.from}</span>{m.to && <span className="text-mute"> {"->"} </span>}{m.to && <span className="font-medium">{m.to}</span>}
                {m.at != null && <span className="ml-1 font-mono text-[10px] text-mute">+{m.at}s</span>}
                <span className="text-mute">: </span><span className="text-text/90">{m.text}</span>
              </div>
            ))}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
