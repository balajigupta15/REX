"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Agent, Deploy, Store } from "./agents";
import { aiGet, aiPost, errText } from "./lib";

/** Deploy to market = a PAPER deployment of the strategy the agent found. The server keeps a watch (existing /ai/versions/{id}/track): the AI-written code is re-run on the latest CLOSED candles,
 *  price comes from the live feed, and P&L is simulated since the deploy time. Signals and alerts only. Rex never places an order. Only trading agents get this; analysts never see it. */
export const PAPER_NOTE = "Paper / signals only. No real orders are placed.";
const POLL_MS = 6000;
const keep = <T,>(a: T[], n = 12) => a.slice(-n);
const lastOf = (l: any): NonNullable<Deploy["last"]> => ({ position: l.position, price: l.price?.price != null ? Number(l.price.price) : null, paperPct: l.paper ? l.paper.return_pct : null, trades: l.paper?.trades ?? 0, bar: l.last_closed_bar, at: Date.now() });

export const canDeploy = (a: Agent) => a.kind === "trading" && !!a.last?.versionId && a.last.status === "done" && a.last.decision !== "NO RESULT" && !!a.lastRunId && a.deploy?.status !== "live";

export function useDeployments(store: Pick<Store, "agents" | "patch">) {
  const { agents, patch } = store;
  const [notice, setNotice] = useState<{ id: string; msg: string } | null>(null);
  const liveKey = useMemo(() => agents.filter((a) => a.kind === "trading" && a.deploy?.status === "live" && a.deploy.watchId).map((a) => `${a.id}:${a.deploy!.watchId}`).join(","), [agents]);

  // one poller for every live deployment: refresh position / price / paper P&L, and raise an alert when the signal flips or a paper trade closes
  useEffect(() => {
    if (!liveKey) return;
    const pairs = liveKey.split(",").map((s) => s.split(":"));
    const poll = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      for (const [id, wid] of pairs) {
        aiGet(`/watches/${wid}`).then((r) => {
          if (r.status === "stopped") { patch(id, (a) => (a.deploy?.status === "live" ? { deploy: { ...a.deploy, status: "stopped", error: null, stoppedAt: Date.now() } } : {})); return; }
          const l = r.live, nl = lastOf(l);
          patch(id, (a) => {
            const d = a.deploy; if (!d || d.status !== "live") return {};
            const al = [...d.alerts];
            if (d.last && d.last.position !== nl.position) al.push({ at: Date.now(), text: `Signal changed: ${d.last.position} to ${nl.position}${nl.price ? ` near ${nl.price.toLocaleString()}` : ""}` });
            if (d.last && nl.trades > d.last.trades) { const c = l.paper?.closed?.[l.paper.closed.length - 1]; al.push({ at: Date.now(), text: `Paper trade closed${c ? `: ${c.side} ${c.return_pct >= 0 ? "+" : ""}${c.return_pct}%` : ""}` }); }
            return { deploy: { ...d, last: nl, error: null, alerts: keep(al) } };
          });
        }).catch((e) => patch(id, (a) => (a.deploy?.status === "live" ? { deploy: { ...a.deploy, error: /404|not found/i.test(errText(e)) ? "The server no longer has this deployment (it was reset). Stop it and deploy again." : `Could not refresh: ${errText(e)}. Retrying.` } } : {})));
      }
    };
    poll(); const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [liveKey, patch]);

  const deploy = useCallback(async (a: Agent) => {
    if (!canDeploy(a) || a.kind !== "trading") return;
    const base: Deploy = { status: "live", pending: true, versionId: a.last!.versionId!, runId: a.lastRunId!, title: a.last!.title || "strategy", symbol: a.symbol, startedAt: Date.now(), alerts: [] };
    setNotice(null); patch(a.id, { deploy: base });                                // optimistic: the Live badge shows at once
    try {
      const r = await aiPost(`/versions/${base.versionId}/track`);
      const nl = lastOf(r.live);
      patch(a.id, (x) => (x.deploy ? { deploy: { ...x.deploy, pending: false, watchId: r.watch_id, entryPrice: nl.price, last: nl, alerts: [{ at: Date.now(), text: `Deployed (paper). Strategy is ${nl.position} on the last closed candle${nl.price ? `, price ${nl.price.toLocaleString()}` : ""}.` }] } } : {}));
    } catch (e) { patch(a.id, { deploy: undefined }); setNotice({ id: a.id, msg: `Could not deploy: ${errText(e)}` }); }
  }, [patch]);

  const stop = useCallback(async (a: Agent) => {
    const d = a.deploy; if (!d || d.status !== "live") return;
    if (!d.watchId || d.pending) return;
    patch(a.id, { deploy: { ...d, pending: true } });
    try {
      await aiPost(`/watches/${d.watchId}/stop`);
      patch(a.id, { deploy: { ...d, pending: false, status: "stopped", error: null, stoppedAt: Date.now(), alerts: keep([...d.alerts, { at: Date.now(), text: "Stopped by you." }]) } });
    } catch (e) {
      patch(a.id, { deploy: { ...d, pending: false, error: `Could not stop: ${errText(e)}. Press Stop to retry.` } });
    }
  }, [patch]);
  return { deploy, stop, notice, clearNotice: () => setNotice(null) };
}

