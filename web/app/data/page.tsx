"use client";
import { useCallback, useEffect, useState } from "react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { PageHead } from "@/components/page-head";
import { get, post } from "@/lib/api";

interface Part { market: string; symbol: string; interval: string; rows: number; first: string; last: string; updated_at: string; updated_age_s: number | null; validation: string; issues: { code: string; level: string; msg: string }[]; freshness: string; freshness_detail: string; source?: string | null; source_note?: string | null; crosscheck?: string | null }
interface Run { id: number; trigger: string; market: string; started_at: string; finished_at: string | null; status: string; symbols_ok: number; symbols_failed: number; rows_added: number; message: string | null; age_s?: number }
interface CC { checked_at: string | null; reachable: boolean | null; tolerance_pct?: number; errors?: string[]; results: { symbol: string; date: string; yfinance: number; nse: number | null; diff_pct: number | null; flag: string }[]; mismatches: any[]; note?: string }
interface PStatus {
  enabled: boolean; store: string; format: string; now_utc: string; schedule: string; readers: string;
  totals: { partitions: number; rows: number; stale: number; missing: number; with_issues: number };
  last_run: Record<string, Run | null>; next_run: Record<string, string>; recent_runs: Run[]; partitions: Part[]; missing: { market: string; symbol: string; interval: string }[];
  modes: Record<string, string>; sources?: Record<string, string>; crosscheck?: CC | null; trigger: { running: boolean; last: any; error: string | null };
}

