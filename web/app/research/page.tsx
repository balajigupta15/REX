"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, Info } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormField, Select, Label } from "@/components/ui/input";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { Metric } from "@/components/metric";
import { Explain } from "@/components/explain";
import { EarningsChart, PriceChart, RiskChart } from "@/components/charts";
import { useGet } from "@/lib/hooks";
import type { Brief, Move } from "@/lib/types";
import { cn, pp, tone } from "@/lib/utils";

const TL: Record<string, any> = { uptrend: "gain", downtrend: "loss", mixed: "warn" };

function Moves({ title, rows }: { title: string; rows: Move[] }) {
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-wide text-mute">{title}</div>
      <Table><thead><tr><Th>Date</Th><Th className="text-right">Move</Th><Th className="text-right">Volume</Th><Th className="text-right">Close</Th></tr></thead>
        <tbody>{rows.map((m) => <tr key={m.date} className="border-t border-line"><Td>{m.date}</Td><Td className={cn("num text-right font-medium", tone(m.return_pct))}>{pp(m.return_pct)}</Td>
          <Td className="num text-right text-mute">{m.volume_ratio == null ? "n/a" : `${m.volume_ratio.toFixed(1)}x`}</Td><Td className="num text-right">{m.close.toLocaleString("en-IN")}</Td></tr>)}</tbody></Table>
    </div>
  );
}

