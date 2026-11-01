"use client";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Play, History, Info, Wand2 } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select, Textarea } from "@/components/ui/input";
import { Alert, Skeleton } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { PageHead } from "@/components/page-head";
import { LastTime } from "@/components/tracker-bits";
import { COST_DEFAULT, CostModel, NewsList, costBody, type CostChoice } from "@/components/quant-bits";
import { useDraft, useGet } from "@/lib/hooks";
import { post } from "@/lib/api";
import { useRex } from "@/lib/store";
import type { Backtest, ParamMeta, ParseResult, Templates } from "@/lib/types";
import { pctPlain } from "@/lib/utils";

const EXAMPLES = [
  { label: "Breakout", text: "Buy Reliance when it breaks above the 20-day high. Hold 10 days, 3% stop loss, 8% take profit." },
  { label: "MA crossover", text: "TCS: buy when the 50 day moving average crosses above the 200 day, 4% stop, hold 3 weeks." },
  { label: "RSI bounce", text: "Buy Infosys when RSI(14) drops below 30, exit after 7 days, 2.5% stop loss, no take profit." },
];

function Field({ m, value, onChange }: { m: ParamMeta & { optional?: boolean }; value: any; onChange: (v: any) => void }) {
  if (m.type === "choice") return <FormField><Label>{m.label}</Label><Select value={value ?? m.default} onChange={(e) => onChange(e.target.value)}>{m.choices!.map((c) => <option key={String(c)}>{String(c)}</option>)}</Select></FormField>;
  return (
    <FormField><Label>{m.label}{m.optional && <span className="ml-1 normal-case text-mute/70">(optional)</span>}</Label>
      <Input type="number" min={m.min} max={m.max} step={m.step ?? (m.type === "int" ? 1 : 0.1)} value={value ?? ""} placeholder={m.optional ? "off" : String(m.default)}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} /></FormField>
  );
}

