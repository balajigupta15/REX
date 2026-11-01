"use client";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, Wand2 } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormField, Input, Label, Select, Textarea } from "@/components/ui/input";
import { Alert, Skeleton } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { PageHead } from "@/components/page-head";
import { CryptoLastTime } from "@/components/crypto-bits";
import { useDraft, useGet } from "@/lib/hooks";
import { post } from "@/lib/api";
import { useCrypto, IV_LABEL, type CBacktest, type CSymbols } from "@/lib/crypto";
import type { ParamMeta, ParseResult } from "@/lib/types";

const EXAMPLES = [
  { label: "BTC breakout (daily)", text: "Buy Bitcoin when it breaks above the 20-day high. Hold 10 days, 5% stop loss, 10% take profit." },
  { label: "ETH 4h breakout", text: "Buy ETH on 4h when price breaks 20-day high, hold 6 hours, stop 3%" },
  { label: "SOL 1h RSI", text: "Buy SOL on the 1h chart when RSI(14) drops below 30, exit after 12 hours, 2% stop loss, 4% take profit." },
];

function Field({ m, value, onChange }: { m: ParamMeta & { optional?: boolean }; value: any; onChange: (v: any) => void }) {
  if (m.type === "choice") return <FormField><Label>{m.label}</Label><Select value={value ?? m.default} onChange={(e) => onChange(e.target.value)}>{m.choices!.map((c) => <option key={String(c)}>{String(c)}</option>)}</Select></FormField>;
  return (
    <FormField><Label>{m.label}{m.optional && <span className="ml-1 normal-case text-mute/70">(optional)</span>}</Label>
      <Input type="number" min={m.min} max={m.max} step={m.step ?? (m.type === "int" ? 1 : 0.1)} value={value ?? ""} placeholder={m.optional ? "off" : String(m.default)}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} /></FormField>
  );
}

