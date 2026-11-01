"use client";
import { useEffect, useState } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, Table, Td, Th } from "@/components/ui/misc";
import { EquityChart } from "@/components/charts";
import { cn } from "@/lib/utils";
import { aiGet, errText, n2, pc, type Debate, type Experiment, type Final, type Risk, type Run, type Step, type Version } from "./lib";
import { CodeBlock, TrackLive } from "./parts";

type Tone = "gain" | "loss" | "warn" | "neutral";
const tone = (s: string): Tone => (["ok", "kept", "APPROVE", "EVIDENCE HELD UP"].includes(s) ? "gain" : ["error", "VETO", "DO NOT TRADE"].includes(s) ? "loss" : ["running", "CAUTION", "WEAK EVIDENCE"].includes(s) ? "warn" : "neutral");

export function Timeline({ steps, running, max }: { steps: Step[]; running: boolean; max: number }) {
  const groups: { role: string; items: Step[] }[] = [];
  for (const s of steps) { const g = groups[groups.length - 1]; if (g && g.role === s.role) g.items.push(s); else groups.push({ role: s.role, items: [s] }); }
  return (
    <Card data-testid="timeline">
      <CardHeader className="flex-row items-center justify-between"><CardTitle>Agent timeline</CardTitle><span className="text-xs text-mute">{steps.length} steps, cap {max}</span></CardHeader>
      <CardBody className="space-y-3">
        {groups.length === 0 && <p className="text-sm text-mute">Waiting for the first step.</p>}
        {groups.map((g, gi) => (
          <div key={gi} className="grid gap-x-4 sm:grid-cols-[11rem_1fr]">
            <div className="pb-1 text-xs font-medium uppercase tracking-wide text-mute">{g.role}</div>
            <div className="space-y-2">
              {g.items.map((s) => (
                <div key={s.idx} className="rounded border border-line bg-ink/40 px-3 py-2">
                  <div className="flex items-center gap-2 text-xs"><code className="text-mute">{s.tool}</code><Badge tone={tone(s.status)}>{s.status}</Badge>
                    {s.tool === "recall_memory" && s.output?.source && <Badge>source: {s.output.source}</Badge>}
                    {s.started && s.ended ? <span className="text-mute">{(s.ended - s.started).toFixed(1)}s</span> : null}</div>
                  {s.summary && <p className="mt-1 text-sm leading-relaxed">{s.summary}</p>}
                  {s.error && <p className="mt-1 text-sm text-loss">{s.error.message}</p>}
                  {(s.inputs || s.output) && (
                    <details className="mt-1 text-xs text-mute"><summary className="cursor-pointer select-none">inputs and output</summary>
                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-panel2 p-2 font-mono">{JSON.stringify({ inputs: s.inputs, output: s.output }, null, 1).slice(0, 3000)}</pre></details>)}
                </div>
              ))}
            </div>
          </div>
        ))}
        {running && <p className="text-sm text-mute" aria-live="polite">Working on the next step...</p>}
      </CardBody>
    </Card>
  );
}

export function ExperimentLog({ rows }: { rows: Experiment[] }) {
  if (!rows.length) return null;
  return (
    <Card data-testid="experiment-log">
      <CardHeader><CardTitle>Experiment log</CardTitle><p className="text-xs text-mute">Kept or dropped is decided on the train period only. The locked period is shown for transparency and is never used to choose.</p></CardHeader>
      <CardBody className="overflow-x-auto">
        <Table>
          <thead><tr><Th>Round</Th><Th>Hypothesis</Th><Th>Train Sharpe</Th><Th>Train trades</Th><Th>Locked return</Th><Th>Buy &amp; hold</Th><Th>Verdict</Th><Th>Outcome</Th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.round}><Td>{r.round}</Td><Td className="max-w-[16rem] truncate" title={r.hypothesis_text || r.hypothesis}>{r.hypothesis}</Td><Td className="num">{n2(r.train_sharpe)}</Td><Td className="num">{r.train_trades}</Td>
              <Td className={cn("num", r.locked_return >= 0 ? "text-gain" : "text-loss")}>{pc(r.locked_return)}</Td><Td className="num">{pc(r.locked_bh)}</Td><Td>{r.verdict}</Td>
              <Td><Badge tone={tone(r.status)} title={r.reason}>{r.status}</Badge></Td></tr>))}</tbody>
        </Table>
        <ul className="mt-2 space-y-0.5 text-xs text-mute">{rows.map((r) => <li key={r.round}>Round {r.round}: {r.reason}</li>)}</ul>
      </CardBody>
    </Card>
  );
}