function Build() {
  const router = useRouter();
  const query = useSearchParams();
  const selectedTicker = query.get("t");
  const { setBacktest, setRefine } = useRex();
  const { data: t, error } = useGet<Templates>("/strategies/templates");
  const [ticker, setTicker] = useDraft("rex.build.ticker", "RELIANCE.NS");
  useEffect(() => {
    if (selectedTicker) { const value = selectedTicker.toUpperCase().trim(); setTicker(value.includes(".") ? value : `${value}.NS`); }
  }, [selectedTicker]);
  const [tpl, setTpl] = useDraft("rex.build.tpl", "breakout");
  const [params, setParams] = useDraft<Record<string, any>>("rex.build.params", {});
  const [exits, setExits] = useDraft<Record<string, any>>("rex.build.exits", {});
  const [cost, setCost] = useDraft<number | null>("rex.build.cost", null);
  const [cm, setCm] = useDraft<CostChoice>("rex.build.cm", COST_DEFAULT);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [mem, setMem] = useState<any>(null);
  const [memErr, setMemErr] = useState<string | null>(null);

  const [text, setText] = useDraft("rex.build.text", "");
  const [parsing, setParsing] = useState(false);
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [parsed, setParsed] = useState<ParseResult | null>(null);

  const meta = t?.templates[tpl];
  const changeTpl = (v: string) => { setTpl(v); setParams({}); };
  const body = useMemo(() => {
    const clean = (o: Record<string, any>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== "" && v !== undefined));
    // exits keep explicit nulls: an empty optional exit means "off" (stop-loss / take-profit disabled)
    const keepNull = (o: Record<string, any>) => Object.fromEntries(Object.entries(o).filter(([k, v]) => v !== "" && v !== undefined && (v !== null || k === "stop_pct" || k === "tp_pct")));
    return { ticker, template: tpl, params: clean(params), exits: keepNull(exits), cost_bps: cost ?? undefined, ...costBody(cm) };
  }, [ticker, tpl, params, exits, cost, cm]);

  // "Before you run" memory card: debounced, read-only (remember=false is not needed: /memory/check never writes)
  const timer = useRef<any>(null);
  useEffect(() => {
    if (!t) return;
    clearTimeout(timer.current); setMemErr(null);
    timer.current = setTimeout(() => { post("/memory/check", body).then(setMem).catch((e) => { setMem(null); setMemErr(e.message); }); }, 500);
    return () => clearTimeout(timer.current);
  }, [body, t]);

  const parse = async () => {
    setParsing(true); setParseErr(null);
    try {
      const r = await post<ParseResult>("/strategies/parse", { text });
      setParsed(r);
      if (r.strategy) {   // auto-fill the form below; every field stays editable
        setTicker(r.strategy.ticker); setTpl(r.strategy.template); setParams({ ...r.strategy.params }); setExits({ ...r.strategy.exits });
      }
    } catch (e: any) { setParsed(null); setParseErr(e.message); }
    finally { setParsing(false); }
  };

  const run = async () => {
    setBusy(true); setErr(null);
    try { const r = await post<Backtest>("/backtest", body); setBacktest(r); setRefine(null); router.push(`/results${r.saved_result_id ? `?id=${r.saved_result_id}` : ""}`); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };

  return (
    <div>
      <PageHead title="Strategy builder" sub="Pick a template and a stock, set the parameters and costs, run. Rex checks its memory first and tells you what it already knows about this setup." />
      {error && <Alert title="Could not load templates">{error}</Alert>}
      {t && (
        <Card className="mb-5" data-testid="nl-card">
          <CardHeader><div className="flex items-center gap-2"><CardTitle>Describe your strategy</CardTitle></div>
            <CardDesc>Write it the way you would say it. Rex turns it into rules, fills the form below, and you check it before anything runs. Supported: N-day-high breakout, MA crossover, RSI, mean reversion, plus hold days, stop-loss and take-profit.</CardDesc></CardHeader>
          <CardBody className="space-y-3">
            <Textarea data-testid="nl-text" aria-label="Describe your strategy" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Buy Reliance when it breaks above the 20-day high, hold 10 days, 3% stop loss, 8% take profit" />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-mute">Try:</span>
              {EXAMPLES.map((x) => <button key={x.label} type="button" data-testid="nl-chip" onClick={() => setText(x.text)}
                className="rounded-full border border-line bg-ink px-3 py-1 text-xs text-text hover:border-line hover:text-text">{x.label}</button>)}
              <div className="ml-auto flex items-center gap-3">
                <span className="text-xs text-mute">{text.length}/1000</span>
                <Button data-testid="nl-parse" onClick={parse} disabled={parsing || !text.trim()}><Wand2 className="h-4 w-4" />{parsing ? "Parsing…" : "Parse"}</Button>
              </div>
            </div>
            {parseErr && <Alert title="Could not parse">{parseErr}</Alert>}
            {parsed && (
              <div data-testid="parsed-card" className="space-y-3 rounded-lg border border-line bg-ink p-3.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{parsed.strategy ? "Parsed rules" : "Nothing to run yet"}</span>
                  <Badge tone={parsed.parser === "gemini" ? "sky" : "neutral"}>{parsed.parser === "gemini" ? `Gemini${parsed.model ? ` · ${parsed.model}` : ""}` : "Rules parser"}</Badge>
                  {parsed.strategy && <Badge tone={parsed.confidence >= 0.75 ? "gain" : parsed.confidence >= 0.5 ? "warn" : "loss"}>confidence {Math.round(parsed.confidence * 100)}%</Badge>}
                  {parsed.cached && <Badge>cached</Badge>}
                </div>
                {parsed.strategy && parsed.summary && <p data-testid="parsed-summary" className="text-text">{parsed.summary}</p>}
                {parsed.strategy && (
                  <div className="flex flex-wrap gap-1.5">
                    {[["ticker", parsed.strategy.ticker], ...Object.entries(parsed.strategy.params).map(([k, v]) => ["params." + k, v]), ...Object.entries(parsed.strategy.exits).map(([k, v]) => ["exits." + k, v === null ? "off" : v])].map(([k, v]) => (
                      <Badge key={String(k)} tone={parsed.fields_from_text.includes(String(k)) ? "rex" : "neutral"} title={parsed.fields_from_text.includes(String(k)) ? "from your text" : "default"}>{String(k).replace("params.", "").replace("exits.", "")}: {String(v)}</Badge>))}
                  </div>
                )}
                {parsed.strategy && <p className="text-xs text-mute">Highlighted = taken from your text; grey = default. Form below is filled and editable.</p>}
                {parsed.fallback_reason && <Alert tone="info" title="Gemini unavailable">{parsed.fallback_reason}</Alert>}
                {parsed.unsupported.length > 0 && (
                  <Alert tone="warn" title="Not supported / ignored" className="[&_li]:ml-4 [&_li]:list-disc"><ul data-testid="parsed-unsupported">{parsed.unsupported.map((u, i) => <li key={i}>{u}</li>)}</ul></Alert>)}
                {parsed.assumptions.length > 0 && (
                  <Alert tone="info" title="Assumptions Rex made" className="[&_li]:ml-4 [&_li]:list-disc"><ul data-testid="parsed-assumptions">{parsed.assumptions.map((u, i) => <li key={i}>{u}</li>)}</ul></Alert>)}
              </div>
            )}
          </CardBody>
        </Card>
      )}
      {!t && !error && <Skeleton className="h-96" />}
      {t && meta && (
        <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader><CardTitle>Setup</CardTitle><CardDesc>{meta.description}</CardDesc></CardHeader>
            <CardBody className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <FormField><Label>Stock</Label>
                  <Input list="tickers" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} />
                  <datalist id="tickers">{t.tickers.map((x) => <option key={x} value={x} />)}</datalist></FormField>
                <FormField><Label>Template</Label>
                  <Select value={tpl} onChange={(e) => changeTpl(e.target.value)}>{Object.entries(t.templates).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select></FormField>
              </div>
              <div><div className="mb-2 text-sm font-medium">Parameters</div>
                <div className="grid gap-4 sm:grid-cols-3">{meta.params.map((m) => <Field key={m.name} m={m} value={params[m.name] ?? m.default} onChange={(v) => setParams((p) => ({ ...p, [m.name]: v }))} />)}</div></div>
              <div><div className="mb-2 text-sm font-medium">Exits</div>
                <div className="grid gap-4 sm:grid-cols-3">{t.exits.map((m) => <Field key={m.name} m={m} value={exits[m.name] ?? (m.optional ? null : m.default)} onChange={(v) => setExits((p) => ({ ...p, [m.name]: v }))} />)}</div></div>
              <CostModel value={cm} onChange={setCm} />
              {cm.model === "bps" && <FormField className="max-w-xs"><Label>Round-trip cost (bps)</Label>
                <Input type="number" min={0} max={500} step={1} value={cost ?? t.cost_bps_default} onChange={(e) => setCost(e.target.value === "" ? null : Number(e.target.value))} />
                <p className="mt-1 text-xs text-mute">{t.cost_note}</p></FormField>}
              {err && <Alert title="Run failed">{err}</Alert>}
              <div className="flex items-center gap-3">
                <Button size="lg" onClick={run} disabled={busy}><Play className="h-4 w-4" />{busy ? "Running…" : "Run backtest"}</Button>
                <span className="text-xs text-mute">The last {t.locked_days} days are held out and only shown as the “locked” window.</span>
              </div>
            </CardBody>
          </Card>

          <div className="space-y-5 lg:sticky lg:top-20 lg:self-start">
          <LastTime ticker={ticker} template={tpl} />
          <NewsList symbol={ticker} limit={5} />
          <Card className="h-fit" data-testid="memory-card">
            <CardHeader><div className="flex items-center gap-2"><History className="h-4 w-4 text-rex" /><CardTitle>Before you run: Rex remembers</CardTitle></div></CardHeader>
            <CardBody className="space-y-3 text-sm">
              {memErr && <Alert title="Memory check failed">{memErr}</Alert>}
              {!mem && !memErr && <Skeleton className="h-24" />}
              {mem && (<>
                {mem.warnings?.length ? mem.warnings.map((w: any, i: number) => (
                  <div key={i} className={`rounded-lg border p-2.5 ${w.level === "warn" ? "border-warn/40 bg-warn/10" : "border-line bg-ink"}`}>{w.text}</div>
                )) : <p className="text-mute">Nothing in memory contradicts this setup.</p>}
                {mem.similar?.n > 0 && <p className="text-xs text-mute">{mem.similar.n} similar past trades: win rate {pctPlain(mem.similar.win_rate, 0)} vs {pctPlain(mem.similar.base_win_rate, 0)} baseline.</p>}
                <div className="flex flex-wrap gap-1.5 pt-1">{Object.entries(mem.current_setup?.tags ?? {}).map(([k, v]) => <Badge key={k}>{k}: {String(v)}</Badge>)}</div>
                <p className="flex gap-1.5 text-xs text-mute"><Info className="mt-0.5 h-3 w-3 shrink-0" />Drawn from {mem.memory_runs} remembered runs. Hints from past runs, not predictions.</p>
              </>)}
            </CardBody>
          </Card>
          </div>
        </div>
      )}
    </div>
  );
}

export default function BuildPage() { return <Suspense fallback={<Skeleton className="h-96" />}><Build /></Suspense>; }
