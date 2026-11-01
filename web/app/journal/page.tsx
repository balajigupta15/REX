"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Upload, FlaskConical, Trash2, AlertOctagon, FileDown } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { PageHead } from "@/components/page-head";
import { Metric } from "@/components/metric";
import { SampleBadge } from "@/components/sample-badge";
import { HoldChart, WinLossChart } from "@/components/charts";
import { Pager } from "@/components/history";
import { api, API, get, post, ApiError } from "@/lib/api";
import type { JSummary, Pattern } from "@/lib/types";
import { inr, pctPlain, pp, tone, cn } from "@/lib/utils";

function PatternCard({ p, bad }: { p: Pattern; bad: boolean }) {
  return (
    <div className={cn("rounded-lg border p-3 text-sm", bad ? "border-loss/30 bg-loss/5" : "border-gain/30 bg-gain/5")}>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Badge tone={bad ? "loss" : "gain"}>{bad ? "repeat mistake" : "your edge"}</Badge>
        <span className="num text-xs text-mute">n={p.n} · win {pctPlain(p.win_rate, 0)} vs {pctPlain(p.base_win_rate, 0)} · avg {pp(p.avg_return_pct)} · {inr(p.avg_pnl)}/trade</span>
      </div>
      <p className="text-text/90">{p.text}</p>
      {bad && p.wait_for && <p className="mt-1 text-xs text-warn">Wait for: {p.wait_for}</p>}
    </div>
  );
}