export function DebatePanel({ debate, risk }: { debate: Debate | null; risk: Risk | null }) {
  if (!debate && !risk) return null;
  const pts: [string, string][] = risk ? [...risk.vetoes.map((t) => ["bg-loss", t] as [string, string]), ...risk.cautions.map((t) => ["bg-warn", t] as [string, string]), ...risk.passes.map((t) => ["bg-gain", t] as [string, string])] : [];
  return (
    <Card data-testid="debate-panel">
      <CardHeader><CardTitle>Bull vs Bear, and the Risk Manager</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        {debate && <div className="grid gap-4 md:grid-cols-2">
          <div><div className="mb-1 text-xs font-medium uppercase tracking-wide text-mute">Bull researcher</div><ul className="list-disc space-y-1 pl-4 text-sm">{debate.bull.map((t, i) => <li key={i}>{t}</li>)}</ul></div>
          <div><div className="mb-1 text-xs font-medium uppercase tracking-wide text-mute">Bear researcher</div><ul className="list-disc space-y-1 pl-4 text-sm">{debate.bear.map((t, i) => <li key={i}>{t}</li>)}</ul></div>
        </div>}
        {debate && <p className="text-xs text-mute">{debate.source === "gemini" ? "Argued by Gemini from the measured numbers (one call)." : "Built by rules from the measured numbers (no model call)."}</p>}
        {risk && <div className="border-t border-line pt-3"><div className="flex items-center gap-2 text-sm"><span className="text-xs font-medium uppercase tracking-wide text-mute">Risk Manager verdict</span><Badge tone={tone(risk.verdict)}>{risk.verdict}</Badge></div>
          <ul className="mt-2 space-y-1 text-sm">{pts.map(([c, t], i) => <li key={i} className="flex gap-2"><span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", c)} />{t}</li>)}</ul></div>}
      </CardBody>
    </Card>
  );
}

const Row = ({ label, a, b, bh }: { label: string; a: React.ReactNode; b: React.ReactNode; bh?: React.ReactNode }) => <tr><Td>{label}</Td><Td className="num">{a}</Td><Td className="num">{b}</Td><Td className="num text-mute">{bh ?? ""}</Td></tr>;
const wr = (x: number | null | undefined) => (x == null ? "n/a" : `${(x * 100).toFixed(0)}%`);

export function FinalCard({ f, run }: { f: Final; run: Run }) {
  const [v, setV] = useState<Version | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (f.version_id) aiGet<Version>(`/versions/${f.version_id}`).then(setV).catch((e) => setErr(errText(e))); }, [f.version_id]);
  const m = f.metrics; const bl = m?.benchmark_locked;
  return (
    <Card data-testid="final-card" className="border-text/30">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2"><CardTitle className="text-base">Result</CardTitle><Badge tone={tone(f.decision)}>{f.decision}</Badge>
          <Badge>{f.mode === "offline" ? "offline mode: code composed locally" : `code written by ${f.model ?? "Gemini"}`}</Badge></div>
        <p className="text-sm text-text/90">{f.headline}</p>
        <p className="text-xs text-mute">{f.disclaimer}</p>
      </CardHeader>
      <CardBody className="space-y-4">
        {m && f.chart && <>
          <div className="text-sm font-medium">{f.title}</div>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="overflow-x-auto"><Table>
              <thead><tr><Th></Th><Th>Train</Th><Th>Locked final period</Th><Th>Buy &amp; hold, locked</Th></tr></thead>
              <tbody>
                <Row label="Return" a={pc(m.train.total_return)} b={pc(m.locked.total_return)} bh={pc(bl?.total_return)} />
                <Row label="Sharpe" a={n2(m.train.sharpe)} b={n2(m.locked.sharpe)} bh={n2(bl?.sharpe)} />
                <Row label="Max drawdown" a={pc(m.train.max_drawdown)} b={pc(m.locked.max_drawdown)} bh={pc(bl?.max_drawdown)} />
                <Row label="Trades" a={m.train.trades} b={m.locked.trades} />
                <Row label="Win rate" a={wr(m.train.win_rate)} b={wr(m.locked.win_rate)} />
              </tbody>
            </Table></div>
            <div className="space-y-1 text-sm">
              {f.periods && <div className="text-xs text-mute">Train {f.periods.train[0]} to {f.periods.train[1]}; locked from {f.periods.locked[0]}.</div>}
              <div><span className="text-mute">Overfit verdict: </span>{f.verdict?.label}</div>
              <ul className="list-disc space-y-0.5 pl-4 text-text/90">{(f.verdict?.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
              <div className="pt-1 text-xs text-mute">Deflated Sharpe: {f.dsr?.dsr == null ? "n/a" : `${(f.dsr.dsr * 100).toFixed(0)}%`} after {f.trials} trial(s). Costs: {f.cost}.</div>
            </div>
          </div>
          <div><div className="mb-1 text-xs text-mute">Equity, strategy vs buy and hold (train then locked period, starts at 1.0)</div><EquityChart w={f.chart} labelA={f.title || "Strategy"} /></div>
        </>}
        {f.rules && f.rules.length > 0 && <div><div className="mb-1 text-xs font-medium text-mute">Rules in plain English</div><ul className="list-disc space-y-0.5 pl-4 text-sm">{f.rules.map((r, i) => <li key={i}>{r}</li>)}</ul></div>}
        {err && <Alert tone="warn">Could not load the strategy code: {err}</Alert>}
        {v && <CodeBlock code={v.code} title="signals(df) code the agent wrote" />}
        {f.limits && <ul className="list-disc space-y-0.5 pl-4 text-xs text-mute">{f.limits.map((l, i) => <li key={i}>{l}</li>)}</ul>}
        <div className="rounded border border-line bg-ink/40 px-3 py-2 text-xs text-mute" data-testid="lesson"><span className="font-medium text-text/80">Lesson written to memory ({(f.memory_written || ["SQLite"]).join(" + ")}{f.meko_status === "Meko failed" ? "; Meko write failed" : ""}): </span>{f.lesson}</div>
        {f.version_id && f.decision !== "NO RESULT" && <TrackLive versionId={f.version_id} symbol={run.target.symbol} />}
      </CardBody>
    </Card>
  );
}
