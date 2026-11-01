"use client";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { get } from "@/lib/api";
import { useRex } from "@/lib/store";
import { useCrypto } from "@/lib/crypto";
import { Alert, Skeleton } from "@/components/ui/misc";
import { Button } from "@/components/ui/button";

function LoadResult({ market, kind, children }: { market: "nse" | "crypto"; kind: "backtest" | "refine"; children: ReactNode }) {
  const id = useSearchParams().get("id");
  const stock = useRex(), crypto = useCrypto();
  const [loaded, setLoaded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    setError(null);
    get<any>(`/results/${encodeURIComponent(id)}`, { signal: controller.signal }).then((r) => {
      if (controller.signal.aborted) return;
      if ((!!r.spec?.symbol !== (market === "crypto")) || (!!r.refined !== (kind === "refine"))) throw new Error("This link points to a different result type. Open it from Memory.");
      const store = market === "crypto" ? crypto : stock;
      if (kind === "refine") store.setRefine(r); else store.setBacktest(r);
      setLoaded(id);
    }).catch((e) => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
    // Store setters only persist the fetched snapshot; refetch on result identity, not context updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, market, kind, attempt]);
  if (id && loaded !== id) return error
    ? <Alert title="Could not reopen this result">{error} <Button size="sm" onClick={() => setAttempt((n) => n + 1)}>Retry</Button> <Link className="underline" href={market === "crypto" ? "/crypto/memory" : "/memory"}>Open Memory</Link></Alert>
    : <Skeleton className="h-96" />;
  return children;
}

export function SavedResult(props: { market: "nse" | "crypto"; kind: "backtest" | "refine"; children: ReactNode }) {
  return <Suspense fallback={<Skeleton className="h-96" />}><LoadResult {...props} /></Suspense>;
}