export default function Journal() {
  const [summary, setSummary] = useState<JSummary | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [trades, setTrades] = useState<any[]>([]);
  const [imp, setImp] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try { const s = await get<JSummary>("/trades/summary"); setSummary(s); const t = await get<any>(`/trades?limit=25&offset=${page * 25}`); setTrades(Array.isArray(t) ? t : t.trades ?? []); setTotal(t.n ?? t.length ?? 0); }
    catch (e: any) { setSummary(null); setTrades([]); if (!(e instanceof ApiError && e.status === 422)) setErr(e.message); }
    finally { setLoading(false); }
  }, [page]);
  useEffect(() => { refresh(); }, [refresh]);

  const upload = async (f: File) => {
    setBusy("upload"); setErr(null);
    try { const fd = new FormData(); fd.append("file", f); setImp(await api(`/trades/import?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: fd })); await refresh(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const sample = async () => { setBusy("sample"); setErr(null); try { setImp(await post("/trades/import-sample")); await refresh(); } catch (e: any) { setErr(e.message); } finally { setBusy(null); } };
  const clear = async () => { setBusy("clear"); try { await api("/trades", { method: "DELETE" }); setImp(null); await refresh(); } catch (e: any) { setErr(e.message); } finally { setBusy(null); } };

  const isSample = summary?.source_used === "sample";
  return (
    <div className="space-y-6">
      <PageHead title="Trade journal" sub="Import a broker tradebook (CSV). Rex pairs fills into round trips, tags the market context at each entry, and finds the setups you keep repeating." right={isSample ? <SampleBadge /> : undefined} />
      {err && <Alert title="Something went wrong">{err}</Alert>}

      <Card>
        <CardBody className="grid gap-4 md:grid-cols-[1fr_auto]">
          <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
            onClick={() => fileRef.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileRef.current?.click(); } }} data-testid="dropzone"
            className={cn("flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed p-8 text-center transition", drag ? "border-rex bg-rex/5" : "border-line hover:border-mute/60")}>
            <Upload className="mb-2 h-6 w-6 text-rex" />
            <div className="font-medium">{busy === "upload" ? "Importing…" : "Drop your tradebook CSV here, or click to choose"}</div>
            <div className="mt-1 text-xs text-mute">Zerodha-style columns (symbol, trade_date, trade_type, quantity, price). Headers are auto-mapped. Stays on this machine.</div>
            <input ref={fileRef} type="file" accept=".csv,text/csv" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          </div>
          <div className="flex flex-col justify-center gap-2">
            <Button onClick={sample} disabled={!!busy} size="lg"><FlaskConical className="h-4 w-4" />{busy === "sample" ? "Loading…" : "Load sample journal"}</Button>
            <Button asChild variant="outline"><a href={`${API}/trades/sample`} download><FileDown className="h-4 w-4" />Download sample CSV</a></Button>
            <p className="max-w-[16rem] text-xs text-mute">The sample is synthetic trading behaviour on real prices. It is always labelled SAMPLE DATA.</p>
          </div>
        </CardBody>
      </Card>

      {imp && (
        <Card data-testid="import-result"><CardHeader><div className="flex items-center gap-2"><CardTitle>Import result</CardTitle>{imp.is_sample && <SampleBadge />}</div><CardDesc>{imp.label}</CardDesc></CardHeader>
          <CardBody className="space-y-3 text-sm">
            <div className="grid gap-3 sm:grid-cols-4"><Metric label="Rows read" value={imp.rows_read} /><Metric label="Rows rejected" value={imp.rows_rejected.length} valueClass={imp.rows_rejected.length ? "text-warn" : undefined} />
              <Metric label="Round trips" value={imp.round_trips} sub={`${imp.new_trades} new, ${imp.duplicates_skipped} duplicates skipped`} /><Metric label="Open positions" value={imp.open_positions.length} sub="excluded from stats" /></div>
            <div><div className="mb-1 text-xs uppercase tracking-wide text-mute">Column mapping</div><div className="flex flex-wrap gap-1.5">{Object.entries(imp.column_mapping).map(([k, v]) => <Badge key={k}>{k} ← {String(v)}</Badge>)}</div></div>
            {imp.rows_rejected.length > 0 && <Alert tone="warn" title="Rejected rows (never silently dropped)"><ul className="list-inside list-disc">{imp.rows_rejected.slice(0, 8).map((r: any, i: number) => <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>)}</ul></Alert>}
            {imp.context_missing?.length > 0 && <Alert tone="warn" title="Market context missing for some symbols">{imp.context_missing.map((c: any) => `${c.ticker}: ${c.reason}`).join("; ")}</Alert>}
            {imp.open_positions.length > 0 && <div className="text-xs text-mute">Still open: {imp.open_positions.map((o: any) => `${o.symbol} ${o.qty_open} @ ${o.avg_entry}`).join(", ")}</div>}
          </CardBody></Card>
      )}

      {loading && <div className="grid gap-3 sm:grid-cols-4"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>}
      {!loading && !summary && !err && <Alert tone="info" title="No journal yet">Load the sample journal to see the mistakes dashboard, or drop in your own tradebook.</Alert>}

      {summary && (<>
        <div className="flex items-center justify-between"><h2 className="text-xl font-semibold">Mistakes dashboard {isSample && <SampleBadge />}</h2>
          <Dialog><DialogTrigger asChild><Button variant="ghost" size="sm" disabled={!!busy}><Trash2 className="h-3.5 w-3.5" />Clear journal</Button></DialogTrigger>
            <DialogContent><DialogTitle>Clear the journal?</DialogTitle><DialogDescription>This deletes imported round trips (sample and real) from this machine&apos;s Rex database. Backtest memory is kept.</DialogDescription>
              <div className="mt-5 flex justify-end gap-2"><DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose><DialogClose asChild><Button variant="danger" onClick={clear}>Clear</Button></DialogClose></div></DialogContent></Dialog></div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="journal-metrics">
          <Metric label="Win rate" value={pctPlain(summary.overall.win_rate, 0)} sub={`${summary.overall.wins} of ${summary.overall.n} round trips`} />
          <Metric label="Total P&L" value={inr(summary.overall.total_pnl)} valueClass={tone(summary.overall.total_pnl)} sub={`${inr(summary.overall.avg_pnl)} per trade`} />
          <Metric label="Avg win / avg loss" value={<span><span className="text-gain">{pp(summary.overall.avg_win_pct, 1)}</span> / <span className="text-loss">{pp(summary.overall.avg_loss_pct, 1)}</span></span>} valueClass="text-xl" sub="small wins, big losses is the classic trap" />
          <Metric label="Avg hold" value={summary.overall.avg_hold_days != null ? `${summary.overall.avg_hold_days.toFixed(1)} days` : "—"} sub={`${summary.patterns_tested} patterns tested`} />
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Card><CardHeader><CardTitle>Hold time: losers vs winners</CardTitle><CardDesc>{summary.hold_behaviour?.text ?? "Not enough winners and losers to compare hold times."}</CardDesc></CardHeader>
            <CardBody>{summary.hold_behaviour ? <HoldChart losers={summary.hold_behaviour.avg_hold_losers} winners={summary.hold_behaviour.avg_hold_winners} /> : <p className="text-sm text-mute">n/a</p>}</CardBody></Card>
          <Card><CardHeader><CardTitle>Average win vs average loss</CardTitle><CardDesc>Return per round trip, in percent.</CardDesc></CardHeader>
            <CardBody>{summary.overall.avg_win_pct != null && summary.overall.avg_loss_pct != null ? <WinLossChart win={summary.overall.avg_win_pct} loss={summary.overall.avg_loss_pct} /> : <p className="text-sm text-mute">n/a</p>}</CardBody></Card>
        </div>
        <Card><CardHeader><div className="flex items-center gap-2"><AlertOctagon className="h-4 w-4 text-loss" /><CardTitle>Top repeated mistakes</CardTitle></div>
          <CardDesc>Setups where you do clearly worse than your own average. Small samples: hints, not proof.</CardDesc></CardHeader>
          <CardBody className="space-y-3">{summary.bad_patterns.length ? summary.bad_patterns.map((p, i) => <PatternCard key={i} p={p} bad />) : <p className="text-sm text-mute">No repeat-mistake pattern found with enough trades.</p>}</CardBody></Card>
        {summary.good_patterns.length > 0 && <Card><CardHeader><div className="flex items-center gap-2"><CardTitle>Where you do well</CardTitle></div></CardHeader>
          <CardBody className="space-y-3">{summary.good_patterns.map((p, i) => <PatternCard key={i} p={p} bad={false} />)}</CardBody></Card>}
        <Card><CardHeader><CardTitle>Round trips ({total})</CardTitle></CardHeader><CardBody>
          <Table><thead><tr><Th>Symbol</Th><Th>Side</Th><Th>Entry</Th><Th>Exit</Th><Th>Hold</Th><Th>Return</Th><Th>P&L</Th></tr></thead><tbody>
            {trades.map((t, i) => <tr key={i}><Td>{t.symbol}</Td><Td>{t.direction}</Td><Td className="num">{t.entry_date}</Td><Td className="num">{t.exit_date}</Td><Td className="num">{t.hold_days}d</Td>
              <Td className={cn("num", tone(t.ret))}>{pp((t.ret ?? 0) * 100)}</Td><Td className={cn("num", tone(t.pnl))}>{inr(t.pnl)}</Td></tr>)}</tbody></Table>
          <Pager page={page} total={total} size={25} onChange={setPage} loading={loading} /></CardBody></Card>
        <div className="text-right"><Button asChild><Link href="/check">Run a pre-trade check →</Link></Button></div>
      </>)}
    </div>
  );
}
