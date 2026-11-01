"use client";
import { useEffect, useState } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { type Run, type Status } from "./lib";
import { completedResult, TOOLS, hireGoal, hireTarget, isLive, assetLabel, type Agent, type Store } from "./agents";
import { Gallery } from "./gallery";
import { DeployBox, LiveBadge, useDeployments } from "./deploy";
import type { ReportRun } from "./use-report";

export type { Hire } from "./agents";
export { hireGoal, hireTarget } from "./agents";

/** Your agents: hired from the gallery or built by you. Selecting a card makes it the one driving the desk. Trading agents run the research swarm and can then be deployed as a paper watch;
 *  non-trading agents run a report and never show a deploy option. Everything is stored in this browser only. */
export function AgentRoster({ store, status, run, rid, busy, reports, onRunTrading, onRunReport, onOpen, onSelect }: {
  store: Store; status: Status | null; run: Run | null; rid: string | null; busy: boolean; reports: Record<string, ReportRun>;
  onRunTrading: (a: Agent) => Promise<string | null>; onRunReport: (a: Agent) => void; onOpen: (runId: string) => void; onSelect: (id: string | null) => void;
}) {
  const { agents, ready, selId, patch, remove, add } = store;
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState<string[]>([]);
  const dep = useDeployments(store);

  // when the run that belongs to an agent finishes, remember its result (and the strategy version, which is what a deployment needs) on the card
  useEffect(() => {
    if (!run) return;
    const result = completedResult(run);
    if (!result) return;
    const a = agents.find((h) => h.lastRunId === run.id);
    if (!a || (a.last?.status === result.status && a.last?.headline === result.headline)) return;
    patch(a.id, { last: result });
  }, [run, agents, patch]);

  const hire = (a: Agent) => { add(a); onSelect(a.id); };
  const runTrading = async (a: Agent) => {
    store.select(a.id);
    const id = await onRunTrading(a);
    if (id) patch(a.id, { lastRunId: id, last: undefined });
  };
  const removeAgent = (a: Agent) => {
    setLeaving((l) => [...l, a.id]);
    setTimeout(() => { remove(a.id); setLeaving((l) => l.filter((x) => x !== a.id)); }, 160);
  };
  const anyLive = agents.some((a) => a.deploy?.status === "live");

  return (
    <Card data-testid="roster">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <div><CardTitle>Your agents</CardTitle><p className="text-xs text-mute">Hire a ready-made agent or build your own, then select one to drive the desk below. Agents are saved in this browser only.</p></div>
        <div className="flex items-center gap-2">
          {anyLive && <Badge tone="gain"><span className="h-1.5 w-1.5 rounded-full bg-gain motion-safe:animate-pulse" />paper deployment running</Badge>}
          <Button variant="outline" size="sm" onClick={() => setOpen((x) => !x)} data-testid="hire-open" aria-expanded={open}>{open ? "Close" : "Hire an agent"}</Button>
        </div>
      </CardHeader>
      <CardBody className="space-y-3">
        {open && <Gallery status={status} onHire={hire} />}
        {!ready && <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" data-testid="roster-loading"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>}
        {ready && agents.length === 0 && !open && <p className="min-h-10 text-sm text-mute" data-testid="roster-empty">No agents hired yet. Hire one from the gallery to give Rex a standing assignment.</p>}
        {dep.notice && <p className="rounded border border-warn/40 bg-warn/10 px-3 py-1.5 text-xs text-warn" role="alert">{dep.notice.msg} <button className="ml-2 underline" onClick={dep.clearNotice}>Dismiss</button></p>}
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3" data-testid="agent-list">
          {agents.map((h) => {
            const trading = h.kind === "trading";
            const selected = selId === h.id;
            const mine = trading && !!h.lastRunId && run?.id === h.lastRunId;
            const working = mine && isLive(run);
            const rep = reports[h.id];
            const reporting = !!rep?.busy;
            const hasResult = trading && h.last && !working;
            const st = trading ? (working ? "working" : hasResult ? (h.last!.status === "done" ? "done" : "failed") : "idle") : reporting ? "working" : h.report ? "done" : rep?.err ? "failed" : "idle";
            const dd = h.last?.dd;
            return (
              <div key={h.id} data-testid="agent-card" data-agent-id={h.id} data-kind={h.kind} data-state={st} data-selected={selected}
                className={cn("flex min-h-[10.5rem] flex-col rounded border bg-ink/40 px-3 py-2 transition-all duration-200", leaving.includes(h.id) ? "rex-out" : "rex-in", selected ? "border-text/50 bg-panel2/40" : "border-line hover:border-mute/50")}>
                <div className="flex items-center justify-between gap-2">
                  <button className="min-w-0 truncate rounded text-left text-sm font-medium focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-text/50" aria-pressed={selected} data-testid="agent-select" onClick={() => onSelect(selected ? null : h.id)} title={selected ? "Selected: drives the desk. Click to deselect." : "Select to drive the desk"}>{h.name}</button>
                  <span className="flex shrink-0 items-center gap-1.5 text-[11px] text-mute">
                    {h.deploy && <LiveBadge d={h.deploy} />}
                    <span className={cn("h-1.5 w-1.5 rounded-full", st === "working" ? "bg-sky motion-safe:animate-pulse" : st === "done" ? "bg-gain" : st === "failed" ? "bg-loss" : "bg-line")} />{st}
                  </span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-mute">
                  <Badge tone={trading ? "sky" : "neutral"} data-testid="agent-kind-badge">{trading ? "Trading" : "Non-trading"}</Badge>
                  <span>{h.symbol}{trading ? ` ${h.market === "nse" ? "daily" : h.interval}, ${h.style === "any" ? "any style" : h.style}, max drawdown ${h.maxDd}%` : `, tools: ${h.tools.map((t) => TOOLS.find((x) => x.id === t)?.label.split(" /")[0].toLowerCase()).join(", ")}`}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs text-mute" title={trading ? h.goal : h.role}>{trading ? h.goal : h.role}</p>
                {working && <p className="mt-1 text-xs">On shift: the desk below is running this brief.</p>}
                {hasResult && h.last && (
                  <div className="mt-1 space-y-1 text-xs">
                    <div className="flex flex-wrap items-center gap-1.5"><Badge tone={h.last.decision === "NO RESULT" || h.last.decision === "DO NOT TRADE" ? "loss" : h.last.decision === "WEAK EVIDENCE" ? "warn" : "gain"}>{h.last.decision ?? "done"}</Badge>
                      {dd != null && <Badge tone={dd <= h.maxDd ? "gain" : "loss"} data-testid="dd-check">{`locked drawdown ${dd.toFixed(1)}% ${dd <= h.maxDd ? "within" : "over"} ${h.maxDd}% limit`}</Badge>}</div>
                    <p className="line-clamp-2 text-mute" title={h.last.headline}>{h.last.headline}</p>
                  </div>)}
                {st === "failed" && trading && h.last?.headline && <p className="mt-1 line-clamp-2 text-xs text-loss">{h.last.headline}</p>}
                {!trading && h.report && !reporting && <p className="mt-1 line-clamp-2 text-xs text-text/90" data-testid="agent-report-line">Report ready: {h.report.summary[0]}</p>}
                {!trading && rep?.err && <p className="mt-1 text-xs text-loss">{rep.err}</p>}
                <div className="flex-1" />
                <DeployBox a={h} onDeploy={() => dep.deploy(h)} onStop={() => dep.stop(h)} />
                <div className="mt-2 flex items-center gap-2">
                  {trading
                    ? <Button size="sm" variant="outline" data-testid="agent-run" disabled={busy || isLive(run)} onClick={() => runTrading(h)}>{working ? "Running..." : h.lastRunId ? "Run again" : "Run"}</Button>
                    : <Button size="sm" variant="outline" data-testid="agent-report" disabled={reporting} onClick={() => { onSelect(h.id); onRunReport(h); }}>{reporting ? "Writing..." : h.report ? "Report again" : "Report"}</Button>}
                  {trading && h.lastRunId && rid !== h.lastRunId && <Button size="sm" variant="ghost" onClick={() => { store.select(h.id); onOpen(h.lastRunId!); }}>Open last run</Button>}
                  <Button size="sm" variant="ghost" className="ml-auto" data-testid="agent-remove" disabled={working || reporting || h.deploy?.status === "live"} title={h.deploy?.status === "live" ? "Stop the deployment first" : undefined} onClick={() => removeAgent(h)} aria-label={`Remove ${h.name}`}>Remove</Button>
                </div>
              </div>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
}
