"use client";
import { useEffect, useState } from "react";
import { Button } from "./ui/button";

/** Optional LLM explainer. Hidden unless the Next server has GROQ_API_KEY or GEMINI_API_KEY. The app works fully without it. */
export function Explain({ kind, facts }: { kind: string; facts: unknown }) {
  const [enabled, setEnabled] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { fetch("/api/explain").then((r) => r.json()).then((j) => setEnabled(!!j.enabled)).catch(() => {}); }, []);
  if (!enabled) return null;
  const go = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/explain", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind, facts }) });
      const j = await r.json(); if (!r.ok) throw new Error(j.error || "explain failed"); setText(j.text);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="mt-3">
      <Button variant="outline" size="sm" onClick={go} disabled={busy}>{busy ? "Explaining..." : "Explain in plain words"}</Button>
      {err && <p className="mt-2 text-xs text-loss">{err}</p>}
      {text && <p className="mt-2 rounded-lg border border-line bg-ink p-3 text-sm text-text/90">{text}<span className="mt-1 block text-xs text-mute">AI wording of the numbers above. The numbers come from the Rex API, not the AI.</span></p>}
    </div>
  );
}