export default function CryptoBuild() {
  const router = useRouter();
  const { setBacktest, setRefine } = useCrypto();
  const { data: t, error } = useGet<CSymbols>("/crypto/symbols");
  const [symbol, setSymbol] = useDraft("rex.crypto.build.symbol", "BTCUSDT");
  const [interval, setInterval_] = useDraft("rex.crypto.build.interval", "1d");
  useEffect(() => { const q = new URLSearchParams(window.location.search).get("coin"); if (q) setSymbol(q.toUpperCase()); }, []);   // deep link from the global asset search
  const [tpl, setTpl] = useDraft("rex.crypto.build.tpl", "breakout");
  const [params, setParams] = useDraft<Record<string, any>>("rex.crypto.build.params", {});
  const [exits, setExits] = useDraft<Record<string, any>>("rex.crypto.build.exits", {});
  const [cost, setCost] = useDraft<number | null>("rex.crypto.build.cost", null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useDraft("rex.crypto.build.text", "");
  const [parsing, setParsing] = useState(false);
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [parsed, setParsed] = useState<(ParseResult & { symbol?: string; interval?: string }) | null>(null);

  const meta = t?.templates[tpl];
  const iv = t?.intervals.find((x) => x.interval === interval);
  const unit = interval === "1d" ? "days" : "candles";
  const body = useMemo(() => {
    const clean = (o: Record<string, any>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== "" && v !== undefined));
    const keepNull = (o: Record<string, any>) => Object.fromEntries(Object.entries(o).filter(([k, v]) => v !== "" && v !== undefined && (v !== null || k === "stop_pct" || k === "tp_pct")));
    return { symbol, interval, template: tpl, params: clean(params), exits: keepNull(exits), cost_bps: cost ?? undefined };
  }, [symbol, interval, tpl, params, exits, cost]);

  const parse = async () => {
    setParsing(true); setParseErr(null);
    try {
      const r = await post<any>("/crypto/strategies/parse", { text });
      setParsed(r);
      if (r.strategy) { setSymbol(r.symbol); setInterval_(r.interval); setTpl(r.strategy.template); setParams({ ...r.strategy.params }); setExits({ ...r.strategy.exits }); }
    } catch (e: any) { setParsed(null); setParseErr(e.message); }
    finally { setParsing(false); }
  };
  const run = async () => {
    setBusy(true); setErr(null);
    try { const r = await post<CBacktest>("/crypto/backtest", body); setBacktest(r); setRefine(null); router.push(`/crypto/results${r.saved_result_id ? `?id=${r.saved_result_id}` : ""}`); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };

  return (
    <div>
      <PageHead title="Crypto strategy builder" sub="Describe a strategy in plain English or pick the rules yourself. Test it on real Binance candles (daily, 4-hour or 1-hour), with 10 bps default costs. Rex checks its crypto memory first." />
      {error && <Alert title="Could not load crypto settings">{error}</Alert>}
      {t && (
        <Card className="mb-5" data-testid="nl-card">
          <CardHeader><div className="flex items-center gap-2"><CardTitle>Describe your strategy</CardTitle></div>
            <CardDesc>Name the coin (BTC, ETH, SOL) and optionally the candle size (daily, 4h, 1h). Same rule parser as the stock section: N-candle breakout, MA crossover, RSI, mean reversion, plus hold, stop-loss and take-profit. On 1h/4h charts every “day” in your rule means one candle.</CardDesc></CardHeader>
          <CardBody className="space-y-3">
            <Textarea data-testid="nl-text" aria-label="Describe your crypto strategy" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Buy ETH on 4h when price breaks 20-day high, hold 6 hours, stop 3%" />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-mute">Try:</span>
              {EXAMPLES.map((x) => <button key={x.label} type="button" data-testid="nl-chip" onClick={() => setText(x.text)}
                className="rounded-full border border-line bg-ink px-3 py-1 text-xs text-text hover:border-mute/60 hover:text-text">{x.label}</button>)}
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
                  <Badge>Rules parser</Badge>
                  {parsed.strategy && <Badge tone={parsed.confidence >= 0.75 ? "gain" : parsed.confidence >= 0.5 ? "warn" : "loss"}>confidence {Math.round(parsed.confidence * 100)}%</Badge>}
                </div>
                {parsed.strategy && parsed.summary && <p data-testid="parsed-summary" className="text-text">{parsed.summary}</p>}
                {parsed.unsupported.length > 0 && <Alert tone="warn" title="Not supported / ignored" className="[&_li]:ml-4 [&_li]:list-disc"><ul data-testid="parsed-unsupported">{parsed.unsupported.map((u, i) => <li key={i}>{u}</li>)}</ul></Alert>}
                {parsed.assumptions.length > 0 && <Alert tone="info" title="Assumptions Rex made" className="[&_li]:ml-4 [&_li]:list-disc"><ul data-testid="parsed-assumptions">{parsed.assumptions.map((u, i) => <li key={i}>{u}</li>)}</ul></Alert>}
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
              <div className="grid gap-4 sm:grid-cols-3">
                <FormField><Label>Coin</Label>
                  <Select value={symbol} onChange={(e) => setSymbol(e.target.value)} data-testid="coin-select">{t.symbols.map((x) => <option key={x.symbol} value={x.symbol}>{x.name} ({x.symbol.replace("USDT", "")}/USDT)</option>)}</Select></FormField>
                <FormField><Label>Candles</Label>
                  <Select value={interval} onChange={(e) => setInterval_(e.target.value)} data-testid="interval-select">{t.intervals.map((x) => <option key={x.interval} value={x.interval}>{IV_LABEL[x.interval] ?? x.label} ({x.interval})</option>)}</Select></FormField>
                <FormField><Label>Template</Label>
                  <Select value={tpl} onChange={(e) => { setTpl(e.target.value); setParams({}); }}>{Object.entries(t.templates).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Select></FormField>
              </div>
              <div><div className="mb-2 text-sm font-medium">Parameters <span className="font-normal text-mute">(in {unit})</span></div>
                <div className="grid gap-4 sm:grid-cols-3">{meta.params.map((m) => <Field key={m.name} m={m} value={params[m.name] ?? m.default} onChange={(v) => setParams((p) => ({ ...p, [m.name]: v }))} />)}</div></div>
              <div><div className="mb-2 text-sm font-medium">Exits <span className="font-normal text-mute">(hold is counted in {unit})</span></div>
                <div className="grid gap-4 sm:grid-cols-3">{t.exits.map((m) => <Field key={m.name} m={m} value={exits[m.name] ?? (m.optional ? null : m.default)} onChange={(v) => setExits((p) => ({ ...p, [m.name]: v }))} />)}</div></div>
              <FormField className="max-w-xs"><Label>Round-trip cost (bps)</Label>
                <Input type="number" min={0} max={500} step={1} value={cost ?? t.cost_bps_default} onChange={(e) => setCost(e.target.value === "" ? null : Number(e.target.value))} data-testid="cost-input" />
                <p className="mt-1 text-xs text-mute">ASSUMPTION: 10 bps default for crypto (fees plus slippage), editable.</p></FormField>
              {err && <Alert title="Run failed">{err}</Alert>}
              <div className="flex flex-wrap items-center gap-3">
                <Button size="lg" onClick={run} disabled={busy} data-testid="run-backtest"><Play className="h-4 w-4" />{busy ? "Running…" : "Run backtest"}</Button>
                <span className="text-xs text-mute">Locked final period: {iv?.locked}. It is only shown as the “locked” window.</span>
              </div>
            </CardBody>
          </Card>
          <div className="space-y-5 lg:sticky lg:top-20 lg:self-start"><CryptoLastTime symbol={symbol} template={tpl} /></div>
        </div>
      )}
    </div>
  );
}
