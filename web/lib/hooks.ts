"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

/** Fetch a GET endpoint once (and on reload()). Never throws: returns {data, error, loading}. */
export function useGet<T = any>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const latest = useRef(0);   // only the newest request may write state, so a slow earlier response can never overwrite a newer one (audit: /research?t=JIOFIN showed RELIANCE)
  const load = useCallback(() => {
    if (!path) return;
    const id = ++latest.current;
    setLoading(true);
    get<T>(path).then((d) => { if (id === latest.current) { setData(d); setError(null); } }).catch((e) => { if (id === latest.current) setError(e.message); }).finally(() => { if (id === latest.current) setLoading(false); });
  }, [path]);
  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load };
}

export interface MemStatus {
  runs: number; trades: number; lessons: number; journal_trades: number; backend: string;
  meko: { enabled: boolean; connected: boolean | null; endpoint: string; agent_id: string; tool_count: number; memory_tools: string[]; last_error: string | null; writes_ok: number; writes_failed: number; last_recall_ms: number | null; note: string };
}
// Meko verifies in the background, so poll /memory/status a few times until `connected` is known.
export function useMemStatus() {
  const [s, setS] = useState<MemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true; let n = 0; let t: any;
    const tick = () => get<MemStatus>("/memory/status").then((d) => {
      if (!alive) return; setS(d); setError(null);
      if (d.meko.enabled && d.meko.connected == null && n++ < 8) t = setTimeout(tick, 2500);
    }).catch((e) => alive && setError(e.message));
    tick();
    return () => { alive = false; clearTimeout(t); };
  }, []);
  return { status: s, error };
}


/** Tab-local drafts survive navigating away without overwriting saved values on mount. */
export function useDraft<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try { const saved = sessionStorage.getItem(key); if (saved != null) setValue(JSON.parse(saved)); } catch { /* retain default */ }
    setReady(true);
  }, [key]);
  useEffect(() => { if (ready) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* keep in-memory draft */ } } }, [key, ready, value]);
  return [value, setValue] as const;
}