export function LiveBadge({ d }: { d: Deploy }) {
  return d.status === "live"
    ? <Badge tone="gain" data-testid="live-badge"><span className="h-1.5 w-1.5 rounded-full bg-gain motion-safe:animate-pulse" />Live, paper</Badge>
    : <Badge data-testid="stopped-badge">Stopped</Badge>;
}

const sgn = (x: number | null | undefined, suffix = "%") => (x == null ? "n/a" : `${x >= 0 ? "+" : ""}${x.toFixed(2)}${suffix}`);
export function DeployBox({ a, onDeploy, onStop }: { a: Agent; onDeploy: () => void; onStop: () => void }) {
  const d = a.deploy;
  if (a.kind !== "trading") return null;
  if (!d) {
    if (!canDeploy(a)) return null;
    return (
      <div className="mt-2 border-t border-line pt-2" data-testid="deploy-box">
        <div className="flex flex-wrap items-center gap-2"><Button size="sm" data-testid="deploy-start" onClick={onDeploy}>Deploy to market</Button><span className="text-[11px] text-mute">{PAPER_NOTE}</span></div>
        {(a.last?.decision === "DO NOT TRADE" || a.last?.decision === "WEAK EVIDENCE") && <p className="mt-1 text-[11px] text-warn">Rex's call was {a.last.decision}. A paper deployment lets you watch it anyway; it risks nothing.</p>}
      </div>);
  }
  const l = d.last;
  const move = l?.price != null && d.entryPrice ? ((l.price - d.entryPrice) / d.entryPrice) * 100 : null;
  return (
    <div className="mt-2 space-y-1.5 border-t border-line pt-2" data-testid="deploy-box" data-deploy={d.status}>
      <div className="flex flex-wrap items-center gap-2"><LiveBadge d={d} /><span className="text-[11px] text-mute">{PAPER_NOTE}</span>
        {d.status === "live" && <Button size="sm" variant="danger" className="ml-auto" data-testid="deploy-stop" onClick={onStop} disabled={d.pending}>Stop</Button>}</div>
      <div className="grid min-h-[3.25rem] grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
        <div><span className="text-mute">Position: </span><b data-testid="deploy-position">{d.pending ? "starting..." : l?.position ?? "n/a"}</b></div>
        <div><span className="text-mute">Price: </span>{l?.price != null ? l.price.toLocaleString() : "n/a"}</div>
        <div><span className="text-mute">Since deploy: </span>{sgn(move)}{d.entryPrice ? <span className="text-mute"> (from {d.entryPrice.toLocaleString()})</span> : null}</div>
        <div><span className="text-mute">Paper P&amp;L: </span><span className={cn(l?.paperPct != null && (l.paperPct >= 0 ? "text-gain" : "text-loss"))}>{sgn(l?.paperPct)}</span> <span className="text-mute">({l?.trades ?? 0} trades)</span></div>
      </div>
      {d.error && d.status === "live" && <p className="text-[11px] text-warn" role="status">{d.error}</p>}
      {d.alerts.length > 0 && <ul className="max-h-20 space-y-0.5 overflow-auto text-[11px] text-mute" data-testid="deploy-alerts">{[...d.alerts].reverse().map((x, i) => <li key={i}><span className="font-mono">{new Date(x.at).toLocaleTimeString()}</span> {x.text}</li>)}</ul>}
    </div>
  );
}
