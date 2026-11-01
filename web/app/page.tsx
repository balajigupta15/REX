"use client";
import Link from "next/link";
import { ArrowRight, FlaskConical, BookOpen, ShieldCheck, Brain, Lock, Radar, FileText } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, Skeleton } from "@/components/ui/misc";
import { Metric } from "@/components/metric";
import { SampleBadge } from "@/components/sample-badge";
import { BackendChip } from "@/components/backend-chip";
import { useGet, useMemStatus } from "@/lib/hooks";
import type { JSummary, Proof } from "@/lib/types";
import { inr, pctPlain, tone, cn } from "@/lib/utils";

const features = [
  { href: "/build", icon: FlaskConical, title: "Test an idea honestly", body: "Pick a template, backtest it with costs, then refine it. The last year of data stays locked until the end, so overfitting shows up as a badge, not a surprise." },
  { href: "/journal", icon: BookOpen, title: "See your repeat mistakes", body: "Drop in a broker tradebook. Rex pairs fills into round trips, tags each entry's market context and finds the setups you keep losing in." },
  { href: "/check", icon: ShieldCheck, title: "Check before you click", body: "Symbol, date, side. Rex recalls your most similar past trades and says proceed, reduce size or wait, with the reasons and the sample size. A Challenger argues the other side and can veto." },
  { href: "/scout", icon: Radar, title: "Scout where to look", body: "A transparent, rule-based ranking of the watchlist by trend, momentum and volume, with the reasons for every rank." },
  { href: "/research", icon: FileText, title: "Read a stock in plain English", body: "Five years of trend, biggest moves, volatility, support and resistance and earnings reactions for any NSE stock in the cache." },
];

export default function Home() {
  const { status, error } = useMemStatus();
  const j = useGet<JSummary>("/trades/summary");
  const p = useGet<Proof>("/memory/proof");
  const apiDown = error && status == null;
  const isSample = j.data?.source_used === "sample";
  return (
    <div className="space-y-10">
      <section className="rounded-md border border-line bg-panel p-6 sm:p-8">
        <div className="mb-4 flex flex-wrap gap-2"><Badge tone="rex">Paper only</Badge><Badge tone="neutral">Educational</Badge><BackendChip status={status} /></div>
        <h1 className="max-w-3xl text-3xl font-semibold leading-tight tracking-tight">Rex: the trading coach <span className="text-mute">with a memory</span></h1>
        <p className="mt-3 max-w-2xl text-base text-mute">Most tools forget everything the moment you close the tab. Rex remembers every backtest, every refinement and every trade you took, and brings the relevant lessons back right before you repeat a mistake.</p>
        <div className="mt-7 flex flex-wrap gap-3">
          <Button asChild size="lg"><Link href="/journal">Try the journal demo <ArrowRight className="h-4 w-4" /></Link></Button>
          <Button asChild size="lg" variant="outline"><Link href="/build">Build a strategy</Link></Button>
          <Button asChild size="lg" variant="ghost"><Link href="/memory">See the proof</Link></Button>
        </div>
      </section>

      {apiDown && <Alert title="The Rex API is not reachable">{error}</Alert>}

      <section className="grid gap-4 md:grid-cols-3">
        {features.map((f) => (
          <Link key={f.href} href={f.href} className="group">
            <Card className="h-full transition group-hover:border-mute/60">
              <CardHeader><f.icon className="mb-2 h-5 w-5 text-rex" /><CardTitle>{f.title}</CardTitle></CardHeader>
              <CardBody><p className="text-sm text-mute">{f.body}</p></CardBody>
            </Card>
          </Link>
        ))}
      </section>

      <section>
        <div className="mb-3 flex items-center gap-2"><h2 className="text-xl font-semibold">Live from your memory</h2>{isSample && <SampleBadge />}</div>
        {!status && !error ? <div className="grid gap-3 sm:grid-cols-4"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div> : status && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Backtests remembered" value={status.runs} sub={`${status.trades.toLocaleString()} trades stored`} />
            <Metric label="Lessons learned" value={status.lessons} sub="generated from measured stats" />
            <Metric label="Journal round trips" value={status.journal_trades} sub={status.journal_trades ? (isSample ? "synthetic sample journal" : "your imported journal") : "none yet: import one"} />
            <Metric label="Journal win rate" value={j.data ? pctPlain(j.data.overall.win_rate, 0) : "n/a"} sub={j.data ? `${j.data.overall.n} trades, total ${inr(j.data.overall.total_pnl)}` : "import a journal first"} />
          </div>
        )}
        {status && status.runs === 0 && <Alert tone="warn" title="Memory is empty" className="mt-3">Open the <Link className="underline" href="/memory">Memory page</Link> and press “Seed demo memory”, or run a backtest.</Alert>}
      </section>

      {p.data && (
        <section>
          <Card>
            <CardHeader><div className="flex flex-wrap items-center gap-2"><Brain className="h-4 w-4 text-rex" /><CardTitle>Does memory help? Walk-forward test on the journal</CardTitle>{p.data.is_sample && <SampleBadge />}</div>
              <CardDesc>Each trade is checked using only trades that closed before it. “Wait” = skip, “reduce size” = half size.</CardDesc></CardHeader>
            <CardBody>
              <div className="grid gap-3 sm:grid-cols-3">
                <Metric label="Max drawdown" value={<span><span className="text-mute">{inr(p.data.off.max_drawdown)}</span> → <span className="text-gain">{inr(p.data.on.max_drawdown)}</span></span>} sub="memory OFF → ON" valueClass="text-lg" />
                <Metric label="Total P&L" value={<span><span className="text-mute">{inr(p.data.off.total_pnl)}</span> → <span className={tone(p.data.on.total_pnl)}>{inr(p.data.on.total_pnl)}</span></span>} sub="memory OFF → ON" valueClass="text-lg" />
                <Metric label="Flagged trades, half size" value={<span className={cn(tone(p.data.reduced.pnl_half_size))}>{inr(p.data.reduced.pnl_half_size)}</span>} sub={`vs ${inr(p.data.reduced.pnl_full_size)} at full size (${p.data.reduced.n} trades)`} valueClass="text-lg" />
              </div>
              <p className="mt-3 text-xs text-mute">Read it plainly: {p.data.on.max_drawdown > p.data.off.max_drawdown ? "following the checks reduced the worst drawdown" : "following the checks did not reduce the worst drawdown"}, and {p.data.on.total_pnl >= p.data.off.total_pnl ? "total P&L was higher" : `total P&L was lower, because the ${p.data.skipped.n} skipped trades would have made ${inr(p.data.skipped.pnl_if_taken)}`}. One journal is a demo, not proof. <Link className="text-rex underline" href="/memory">Full chart</Link></p>
            </CardBody>
          </Card>
        </section>
      )}

      <p className="flex items-center gap-2 text-xs text-mute"><Lock className="h-3 w-3" /> The final year of every dataset is locked during refinement and only opened to grade the result.</p>
    </div>
  );
}
