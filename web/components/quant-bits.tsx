"use client";
import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Table, Td, Th } from "@/components/ui/misc";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { get, post } from "@/lib/api";
import type { NewsOut, OverfitStats, WalkForward } from "@/lib/types";
import { cn, num, pct, pctPlain, tone } from "@/lib/utils";

const axis = { stroke: "#8a8f98", fontSize: 11 };
const tip = { contentStyle: { background: "#111214", border: "1px solid #26282c", borderRadius: 4, fontSize: 12 }, labelStyle: { color: "#8a8f98" } };

/* ------------------------------------------------------------------ PBO + DSR */
const LEVEL = { low: ["gain", "Low"], moderate: ["warn", "Moderate"], high: ["loss", "High"] } as const;

export function OverfitCard({ stats }: { stats?: OverfitStats }) {
  if (!stats) return null;
  const { pbo, dsr } = stats;
  const lv = pbo.level ? LEVEL[pbo.level] : null;
  return (
    <Card data-testid="overfit-card">
      <CardHeader><CardTitle>Overfitting check</CardTitle>
        <CardDesc>Two standard tests of whether the best settings found by the search are real or just the luckiest of many tries. Both use the training period only.</CardDesc></CardHeader>
      <CardBody className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-md border border-line p-3" data-testid="pbo-box">
            <div className="text-xs text-mute">Probability of Backtest Overfitting (PBO)</div>
            {pbo.available ? <>
              <div className="mt-1 flex items-baseline gap-3"><span className="num text-2xl font-semibold" data-testid="pbo-value">{pctPlain(pbo.pbo, 0)}</span>
                {lv && <Badge tone={lv[0] as any}>{lv[1]} risk</Badge>}</div>
              <p className="mt-2 text-sm text-text/90">{pbo.words}</p>
              <p className="mt-2 text-xs text-mute">{pbo.splits?.toLocaleString("en-IN")} train/test splits of {pbo.blocks} blocks, {pbo.candidates} candidates (CSCV, Bailey, Borwein, López de Prado &amp; Zhu).</p>
            </> : <p className="mt-2 text-sm text-mute">Not available: {pbo.reason}</p>}
          </div>
          <div className="rounded-md border border-line p-3" data-testid="dsr-box">
            <div className="text-xs text-mute">Deflated Sharpe Ratio (DSR)</div>
            {dsr.available && dsr.dsr != null ? <>
              <div className="mt-1 flex items-baseline gap-3"><span className="num text-2xl font-semibold" data-testid="dsr-value">{pctPlain(dsr.dsr, 0)}</span>
                <Badge tone={dsr.verdict === "likely_real" ? "gain" : dsr.verdict === "uncertain" ? "warn" : "loss"}>{dsr.verdict === "likely_real" ? "Likely real" : dsr.verdict === "uncertain" ? "Not convincing" : "Looks like luck"}</Badge></div>
              <p className="mt-2 text-sm text-text/90">{dsr.words}</p>
              <p className="mt-2 text-xs text-mute">{dsr.n_trials} trials counted. Bailey &amp; López de Prado (2014).</p>
            </> : <p className="mt-2 text-sm text-mute">Not available{dsr.reason ? `: ${dsr.reason}` : "."}</p>}
          </div>
        </div>
        <details className="text-sm text-mute">
          <summary className="cursor-pointer text-text/80">How to read these</summary>
          <ul className="mt-2 list-inside list-disc space-y-1">
            <li><b className="text-text/90">PBO</b> near 0% means whichever setting wins on one half of the history keeps doing well on the other half. Around 50% means picking the winner is a coin flip. Above 50% means the winner tends to do worse than average on unseen data.</li>
            <li><b className="text-text/90">DSR</b> corrects the Sharpe ratio for the number of settings tried (the more you try, the better the best one looks by luck) and for fat tails. 95% or more is the usual bar.</li>
            <li>Passing both does not make a strategy profitable in future. Failing either is a strong reason to distrust the tuned settings.</li>
          </ul>
        </details>
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ walk-forward */
const WF = { holds: ["gain", "HOLDS UP"], fails: ["loss", "DOES NOT HOLD"], inconclusive: ["warn", "INCONCLUSIVE"] } as const;

export function WalkForwardPanel({ ticker, template, params, exits, cost_bps, cost_model, slippage_bps, trade_value }: {
  ticker: string; template: string; params: Record<string, any>; exits: Record<string, any>; cost_bps: number; cost_model?: string; slippage_bps?: number | null; trade_value?: number | null }) {
  const [mode, setMode] = useState<"rolling" | "anchored">("rolling");
  const [r, setR] = useState<WalkForward | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const india = cost_model && cost_model !== "bps";
      setR(await post<WalkForward>("/walkforward", { ticker, template, params, exits, mode, remember: false,
        ...(india ? { cost_model, slippage_bps, trade_value } : { cost_bps }) }));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const rows = r ? r.chart.dates.map((d, i) => ({ d, s: r.chart.equity[i], o: r.chart.original_equity[i], b: r.chart.benchmark_equity[i] })) : [];
  const v = r ? WF[r.verdict.flag] : null;
  return (
    <div className="space-y-4" data-testid="wf-panel">
      <Card><CardHeader><CardTitle>Walk-forward analysis</CardTitle>
        <CardDesc>Instead of one tuning period and one locked year, Rex repeats the whole exercise through time: tune on a window of history, trade the next unseen window with those settings, slide forward, repeat. The stitched out-of-sample result is what you would actually have got by re-tuning as you went.</CardDesc></CardHeader>
        <CardBody className="flex flex-wrap items-end gap-3">
          <FormField className="w-48"><Label>Windows</Label>
            <Select value={mode} onChange={(e) => setMode(e.target.value as any)} data-testid="wf-mode"><option value="rolling">Rolling (fixed 2-year train)</option><option value="anchored">Anchored (train grows)</option></Select></FormField>
          <Button onClick={run} disabled={busy} data-testid="wf-run">{busy ? "Running…" : "Run walk-forward"}</Button>
          <span className="text-xs text-mute">{ticker} · {template.replace("_", " ")} · re-tuned in every window · yearly out-of-sample steps</span>
        </CardBody></Card>
      {err && <Alert title="Walk-forward failed">{err}</Alert>}
      {r && v && <>
        <div className="flex flex-wrap items-center gap-4 rounded-md border border-line p-4" data-testid="wf-verdict" data-flag={r.verdict.flag}>
          <Badge tone={v[0] as any}>{v[1]}</Badge>
          <p className="min-w-0 flex-1 text-sm">{r.verdict.headline}</p>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[["Out-of-sample return", pct(r.summary.metrics.total_return), tone(r.summary.metrics.total_return)], ["OOS Sharpe", num(r.summary.metrics.sharpe), ""],
            ["Max drawdown", pct(r.summary.metrics.max_drawdown), "text-loss"], ["Profitable windows", `${r.summary.windows_profitable}/${r.summary.windows_total}`, ""],
            ["Efficiency", r.summary.efficiency == null ? "n/a" : num(r.summary.efficiency), ""]].map(([k, val, c]) => (
            <div key={k} className="rounded-md border border-line p-3"><div className="text-xs text-mute">{k}</div><div className={cn("num mt-1 text-lg font-semibold", c)}>{val}</div></div>))}
        </div>
        <Card><CardHeader><CardTitle>Stitched out-of-sample equity</CardTitle>
          <CardDesc>Dashed vertical lines mark each re-tune. Strategy re-tuned each window vs your original settings vs buy &amp; hold on the same days. {r.cost}.</CardDesc></CardHeader>
          <CardBody><div className="h-72" data-testid="wf-chart"><ResponsiveContainer>
            <LineChart data={rows} margin={{ left: 0, right: 8, top: 8 }}>
              <CartesianGrid stroke="#1c1d20" vertical={false} />
              <XAxis dataKey="d" tick={axis} minTickGap={50} />
              <YAxis tick={axis} domain={["auto", "auto"]} width={44} tickFormatter={(x) => x.toFixed(2)} />
              <Tooltip {...tip} formatter={(x: any) => Number(x).toFixed(3)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {r.chart.boundaries.slice(1).map((b) => <ReferenceLine key={b} x={b} stroke="#3a3d42" strokeDasharray="3 3" />)}
              <Line dataKey="s" name="Re-tuned each window" stroke="#e6e7e9" dot={false} strokeWidth={1.75} isAnimationActive={false} />
              <Line dataKey="o" name="Original settings" stroke="#8a8f98" dot={false} strokeWidth={1.25} isAnimationActive={false} />
              <Line dataKey="b" name="Buy & hold" stroke="#8a8f98" dot={false} strokeWidth={1} strokeDasharray="4 3" isAnimationActive={false} />
            </LineChart></ResponsiveContainer></div></CardBody></Card>
        <Card><CardHeader><CardTitle>Per-window results</CardTitle><CardDesc>Train = what the search saw. Test = the next window, never seen while tuning. Train Sharpe far above test Sharpe is the signature of overfitting.</CardDesc></CardHeader>
          <CardBody><Table data-testid="wf-table"><thead><tr><Th>#</Th><Th>Train</Th><Th>Test</Th><Th>Chosen settings</Th><Th className="text-right">Train Sharpe</Th><Th className="text-right">Test return</Th><Th className="text-right">Test Sharpe</Th><Th className="text-right">Trades</Th><Th className="text-right">Original</Th><Th className="text-right">Buy &amp; hold</Th></tr></thead>
            <tbody>{r.windows.map((w) => (
              <tr key={w.window} data-testid="wf-row"><Td className="num text-mute">{w.window}</Td><Td className="num text-xs">{w.train_start} → {w.train_end}</Td><Td className="num text-xs">{w.test_start} → {w.test_end}</Td>
                <Td className="num text-xs">{[...Object.entries(w.params), ...Object.entries(w.exits).map(([k, x]) => [k, x ?? "off"])].map(([k, x]) => `${k}=${x}`).join(" ")}</Td>
                <Td className="num text-right">{num(w.train_sharpe)}</Td><Td className={cn("num text-right", tone(w.test_return))}>{pct(w.test_return)}</Td><Td className="num text-right">{num(w.test_sharpe)}</Td>
                <Td className="num text-right">{w.test_trades}</Td><Td className={cn("num text-right", tone(w.original_test_return))}>{pct(w.original_test_return)}</Td><Td className={cn("num text-right", tone(w.buy_hold_test_return))}>{pct(w.buy_hold_test_return)}</Td></tr>))}</tbody></Table>
            <ul className="mt-3 list-inside list-disc space-y-1 text-xs text-mute">{r.verdict.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></CardBody></Card>
      </>}
    </div>
  );
}

/* ------------------------------------------------------------------ cost model */
export interface CostChoice { model: string; slippage: number | null; tradeValue: number | null }
export const COST_DEFAULT: CostChoice = { model: "bps", slippage: null, tradeValue: null };
export const costBody = (c: CostChoice) => (c.model === "bps" ? {} : { cost_model: c.model, slippage_bps: c.slippage ?? undefined, trade_value: c.tradeValue ?? undefined });

export function CostModel({ value, onChange }: { value: CostChoice; onChange: (c: CostChoice) => void }) {
  const [ex, setEx] = useState<any>(null);
  const india = value.model !== "bps";
  useEffect(() => {
    if (!india) { setEx(null); return; }
    const kind = value.model === "india_intraday" ? "intraday" : "delivery";
    get(`/costs/models?kind=${kind}&trade_value=${value.tradeValue ?? 100000}&slippage_bps=${value.slippage ?? 5}`).then(setEx).catch(() => setEx(null));
  }, [india, value.model, value.slippage, value.tradeValue]);
  return (
    <div className="space-y-2" data-testid="cost-model">
      <div className="grid gap-3 sm:grid-cols-3">
        <FormField><Label>Cost model</Label>
          <Select value={value.model} onChange={(e) => onChange({ ...value, model: e.target.value })} data-testid="cost-model-select">
            <option value="bps">Flat bps (simple)</option><option value="india_delivery">India: delivery (CNC)</option><option value="india_intraday">India: intraday (MIS)</option></Select></FormField>
        {india && <>
          <FormField><Label>Slippage per leg (bp)</Label><Input type="number" min={0} max={200} step={1} placeholder="5" value={value.slippage ?? ""} onChange={(e) => onChange({ ...value, slippage: e.target.value === "" ? null : Number(e.target.value) })} /></FormField>
          <FormField><Label>Trade size (₹)</Label><Input type="number" min={1000} step={10000} placeholder="100000" value={value.tradeValue ?? ""} onChange={(e) => onChange({ ...value, tradeValue: e.target.value === "" ? null : Number(e.target.value) })} /></FormField></>}
      </div>
      {india && ex && <p className="text-xs text-mute" data-testid="cost-total">
        Round trip ≈ <span className="num text-text">{ex.example.round_trip_bps.toFixed(1)} bp</span> on ₹{Number(ex.example.trade_value).toLocaleString("en-IN")}: brokerage, STT, exchange charges, GST, SEBI fee, stamp duty{value.model === "india_delivery" ? ", DP charge" : ""} and slippage. Rates from {ex.source}. Crypto keeps its flat bps fee.</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ news */
export function NewsList({ symbol, limit = 6 }: { symbol: string; limit?: number }) {
  const [n, setN] = useState<NewsOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setN(null); setErr(null); get<NewsOut>(`/news?symbol=${encodeURIComponent(symbol)}&limit=${limit}`).then(setN).catch((e) => setErr(e.message)); }, [symbol, limit]);
  const ago = (iso: string | null) => {
    if (!iso) return "";
    const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    return m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
  };
  return (
    <Card data-testid="news-card"><CardHeader><CardTitle>Latest news</CardTitle></CardHeader>
      <CardBody>
        {!n && !err && <p className="text-sm text-mute">Loading…</p>}
        {err && <p className="text-sm text-mute">News unavailable: {err}</p>}
        {n && n.items.length === 0 && <p className="text-sm text-mute" data-testid="news-empty">{n.disabled ? "News is switched off on this server." : "No recent headlines found."}</p>}
        {n && n.items.length > 0 && <ul className="divide-y divide-line/60" data-testid="news-list">
          {n.items.map((i, k) => (
            <li key={k} className="py-2"><a href={i.url} target="_blank" rel="noopener noreferrer" className="text-sm text-text hover:underline" data-testid="news-item">{i.title}</a>
              <div className="mt-0.5 text-xs text-mute">{i.publisher}{i.published ? ` · ${ago(i.published)}` : ""}</div></li>))}</ul>}
        {n && <p className="mt-2 text-xs text-mute">Via {n.sources.length ? n.sources.map((s) => s.replace("_", " ")).join(" + ") : "no source"}. Headlines as published; Rex does not score or interpret them.</p>}
      </CardBody></Card>
  );
}
