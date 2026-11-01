"use client";
import { useEffect, useRef } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { TOOLS, type Agent, type Report } from "./agents";
import { toolOwner, type ToolState } from "./report";
import type { ReportRun } from "./use-report";

const dot: Record<ToolState, string> = { idle: "bg-line", working: "bg-sky motion-safe:animate-pulse", done: "bg-gain", failed: "bg-loss" };

/** Desk view for a selected non-trading agent: one card per tool it may use, and the hand-offs. Same look as the trading desk, no trading controls. */
export function ReportDesk({ agent, state }: { agent: Agent; state?: ReportRun }) {
  const box = useRef<HTMLDivElement>(null);
  const feed = state?.progress?.feed ?? [];
  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [feed.length]);
  const st = (id: string): ToolState => (state?.progress?.states[id] as ToolState) ?? (agent.report && agent.tools.includes(id as any) ? "done" : "idle");
  const busy = !!state?.busy;
  return (
    <Card data-testid="report-desk" className="rex-in">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <div><CardTitle>{agent.name} on shift</CardTitle><p className="text-xs text-mute">A non-trading agent. It reads the data its tools return and writes a report. It cannot trade or be deployed.</p></div>
        <div className="flex items-center gap-2 text-xs text-mute" aria-live="polite" data-testid="desk-phase">
          <span className={cn("h-2 w-2 rounded-full", busy ? "bg-sky motion-safe:animate-pulse" : agent.report ? "bg-gain" : "bg-line")} />{busy ? "Working" : agent.report ? "Report ready" : "Standing by"}
        </div>
      </CardHeader>
      <CardBody className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="grid content-start gap-2 sm:grid-cols-2" data-testid="report-tools">
          {TOOLS.map((t) => {
            const on = agent.tools.includes(t.id), s = on ? st(t.id) : "idle";
            return (
              <div key={t.id} data-tool={t.id} data-state={s} className={cn("min-h-[4.25rem] rounded border bg-ink/40 px-3 py-2 transition-colors duration-200", s === "working" ? "border-sky/60" : s === "failed" ? "border-loss/50" : "border-line", !on && "opacity-40")}>
                <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{toolOwner(t.id)}</span>
                  <span className="flex items-center gap-1.5 text-[11px] text-mute"><span className={cn("h-1.5 w-1.5 rounded-full", dot[s])} />{on ? s : "not allowed"}</span></div>
                <div className="text-[11px] text-mute">{t.label}</div>
                <p className="mt-1 text-xs text-mute">{!on ? "Not in this agent's tool list" : s === "working" ? "Fetching..." : s === "done" ? "Done" : s === "failed" ? "Failed, see report" : "Waiting for a run"}</p>
              </div>);
          })}
        </div>
        <div className="flex min-h-0 flex-col">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-mute">Hand-offs</div>
          <div ref={box} className="h-44 space-y-1.5 overflow-auto rounded border border-line bg-ink/40 p-2" data-testid="desk-feed">
            {feed.length === 0 && <p className="text-xs text-mute">Messages between tools appear here once a report starts.</p>}
            {feed.map((m) => (
              <div key={m.key} className="rex-in text-xs leading-snug"><span className="font-medium">{m.from}</span><span className="ml-1 font-mono text-[10px] text-mute">+{m.at}s</span><span className="text-mute">: </span><span className="text-text/90">{m.text}</span></div>
            ))}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

export function ReportView({ agent, state }: { agent: Agent; state?: ReportRun }) {
  const r: Report | undefined = agent.report;
  if (state?.busy && !r) return <div className="space-y-2" data-testid="report-loading"><Skeleton className="h-24" /><Skeleton className="h-32" /></div>;
  if (!r) return <p className="text-sm text-mute" data-testid="report-empty">{state?.err ? `The report failed: ${state.err}` : `No report yet. Press Report on ${agent.name} to write one.`}</p>;
  return (
    <Card data-testid="report" className={cn("rex-in transition-opacity", state?.busy && "opacity-60")}>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <CardTitle className="text-base">Report: {agent.name} on {r.asset}</CardTitle><Badge>non-trading</Badge>
        <span className="ml-auto text-xs text-mute">{new Date(r.at).toLocaleString()}{state?.busy ? ", refreshing..." : ""}</span>
      </CardHeader>
      <CardBody className="space-y-4">
        <ul className="list-disc space-y-1 pl-4 text-sm" data-testid="report-summary">{r.summary.map((s, i) => <li key={i}>{s}</li>)}</ul>
        <div className="grid gap-4 md:grid-cols-2">
          {r.sections.map((s) => (
            <div key={s.tool} className="rounded border border-line bg-ink/40 p-3" data-section={s.tool}>
              <div className="mb-1 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-mute">{s.title}<Badge tone={s.status === "ok" ? "gain" : "loss"}>{s.status}</Badge></div>
              {s.error && <p className="text-sm text-loss">{s.error}</p>}
              {s.lines.map((l, i) => <p key={i} className="mb-1 text-sm leading-relaxed">{l}</p>)}
              {s.items && s.items.length > 0 && <ul className="mt-1 space-y-1.5 text-sm">{s.items.map((it, i) => (
                <li key={i}><a className="underline-offset-2 hover:underline" href={it.url} target="_blank" rel="noreferrer noopener">{it.text}</a><div className="text-[11px] text-mute">{it.meta}</div></li>))}</ul>}
            </div>
          ))}
        </div>
        <p className="text-xs text-mute">Facts from Rex's own endpoints, no model and no advice. News is listed as published. Prices on NSE are delayed or the last close.</p>
      </CardBody>
    </Card>
  );
}
