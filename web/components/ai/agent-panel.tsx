"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Card, CardBody } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Select, Textarea, Label } from "@/components/ui/input";
import { Alert, Skeleton } from "@/components/ui/misc";
import { aiGet, aiPost, errText, type Run, type Status } from "./lib";
import { TradingDesk } from "./desk";
import { AgentRoster } from "./roster";
import { hireGoal, hireTarget, useAgents } from "./agents";
import { useReportRuns } from "./use-report";
import { ReportDesk, ReportView } from "./report-view";
import { DebatePanel, ExperimentLog, FinalCard, Timeline } from "./run-view";

export function AgentPanel({ status }: { status: Status | null }) {
  const router = useRouter(); const path = usePathname(); const sp = useSearchParams();
  const rid = sp.get("run");
  const [goal, setGoal] = useState("");
  const [rounds, setRounds] = useState("3");
  const [offline, setOffline] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  useEffect(() => { aiGet("/runs").then((r) => setHistory(r.runs)).catch(() => {}); }, [rid]);
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const resumed = useRef<string | null>(null);
  const store = useAgents();
  const reports = useReportRuns(store);
  const sel = store.selected;
  const analyst = sel?.kind === "analyst" ? sel : null;
  // selecting an agent makes it drive the desk: a trading agent shows its last run, an analyst shows its report
  const selectAgent = useCallback((id: string | null) => {
    store.select(id);
    const a = store.agents.find((x) => x.id === id);
    if (a) router.replace(a.kind === "trading" && a.lastRunId ? `${path}?run=${a.lastRunId}` : path);
  }, [store, router, path]);

  // poll the run by id (works after a refresh because the id is in the URL)
  useEffect(() => {
    if (!rid) { setRun(null); return; }
    let alive = true; let timer: ReturnType<typeof setTimeout> | undefined; let first = true;
    const tick = async () => {
      try {
        const r = await aiGet<Run>(`/runs/${rid}`);
        if (!alive) return;
        setRun(r); setErr(null); setLoading(false); first = false;
        if (r.status === "interrupted" && resumed.current !== rid) {          // the server restarted or the worker died: continue from the saved steps
          resumed.current = rid;
          aiPost<Run>(`/runs/${rid}/resume`).then((x) => alive && setRun(x)).catch((e) => alive && setErr(errText(e)));
        }
        if (["queued", "running", "interrupted"].includes(r.status)) timer = setTimeout(tick, 1200);
      } catch (e) {
        if (!alive) return;
        setErr(errText(e)); setLoading(false);
        if (first === false) timer = setTimeout(tick, 3000);               // transient failure while running: keep trying
      }
    };
    setLoading(true); tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [rid]);

  const start = useCallback(async (g: string, target?: unknown, preferences?: { style: string; max_drawdown_pct: number }): Promise<string | null> => {
    if (g.trim().length < 3) { setErr("Describe what you want the agent to find, for example: Find a BTC strategy on 1h candles that won't overfit."); return null; }
    setBusy(true); setErr(null); setRun(null);
    try {
      const r = await aiPost<Run>("/runs", { goal: g.trim(), rounds: Number(rounds), offline, ...preferences, ...(target ? { target } : {}) });
      resumed.current = null;
      router.replace(`${path}?run=${r.id}`); setRun(r);
      return r.id;
    } catch (e) { setErr(errText(e)); return null; } finally { setBusy(false); }
  }, [rounds, offline, router, path]);

  const running = !!run && ["queued", "running", "interrupted"].includes(run.status);
  const crypto = status?.examples.filter((e) => e.market === "crypto") ?? [];
  const nse = status?.examples.filter((e) => e.market !== "crypto") ?? [];
  return (
    <div className="space-y-4">
      <Card><CardBody className="space-y-3">
        <FormField><Label htmlFor="goal">What should the agent research?</Label>
          <Textarea id="goal" rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Find a BTC strategy on 1h candles that won't overfit" disabled={running} /></FormField>
        <div className="flex flex-wrap items-center gap-2">
          {crypto.map((e) => <Button key={e.text} variant="outline" size="sm" disabled={running} onClick={() => setGoal(e.text)}>{e.text}</Button>)}
          {nse.map((e) => <Button key={e.text} variant="ghost" size="sm" disabled={running} onClick={() => setGoal(e.text)}>NSE: {e.text}</Button>)}
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <FormField><Label htmlFor="rounds">Research rounds</Label><Select id="rounds" value={rounds} onChange={(e) => setRounds(e.target.value)} disabled={running} className="w-24">{(status?.round_options ?? [1, 2, 3, 4]).map((n) => <option key={n}>{n}</option>)}</Select></FormField>
          <label className="flex items-center gap-2 pb-2 text-sm text-mute"><input type="checkbox" checked={offline} onChange={(e) => setOffline(e.target.checked)} disabled={running} />Offline mode (no Gemini calls)</label>
          <Button onClick={() => { store.select(null); start(goal); }} disabled={busy || running} className="ml-auto">{busy ? "Starting..." : running ? "Running..." : "Run research agent"}</Button>
        </div>
        {status && status.mode === "offline" && !offline && <p className="text-xs text-mute">Gemini is not available right now, so runs use offline mode: new strategy code is composed locally from indicator blocks and searched on the train period. It is labeled that way in the results.</p>}
      </CardBody></Card>

      <FormField><Label htmlFor="run-history">Past research runs</Label><Select id="run-history" value={rid ?? ""} onChange={(e) => router.replace(e.target.value ? `${path}?run=${e.target.value}` : path)}><option value="">New research run</option>{history.map((r) => <option key={r.id} value={r.id}>{r.status} · {r.goal}</option>)}</Select></FormField>
      <AgentRoster store={store} status={status} run={run} rid={rid} busy={busy} reports={reports.runs}
        onRunTrading={(a) => start(hireGoal(a), hireTarget(a), { style: a.style, max_drawdown_pct: a.maxDd })} onRunReport={(a) => { router.replace(path); reports.run(a); }} onOpen={(id) => router.replace(`${path}?run=${id}`)} onSelect={selectAgent} />
      {analyst ? <ReportDesk agent={analyst} state={reports.runs[analyst.id]} /> : <TradingDesk run={run} agentName={sel?.name} />}
      {err && <Alert title="Something went wrong">{err}</Alert>}
      {analyst && <ReportView agent={analyst} state={reports.runs[analyst.id]} />}
      {!analyst && loading && !run && <div className="space-y-2"><Skeleton className="h-24" /><Skeleton className="h-40" /></div>}
      {!analyst && !rid && !run && !err && <p className="text-sm text-mute" data-testid="empty-state">No run yet. Pick an example above or describe a goal, then run the agent. Each run gets an id in the address bar, so you can refresh or come back to it.</p>}
      {!analyst && run && (
        <div className="space-y-4" data-testid="run">
          <div className="flex flex-wrap items-center gap-2 text-sm"><span className="text-mute">Goal:</span><span>{run.goal}</span>
            <Badge tone={run.status === "done" ? "gain" : run.status === "failed" ? "loss" : "warn"}>{run.status}</Badge>
            <Badge>{run.mode === "offline" ? "offline mode" : "Gemini"}</Badge><Badge>{run.target.symbol} {run.target.interval}</Badge>
            <span className="ml-auto font-mono text-xs text-mute">run {run.id}</span></div>
          {run.notices.map((n, i) => <Alert key={i} tone="info">{n}</Alert>)}
          {run.error && <Alert title={run.error.code}>{run.error.message}{run.error.detail ? ` (${run.error.detail})` : ""}{run.error.retryable && <Button size="sm" variant="outline" className="ml-3" onClick={() => aiPost<Run>(`/runs/${run.id}/resume`).then(setRun).catch((e) => setErr(errText(e)))}>Retry</Button>}</Alert>}
          {run.final && <FinalCard f={run.final} run={run} />}
          <DebatePanel debate={run.debate} risk={run.risk} />
          <ExperimentLog rows={run.experiments} />
          <Timeline steps={run.steps} running={running} max={run.max_steps} />
        </div>
      )}
    </div>
  );
}