const ago = (s: number | null | undefined) => (s == null ? "n/a" : s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`);
const tone = (s: string): "gain" | "loss" | "warn" | "neutral" => (s === "ok" || s === "seeded" || s === "fresh" ? "gain" : s === "failed" || s === "error" || s === "crashed" ? "loss" : s === "partial" || s === "stale" || s === "warn" || s === "running" ? "warn" : "neutral");
const day = (t: string | null) => (t ? t.slice(0, 16).replace("T", " ") : "n/a");

export default function DataPage() {
  const [s, setS] = useState<PStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [ccBusy, setCcBusy] = useState(false);
  const load = useCallback(() => get<PStatus>("/pipeline/status").then((d) => { setS(d); setErr(null); }).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const run = async (market: string, offline?: boolean) => {
    setBusy(market + String(offline)); setErr(null);
    try { await post("/pipeline/run", { market, offline: offline ?? null }); setTimeout(load, 1500); setTimeout(load, 6000); }
    catch (e: any) { setErr(e.message); } finally { setTimeout(() => setBusy(null), 1200); }
  };
  const crosscheck = async () => {
    setCcBusy(true); setErr(null);
    try { await post("/pipeline/crosscheck", {}); await load(); } catch (e: any) { setErr(e.message); } finally { setCcBusy(false); }
  };
  if (err && !s) return <Alert title="Could not load pipeline status">{err}</Alert>;
  if (!s) return <p className="text-sm text-mute">Loading…</p>;
  const running = s.trigger.running;
  return (
    <div className="space-y-5" data-testid="data-page">
      <PageHead title="Data pipeline" sub="Where Rex's prices come from, how fresh they are, and whether they passed validation."
        right={<div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => run("all", true)} disabled={running || !!busy} data-testid="seed-btn">Seed from bundled cache</Button>
          <Button size="sm" onClick={() => run("all")} disabled={running || !!busy} data-testid="run-now">{running ? "Running…" : "Run ingestion now"}</Button></div>} />
      {err && <Alert title="Problem">{err}</Alert>}
      {s.trigger.error && <Alert title="Last manual run failed">{s.trigger.error}</Alert>}
      {!s.enabled && <Alert tone="info" title="The store is empty">The app is reading the bundled CSV cache. Seed the store from it (no network), or run the pipeline: <code>python -m pipeline run</code> or <code>docker compose up</code>. Loaders read the store first once it exists.</Alert>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="data-tiles">
        {[["Partitions", `${s.totals.partitions}`, `${s.totals.missing} missing`], ["Rows stored", s.totals.rows.toLocaleString("en-US"), s.format],
          ["Stale", `${s.totals.stale}`, "last bar behind schedule"], ["With validation issues", `${s.totals.with_issues}`, "see table"]].map(([l, v, sub]) => (
          <Card key={l}><CardBody className="p-3"><div className="text-xs text-mute">{l}</div><div className="num text-xl font-semibold">{v}</div><div className="truncate text-[11px] text-mute">{sub}</div></CardBody></Card>))}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {(["nse", "crypto"] as const).map((m) => { const r = s.last_run[m]; return (
          <Card key={m} data-testid={`data-last-run-${m}`}><CardHeader className="p-3 pb-1"><div className="flex items-center gap-2"><CardTitle className="text-sm">{m === "nse" ? "NSE stocks (daily)" : "Crypto (1d / 4h / 1h)"}</CardTitle>
            {r && <Badge tone={tone(r.status)}>{r.status}</Badge>}</div>
            <CardDesc className="text-xs">Mode: {s.modes[m]} · next run {m === "nse" ? s.next_run.nse_ist : s.next_run.crypto}</CardDesc></CardHeader>
            <CardBody className="p-3 pt-1 text-xs text-mute">{r ? <>Last run #{r.id} {ago(r.age_s)} ({r.trigger}): {r.symbols_ok} ok, {r.symbols_failed} failed, +{r.rows_added.toLocaleString("en-US")} rows.{r.message ? <span className="text-loss"> {r.message}</span> : null}</> : "No run yet."}</CardBody></Card>); })}
      </div>

      <Card><CardHeader className="p-3 pb-1"><CardTitle className="text-sm">Freshness per symbol</CardTitle><CardDesc className="text-xs">{s.schedule} Store: <span className="num">{s.store}</span></CardDesc></CardHeader>
        <CardBody className="overflow-x-auto p-0">
          <table className="w-full text-xs" data-testid="data-table">
            <thead className="border-y border-line text-left text-mute"><tr>{["Market", "Symbol", "Interval", "Rows", "First", "Last bar", "Updated", "Freshness", "Validation", "Source", "Cross-check"].map((h) => <th key={h} className="px-3 py-1.5 font-medium">{h}</th>)}</tr></thead>
            <tbody>{s.partitions.map((p) => (
              <tr key={`${p.market}${p.symbol}${p.interval}`} className="border-b border-line/60" data-testid="data-row" data-symbol={p.symbol} data-interval={p.interval} data-freshness={p.freshness}>
                <td className="px-3 py-1.5 text-mute">{p.market}</td><td className="px-3 py-1.5 font-medium">{p.symbol}</td><td className="px-3 py-1.5">{p.interval}</td>
                <td className="num px-3 py-1.5 text-right">{p.rows.toLocaleString("en-US")}</td><td className="num px-3 py-1.5">{day(p.first).slice(0, 10)}</td><td className="num px-3 py-1.5" title={p.freshness_detail}>{day(p.last)}</td>
                <td className="px-3 py-1.5 text-mute">{ago(p.updated_age_s)}</td><td className="px-3 py-1.5"><Badge tone={tone(p.freshness)}>{p.freshness}</Badge></td>
                <td className="px-3 py-1.5" title={p.issues.map((i) => i.msg).join("; ")}><Badge tone={tone(p.validation)}>{p.validation}{p.issues.length ? ` · ${p.issues.length}` : ""}</Badge></td>
                <td className="px-3 py-1.5" data-testid="data-source" title={p.source_note ?? ""}>{p.source ?? <span className="text-mute">seed</span>}{p.source_note ? <span className="text-warn"> *</span> : null}</td>
                <td className="px-3 py-1.5" data-testid="data-crosscheck">{p.crosscheck ? <Badge tone={p.crosscheck === "ok" ? "gain" : "warn"}>{p.crosscheck}</Badge> : <span className="text-mute">n/a</span>}</td></tr>))}
              {s.missing.map((p) => <tr key={`m${p.market}${p.symbol}${p.interval}`} className="border-b border-line/60 text-mute"><td className="px-3 py-1.5">{p.market}</td><td className="px-3 py-1.5">{p.symbol}</td><td className="px-3 py-1.5">{p.interval}</td><td colSpan={8} className="px-3 py-1.5">not ingested yet</td></tr>)}
            </tbody></table></CardBody></Card>

      <Card data-testid="sources-card"><CardHeader className="p-3 pb-1"><div className="flex items-center justify-between gap-2"><CardTitle className="text-sm">Sources and cross-check</CardTitle>
          <Button variant="outline" size="sm" onClick={crosscheck} disabled={ccBusy} data-testid="crosscheck-btn">{ccBusy ? "Checking…" : "Run cross-check"}</Button></div>
          <CardDesc className="text-xs">Every source is free and keyless. The first source in a chain that answers is used; the table shows which one served each symbol.</CardDesc></CardHeader>
        <CardBody className="space-y-2 p-3 pt-1 text-xs text-mute">
          {s.sources && <ul className="space-y-0.5" data-testid="sources-list">{Object.entries(s.sources).map(([k, v]) => <li key={k}><span className="inline-block w-24 text-text/80">{k.replace("_", " ")}</span>{v}</li>)}</ul>}
          {s.crosscheck?.checked_at
            ? <p data-testid="crosscheck-summary">{s.crosscheck.reachable
                ? <>Last cross-check {day(s.crosscheck.checked_at)} UTC: {s.crosscheck.results.filter((r) => r.flag === "ok").length} of {s.crosscheck.results.length} closes within {s.crosscheck.tolerance_pct}% of the NSE bhavcopy; <span className={s.crosscheck.mismatches.length ? "text-warn" : ""}>{s.crosscheck.mismatches.length} mismatches</span>. {s.crosscheck.note}</>
                : <>Last cross-check {day(s.crosscheck.checked_at)} UTC could not reach the NSE bhavcopy ({(s.crosscheck.errors ?? []).join("; ") || "unreachable"}). Nothing was changed.</>}</p>
            : <p>The stored NSE closes have not been compared with the exchange's official bhavcopy yet.</p>}
          {s.crosscheck?.mismatches.length ? <table className="w-full"><thead className="text-left"><tr><th className="py-1">Symbol</th><th>Date</th><th>yfinance</th><th>NSE</th><th>Diff %</th></tr></thead>
            <tbody>{s.crosscheck.mismatches.map((m: any, i: number) => <tr key={i}><td className="py-0.5">{m.symbol}</td><td>{m.date}</td><td className="num">{m.yfinance}</td><td className="num">{m.nse ?? "n/a"}</td><td className="num text-warn">{m.diff_pct ?? "n/a"}</td></tr>)}</tbody></table> : null}
        </CardBody></Card>

      <Card><CardHeader className="p-3 pb-1"><CardTitle className="text-sm">Run log</CardTitle></CardHeader>
        <CardBody className="overflow-x-auto p-0">
          <table className="w-full text-xs" data-testid="run-log"><thead className="border-y border-line text-left text-mute"><tr>{["#", "Started (UTC)", "Market", "Trigger", "Status", "OK", "Failed", "Rows added"].map((h) => <th key={h} className="px-3 py-1.5 font-medium">{h}</th>)}</tr></thead>
            <tbody>{s.recent_runs.map((r) => <tr key={r.id} className="border-b border-line/60"><td className="num px-3 py-1.5">{r.id}</td><td className="num px-3 py-1.5">{day(r.started_at)}</td><td className="px-3 py-1.5">{r.market}</td><td className="px-3 py-1.5 text-mute">{r.trigger}</td>
              <td className="px-3 py-1.5"><Badge tone={tone(r.status)}>{r.status}</Badge></td><td className="num px-3 py-1.5">{r.symbols_ok}</td><td className="num px-3 py-1.5">{r.symbols_failed}</td><td className="num px-3 py-1.5">{r.rows_added.toLocaleString("en-US")}</td></tr>)}
              {s.recent_runs.length === 0 && <tr><td colSpan={8} className="px-3 py-3 text-mute">No runs yet.</td></tr>}</tbody></table></CardBody></Card>
      <p className="text-xs text-mute">{s.readers} Validation checks: duplicates, gaps, OHLC sanity (high ≥ open/close/low ≥ low), non-positive prices, stale last bar. Rows that fail are dropped and counted, never silently kept.</p>
    </div>
  );
}
