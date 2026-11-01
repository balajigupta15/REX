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
import { CryptoTrackPanel } from "@/components/crypto-bits";
import { useCrypto, IV_LABEL, unitOf, type CRefine } from "@/lib/crypto";
import { post } from "@/lib/api";
import { pct } from "@/lib/utils";

function CryptoResults() {
  const { backtest: b, setRefine } = useCrypto();
  const router = useRouter();
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  if (!b) return (
    <div><PageHead title="Crypto backtest results" />
      <Alert tone="info" title="No crypto backtest yet">Run one in the <Link className="underline" href="/crypto">Crypto Build</Link> page.</Alert></div>
  );
  const { spec, result: r, data } = b;
  const unit = unitOf(spec.interval);
  const refine = async () => {
    setBusy(true); setErr(null);
    try { const refined = await post<CRefine>("/crypto/refine", { symbol: spec.symbol, interval: spec.interval, template: spec.template, params: spec.params, exits: spec.exits, cost_bps: spec.cost_bps }); setRefine(refined); router.push(`/crypto/refine${refined.saved_result_id ? `?id=${refined.saved_result_id}` : ""}`); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };
  const w = (k: "train" | "locked" | "full") => r[k];
  const lbl = { train: "Train (tuning period)", locked: `Locked final period`, full: "Full period" } as const;
  return (
    <div>
      <PageHead title={`${spec.symbol.replace("USDT", "")}/USDT · ${spec.template.replace("_", " ")} · ${IV_LABEL[spec.interval]}`}
        sub={`${r.full.start} to ${r.full.end} (UTC). Cost ${spec.cost_bps} bps per round trip. Benchmark is buy & hold of the same coin. Hold and counts are in ${unit}.`}
        right={<Button onClick={refine} disabled={busy} data-testid="refine-btn"><Wand2 className="h-4 w-4" />{busy ? "Refining (grid search)…" : "Refine this strategy"}</Button>} />
      {err && <Alert title="Refine failed" className="mb-4">{err}</Alert>}
      <p className="mb-4 text-xs text-mute" data-testid="data-line">Data: Binance {data.interval_label} candles, {data.bars.toLocaleString()} closed candles, {data.first} to {data.last} UTC, source {data.source}. Locked: {data.locked_rule}, from {data.locked_from}.</p>
      <Tabs defaultValue="full">
        <TabsList><TabsTrigger value="train">Train</TabsTrigger><TabsTrigger value="locked"><Lock className="mr-1 h-3 w-3" />Locked final period</TabsTrigger><TabsTrigger value="full">Full</TabsTrigger></TabsList>
        {(["train", "locked", "full"] as const).map((k) => (
          <TabsContent key={k} value={k} className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm text-mute"><Badge tone={k === "locked" ? "rex" : "neutral"}>{lbl[k]}</Badge>{w(k).start} to {w(k).end}
              <span>· strategy {pct(w(k).metrics.total_return)} vs buy & hold {pct(w(k).benchmark.total_return)}</span></div>
            <MetricsGrid w={w(k)} unit={unit} />
            <Card><CardHeader><CardTitle>Equity curve vs buy & hold</CardTitle><CardDesc>Growth of 1.00 invested at the start of this window, with drawdown below. Long only, one position at a time.</CardDesc></CardHeader>
              <CardBody><EquityChart w={w(k)} /></CardBody></Card>
            <Card><CardHeader><CardTitle>Trades ({w(k).trades.length})</CardTitle></CardHeader><CardBody><TradeTable trades={w(k).trades} unit={spec.interval === "1d" ? "Days" : "Candles"} /></CardBody></Card>
          </TabsContent>
        ))}
      </Tabs>
      <div className="mt-5"><CryptoTrackPanel symbol={spec.symbol} interval={spec.interval} template={spec.template} cost_bps={spec.cost_bps} variants={[{ label: "original", title: "Your strategy", params: spec.params, exits: spec.exits }]} /></div>
      {b.memory?.headline && <Card className="mt-5" data-testid="saved-memory"><CardBody className="text-xs text-mute">Saved to crypto memory: {b.memory.headline}</CardBody></Card>}
      {b.notes?.length > 0 && <Card className="mt-5"><CardBody><ul className="list-inside list-disc space-y-1 text-xs text-mute">{b.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></CardBody></Card>}
    </div>
  );
}

export default function ResultRoute() { return <SavedResult market="crypto" kind="backtest"><CryptoResults /></SavedResult>; }
