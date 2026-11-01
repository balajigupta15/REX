"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Card, CardBody } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { PageHead } from "@/components/page-head";
import { NewsList } from "@/components/quant-bits";
import { get, post } from "@/lib/api";

interface Meta { available: boolean; reason?: string; name?: string; rank?: number | null; description?: string; categories?: string[]; homepage?: string | null; market_cap_usd?: number | null; volume_24h_usd?: number | null; circulating_supply?: number | null; max_supply?: number | null; ath_usd?: number | null; ath_date?: string | null; genesis_date?: string | null }
const big = (x?: number | null) => (x == null ? "n/a" : x >= 1e12 ? `$${(x / 1e12).toFixed(2)}T` : x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : `$${(x / 1e6).toFixed(0)}M`);
function CoinMeta({ symbol }: { symbol: string }) {
  const [m, setM] = useState<Meta | null>(null);
  useEffect(() => { get<Meta>(`/assets/meta?symbol=${encodeURIComponent(symbol)}`).then(setM).catch(() => setM(null)); }, [symbol]);
  if (!m) return null;
  if (!m.available) return <Card data-testid="coin-meta"><CardBody className="text-xs text-mute">Coin profile unavailable: {m.reason}</CardBody></Card>;
  return (
    <Card data-testid="coin-meta"><CardBody className="space-y-2">
      <div className="text-sm font-medium">About {m.name}{m.rank ? <span className="ml-2 text-xs font-normal text-mute">CoinGecko rank #{m.rank}</span> : null}</div>
      {m.description && <p className="text-sm text-text/90">{m.description}</p>}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-mute">
        <span>Market cap <span className="num text-text">{big(m.market_cap_usd)}</span></span><span>24h volume <span className="num text-text">{big(m.volume_24h_usd)}</span></span>
        <span>Circulating <span className="num text-text">{m.circulating_supply ? Math.round(m.circulating_supply).toLocaleString("en-US") : "n/a"}</span></span>
        <span>Max supply <span className="num text-text">{m.max_supply ? Math.round(m.max_supply).toLocaleString("en-US") : "none"}</span></span>
        <span>All-time high <span className="num text-text">{m.ath_usd == null ? "n/a" : `$${m.ath_usd.toLocaleString("en-US")}`}{m.ath_date ? ` (${m.ath_date})` : ""}</span></span>
        {m.homepage && <a className="underline" href={m.homepage} target="_blank" rel="noopener noreferrer">Website</a>}
      </div>
      <p className="text-[11px] text-mute">Profile data from CoinGecko. Prices come from the exchange, not from here.</p>
    </CardBody></Card>
  );
}

interface Q { symbol: string; short: string; name: string; asset_class: "stock" | "crypto"; market: string; price: number | null; price_time?: string; live?: boolean; source?: string; state?: string; currency?: string; last_close?: number; last_close_date?: string; next_open?: string; error?: string }

function AssetView() {
  const sym = useSearchParams().get("symbol") ?? "";
  const router = useRouter();
  const [q, setQ] = useState<Q | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!sym) return;
    let alive = true; let t: any;
    const tick = () => get<Q>(`/assets/quote?symbol=${encodeURIComponent(sym)}`).then((d) => { if (alive) { setQ(d); setErr(null); } }).catch((e) => alive && setErr(e.message)).finally(() => { if (alive) t = setTimeout(tick, sym.endsWith("USDT") ? 3000 : 15000); });
    tick();
    return () => { alive = false; clearTimeout(t); };
  }, [sym]);
  if (!sym) return <Alert tone="info" title="No asset selected">Press Ctrl/Cmd+K and search for a stock or coin.</Alert>;
  if (err && !q) return <Alert title="Could not open this asset">{err}</Alert>;
  if (!q) return <p className="text-sm text-mute">Loading…</p>;
  const stock = q.asset_class === "stock";
  const cur = q.currency === "INR" ? "₹" : "$";
  const track = async () => {
    setBusy(true); setErr(null);
    try {
      const s = stock ? await post<any>("/paper/start", { ticker: q.symbol, template: "breakout", params: {}, exits: {}, cost_bps: 15, label: "original" })
                      : await post<any>("/crypto/tracker/start", { symbol: q.symbol, interval: "1d", template: "breakout", params: {}, exits: {}, cost_bps: 10, label: "original" });
      router.push(stock ? `/paper?id=${s.id}` : `/crypto/tracker?id=${s.id}`);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  const acts: [string, string, string][] = stock
    ? [["act-research", "Research", `/research?t=${q.symbol}`], ["act-build", "Build strategy", `/build?t=${q.symbol}`], ["act-check", "Pre-trade check", `/check?symbol=${q.symbol}`]]
    : [["act-build", "Crypto Build", `/crypto?coin=${q.symbol}`], ["act-tracker", "Crypto Tracker", "/crypto/tracker"]];
  return (
    <div className="space-y-4" data-testid="asset-view" data-symbol={q.symbol} data-class={q.asset_class}>
      <PageHead title={`${q.short} · ${q.name}`} sub={`${stock ? "NSE stock" : "Crypto (Binance USDT pair)"}. ${q.market}.`}
        right={<Badge tone={q.live ? "gain" : "neutral"}>{stock ? (q.live ? "Live (~15 min delayed)" : "Last close") : (q.live ? "Real-time" : "Cached")}</Badge>} />
      <Card><CardBody className="flex flex-wrap items-end gap-x-8 gap-y-2">
        <div><div className="text-xs text-mute">{stock ? "Price" : "Price (USDT)"}</div>
          <div className="num text-3xl font-semibold" data-testid="asset-price">{q.price == null ? "n/a" : `${cur}${q.price.toLocaleString(stock ? "en-IN" : "en-US", { maximumFractionDigits: q.price < 10 ? 4 : 2 })}`}</div></div>
        <div className="text-xs text-mute" data-testid="asset-state">{q.state}{q.price_time ? ` · ${q.price_time}` : ""}{q.source ? ` · ${q.source}` : ""}{stock && q.next_open ? ` · next open ${q.next_open}` : ""}</div>
        {q.error && <div className="text-xs text-loss">{q.error}</div>}
      </CardBody></Card>
      <div className="flex flex-wrap items-center gap-2" data-testid="asset-actions">
        {acts.map(([id, label, href]) => <Link key={id} href={href}><Button variant="outline" data-testid={id}>{label}</Button></Link>)}
        <Button onClick={track} disabled={busy} data-testid="act-track">{busy ? "Starting…" : "Track (default breakout strategy)"}</Button>
      </div>
      {!stock && <CoinMeta symbol={q.symbol} />}
      <NewsList symbol={q.symbol} />
      {err && <Alert title="Something went wrong">{err}</Alert>}
      <p className="text-xs text-mute">Rex never places orders. Tracking follows the live price against the strategy's rules; you trade in your own {stock ? "broker" : "exchange"} app.</p>
    </div>
  );
}

export default function AssetPage() {
  return <Suspense fallback={<p className="text-sm text-mute">Loading…</p>}><AssetView /></Suspense>;
}
