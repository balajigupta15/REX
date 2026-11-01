"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Alert, Table, Td, Th } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { PageHead } from "@/components/page-head";
import { CostModel, COST_DEFAULT, costBody, type CostChoice } from "@/components/quant-bits";
import { useGet } from "@/lib/hooks";
import { get, post } from "@/lib/api";
import type { Lab, LabRow, ParamMeta, Templates } from "@/lib/types";
import { cn, num, pct, pctPlain, tone } from "@/lib/utils";

const VERDICT = { holds: ["gain", "Holds"], fades: ["warn", "Fades"], loses: ["loss", "Loses"], few_trades: ["neutral", "Few trades"] } as const;
type Key = "short" | "asset_class" | "total_return" | "sharpe" | "max_drawdown" | "trades" | "buy_hold_return" | "verdict";
const COLS: [Key, string, boolean][] = [["short", "Asset", false], ["asset_class", "Class", false], ["total_return", "Return", true], ["sharpe", "Sharpe", true], ["max_drawdown", "Max DD", true],
  ["trades", "Trades", true], ["buy_hold_return", "Buy & hold", true], ["verdict", "Verdict", false]];

function LabPage() {
  const { data: t, error } = useGet<Templates>("/strategies/templates");
  const [tpl, setTpl] = useState("breakout");
  const [params, setParams] = useState<Record<string, any>>({});
  const [cm, setCm] = useState<CostChoice>(COST_DEFAULT);
  const [cost, setCost] = useState<number | null>(null);
  const [res, setRes] = useState<Lab | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<{ k: Key; dir: 1 | -1 }>({ k: "sharpe", dir: -1 });
  const meta = t?.templates[tpl];
  const [exits, setExits] = useState<Record<string, any>>({});
  const id = useSearchParams().get("id");
  useEffect(() => {
    if (!id) return;
    let alive = true;
    get<any>(`/results/${encodeURIComponent(id)}`).then((r) => {
      if (!alive) return;
      setTpl(r.spec.template); setParams(r.refined?.params ?? r.spec.params); setExits(r.refined?.exits ?? r.spec.exits);
      setCost(r.spec.cost_bps); setCm({ model: r.spec.cost_model ?? "bps", slippage: r.spec.slippage_bps ?? null, tradeValue: r.spec.trade_value ?? null });
    }).catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [id]);

  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== "" && v !== undefined));
      setRes(await post<Lab>("/lab/run", { template: tpl, params: clean, exits, cost_bps: cost ?? undefined, ...costBody(cm) }));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const rows = useMemo(() => {
    if (!res) return [] as LabRow[];
    const { k, dir } = sort;
    return [...res.rows].sort((a, b) => { const x = a[k] as any, y = b[k] as any; return (typeof x === "string" ? x.localeCompare(y) : (x ?? -Infinity) - (y ?? -Infinity)) * dir; });
  }, [res, sort]);
  const head = (k: Key) => setSort((s) => (s.k === k ? { k, dir: (-s.dir) as 1 | -1 } : { k, dir: k === "short" || k === "asset_class" || k === "verdict" ? 1 : -1 }));

  return (
    <div className="space-y-5" data-testid="lab-page">
      <PageHead title="Strategy Lab" sub="One strategy, every asset. Same rules, same exits, same costs on 10 NSE stocks and BTC, ETH, SOL. A strategy that only works on one name is usually luck." />
      {error && <Alert title="Could not load templates">{error}</Alert>}
      {t && <Card><CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FormField><Label>Template</Label><Select value={tpl} onChange={(e) => { setTpl(e.target.value); setParams({}); }} data-testid="lab-template">{Object.entries(t.templates).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select></FormField>
          {meta?.params.map((m: ParamMeta) => m.type === "choice"
            ? <FormField key={m.name}><Label>{m.label}</Label><Select value={params[m.name] ?? m.default} onChange={(e) => setParams({ ...params, [m.name]: e.target.value })}>{m.choices!.map((c) => <option key={String(c)}>{String(c)}</option>)}</Select></FormField>
            : <FormField key={m.name}><Label>{m.label}</Label><Input type="number" min={m.min} max={m.max} step={m.step ?? (m.type === "int" ? 1 : 0.1)} placeholder={String(m.default)} value={params[m.name] ?? ""} onChange={(e) => setParams({ ...params, [m.name]: e.target.value === "" ? null : Number(e.target.value) })} /></FormField>)}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">{t.exits.map((m) => <FormField key={m.name}><Label>{m.label}</Label><Input type="number" min={m.min} max={m.max} step={m.step ?? 1} value={exits[m.name] ?? ""} placeholder={m.optional ? "off" : String(m.default)} onChange={(e) => setExits((x) => ({ ...x, [m.name]: e.target.value === "" ? (m.optional ? null : m.default) : Number(e.target.value) }))} /></FormField>)}</div>
        <CostModel value={cm} onChange={setCm} />
        {cm.model === "bps" && <FormField className="max-w-xs"><Label>Round-trip cost, stocks (bps)</Label><Input type="number" min={0} max={500} step={1} placeholder={String(t.cost_bps_default)} value={cost ?? ""} onChange={(e) => setCost(e.target.value === "" ? null : Number(e.target.value))} /></FormField>}
        <div className="flex items-center gap-3"><Button onClick={run} disabled={busy} data-testid="lab-run">{busy ? "Running on 13 assets…" : "Run on all assets"}</Button>
          <span className="text-xs text-mute">Your chosen exits, with strategy defaults for unchanged fields. Daily bars. Crypto uses its own flat fee.</span></div>
      </CardBody></Card>}
      {err && <Alert title="Lab run failed">{err}</Alert>}
      {res && <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5" data-testid="lab-summary">
          {[["Assets tested", String(res.summary.assets)], ["Holds up", String(res.summary.holds)], ["Fades", String(res.summary.fades)], ["Loses money", String(res.summary.loses)], ["Median Sharpe", num(res.summary.median_sharpe)]].map(([k, v]) => (
            <div key={k} className="rounded-md border border-line p-3"><div className="text-xs text-mute">{k}</div><div className="num mt-1 text-lg font-semibold">{v}</div></div>))}
        </div>
        <Card><CardHeader><CardTitle>Results · {tpl.replace("_", " ")}</CardTitle><CardDesc>{res.cost}. Click a column to sort. Verdict uses the locked final year, never the period the settings were chosen on.</CardDesc></CardHeader>
          <CardBody><Table data-testid="lab-table"><thead><tr>{COLS.map(([k, label, right]) => (
            <Th key={k} className={cn("cursor-pointer select-none", right && "text-right")} onClick={() => head(k)} data-testid={`lab-sort-${k}`}>
              <span className="inline-flex items-center gap-1">{label}{sort.k === k && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span></Th>))}</tr></thead>
            <tbody>{rows.map((r) => { const v = VERDICT[r.verdict]; return (
              <tr key={r.symbol} data-testid="lab-row" data-symbol={r.symbol} data-verdict={r.verdict}>
                <Td><Link className="font-medium hover:underline" href={`/asset?symbol=${r.symbol}`}>{r.short}</Link><span className="ml-2 text-xs text-mute">{r.name}</span></Td>
                <Td className="text-mute">{r.asset_class}</Td><Td className={cn("num text-right", tone(r.total_return))}>{pct(r.total_return)}</Td><Td className="num text-right">{num(r.sharpe)}</Td>
                <Td className="num text-right text-loss">{pct(r.max_drawdown)}</Td><Td className="num text-right">{r.trades}</Td><Td className={cn("num text-right", tone(r.buy_hold_return))}>{pct(r.buy_hold_return)}</Td>
                <Td title={r.verdict_text}><Badge tone={v[0] as any}>{v[1]}</Badge><span className="ml-2 text-xs text-mute">locked yr {pct(r.locked_return)} · {r.locked_trades} trades</span></Td></tr>); })}</tbody></Table>
            {res.errors.length > 0 && <div className="mt-3 space-y-1 text-xs text-warn" data-testid="lab-errors">{res.errors.map((e) => <div key={e.symbol}>{e.symbol}: {e.error}</div>)}</div>}
            <ul className="mt-3 list-inside list-disc space-y-1 text-xs text-mute">{res.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></CardBody></Card>
        <p className="text-xs text-mute">Rex never places orders. {res.summary.beats_buy_hold} of {res.summary.assets} assets beat buy &amp; hold over the full period ({pctPlain(res.summary.beats_buy_hold / Math.max(1, res.summary.assets), 0)}).</p>
      </>}
    </div>
  );
}

export default function LabRoute() { return <Suspense fallback={<p>Loading Lab…</p>}><LabPage /></Suspense>; }
