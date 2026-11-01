"use client";
import { useState } from "react";
import Link from "next/link";
import { useGet } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Skeleton } from "@/components/ui/misc";

export function Pager({ page, total, size, onChange, loading = false }: { page: number; total: number; size: number; onChange: (page: number) => void; loading?: boolean }) {
  return <div className="mt-3 flex items-center justify-between gap-3 text-xs" aria-label="Pagination">
    <Button size="sm" variant="outline" disabled={!page || loading} onClick={() => onChange(page - 1)}>Previous</Button>
    <span role="status">{total ? `${page * size + 1}–${Math.min((page + 1) * size, total)} of ${total}` : "No entries"}</span>
    <Button size="sm" variant="outline" disabled={(page + 1) * size >= total || loading} onClick={() => onChange(page + 1)}>Next</Button>
  </div>;
}

export function ResultHistory({ market = "nse" }: { market?: "nse" | "crypto" }) {
  const [page, setPage] = useState(0);
  const { data, error, loading, reload } = useGet<{ total: number; results: { id: string; kind: string; symbol: string; ts: number }[] }>(`/results?market=${market}&limit=20&offset=${page * 20}`);
  return <Card><CardHeader><CardTitle>Saved results</CardTitle></CardHeader><CardBody>
    {error && <Alert>{error} <Button size="sm" onClick={reload}>Retry</Button></Alert>}
    {loading ? <Skeleton className="h-24" /> : <ul className="space-y-2 text-sm">{data?.results.map((r) => <li key={r.id}>
      <Link className="underline" href={`${market === "crypto" ? "/crypto" : ""}/${r.kind === "refine" ? "refine" : "results"}?id=${r.id}`}>{r.symbol} · {r.kind} · {new Date(r.ts * 1000).toLocaleString()}</Link>
    </li>)}</ul>}
    {data?.total === 0 && <p className="text-sm text-mute">New backtests and refinements appear here with a link to their full result. Older runs retain their summaries below.</p>}
    <Pager page={page} total={data?.total ?? 0} size={20} onChange={setPage} loading={loading} />
  </CardBody></Card>;
}

export function StrategyHistory() {
  const [page, setPage] = useState(0);
  const { data, error, loading } = useGet<any>(`/memory/strategy-runs?limit=20&offset=${page * 20}`);
  return <Card><CardHeader><CardTitle>Strategy runs and tracked trades</CardTitle></CardHeader><CardBody>
    {error && <Alert>{error}</Alert>}
    {loading ? <Skeleton className="h-24" /> : <ul className="space-y-3 text-sm">{data?.runs.map((r: any) => <li key={r.id}>
      <b>{r.ticker} · {r.template}</b> · {r.verdict}<p>{r.headline}</p><p className="text-xs text-mute">{r.lesson}</p>
      {r.session_id && <Link className="underline text-xs" href={`/paper?id=${r.session_id}`}>Open tracker</Link>}
    </li>)}</ul>}
    {data?.total === 0 && <p className="text-sm text-mute">No strategy runs yet.</p>}
    <Pager page={page} total={data?.total ?? 0} size={20} onChange={setPage} loading={loading} />
  </CardBody></Card>;
}
