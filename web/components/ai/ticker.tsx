"use client";
import { useEffect, useRef, useState } from "react";
import { get } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Quote { symbol: string; price: number; live: boolean; stale?: boolean; provider: string }
/** Live crypto price from /crypto/price, refreshed about every 3 s (paused while the tab is hidden). Shows honest stale or unavailable states. */
export function PriceTicker({ symbol = "BTCUSDT", every = 3000 }: { symbol?: string; every?: number }) {
  const [q, setQ] = useState<Quote | null>(null);
  const [err, setErr] = useState(false);
  const prev = useRef<number | null>(null);
  const [dir, setDir] = useState(0);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const r = await get<Quote>(`/crypto/price?symbol=${symbol}`);
        if (!alive) return;
        if (prev.current != null && r.price !== prev.current) setDir(r.price > prev.current ? 1 : -1);
        prev.current = r.price; setQ(r); setErr(false);
      } catch { if (alive) setErr(true); }
    };
    tick();
    const t = setInterval(tick, every);
    return () => { alive = false; clearInterval(t); };
  }, [symbol, every]);
  return (
    <div className="flex items-baseline gap-3 rounded-md border border-line bg-panel px-4 py-2.5" data-testid="price-ticker">
      <span className="text-xs text-mute">{symbol.replace("USDT", "")} / USDT</span>
      {q ? <span className={cn("num text-xl font-semibold tabular-nums", dir > 0 && "text-gain", dir < 0 && "text-loss")}>{q.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
        : <span className="num text-xl text-mute">{err ? "unavailable" : "loading"}</span>}
      <span className="ml-auto text-xs text-mute">{err && q ? "reconnecting" : q ? `${q.live ? "live" : q.stale ? "stale, cached" : "cached"} via ${q.provider}` : err ? "price feed unreachable" : ""}</span>
    </div>
  );
}
