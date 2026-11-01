"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/misc";
import { aiGet, aiPost, errText, pc } from "./lib";

export function CodeBlock({ code, title }: { code: string; title: string }) {
  return (
    <details className="rounded border border-line">
      <summary className="cursor-pointer select-none px-3 py-1.5 text-sm text-mute hover:text-text">{title}</summary>
      <pre className="max-h-80 overflow-auto border-t border-line bg-ink/60 p-3 font-mono text-xs leading-relaxed" data-testid="code-block">{code}</pre>
    </details>
  );
}

/** "Track this live": a paper watch. The AI-written code is re-run on the latest CLOSED candles every few seconds; nothing is ordered. */
export function TrackLive({ versionId, symbol }: { versionId: string; symbol: string }) {
  const [wid, setWid] = useState<string | null>(null);
  const [live, setLive] = useState<any>(null);
  const [state, setState] = useState("loading");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    setWid(null); setLive(null); setState("loading"); setErr(null);
    aiGet(`/watches?version_id=${encodeURIComponent(versionId)}`).then(async (r) => {
      const watch = r.watches.find((w: any) => w.version_id === versionId && w.status !== "stopped");
      if (!alive) return;
      if (!watch) { setState("idle"); return; }
      const current = await aiGet(`/watches/${watch.id}`);
      if (alive) { setWid(watch.id); setLive(current.live); setState(current.status); }
    }).catch((e) => { if (alive) { setErr(errText(e)); setState("unavailable"); } });
    return () => { alive = false; };
  }, [versionId]);
  useEffect(() => {
    if (!wid || state !== "running") return;
    let alive = true, pending = false;
    const tick = async () => {
      if (document.hidden || pending) return;
      pending = true;
      try { const r = await aiGet(`/watches/${wid}`); if (alive) { setLive(r.live); setState(r.status); setErr(null); } }
      catch (e) { if (alive) setErr(errText(e)); }
      finally { pending = false; }
    };
    const t = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [wid, state]);
  const start = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try { const r = await aiPost(`/versions/${versionId}/track`); setWid(r.watch_id); setLive(r.live); setState("running"); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  };
  const stop = async () => {
    if (!wid || busy) return;
    setBusy(true); setErr(null);
    try { await aiPost(`/watches/${wid}/stop`); setState("stopped"); }
    catch (e) { setErr(`Could not stop. Retry Stop: ${errText(e)}`); } finally { setBusy(false); }
  };
  return <div className="space-y-2 border-t border-line pt-3" data-testid="track-live">
    <div className="flex items-center gap-3">
      {state === "running" ? <Button variant="outline" onClick={stop} disabled={busy}>{busy ? "Stopping…" : "Stop paper watch"}</Button>
        : <Button variant="outline" onClick={start} disabled={busy || state === "loading" || state === "unavailable"}>{state === "loading" ? "Checking watches…" : busy ? "Starting…" : state === "stopped" ? "Start new paper watch" : "Track this live"}</Button>}
      <span className="text-xs text-mute">{state === "running" ? "Paper watch running" : state === "stopped" ? "Stopped" : `Paper watch on ${symbol}`}. No orders are placed.</span>
    </div>
    {err && <Alert tone="warn">{err}{state === "unavailable" && <Button size="sm" onClick={() => window.location.reload()}>Reload to retry</Button>}</Alert>}
    {live && <div className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      <div><span className="text-mute">Strategy position: </span><b>{live.position}</b> (since {live.last_change_bar}, {live.bars_in_position} bars)</div>
      <div><span className="text-mute">Last closed candle: </span>{live.last_closed_bar}</div>
      <div><span className="text-mute">Price: </span>{live.price?.price ? Number(live.price.price).toLocaleString() : "n/a"}</div>
      <div><span className="text-mute">Paper result: </span>{live.paper?.trades ?? 0} closed trades, {live.paper?.return_pct ?? 0}%</div>
      <p className="text-xs text-mute sm:col-span-2">{live.note}</p>
    </div>}
  </div>;
}
