"use client";
import { useEffect, useRef, useState } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Select, Textarea, Label } from "@/components/ui/input";
import { Alert, Table, Td, Th } from "@/components/ui/misc";
import { EquityChart } from "@/components/charts";
import { aiGet, aiPost, errText, n2, pc, type Status, type Version } from "./lib";
import { useSearchParams, useRouter } from "next/navigation";
import { CodeBlock, TrackLive } from "./parts";

interface Msg { role: "user" | "assistant"; text: string }
/** Chat builder: describe a strategy, Gemini (or the offline synthesizer) writes signals(df); follow-ups edit the same code. Each reply is a saved version. */
export function Builder({ status }: { status: Status | null }) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setIv] = useState("1h");
  const [text, setText] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [sel, setSel] = useState<Version | null>(null);
  const [cmp, setCmp] = useState<{ diff: string; a: any; b: any } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const symbols = status?.symbols ?? [{ symbol: "BTCUSDT", name: "Bitcoin" }];
  const nse = status?.nse ?? [];
  const isNse = nse.includes(symbol);
  const router = useRouter();
  const chat = useSearchParams().get("chat");
  const inFlight = useRef(false);
  const [sessions, setSessions] = useState<any[]>([]);
  const [restoring, setRestoring] = useState(false);
  useEffect(() => { aiGet("/sessions").then((r) => setSessions(r.sessions)).catch((e) => setErr(errText(e))); }, [sid]);
  useEffect(() => {
    if (!chat) { setSid(null); setMsgs([]); setVersions([]); setSel(null); setCmp(null); return; }
    if (chat === sid) return;
    let alive = true; setRestoring(true); setErr(null);
    aiGet(`/sessions/${encodeURIComponent(chat)}`).then((r) => {
      if (!alive) return;
      setSid(chat); setSymbol(r.session.symbol); setIv(r.session.interval);
      setMsgs(r.messages); setRestoring(false); setVersions(r.versions); setSel(r.versions[r.versions.length - 1] ?? null); setCmp(null);
    }).catch((e) => alive && setErr(errText(e))).finally(() => alive && setRestoring(false));
    return () => { alive = false; };
  }, [chat]);
  const newChat = () => { setSid(null); setMsgs([]); setVersions([]); setSel(null); setCmp(null); setNotice(null); setErr(null); setText(""); router.replace("/ai?tab=chat"); };

  const send = async () => {
    if (text.trim().length < 2 || busy || restoring || inFlight.current) return;
    inFlight.current = true;
    const m = text.trim(); setMsgs((x) => [...x, { role: "user", text: m }]); setText(""); setBusy(true); setErr(null); setCmp(null);
    try {
      const r = await aiPost("/chat", { message: m, session_id: sid, parent_version_id: sel?.id ?? null, target: { market: isNse ? "nse" : "crypto", symbol, interval: isNse ? "1d" : interval } });
      setSid(r.session_id); router.replace(`/ai?tab=chat&chat=${r.session_id}`); setSel(r.version); setNotice(r.notice || null);
      setMsgs((x) => [...x, { role: "assistant", text: r.reply }]);
      const s = await aiGet(`/sessions/${r.session_id}`); setVersions(s.versions);
    } catch (e) { setErr(errText(e)); } finally { inFlight.current = false; setBusy(false); }
  };
  const open = async (id: string) => { try { setSel(await aiGet<Version>(`/versions/${id}`)); setCmp(null); } catch (e) { setErr(errText(e)); } };
  const compare = async (a: string, b: string) => { try { setCmp(await aiGet(`/versions/${a}/diff/${b}`)); } catch (e) { setErr(errText(e)); } };
  const r = sel?.result;
  return (
    <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
      <Card><CardHeader><CardTitle>Describe the strategy</CardTitle><Button size="sm" variant="outline" onClick={newChat} disabled={busy || restoring}>New conversation</Button></CardHeader><CardBody className="space-y-3">
        <FormField><Label htmlFor="chat-session">Saved conversations</Label><Select id="chat-session" value={sid ?? ""} disabled={busy || restoring} onChange={(e) => e.target.value ? router.replace(`/ai?tab=chat&chat=${e.target.value}`) : newChat()}><option value="">New conversation</option>{sessions.map((s) => <option key={s.id} value={s.id}>{s.symbol} · {s.title}</option>)}</Select></FormField>
        {restoring && <p role="status">Opening conversation…</p>}
        <div className="grid grid-cols-2 gap-2">
          <FormField><Label htmlFor="sym">Market</Label><Select id="sym" value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!!sid}>
            <optgroup label="Crypto">{symbols.map((s) => <option key={s.symbol} value={s.symbol}>{s.symbol}</option>)}</optgroup>
            <optgroup label="NSE (daily)">{nse.map((t) => <option key={t}>{t}</option>)}</optgroup></Select></FormField>
          <FormField><Label htmlFor="iv">Candles</Label><Select id="iv" value={isNse ? "1d" : interval} onChange={(e) => setIv(e.target.value)} disabled={!!sid || isNse}>{(status?.intervals ?? ["1h", "4h", "1d"]).map((i) => <option key={i}>{i}</option>)}</Select></FormField>
        </div>
        <div className="max-h-56 space-y-2 overflow-y-auto text-sm" aria-live="polite">
          {msgs.length === 0 && <p className="text-mute">Example: buy when price breaks the 10-day high and the 50-day average is rising, exit on a 3% stop.</p>}
          {msgs.map((m, i) => <div key={i} className={m.role === "user" ? "text-text" : "text-mute"}><span className="text-xs uppercase tracking-wide">{m.role === "user" ? "You" : "Builder"}</span><p>{m.text}</p></div>)}
        </div>
        <Textarea aria-label="Strategy instructions" disabled={busy || restoring} maxLength={1500} rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={sel ? "Edit it: make the stop 2% and add a volume filter" : "Describe your strategy"} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }} />
        <Button onClick={send} disabled={busy || restoring || text.trim().length < 2} className="w-full">{busy ? "Writing and testing..." : sel ? "Edit current version" : "Write strategy"}</Button>
        {err && <Alert>{err}</Alert>}
        {versions.length > 0 && <div className="space-y-1 border-t border-line pt-3"><div className="text-xs font-medium text-mute">Versions</div>
          {versions.map((v, i) => <div key={v.id} className="flex items-center gap-2 text-sm"><button className={`flex-1 truncate text-left hover:text-text ${sel?.id === v.id ? "text-text" : "text-mute"}`} onClick={() => open(v.id)}>v{v.n} {v.title}</button>
            {i > 0 && <button className="text-xs text-mute underline hover:text-text" onClick={() => compare(versions[i - 1].id, v.id)}>diff vs v{versions[i - 1].n}</button>}</div>)}</div>}
      </CardBody></Card>
      <div className="space-y-4">
        {!sel && <p className="text-sm text-mute">Nothing built yet. Your first strategy appears here with its code, rules and a backtest on real data.</p>}
        {notice && <Alert tone="info">{notice}</Alert>}
        {sel && r && <Card><CardHeader><div className="flex flex-wrap items-center gap-2"><CardTitle>v{sel.n}: {sel.title}</CardTitle><Badge>{sel.source === "gemini" ? `written by ${sel.model}` : "offline mode"}</Badge><Badge>{sel.verdict.label}</Badge></div><p className="text-sm text-mute">{sel.explanation}</p></CardHeader>
          <CardBody className="space-y-4">
            <ul className="list-disc space-y-0.5 pl-4 text-sm">{sel.rules.map((x, i) => <li key={i}>{x}</li>)}</ul>
            <div className="overflow-x-auto"><Table><thead><tr><Th></Th><Th>Train</Th><Th>Locked final period</Th><Th>Buy &amp; hold, locked</Th></tr></thead><tbody>
              <tr><Td>Return</Td><Td className="num">{pc(r.train.metrics.total_return)}</Td><Td className="num">{pc(r.locked.metrics.total_return)}</Td><Td className="num text-mute">{pc(r.locked.benchmark.total_return)}</Td></tr>
              <tr><Td>Sharpe</Td><Td className="num">{n2(r.train.metrics.sharpe)}</Td><Td className="num">{n2(r.locked.metrics.sharpe)}</Td><Td className="num text-mute">{n2(r.locked.benchmark.sharpe)}</Td></tr>
              <tr><Td>Max drawdown</Td><Td className="num">{pc(r.train.metrics.max_drawdown)}</Td><Td className="num">{pc(r.locked.metrics.max_drawdown)}</Td><Td className="num text-mute">{pc(r.locked.benchmark.max_drawdown)}</Td></tr>
              <tr><Td>Trades</Td><Td className="num">{r.train.metrics.trades}</Td><Td className="num">{r.locked.metrics.trades}</Td><Td></Td></tr></tbody></Table></div>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-mute">{sel.verdict.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
            {r.full?.dates && <EquityChart w={r.full} labelA={sel.title} />}
            <CodeBlock code={sel.code} title="signals(df) code" />
            <TrackLive versionId={sel.id} symbol={sel.symbol} />
          </CardBody></Card>}
        {cmp && <Card><CardHeader><CardTitle>v{cmp.a.n} vs v{cmp.b.n}</CardTitle></CardHeader><CardBody className="space-y-3">
          <Table><thead><tr><Th></Th><Th>v{cmp.a.n} locked</Th><Th>v{cmp.b.n} locked</Th></tr></thead><tbody>
            <tr><Td>Return</Td><Td className="num">{pc(cmp.a.locked.total_return)}</Td><Td className="num">{pc(cmp.b.locked.total_return)}</Td></tr>
            <tr><Td>Sharpe</Td><Td className="num">{n2(cmp.a.locked.sharpe)}</Td><Td className="num">{n2(cmp.b.locked.sharpe)}</Td></tr>
            <tr><Td>Verdict</Td><Td>{cmp.a.verdict}</Td><Td>{cmp.b.verdict}</Td></tr></tbody></Table>
          <pre className="max-h-72 overflow-auto rounded bg-panel2 p-3 font-mono text-xs" data-testid="diff">{cmp.diff || "No code changes."}</pre></CardBody></Card>}
      </div>
    </div>
  );
}
