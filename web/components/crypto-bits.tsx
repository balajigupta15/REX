"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Radar, History, Eye, Activity } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Alert } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { post, get } from "@/lib/api";
import { usd, type CQuote } from "@/lib/crypto";
import type { StrategyRuns } from "@/lib/types";
import { pp, cn } from "@/lib/utils";

export const NO_ORDERS = "Rex never places orders. You trade on your own exchange (Binance, Coinbase, WazirX, CoinDCX or any other); Rex only follows the price and the strategy's rules.";

/** Live price strip: BTC / ETH / SOL, polled every 3 s from the backend (Binance public REST). Crypto is 24/7, so there is no open/closed state. */
export function PriceStrip() {
  const [q, setQ] = useState<CQuote[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [prev, setPrev] = useState<Record<string, number>>({});
  const [okAt, setOkAt] = useState<number | null>(null); const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => {
    let alive = true; let busy = false;
    const tick = () => {
      if (busy) return; busy = true;   // fixed 3 s period, never two requests in flight
      get<{ prices: CQuote[] }>("/crypto/prices").then((d) => {
        if (!alive) return;
        setQ((old) => { setPrev(Object.fromEntries((old ?? []).map((x) => [x.symbol, x.price]))); return d.prices; }); setErr(null); setOkAt(Date.now());
      }).catch((e) => alive && setErr(e.message)).finally(() => { busy = false; });
    };
    tick(); const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const live = !!q && q.every((x) => x.live && !x.stale);
  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-md border border-line bg-panel/80 px-4 py-2.5 text-sm" data-testid="price-strip">
      <Badge tone={live ? "gain" : "warn"} data-testid="live-badge"><Activity className="h-3 w-3" />{q ? (live ? "Real-time" : "Not live (cached)") : "Connecting…"}</Badge>
      {(q ?? []).map((x) => (
        <div key={x.symbol} className="flex items-baseline gap-1.5" data-testid={`px-${x.symbol}`}>
          <span className="text-xs text-mute">{x.symbol.replace("USDT", "")}/USDT</span>
          <span className={cn("num font-semibold", prev[x.symbol] != null && x.price !== prev[x.symbol] && (x.price > prev[x.symbol] ? "text-gain" : "text-loss"))}>{x.error ? "n/a" : usd(x.price)}</span>
        </div>))}
      {okAt && <span className="num text-xs font-medium text-gain" data-testid="px-age">updated {Math.max(0, Math.round((now - okAt) / 1000))}s ago</span>}
      <span className="ml-auto text-xs text-mute">Binance public price, REST poll about every 3 s · 24/7 market, always open · times in UTC</span>
      {err && <span className="w-full text-xs text-loss">{err}</span>}
    </div>
  );
}

export interface Variant { label: "original" | "refined"; title: string; params: Record<string, any>; exits: Record<string, any> }

/** "Track strategy" or "I took this trade on my exchange". Rex never places orders. */
export function CryptoTrackPanel({ symbol, interval, template, cost_bps, variants }: { symbol: string; interval: string; template: string; cost_bps: number; variants: Variant[] }) {
  const router = useRouter();
  const [which, setWhich] = useState(0);
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState("buy");
  const [price, setPrice] = useState(""); const [qty, setQty] = useState("");
  const [busy, setBusy] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  const v = variants[Math.min(which, variants.length - 1)];
  const start = async (mode: "paper" | "logged") => {
    setBusy(mode); setErr(null);
    const body: any = { symbol, interval, template, params: v.params, exits: v.exits, cost_bps, label: v.label };
    if (mode === "logged") body.trade = { side, entry_price: price === "" ? null : Number(price), qty: qty === "" ? null : Number(qty) };
    try { const s = await post<any>("/crypto/tracker/start", body); router.push(`/crypto/tracker?id=${s.id}`); }
    catch (e: any) { setErr(e.message); setBusy(null); }
  };
  return (
    <Card className="border-line" data-testid="track-card">
      <CardHeader><div className="flex items-center gap-2"><Radar className="h-4 w-4 text-rex" /><CardTitle>Track this strategy live</CardTitle></div>
        <CardDesc>Rex follows the live {symbol.replace("USDT", "")} price against this strategy's rules (24/7) and tells you when its exit fires. {NO_ORDERS}</CardDesc></CardHeader>
      <CardBody className="space-y-4">
        {variants.length > 1 && (
          <FormField className="max-w-xs"><Label>Which version</Label>
            <Select value={which} onChange={(e) => setWhich(Number(e.target.value))} data-testid="track-variant">{variants.map((x, i) => <option key={x.label} value={i}>{x.title}</option>)}</Select></FormField>)}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => start("paper")} disabled={!!busy} data-testid="track-paper"><Eye className="h-4 w-4" />{busy === "paper" ? "Starting…" : "Track strategy (no trade)"}</Button>
          <Button variant="outline" onClick={() => setOpen((o) => !o)} data-testid="track-toggle">I took this trade on my exchange</Button>
        </div>
        {open && (
          <div className="space-y-3 rounded-lg border border-line bg-ink p-4" data-testid="trade-form">
            <p className="text-xs text-mute">Log the trade you made on your own exchange. Leave the price blank to use the live price now. Rex does not place or close anything for you.</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <FormField><Label>You</Label><Select value={side} onChange={(e) => setSide(e.target.value)} data-testid="trade-side"><option value="buy">Bought (long)</option><option value="sell">Sold (short)</option></Select></FormField>
              <FormField><Label>Entry price (USDT)</Label><Input type="number" min={0} step="any" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="live price" data-testid="trade-price" /></FormField>
              <FormField><Label>Quantity (coins)</Label><Input type="number" min={0} step="any" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="optional" data-testid="trade-qty" /></FormField>
            </div>
            <Button onClick={() => start("logged")} disabled={!!busy} data-testid="track-logged">{busy === "logged" ? "Starting…" : "Track my trade"}</Button>
          </div>)}
        {err && <Alert title="Could not start tracking">{err}</Alert>}
      </CardBody>
    </Card>
  );
}

