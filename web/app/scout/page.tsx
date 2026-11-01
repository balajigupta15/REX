"use client";
import Link from "next/link";
import { ArrowRight, Radar } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, Skeleton } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { useGet } from "@/lib/hooks";
import type { Scout, ScoutRow } from "@/lib/types";
import { cn, pp, tone } from "@/lib/utils";

const LABEL: Record<ScoutRow["label"], any> = { strong: "gain", constructive: "sky", neutral: "neutral", weak: "loss" };

function Bar({ v, max, cls }: { v: number; max: number; cls: string }) {
  return <div className="h-1.5 w-full overflow-hidden rounded bg-panel2"><div className={cn("h-full rounded", cls)} style={{ width: `${Math.max(0, Math.min(100, (v / max) * 100))}%` }} /></div>;
}

export default function ScoutPage() {
  const { data, error, loading } = useGet<Scout>("/scout");
  return (
    <div className="space-y-6">
      <PageHead title="Scout" sub="A rule-based ranking of the cached NSE watchlist by trend, momentum and volume. It tells you where to look first, with reasons. It is not a prediction." />
      {loading && <Skeleton className="h-96" />}
      {error && <Alert title="Scout failed">{error}</Alert>}
      {data && (<>
        <div className="flex flex-wrap items-center gap-2 text-xs text-mute"><Radar className="h-4 w-4 text-rex" /> {data.ranked.length} of {data.universe} stocks ranked · prices as of {data.as_of}
          {data.errors.map((e) => <Badge key={e.ticker} tone="warn">{e.ticker}: {e.error}</Badge>)}</div>
        <div className="space-y-3">
          {data.ranked.map((r) => (
            <Card key={r.ticker} data-testid="scout-row" data-symbol={r.symbol}>
              <CardBody className="grid gap-4 md:grid-cols-[auto_1fr_auto]">
                <div className="flex items-center gap-4 md:w-56">
                  <div className="num grid h-10 w-10 place-items-center rounded-lg bg-panel2 text-lg font-bold text-rex">{r.rank}</div>
                  <div><div className="text-lg font-semibold">{r.symbol}</div><div className="num text-xs text-mute">₹{r.close.toLocaleString("en-IN")}</div></div>
                  <div className="ml-auto text-right md:ml-2"><div className="num text-2xl font-bold">{r.score.toFixed(0)}</div><Badge tone={LABEL[r.label]}>{r.label}</Badge></div>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="grid grid-cols-3 gap-3 text-xs text-mute">
                    <div>Trend {r.components.trend.toFixed(0)}/40<Bar v={r.components.trend} max={40} cls="bg-sky" /></div>
                    <div>Momentum {r.components.momentum.toFixed(0)}/40<Bar v={r.components.momentum} max={40} cls="bg-rex" /></div>
                    <div>Volume {r.components.volume.toFixed(0)}/20<Bar v={r.components.volume} max={20} cls="bg-gain" /></div>
                  </div>
                  <ul className="list-disc space-y-0.5 pl-5 text-text/90">{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
                  <div className="num flex flex-wrap gap-x-4 text-xs text-mute"><span>3m <b className={tone(r.metrics.ret_3m_pct)}>{pp(r.metrics.ret_3m_pct, 1)}</b></span><span>6m <b className={tone(r.metrics.ret_6m_pct)}>{pp(r.metrics.ret_6m_pct, 1)}</b></span>
                    <span>5d <b className={tone(r.metrics.ret_5d_pct)}>{pp(r.metrics.ret_5d_pct, 1)}</b></span><span>vol {r.metrics.volume_ratio_5d_60d.toFixed(2)}x</span><span>vs 200d <b className={tone(r.metrics.pct_vs_200dma)}>{pp(r.metrics.pct_vs_200dma, 1)}</b></span></div>
                </div>
                <div className="flex items-center gap-2 md:flex-col md:items-stretch">
                  <Link href={`/research?t=${r.symbol}`} className="inline-flex items-center justify-center gap-1 rounded-md border border-line bg-panel2 px-3 py-1.5 text-sm hover:text-text">Research <ArrowRight className="h-3.5 w-3.5" /></Link>
                  <Link href={`/check?symbol=${r.symbol}`} className="inline-flex items-center justify-center gap-1 rounded-md border border-line px-3 py-1.5 text-sm text-mute hover:text-text">Pre-trade check</Link>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
        <Card><CardHeader><CardTitle>How the score works</CardTitle><CardDesc>Fixed rules, shown in full. No LLM, no hidden weights.</CardDesc></CardHeader>
          <CardBody><ul className="list-disc space-y-1 pl-5 text-sm text-mute">{data.rules.map((x, i) => <li key={i}>{x}</li>)}</ul><p className="mt-3 text-xs text-mute">{data.disclaimer}</p></CardBody></Card>
      </>)}
    </div>
  );
}
