"use client";
import { SavedResult } from "@/components/saved-result";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Wand2, Lock } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHead } from "@/components/page-head";
import { EquityChart } from "@/components/charts";
import { MetricsGrid, TradeTable } from "@/components/results-bits";
import { Explain } from "@/components/explain";
import { TrackPanel } from "@/components/tracker-bits";
import { useRex } from "@/lib/store";
import { post } from "@/lib/api";
import type { Refine } from "@/lib/types";
import { pct } from "@/lib/utils";

function Results() {
  const { backtest: b, setRefine } = useRex();
  const router = useRouter();
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  if (!b) return (
    <div><PageHead title="Backtest results" />
      <Alert tone="info" title="No backtest yet">Run one in the <Link className="underline" href="/build">Strategy builder</Link>.</Alert></div>
  );
  const { spec, result: r } = b;
  const refine = async () => {
    setBusy(true); setErr(null);
    try { const refined = await post<Refine>("/refine", { ticker: spec.ticker, template: spec.template, params: spec.params, exits: spec.exits, cost_bps: spec.cost_bps, ...(spec.cost_model && spec.cost_model !== "bps" ? { cost_model: spec.cost_model, slippage_bps: spec.slippage_bps ?? undefined, trade_value: spec.trade_value ?? undefined } : {}) }); setRefine(refined); router.push(`/refine${refined.saved_result_id ? `?id=${refined.saved_result_id}` : ""}`); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };
  const w = (k: "train" | "locked" | "full") => r[k];
  const lbl = { train: "Train (tuning period)", locked: "Locked final year", full: "Full period" } as const;
  return (
    <div>
      <PageHead title={`${spec.ticker} · ${spec.template.replace("_", " ")}`} sub={`${r.full.start} to ${r.full.end}. Cost ${String(Math.round(spec.cost_bps * 10) / 10)} bps per round trip. Benchmark is buy & hold with the same data.`}
        right={<div className="flex gap-2"><Button asChild variant="outline"><Link href={`/lab${b.saved_result_id ? `?id=${b.saved_result_id}` : ""}`}>Test in Lab</Link></Button><Button onClick={refine} disabled={busy}><Wand2 className="h-4 w-4" />{busy ? "Refining (grid search)…" : "Refine this strategy"}</Button></div>} />
      {err && <Alert title="Refine failed" className="mb-4">{err}</Alert>}
      <Tabs defaultValue="full">
        <TabsList><TabsTrigger value="train">Train</TabsTrigger><TabsTrigger value="locked"><Lock className="mr-1 h-3 w-3" />Locked year</TabsTrigger><TabsTrigger value="full">Full</TabsTrigger></TabsList>
        {(["train", "locked", "full"] as const).map((k) => (
          <TabsContent key={k} value={k} className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm text-mute"><Badge tone={k === "locked" ? "rex" : "neutral"}>{lbl[k]}</Badge>{w(k).start} to {w(k).end}
              <span>· strategy {pct(w(k).metrics.total_return)} vs buy & hold {pct(w(k).benchmark.total_return)}</span></div>
            <MetricsGrid w={w(k)} />
            <Card><CardHeader><CardTitle>Equity curve vs buy & hold</CardTitle><CardDesc>Growth of 1.00 invested at the start of this window, with drawdown below.</CardDesc></CardHeader>
              <CardBody><EquityChart w={w(k)} /></CardBody></Card>
            <Card><CardHeader><CardTitle>Trades ({w(k).trades.length})</CardTitle></CardHeader><CardBody><TradeTable trades={w(k).trades} /></CardBody></Card>
          </TabsContent>
        ))}
      </Tabs>
      <div className="mt-5"><TrackPanel ticker={spec.ticker} template={spec.template} cost_bps={spec.cost_bps} cost_model={spec.cost_model} slippage_bps={spec.slippage_bps} trade_value={spec.trade_value} variants={[{ label: "original", title: "Your strategy", params: spec.params, exits: spec.exits }]} /></div>
      {b.notes?.length > 0 && <Card className="mt-5"><CardBody><ul className="list-inside list-disc space-y-1 text-xs text-mute">{b.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></CardBody></Card>}
      <Explain kind="backtest" facts={{ spec, full: r.full.metrics, locked: r.locked.metrics, benchmark_full: r.full.benchmark, benchmark_locked: r.locked.benchmark }} />
    </div>
  );
}

export default function ResultRoute() { return <SavedResult market="nse" kind="backtest"><Results /></SavedResult>; }