export default function Research() {
  const tpl = useGet<{ tickers: string[] }>("/strategies/templates");
  const [sym, setSym] = useState("RELIANCE.NS");
  useEffect(() => { const q = new URLSearchParams(window.location.search).get("t"); if (q) setSym(q.toUpperCase().includes(".") ? q.toUpperCase() : q.toUpperCase() + ".NS"); }, []);
  const { data: b, error, loading } = useGet<Brief>(`/research/${encodeURIComponent(sym)}`);
  const tickers = tpl.data?.tickers ?? [sym];
  const options = tickers.includes(sym) ? tickers : [sym, ...tickers];
  return (
    <div className="space-y-6">
      <PageHead title="Stock research brief" sub="Pick an NSE stock for a plain-English read of its last five years: trend, biggest moves, volatility, support and resistance, and how it reacted to results." />
      <Card><CardBody className="flex flex-wrap items-end gap-4">
        <FormField className="w-60"><Label>Stock</Label><Select value={sym} onChange={(e) => { setSym(e.target.value); window.history.replaceState(null, "", `/research?t=${e.target.value.replace(".NS", "")}`); }} data-testid="research-select">{options.map((t) => <option key={t} value={t}>{t.replace(".NS", "")}</option>)}</Select></FormField>
        {b && <div className="text-xs text-mute">Data: {b.data.source} · {b.bars.toLocaleString()} daily bars · {b.data.first} to {b.data.last}</div>}
        {b && <div className="ml-auto flex gap-2"><Link href={`/check?symbol=${b.symbol}`}><Button variant="outline" size="sm">Pre-trade check</Button></Link><Link href={`/build?t=${encodeURIComponent(sym)}`}><Button variant="outline" size="sm">Backtest an idea</Button></Link></div>}
      </CardBody></Card>

      {loading && <Skeleton className="h-96" />}
      {error && <Alert title="Could not build the brief">{error}</Alert>}

      {b && !loading && (<div className="space-y-6" data-testid="research-brief" data-symbol={b.symbol}>
        <Card className="border-line"><CardHeader><div className="flex flex-wrap items-center gap-2"><FileText className="h-4 w-4 text-rex" /><CardTitle>{b.symbol} in plain English</CardTitle><Badge tone={TL[b.trend.label]}>{b.trend.label}</Badge></div>
          <CardDesc>Written by fixed templates from the numbers below. No LLM, no forecast.</CardDesc></CardHeader>
          <CardBody className="space-y-2 text-sm leading-relaxed">{b.summary.map((p, i) => <p key={i}>{p}</p>)}
            <Explain kind="research" facts={{ symbol: b.symbol, summary: b.summary, trend: b.trend, volatility: b.volatility, levels: { support: b.levels.support, resistance: b.levels.resistance }, earnings: b.earnings.summary }} /></CardBody></Card>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Last close" value={b.close.toLocaleString("en-IN")} sub={`as of ${b.as_of}`} />
          <Metric label={`${b.years}y return`} value={<span className={tone(b.trend.total_return_pct)}>{pp(b.trend.total_return_pct, 0)}</span>} sub={`${pp(b.trend.cagr_pct, 1)} a year`} />
          <Metric label="Volatility (annual)" value={`${b.volatility.annualised_pct.toFixed(0)}%`} sub={`last 20 days ${b.volatility.last_20d_pct.toFixed(0)}% (${b.volatility.percentile_of_history.toFixed(0)}th pct)`} />
          <Metric label="Worst fall" value={<span className="text-loss">{b.volatility.max_drawdown_pct.toFixed(0)}%</span>} sub={`${b.volatility.max_dd_peak} → ${b.volatility.max_dd_trough}`} />
        </div>

        <Card><CardHeader><CardTitle>Trend: 5 years of weekly closes</CardTitle><CardDesc>With 50 and 200-day averages and the nearest support / resistance levels.</CardDesc></CardHeader>
          <CardBody className="space-y-3"><PriceChart b={b} />
            <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              {([["1 month", b.trend.ret_1m], ["3 months", b.trend.ret_3m], ["6 months", b.trend.ret_6m], ["12 months", b.trend.ret_12m]] as const).map(([l, v]) => <div key={l} className="rounded-lg border border-line bg-ink p-2.5"><div className="text-xs text-mute">{l}</div><div className={cn("num text-lg font-semibold", tone(v))}>{v == null ? "n/a" : pp(v, 1)}</div></div>)}
            </div>
            <p className="text-xs text-mute">52-week range {b.trend.low_52w.toLocaleString("en-IN")} to {b.trend.high_52w.toLocaleString("en-IN")} ({pp(b.trend.pct_from_high, 1)} from the high). 50-day {b.trend.sma50.toLocaleString("en-IN")}, 200-day {b.trend.sma200.toLocaleString("en-IN")}.</p></CardBody></Card>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Risk: drawdown and volatility</CardTitle><CardDesc>How far it fell from each peak, and how jumpy it was.</CardDesc></CardHeader>
            <CardBody className="space-y-2"><RiskChart b={b} />
              <div className="grid grid-cols-3 gap-2 text-xs text-mute"><div>Best day <b className="text-gain">{pp(b.volatility.best_day_pct)}</b></div><div>Worst day <b className="text-loss">{pp(b.volatility.worst_day_pct)}</b></div><div>Avg range (60d) <b className="text-text">{b.volatility.avg_daily_range_pct.toFixed(1)}%</b></div></div></CardBody></Card>
          <Card><CardHeader><CardTitle>Support and resistance</CardTitle><CardDesc>Price zones where it turned several times.</CardDesc></CardHeader>
            <CardBody className="space-y-3 text-sm">
              {([["Resistance (above)", b.levels.resistance, "loss"], ["Support (below)", b.levels.support, "gain"]] as const).map(([t, ls, tn]) => (
                <div key={t}><div className="mb-1 text-xs uppercase tracking-wide text-mute">{t}</div>
                  {ls.length === 0 ? <p className="text-mute">No level with 2+ touches in range.</p> :
                    <div className="space-y-1">{ls.map((l) => <div key={l.level} className="flex items-center justify-between rounded-lg border border-line bg-ink px-3 py-1.5"><span className="num font-medium">{l.level.toLocaleString("en-IN")}</span><span className="text-xs text-mute">{l.touches} touches · last {l.last_touch}</span><Badge tone={tn}>{pp(l.distance_pct, 1)}</Badge></div>)}</div>}
                </div>))}
              <p className="text-xs text-mute">{b.levels.method}</p></CardBody></Card>
        </div>

        <Card><CardHeader><CardTitle>Biggest moves</CardTitle><CardDesc>The five best and worst single days. Volume is that day vs the prior 20-day average.</CardDesc></CardHeader>
          <CardBody className="grid gap-4 md:grid-cols-2"><Moves title="Best days" rows={b.biggest_moves.up} /><Moves title="Worst days" rows={b.biggest_moves.down} /></CardBody></Card>

        <Card data-testid="earnings-card"><CardHeader><CardTitle>Earnings reactions</CardTitle><CardDesc>How the price moved around each quarterly result.</CardDesc></CardHeader>
          <CardBody className="space-y-3 text-sm">
            {!b.earnings.available ? <Alert tone="info" title="Earnings data not available"><span className="flex items-center gap-1"><Info className="h-3.5 w-3.5" />{b.earnings.note}</span></Alert> : (<>
              {b.earnings.summary && <div className="grid gap-3 sm:grid-cols-4">
                <Metric label="Results studied" value={b.earnings.summary.n} sub={`${b.earnings.summary.up} up · ${b.earnings.summary.down} down`} />
                <Metric label="Avg move" value={`${b.earnings.summary.avg_abs_move_pct.toFixed(1)}%`} sub={`largest ${b.earnings.summary.max_abs_move_pct.toFixed(1)}%`} />
                <Metric label="After a beat" value={b.earnings.summary.avg_after_beat_pct == null ? "n/a" : pp(b.earnings.summary.avg_after_beat_pct, 1)} sub={`${b.earnings.summary.n_beats} beats`} valueClass="text-xl" />
                <Metric label="After a miss" value={b.earnings.summary.avg_after_miss_pct == null ? "n/a" : pp(b.earnings.summary.avg_after_miss_pct, 1)} sub={`${b.earnings.summary.n_misses} misses`} valueClass="text-xl" />
              </div>}
              <EarningsChart rows={b.earnings.reactions} />
              <details><summary className="cursor-pointer text-xs text-mute hover:text-text">All results ({b.earnings.reactions.length})</summary>
                <Table className="mt-2"><thead><tr><Th>Date</Th><Th className="text-right">EPS est.</Th><Th className="text-right">EPS rep.</Th><Th className="text-right">Surprise</Th><Th className="text-right">2-day move</Th></tr></thead>
                  <tbody>{b.earnings.reactions.map((r) => <tr key={r.date} className="border-t border-line"><Td>{r.date}</Td><Td className="num text-right">{r.eps_estimate ?? "n/a"}</Td><Td className="num text-right">{r.eps_reported ?? "n/a"}</Td>
                    <Td className={cn("num text-right", tone(r.surprise_pct))}>{r.surprise_pct == null ? "n/a" : pp(r.surprise_pct, 1)}</Td><Td className={cn("num text-right font-medium", tone(r.reaction_pct))}>{pp(r.reaction_pct)}</Td></tr>)}</tbody></Table></details>
              <p className="text-xs text-mute">{b.earnings.note}</p></>)}
          </CardBody></Card>
        <p className="text-xs text-mute">{b.disclaimer}</p>
      </div>)}
    </div>
  );
}
