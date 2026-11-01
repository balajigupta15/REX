"use client";
import { useState } from "react";
import { Brain } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/input";
import { Alert, Skeleton } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { Metric } from "@/components/metric";
import { Pager, ResultHistory } from "@/components/history";
import { useGet } from "@/lib/hooks";
import type { StrategyRuns } from "@/lib/types";
import { pp, pretty, cn } from "@/lib/utils";

const tone = (v: string) => (/WON|HELD|WORKED/.test(v) ? "gain" : /LOST|OVERFIT|FADED/.test(v) ? "loss" : /TRACK|INCONCL/.test(v) ? "warn" : "neutral") as any;

export default function CryptoMemory() {
  const [page, setPage] = useState(0);
  const [sym, setSym] = useState("");
  const { data, error } = useGet<StrategyRuns>(`/crypto/memory/strategy-runs?limit=20&offset=${page * 20}${sym ? `&symbol=${sym}` : ""}`);
  return (
    <div className="space-y-5">
      <PageHead title="Crypto memory: last time" sub="Every crypto backtest, refinement and tracked trade Rex remembers, with how real Binance prices moved afterwards. Stored in its own table (market = crypto), apart from your stock memory." />
      <ResultHistory market="crypto" />
      {error && <Alert title="Could not load crypto memory">{error}</Alert>}
      {!data && !error && <Skeleton className="h-48" />}
      {data && (<>
        <div className="grid gap-3 sm:grid-cols-3">
          <Metric label="Remembered crypto runs" value={data.total} />
          <Metric label="Shown" value={data.count} />
          <Metric label="Stored in" value={data.backend} />
        </div>
        <div className="max-w-xs"><Select aria-label="Filter by coin" value={sym} onChange={(e) => { setSym(e.target.value); setPage(0); }} data-testid="mem-coin"><option value="">All coins</option><option value="BTCUSDT">BTC</option><option value="ETHUSDT">ETH</option><option value="SOLUSDT">SOL</option></Select></div>
        {data.runs.length === 0 && <Card><CardBody className="text-sm text-mute" data-testid="mem-empty">No crypto runs remembered yet. Run a crypto backtest and it will show up here.</CardBody></Card>}
        <div className="space-y-3" data-testid="mem-list">
          {data.runs.map((r: any) => (
            <Card key={r.id} data-testid="mem-row"><CardHeader>
              <div className="flex flex-wrap items-center gap-2"><Brain className="h-4 w-4 text-rex" /><CardTitle>{String(r.ticker).replace("USDT", "")}/USDT · {pretty(r.template)}</CardTitle>
                <Badge tone={tone(r.verdict)}>{r.verdict}</Badge><Badge>{pretty(r.kind)}</Badge>{r.interval && <Badge>{r.interval}</Badge>}</div>
              <CardDesc>{r.run_date_label}</CardDesc></CardHeader>
              <CardBody className="space-y-2 text-sm">
                <p>{r.headline}</p>
                <div className="flex flex-wrap gap-4 text-xs text-mute">
                  {([["5 days later", r.fwd5], ["20 days later", r.fwd20], ["Since then", r.since_pct]] as [string, number | null][]).map(([k, x]) => (
                    <span key={k}>{k}: <span className={cn("num font-medium", x == null ? "" : x >= 0 ? "text-gain" : "text-loss")}>{x == null ? "pending" : pp(x, 1)}</span></span>))}
                </div>
                <p className="text-xs"><span className="font-semibold text-rex">Lesson: </span>{r.lesson}</p>
              </CardBody></Card>))}
        </div>
        <Pager page={page} total={data.total} size={20} onChange={setPage} /><p className="text-xs text-mute">{data.note}</p>
      </>)}
    </div>
  );
}