const verdictTone = (v: string) => (/WON|HELD|WORKED/.test(v) ? "gain" : /LOST|OVERFIT|FADED/.test(v) ? "loss" : /TRACK|INCONCL/.test(v) ? "warn" : "neutral") as any;

/** Crypto "Last time" panel: reads /crypto/memory/strategy-runs only (never the NSE memory). */
export function CryptoLastTime({ symbol, template, className }: { symbol: string; template: string; className?: string }) {
  const [d, setD] = useState<StrategyRuns | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      get<StrategyRuns>(`/crypto/memory/strategy-runs?symbol=${encodeURIComponent(symbol)}&template=${encodeURIComponent(template)}&limit=4`)
        .then((x) => { if (alive) { setD(x); setErr(null); } }).catch((e) => alive && setErr(e.message));
    }, 400);
    return () => { alive = false; clearTimeout(t); };
  }, [symbol, template]);
  const l = d?.last_time as any;
  return (
    <Card className={cn("h-fit", className)} data-testid="last-time-card">
      <CardHeader><div className="flex items-center gap-2"><History className="h-4 w-4 text-rex" /><CardTitle>Last time (crypto)</CardTitle></div></CardHeader>
      <CardBody className="space-y-3 text-sm">
        {err && <Alert title="Could not load crypto memory">{err}</Alert>}
        {d && !l && <p className="text-mute" data-testid="last-time-empty">No earlier crypto run of this coin or strategy yet. Rex will remember this one, and show how price moved afterwards.</p>}
        {l && (<>
          <div className="flex flex-wrap items-center gap-1.5"><Badge tone={verdictTone(l.verdict)}>{l.verdict}</Badge><Badge>{l.match}</Badge>{l.interval && <Badge>{l.interval}</Badge>}</div>
          <p data-testid="last-time-headline" className="text-text">{l.headline}</p>
          <div className="grid grid-cols-3 gap-2 text-xs">
            {([["5 days later", l.fwd5], ["20 days later", l.fwd20], ["Since then", l.since_pct]] as [string, number | null][]).map(([k, x]) => (
              <div key={k} className="rounded-lg border border-line bg-ink p-2"><div className="text-mute">{k}</div><div className={cn("num text-sm font-semibold", x == null ? "text-mute" : x >= 0 ? "text-gain" : "text-loss")}>{x == null ? "pending" : pp(x, 1)}</div></div>))}
          </div>
          <p data-testid="last-time-lesson" className="rounded-lg border border-line bg-panel2 p-2.5 text-xs"><span className="font-semibold text-rex">Lesson: </span>{l.lesson}</p>
          {d && d.count > 1 && <p className="text-xs text-mute">{d.count - 1} more similar run{d.count > 2 ? "s" : ""} remembered. <Link className="underline" href="/crypto/memory">See all</Link>.</p>}
        </>)}
        <p className="text-xs text-mute">Price moves after each run come from real Binance daily closes. Crypto memory is kept apart from your stock memory.</p>
      </CardBody>
    </Card>
  );
}
