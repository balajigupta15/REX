"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { get } from "@/lib/api";

export interface AssetHit { symbol: string; short: string; name: string; asset_class: "stock" | "crypto"; market: string }

/** Global asset search: click or Ctrl/Cmd+K. Results grouped Stocks / Crypto, arrow keys + Enter, Esc closes. */
export function AssetSearch() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<AssetHit[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (!open) { setQ(""); setSel(0); } else inputRef.current?.focus(); }, [open]);   // reset on close, so keystrokes typed right after opening are never wiped
  useEffect(() => {
    if (!open) return;
    const id = ++seq.current;
    const t = setTimeout(() => {
      get<{ results: AssetHit[] }>(`/assets/search?q=${encodeURIComponent(q)}&limit=12`)
        .then((d) => { if (id === seq.current) { setHits(d.results); setErr(null); setSel(0); } })
        .catch((e) => id === seq.current && setErr(e.message));
    }, 100);
    return () => clearTimeout(t);
  }, [q, open]);

  // display order = Stocks first, then Crypto (the same order the arrow keys walk)
  const groups = useMemo(() => [["stock", "Stocks (NSE)"], ["crypto", "Crypto"]].map(([k, label]) => ({ k, label, items: hits.filter((h) => h.asset_class === k) })).filter((g) => g.items.length), [hits]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const choose = useCallback((h: AssetHit) => { setOpen(false); router.push(`/asset?symbol=${encodeURIComponent(h.symbol)}`); }, [router]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter" && flat[sel]) { e.preventDefault(); choose(flat[sel]); }
  };
  useEffect(() => { document.querySelector(`[data-idx="${sel}"]`)?.scrollIntoView({ block: "nearest" }); }, [sel]);

  return (
    <>
      <button onClick={() => setOpen(true)} data-testid="asset-search-btn" aria-label="Search assets"
        className="flex items-center gap-2 rounded-md border border-line bg-panel px-2.5 py-1 text-xs text-mute transition hover:text-text">
        <Search className="h-3.5 w-3.5" /><span className="hidden sm:inline">Search assets</span>
        <kbd className="hidden rounded border border-line px-1 font-mono text-[10px] sm:inline">Ctrl K</kbd>
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-[12vh]" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }} data-testid="asset-search-overlay">
          <div className="w-full max-w-lg overflow-hidden rounded-lg border border-line bg-panel" role="dialog" aria-label="Search assets">
            <div className="flex items-center gap-2 border-b border-line px-3">
              <Search className="h-4 w-4 text-mute" />
              <input ref={inputRef} autoFocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} data-testid="asset-search-input" autoComplete="off" spellCheck={false}
                placeholder="Search stocks and crypto: reliance, tcs, btc, bitcoin, sol…" className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-mute" />
              <kbd className="rounded border border-line px-1 font-mono text-[10px] text-mute">Esc</kbd>
            </div>
            <div className="max-h-80 overflow-y-auto py-1" data-testid="asset-results">
              {err && <div className="px-3 py-2 text-xs text-loss">{err}</div>}
              {!err && flat.length === 0 && <div className="px-3 py-6 text-center text-xs text-mute" data-testid="asset-empty">No asset matches “{q}”. Rex covers 10 NSE stocks and BTC, ETH, SOL.</div>}
              {groups.map((g) => (
                <div key={g.k} data-testid={`asset-group-${g.k}`}>
                  <div className="px-3 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-mute">{g.label}</div>
                  {g.items.map((h) => { const i = flat.indexOf(h); return (
                    <button key={h.symbol} data-idx={i} data-testid="asset-result" data-symbol={h.symbol} data-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => choose(h)}
                      className={`flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm ${i === sel ? "bg-panel2" : ""}`}>
                      <span className="num w-28 shrink-0 font-medium">{h.short}</span>
                      <span className="truncate text-mute">{h.name}</span>
                      <span className="ml-auto shrink-0 text-[10px] text-mute">{h.asset_class === "crypto" ? "Crypto" : "NSE"}</span>
                    </button>); })}
                </div>))}
            </div>
            <div className="flex gap-3 border-t border-line px-3 py-1.5 text-[10px] text-mute"><span>↑↓ navigate</span><span>Enter open</span><span>Esc close</span></div>
          </div>
        </div>)}
    </>
  );
}
