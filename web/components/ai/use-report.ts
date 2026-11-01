"use client";
import { useCallback, useState } from "react";
import type { Agent, Store } from "./agents";
import { runReport, type ReportProgress } from "./report";

export interface ReportRun { busy: boolean; progress: ReportProgress | null; err: string | null }
/** Runs report jobs for non-trading agents. State is in memory (a report takes seconds); the finished report is saved on the agent. */
export function useReportRuns(store: Pick<Store, "patch">) {
  const [runs, setRuns] = useState<Record<string, ReportRun>>({});
  const run = useCallback(async (a: Agent) => {
    if (a.kind !== "analyst") return;                                             // trading agents never go through here
    setRuns((r) => ({ ...r, [a.id]: { busy: true, progress: null, err: null } }));
    try {
      const rep = await runReport(a, (p) => setRuns((r) => ({ ...r, [a.id]: { busy: true, progress: p, err: null } })));
      store.patch(a.id, { report: rep });
      setRuns((r) => ({ ...r, [a.id]: { busy: false, progress: r[a.id]?.progress ?? null, err: null } }));
    } catch (e) {
      setRuns((r) => ({ ...r, [a.id]: { busy: false, progress: r[a.id]?.progress ?? null, err: e instanceof Error ? e.message : String(e) } }));
    }
  }, [store]);
  return { runs, run };
}
