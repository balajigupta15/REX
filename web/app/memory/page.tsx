"use client";
import { useState } from "react";
import { Brain, Database, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { PageHead } from "@/components/page-head";
import { Metric } from "@/components/metric";
import { SampleBadge } from "@/components/sample-badge";
import { BackendChip } from "@/components/backend-chip";
import { ProofChart } from "@/components/charts";
import { Pager, ResultHistory, StrategyHistory } from "@/components/history";
import { post } from "@/lib/api";
import { useGet, useMemStatus } from "@/lib/hooks";
import type { Proof } from "@/lib/types";
import { inr, pctPlain, pct, pretty, tone, cn } from "@/lib/utils";

const kindTone: Record<string, any> = { setup_strong: "gain", setup_weak: "loss", refine_overfit: "loss", refine_ok: "gain", refine_no_change: "neutral", refine_inconclusive: "warn" };

export default function Memory() {
  const { status } = useMemStatus();
  const [lessonPage, setLessonPage] = useState(0);
  const [runPage, setRunPage] = useState(0);
  const proof = useGet<Proof>("/memory/proof");
  const [kind, setKind] = useState<string>("all");
  const lessons = useGet<any>(`/memory/lessons?limit=30&offset=${lessonPage * 30}${kind !== "all" ? `&kind=${encodeURIComponent(kind)}` : ""}`);
  const runs = useGet<any>(`/memory/runs?limit=20&offset=${runPage * 20}`);
  const [seeding, setSeeding] = useState(false); const [msg, setMsg] = useState<string | null>(null);
  const seed = async () => { setSeeding(true); setMsg(null); try { const r = await post("/memory/seed?reset=true"); setMsg(`Seeded: ${JSON.stringify(r).slice(0, 160)}`); lessons.reload(); runs.reload(); } catch (e: any) { setMsg(e.message); } finally { setSeeding(false); } };
  const all: any[] = lessons.data?.lessons ?? [];
  const kinds = Array.from(new Set<string>((lessons.data?.counts ?? []).map((l: any) => l.kind)));
  const shown = all.filter((l) => kind === "all" || l.kind === kind);
  const p = proof.data; const m = status?.meko;
  return (
    <div className="space-y-6">
      <PageHead title="Memory & lessons" sub="Everything Rex has learned: remembered runs, lessons generated from measured statistics, and the walk-forward test that asks whether following that memory actually helps." right={<BackendChip status={status} />} />

      <Card><CardHeader><div className="flex items-center gap-2"><Brain className="h-4 w-4 text-rex" /><CardTitle>Memory backend</CardTitle></div></CardHeader><CardBody className="space-y-3 text-sm">
        <div className="grid gap-3 sm:grid-cols-4">
          <Metric label="Remembered runs" value={status?.runs ?? "…"} /><Metric label="Lessons" value={status?.lessons ?? "…"} /><Metric label="Stored trades" value={status?.trades?.toLocaleString() ?? "…"} /><Metric label="Journal round trips" value={status?.journal_trades ?? "…"} />
        </div>
        {m && (
          <div className={cn("rounded-lg border p-3", m.connected ? "border-gain/40 bg-gain/5" : "border-line bg-ink")} data-testid="meko-panel">
            <div className="flex items-center gap-2 font-medium">{m.connected ? <CheckCircle2 className="h-4 w-4 text-gain" /> : m.enabled ? <XCircle className="h-4 w-4 text-loss" /> : <Database className="h-4 w-4 text-mute" />}
              {m.connected ? "Meko is connected and is the live memory mirror" : m.enabled ? "Meko token is set but the server is not reachable" : "Meko not configured: lessons live in local SQLite"}</div>
            <p className="mt-1 text-xs text-mute">{m.note}. SQLite is always the source of truth, so the app works offline.</p>
            {m.enabled && <div className="mt-2 flex flex-wrap gap-1.5 text-xs"><Badge>{m.endpoint}</Badge><Badge>agent_id: {m.agent_id}</Badge><Badge>{m.tool_count} MCP tools</Badge>{m.memory_tools.map((t) => <Badge key={t} tone="sky">{t}</Badge>)}<Badge tone="gain">writes ok: {m.writes_ok}</Badge>{m.writes_failed > 0 && <Badge tone="loss">failed: {m.writes_failed}</Badge>}</div>}
            {m.last_error && <p className="mt-2 text-xs text-loss">last error: {m.last_error}</p>}
          </div>
        )}
      </CardBody></Card>

      <Card><CardHeader><div className="flex flex-wrap items-center gap-2"><CardTitle>Proof: memory OFF vs ON</CardTitle>{p?.is_sample && <SampleBadge />}</div>
        <CardDesc>Walk-forward on your journal: every trade is checked using only trades that closed before it. Memory OFF takes every trade. Memory ON follows Rex&apos;s check (wait = skip, reduce size = half). The first 10 trades per direction are warm-up and taken at full size.</CardDesc></CardHeader>
        <CardBody className="space-y-4">
          {proof.loading && <Skeleton className="h-80" />}
          {proof.error && <Alert tone="info" title="No proof yet">{proof.error.includes("journal") || proof.error.includes("422") ? "Import or load a journal first." : proof.error}</Alert>}
          {p && (<>
            <ProofChart series={p.series} />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="proof-metrics">
              <Metric label="Total P&L" value={<span><span className="text-mute">{inr(p.off.total_pnl)}</span> → <span className={tone(p.on.total_pnl)}>{inr(p.on.total_pnl)}</span></span>} valueClass="text-lg" sub="OFF → ON" />
              <Metric label="Max drawdown" value={<span><span className="text-mute">{inr(p.off.max_drawdown)}</span> → <span className="text-gain">{inr(p.on.max_drawdown)}</span></span>} valueClass="text-lg" sub="OFF → ON" />
              <Metric label="Win rate" value={<span><span className="text-mute">{pctPlain(p.off.win_rate, 1)}</span> → {pctPlain(p.on.win_rate, 1)}</span>} valueClass="text-lg" sub={`${p.on.trades_taken} of ${p.n_trades} trades taken`} />
              <Metric label="Decisions" value={`${p.decisions.proceed} / ${p.decisions.reduce_size} / ${p.decisions.wait}`} valueClass="text-lg" sub={`proceed / reduce / wait · ${p.decisions.warmup} warm-up`} />
            </div>
            <Alert tone={p.on.total_pnl >= p.off.total_pnl ? "gain" : "warn"} title="Read this honestly">
              Skipped (“wait”) trades: {p.skipped.n}, which would have made {inr(p.skipped.pnl_if_taken)}. Half-sized (“reduce”) trades: {p.reduced.n}, {inr(p.reduced.pnl_full_size)} at full size vs {inr(p.reduced.pnl_half_size)} at half size.
              {" "}Max drawdown {p.on.max_drawdown > p.off.max_drawdown ? "improved" : "did not improve"}; total P&amp;L {p.on.total_pnl >= p.off.total_pnl ? "improved" : "was lower"}. {p.notes.join(" ")}
            </Alert>
          </>)}
        </CardBody></Card>

      <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><div><CardTitle>Lessons ({all.length})</CardTitle><CardDesc>Templated from measured statistics. No LLM involved.</CardDesc></div>
        <div className="flex flex-wrap gap-1.5"><Button size="sm" variant={kind === "all" ? "default" : "outline"} onClick={() => { setKind("all"); setLessonPage(0); }}>all</Button>{kinds.map((k) => <Button key={k} size="sm" variant={kind === k ? "default" : "outline"} onClick={() => { setKind(k); setLessonPage(0); }}>{pretty(k)}</Button>)}</div></div></CardHeader>
        <CardBody>{lessons.loading && <Skeleton className="h-40" />}{lessons.error && <Alert title="Could not load lessons">{lessons.error}</Alert>}
          {!lessons.loading && !all.length && !lessons.error && <p className="text-sm text-mute">No lessons yet. Seed the demo memory below or run a backtest.</p>}
          <ul className="space-y-2">{shown.map((l) => <li key={l.id} className="flex gap-3 rounded-lg border border-line bg-ink p-3 text-sm"><Badge tone={kindTone[l.kind] ?? "neutral"} className="h-fit whitespace-nowrap">{pretty(l.kind)}</Badge><div><p className="text-text/90">{l.text}</p><p className="mt-0.5 text-xs text-mute">{l.ticker} · {l.template}</p></div></li>)}</ul>
          <Pager page={lessonPage} total={lessons.data?.total ?? 0} size={30} onChange={setLessonPage} loading={lessons.loading} /></CardBody></Card>

      <ResultHistory /><StrategyHistory />
      <Card><CardHeader><CardTitle>Recent runs</CardTitle></CardHeader><CardBody>{runs.loading ? <Skeleton className="h-32" /> : (
        <Table><thead><tr><Th>Kind</Th><Th>Stock</Th><Th>Template</Th><Th>Train</Th><Th>Locked</Th><Th>Flag</Th></tr></thead><tbody>
          {(Array.isArray(runs.data) ? runs.data : runs.data?.runs ?? []).map((r: any) => <tr key={r.id}><Td>{r.kind}</Td><Td>{r.ticker}</Td><Td>{r.template}</Td>
            <Td className="num">{r.train ? pct(r.train.total_return) : "n/a"}</Td>
            <Td className="num">{r.locked ? pct(r.locked.total_return) : "n/a"}</Td><Td>{r.flag ? <Badge tone={r.flag === "overfit" ? "loss" : r.flag === "ok" ? "gain" : "neutral"}>{r.flag}</Badge> : ""}</Td></tr>)}
        </tbody></Table>)}
        {runs.error && <Alert>{runs.error}</Alert>}
        <div className="mt-3 flex justify-between"><Button size="sm" variant="outline" disabled={!runPage || runs.loading} onClick={() => setRunPage((p) => p - 1)}>Previous runs</Button><span className="text-xs">Page {runPage + 1}</span><Button size="sm" variant="outline" disabled={runs.loading || (runs.data?.length ?? 0) < 20} onClick={() => setRunPage((p) => p + 1)}>Next runs</Button></div>
      </CardBody></Card>

      <div className="flex flex-wrap items-center gap-3">
        <Dialog><DialogTrigger asChild><Button variant="outline" disabled={seeding}><RefreshCw className={cn("h-4 w-4", seeding && "animate-spin")} />{seeding ? "Seeding (runs 54 backtests)…" : "Re-seed demo memory"}</Button></DialogTrigger>
          <DialogContent><DialogTitle>Reset and re-seed demo memory?</DialogTitle><DialogDescription>This deletes remembered runs and lessons from the local database and rebuilds 54 demo runs on the 10 cached stocks. Meko is not touched. It also clears any imported journal (the whole local database is rebuilt), so re-load the journal afterwards.</DialogDescription>
            <div className="mt-5 flex justify-end gap-2"><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><DialogClose asChild><Button onClick={seed}>Reset and seed</Button></DialogClose></div></DialogContent></Dialog>
        {msg && <span className="text-xs text-mute">{msg}</span>}
      </div>
    </div>
  );
}
