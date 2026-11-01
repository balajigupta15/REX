"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Radar, History, Eye } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Alert } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { post, get } from "@/lib/api";
import type { StrategyRuns, TrackSession } from "@/lib/types";
import { pp, cn } from "@/lib/utils";

export interface Variant { label: "original" | "refined"; title: string; params: Record<string, any>; exits: Record<string, any> }

/** "Track this strategy" (no trade) and "I took this trade" (log the trade you placed in your own broker). Rex never places orders. */
export function TrackPanel({ ticker, template, cost_bps, cost_model, slippage_bps, trade_value, variants }: { ticker: string; template: string; cost_bps: number; cost_model?: string; slippage_bps?: number | null; trade_value?: number | null; variants: Variant[] }) {
  const router = useRouter();
  const [which, setWhich] = useState(0);
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState("buy");
  const [price, setPrice] = useState(""); const [qty, setQty] = useState("");
  const [day, setDay] = useState(""); const [time, setTime] = useState("");
  const [busy, setBusy] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  const v = variants[Math.min(which, variants.length - 1)];
  const start = async (mode: "paper" | "logged") => {
    setBusy(mode); setErr(null);
    const body: any = { ticker, template, params: v.params, exits: v.exits, cost_bps, cost_model, slippage_bps: slippage_bps ?? undefined, trade_value: trade_value ?? undefined, label: v.label };
    if (mode === "logged") {
      body.trade = { side, entry_price: price === "" ? null : Number(price), qty: qty === "" ? null : Number(qty), entered_at: day ? `${day}${time ? " " + time : ""}` : null };
    }
    try { const s = await post<TrackSession>("/paper/start", body); router.push(`/paper?id=${s.id}`); }
    catch (e: any) { setErr(e.message); setBusy(null); }
  };
  return (
    <Card className="border-line" data-testid="track-card">
      <CardHeader><div className="flex items-center gap-2"><Radar className="h-4 w-4 text-rex" /><CardTitle>Track this strategy live</CardTitle></div>
        <CardDesc>Rex follows the live price against this strategy's rules and tells you when its exit fires. You place and close the trade in your own broker app; Rex never places an order.</CardDesc></CardHeader>
      <CardBody className="space-y-4">
        {variants.length > 1 && (
          <FormField className="max-w-xs"><Label>Which version</Label>
            <Select value={which} onChange={(e) => setWhich(Number(e.target.value))} data-testid="track-variant">{variants.map((x, i) => <option key={x.label} value={i}>{x.title}</option>)}</Select></FormField>)}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => start("paper")} disabled={!!busy} data-testid="track-paper"><Eye className="h-4 w-4" />{busy === "paper" ? "Starting…" : "Track strategy (no trade)"}</Button>
          <Button variant="outline" onClick={() => setOpen((o) => !o)} data-testid="track-toggle">I took this trade in my broker</Button>
        </div>
        {open && (
          <div className="space-y-3 rounded-lg border border-line bg-ink p-4" data-testid="trade-form">
            <p className="text-xs text-mute">Log the trade you made in Groww, Zerodha or any broker app. Everything is optional: leave price blank to use the latest price, time blank for now.</p>
            <div className="grid gap-3 sm:grid-cols-5">
              <FormField><Label>You</Label><Select value={side} onChange={(e) => setSide(e.target.value)} data-testid="trade-side"><option value="buy">Bought</option><option value="sell">Sold (short)</option></Select></FormField>
              <FormField><Label>Entry price ₹</Label><Input type="number" min={0} step="0.05" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="latest" data-testid="trade-price" /></FormField>
              <FormField><Label>Quantity</Label><Input type="number" min={0} step="1" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="1" data-testid="trade-qty" /></FormField>
              <FormField><Label>Date</Label><Input type="date" value={day} onChange={(e) => setDay(e.target.value)} data-testid="trade-date" /></FormField>
              <FormField><Label>Time (IST)</Label><Input type="time" value={time} onChange={(e) => setTime(e.target.value)} disabled={!day} data-testid="trade-time" /></FormField>
            </div>
            <Button onClick={() => start("logged")} disabled={!!busy} data-testid="track-logged">{busy === "logged" ? "Starting…" : "Track my trade"}</Button>
          </div>)}
        {err && <Alert title="Could not start tracking">{err}</Alert>}
      </CardBody>
    </Card>
  );
}

const verdictTone = (v: string) => (/WON|HELD|WORKED/.test(v) ? "gain" : /LOST|OVERFIT|FADED/.test(v) ? "loss" : /TRACK|INCONCL/.test(v) ? "warn" : "neutral") as any;

/** "Last time" panel: what happened the last time you ran a similar strategy, how price moved afterwards, and a one-line lesson. */
export function LastTime({ ticker, template, className }: { ticker: string; template: string; className?: string }) {
  const [d, setD] = useState<StrategyRuns | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      get<StrategyRuns>(`/memory/strategy-runs?ticker=${encodeURIComponent(ticker)}&template=${encodeURIComponent(template)}&limit=4`)
        .then((x) => { if (alive) { setD(x); setErr(null); } }).catch((e) => alive && setErr(e.message));
    }, 400);
    return () => { alive = false; clearTimeout(t); };
  }, [ticker, template]);
  const l = d?.last_time;
  return (
    <Card className={cn("h-fit", className)} data-testid="last-time-card">
      <CardHeader><div className="flex items-center gap-2"><History className="h-4 w-4 text-rex" /><CardTitle>Last time</CardTitle></div></CardHeader>
      <CardBody className="space-y-3 text-sm">
        {err && <Alert title="Could not load strategy memory">{err}</Alert>}
        {d && !l && <p className="text-mute" data-testid="last-time-empty">No earlier run of this ticker or strategy yet. Rex will remember this one, and show how price moved afterwards.</p>}
        {l && (<>
          <div className="flex flex-wrap items-center gap-1.5"><Badge tone={verdictTone(l.verdict)}>{l.verdict}</Badge><Badge>{l.match}</Badge>{l.replayed && <Badge tone="sky">replayed from real data</Badge>}</div>
          <p data-testid="last-time-headline" className="text-text">{l.headline}</p>
          <div className="grid grid-cols-3 gap-2 text-xs">
            {([["5 days later", l.fwd5], ["20 days later", l.fwd20], ["Since then", l.since_pct]] as [string, number | null][]).map(([k, x]) => (
              <div key={k} className="rounded-lg border border-line bg-ink p-2"><div className="text-mute">{k}</div><div className={cn("num text-sm font-semibold", x == null ? "text-mute" : x >= 0 ? "text-gain" : "text-loss")}>{x == null ? "pending" : pp(x, 1)}</div></div>))}
          </div>
          <p data-testid="last-time-lesson" className="rounded-lg border border-line bg-panel2 p-2.5 text-xs"><span className="font-semibold text-rex">Lesson: </span>{l.lesson}</p>
          {d && d.count > 1 && <p className="text-xs text-mute">{d.count - 1} more similar run{d.count > 2 ? "s" : ""} remembered. <Link className="underline" href="/memory">See all in Memory</Link>.</p>}
        </>)}
        <p className="text-xs text-mute">Price moves after each run come from real yfinance closes. Hints from the past, not predictions.</p>
      </CardBody>
    </Card>
  );
}
