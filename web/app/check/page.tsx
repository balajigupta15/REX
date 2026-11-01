"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, MinusCircle, OctagonX, Brain, Swords, Quote } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { Metric } from "@/components/metric";
import { SampleBadge } from "@/components/sample-badge";
import { BackendChip } from "@/components/backend-chip";
import { Explain } from "@/components/explain";
import { post } from "@/lib/api";
import { useGet, useMemStatus } from "@/lib/hooks";
import type { CheckResult, Templates } from "@/lib/types";
import { inr, pctPlain, pp, pretty, tone, cn } from "@/lib/utils";

const REC = {
  proceed: { label: "PROCEED", cls: "border-gain/50 bg-gain/10 text-gain", Icon: CheckCircle2, line: "Nothing in your history argues against this entry." },
  reduce_size: { label: "REDUCE SIZE", cls: "border-warn/50 bg-warn/10 text-warn", Icon: MinusCircle, line: "Your history says this kind of setup has hurt you. Take half size." },
  wait: { label: "WAIT", cls: "border-loss/50 bg-loss/10 text-loss", Icon: OctagonX, line: "You have repeatedly lost money in this exact setup." },
} as const;

export default function Check() {
  const tpl = useGet<Templates>("/strategies/templates");
  const { status } = useMemStatus();
  const [symbol, setSymbol] = useState("INFY");
  const [date, setDate] = useState("");
  const [side, setSide] = useState("buy");
  const [res, setRes] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [meko, setMeko] = useState<{ state: "idle" | "loading" | "done"; records: any[] }>({ state: "idle", records: [] });

  const run = async (e?: React.FormEvent, sym?: string) => {
    e?.preventDefault(); setBusy(true); setErr(null); setMeko({ state: "idle", records: [] });
    try {
      const r = await post<CheckResult>("/trades/check", { symbol: (sym ?? symbol).trim(), side, date: date || undefined });
      setRes(r);
      if (status?.meko.connected) {   // Meko recall is slow (a few seconds): fetch in the background, never block the check
        setMeko({ state: "loading", records: [] });
        const q = `${r.symbol} ${r.direction} trade setup: ${r.fingerprint.descriptions.join("; ")}`;
        post<any>("/memory/recall", { query: q }).then((x) => setMeko({ state: "done", records: x.records || [] })).catch(() => setMeko({ state: "done", records: [] }));
      }
    } catch (e: any) { setErr(e.message); setRes(null); } finally { setBusy(false); }
  };
  useEffect(() => { if (tpl.data && !res && !busy && !err) { const q = new URLSearchParams(window.location.search).get("symbol")?.toUpperCase(); if (q) { setSymbol(q); run(undefined, q); } else run(); } /* eslint-disable-next-line */ }, [tpl.data]);

  const rec = res ? REC[res.recommendation.action] : null;
  const st = res?.similar.stats;
  return (
    <div className="space-y-5">
      <PageHead title="Pre-trade check" sub="Before you click buy or sell: Rex recalls your most similar past trades and tells you what happened. It evaluates entries only and never places an order." right={<BackendChip status={status} />} />
      <Card><CardBody>
        <form onSubmit={run} className="grid items-end gap-4 sm:grid-cols-[1fr_1fr_1fr_auto]">
          <FormField><Label>Symbol</Label><Input list="syms" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="INFY or INFY.NS" /><datalist id="syms">{tpl.data?.tickers.map((t) => <option key={t} value={t.replace(".NS", "")} />)}</datalist></FormField>
          <FormField><Label>Date (blank = latest close)</Label><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></FormField>
          <FormField><Label>Side</Label><Select value={side} onChange={(e) => setSide(e.target.value)}><option value="buy">Buy (open long)</option><option value="sell">Sell (open short)</option></Select></FormField>
          <Button type="submit" size="lg" disabled={busy || !symbol}>{busy ? "Checking…" : "Check"}</Button>
        </form>
      </CardBody></Card>

      {err && <Alert title="Check failed">{err} {err.includes("journal") && <a className="underline" href="/journal">Import a journal first.</a>}</Alert>}
      {busy && !res && <Skeleton className="h-40" />}

      {res && rec && (<>
        <div className={cn("flex flex-wrap items-center gap-4 rounded-md border p-5", rec.cls)} data-testid="recommendation" data-action={res.recommendation.action}>
          <rec.Icon className="h-10 w-10" />
          <div className="flex-1"><div className="text-xs uppercase tracking-widest opacity-80">{res.symbol} · {res.direction} · {res.date}</div>
            <div className="text-3xl font-bold tracking-tight">{rec.label}{res.recommendation.action === "reduce_size" && <span className="ml-2 text-xl font-semibold">× {res.recommendation.size_factor}</span>}</div>
            <div className="text-sm text-text/90">{rec.line}</div>
            {res.challenger?.overrides_recommendation && <div className="mt-1 text-sm font-semibold text-loss" data-testid="veto-note">Challenger veto: the final call is WAIT.</div>}</div>
          <div className="flex items-center gap-2"><Badge>{res.recommendation.confidence} confidence</Badge>{res.journal.source_used === "sample" && <SampleBadge />}</div>
        </div>

        {res.challenger && (
          <Card className={cn(res.challenger.veto ? "border-loss/60" : "border-line")} data-testid="challenger" data-veto={res.challenger.veto ? "1" : "0"}>
            <CardHeader><div className="flex flex-wrap items-center gap-2"><Swords className="h-4 w-4 text-rex" /><CardTitle>Challenger: the case against this trade</CardTitle>
              {res.challenger.veto ? <Badge tone="loss">VETO: don&apos;t trade</Badge> : <Badge tone={res.challenger.severity === "none" ? "gain" : "warn"}>{res.challenger.severity === "none" ? "no strong objection" : "objections raised"}</Badge>}</div>
              <CardDesc>A second, deliberately sceptical rule set. It argues from your own losses and from current risk, and it can override a “proceed”.</CardDesc></CardHeader>
            <CardBody className="space-y-2 text-sm">
              {res.challenger.arguments.length === 0 && <p className="text-mute">Could not find a data-backed argument against it.</p>}
              {res.challenger.arguments.map((a, i) => <div key={i} className="flex gap-2 rounded-lg border border-line bg-ink p-2.5"><Badge tone={a.weight === "strong" ? "loss" : "warn"}>{a.weight}</Badge><span>{a.text}</span></div>)}
              <p className={cn("text-sm", res.challenger.veto ? "font-medium text-loss" : "text-mute")} data-testid="challenger-verdict">{res.challenger.verdict}</p>
              <div className="flex flex-wrap items-center gap-2 text-xs text-mute"><Badge>case against: {res.challenger.score} / veto at {res.challenger.veto_threshold}</Badge>
                <Badge tone={res.challenger.veto ? "loss" : "neutral"}>final call: {pretty(res.challenger.final_action)}{res.challenger.overrides_recommendation ? " (overrides the recommendation)" : ""}</Badge></div>
              <details className="text-xs text-mute"><summary className="cursor-pointer hover:text-text">How the Challenger scores (fixed rules, no LLM)</summary><ul className="mt-1 list-disc space-y-0.5 pl-5">{res.challenger.rules.map((r, i) => <li key={i}>{r}</li>)}</ul></details>
            </CardBody></Card>
        )}

        <div className="grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Why</CardTitle></CardHeader><CardBody>
            <ul className="list-inside list-disc space-y-1.5 text-sm text-text/90">{res.recommendation.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            {res.recommendation.wait_for && <p className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-2.5 text-sm text-warn">Wait for: {res.recommendation.wait_for}</p>}
            <Explain kind="pretrade" facts={{ symbol: res.symbol, side: res.side, recommendation: res.recommendation, similar: res.similar.stats, fingerprint: res.fingerprint.descriptions, challenger: res.challenger }} />
          </CardBody></Card>
          <Card><CardHeader><CardTitle>Today&apos;s fingerprint</CardTitle><CardDesc>As of {res.fingerprint.as_of}.{!res.fingerprint.complete && " Some context is missing."}</CardDesc></CardHeader><CardBody>
            <div className="flex flex-wrap gap-1.5">{Object.entries(res.fingerprint.tags).map(([k, v]) => <Badge key={k} tone="sky">{k}: {v}</Badge>)}</div>
            <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-mute">{res.fingerprint.descriptions.map((d, i) => <li key={i}>{d}</li>)}</ul>
          </CardBody></Card>
        </div>

        <Card><CardHeader><div className="flex items-center gap-2"><Brain className="h-4 w-4 text-rex" /><CardTitle>Memory recalled</CardTitle></div>
          <CardDesc>{st ? `The ${st.n} most similar of your ${res.journal.n} past trades.` : "No similar past trades found above the similarity threshold."}</CardDesc></CardHeader><CardBody className="space-y-4">
          {st && <div className="grid gap-3 sm:grid-cols-4"><Metric label="Win rate" value={pctPlain(st.win_rate, 0)} sub={`you overall: ${pctPlain(res.journal.overall.win_rate, 0)}`} /><Metric label="Avg return" value={pp(st.avg_return_pct)} valueClass={tone(st.avg_return_pct)} />
            <Metric label="Avg P&L" value={inr(st.avg_pnl)} valueClass={tone(st.avg_pnl)} /><Metric label="Avg hold" value={`${st.avg_hold_days.toFixed(0)} days`} /></div>}
          {res.mistake_pattern?.matched && <Alert tone="warn" title="You have made this mistake before">{res.mistake_pattern.matched.text}</Alert>}
          {res.similar.neighbors.length > 0 && <Table><thead><tr><Th>Similar trade</Th><Th>Dates</Th><Th>Match</Th><Th>Return</Th><Th>P&L</Th><Th>Because</Th></tr></thead><tbody>
            {res.similar.neighbors.map((n, i) => <tr key={i}><Td className="font-medium">{n.ticker.replace(".NS", "")}{n.same_symbol && <Badge className="ml-1" tone="rex">same stock</Badge>}</Td><Td className="num">{n.entry_date} → {n.exit_date}</Td><Td className="num">{n.cosine.toFixed(2)}</Td>
              <Td className={cn("num", tone(n.return_pct))}>{pp(n.return_pct)}</Td><Td className={cn("num", tone(n.pnl))}>{inr(n.pnl)}</Td><Td className="text-xs text-mute">{n.matching.join(", ")}</Td></tr>)}</tbody></Table>}
          {status?.meko.connected && (
            <div className="rounded-lg border border-gain/30 bg-gain/5 p-3" data-testid="meko-recall"><div className="mb-2 flex items-center gap-2 text-sm font-medium text-gain"><Quote className="h-3.5 w-3.5" />Recalled from Meko memory {meko.state === "loading" && <span className="text-xs font-normal text-mute">(searching…)</span>}</div>
              {meko.state === "done" && meko.records.length === 0 && <p className="text-xs text-mute">Nothing relevant stored in Meko yet for this setup.</p>}
              <ul className="space-y-1.5 text-sm">{meko.records.slice(0, 5).map((r, i) => <li key={i} className="text-text/90">• {r.text} <span className="num text-xs text-mute">score {r.score}</span></li>)}</ul></div>
          )}
        </CardBody></Card>
        <ul className="space-y-1 text-xs text-mute">{res.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      </>)}
    </div>
  );
}
