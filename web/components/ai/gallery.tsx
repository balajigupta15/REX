"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Input, Label, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { ALL_TOOLS, PRESETS, TOOLS, mkId, type Agent, type Kind, type Preset, type Style, type Tool } from "./agents";
import type { Status } from "./lib";

type Form = { name: string; kind: Kind; role: string; market: "crypto" | "nse"; symbol: string; interval: Agent["interval"]; style: Style; maxDd: string; goal: string; tools: Tool[]; preset?: string };
const blank: Form = { name: "", kind: "trading", role: "", market: "crypto", symbol: "BTCUSDT", interval: "1h", style: "any", maxDd: "20", goal: "", tools: ALL_TOOLS };
const fromPreset = (p: Preset): Form => ({ ...blank, name: p.name, kind: p.kind, role: p.role, market: p.market, symbol: p.symbol, goal: p.goal, tools: p.tools, preset: p.id });

export function presetAgent(p: Preset): Agent {
  return { id: mkId(), name: p.name, kind: p.kind, role: p.role, preset: p.id, tools: p.tools, market: p.market, symbol: p.symbol, interval: "1h", style: "any", maxDd: 20, goal: p.goal, created: Date.now() };
}

/** Gallery of ready-made roles plus a builder for any custom agent. Hiring is optimistic: the card appears immediately and is saved in this browser. */
export function Gallery({ status, onHire }: { status: Status | null; onHire: (a: Agent) => void }) {
  const [f, setF] = useState<Form>(blank);
  const [err, setErr] = useState<string | null>(null);
  const syms = status?.symbols ?? [{ symbol: "BTCUSDT", name: "Bitcoin" }, { symbol: "ETHUSDT", name: "Ether" }];
  const nse = status?.nse ?? [];
  const trading = f.kind === "trading";
  const toggle = (t: Tool) => setF((x) => ({ ...x, tools: x.tools.includes(t) ? x.tools.filter((y) => y !== t) : [...x.tools, t] }));
  const hire = () => {
    const name = f.name.trim(), goal = f.goal.trim(), role = f.role.trim(), dd = Number(f.maxDd);
    const sym = f.market === "nse" ? f.symbol.trim().toUpperCase().replace(/\.NS$/, "") + ".NS" : f.symbol;
    if (name.length < 2) return setErr("Give the agent a name.");
    if (trading && goal.length < 3) return setErr("Tell the agent what to look for, for example: find a strategy that does not blow up in sell-offs.");
    if (!trading && role.length < 3) return setErr("Describe the agent's job, for example: summarise the news on this asset every morning.");
    if (!trading && f.tools.length === 0) return setErr("Pick at least one tool the agent may use.");
    if (f.market === "nse" && sym.length < 4) return setErr("Enter an NSE ticker such as RELIANCE or TCS.");
    if (trading && (!Number.isFinite(dd) || dd < 1 || dd > 90)) return setErr("Max drawdown must be between 1 and 90 percent.");
    onHire({ id: mkId(), name: name.slice(0, 40), kind: f.kind, role: role.slice(0, 300), preset: f.preset, tools: trading ? ALL_TOOLS : f.tools, market: f.market, symbol: sym, interval: f.interval, style: trading ? f.style : "any", maxDd: trading ? dd : 20, goal: trading ? goal.slice(0, 400) : role.slice(0, 400), created: Date.now() });
    setErr(null); setF({ ...blank, kind: f.kind, market: f.market, symbol: f.symbol });
  };
  return (
    <div className="rex-in space-y-4" data-testid="gallery">
      <div>
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-mute">Hire a ready-made agent</div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {PRESETS.map((p) => (
            <div key={p.id} data-testid={`preset-${p.id}`} className="flex min-h-[8.5rem] flex-col rounded border border-line bg-ink/40 p-3 transition-colors hover:border-mute/50">
              <div className="flex items-center justify-between gap-2"><span className="text-sm font-medium">{p.label}</span><Badge tone={p.kind === "trading" ? "sky" : "neutral"}>{p.kind === "trading" ? "Trading" : "Non-trading"}</Badge></div>
              <p className="mt-1 flex-1 text-xs text-mute">{p.blurb}</p>
              <div className="mt-2 flex items-center gap-2">
                <Button size="sm" data-testid={`preset-hire-${p.id}`} onClick={() => onHire(presetAgent(p))}>Hire</Button>
                <Button size="sm" variant="ghost" onClick={() => { setF(fromPreset(p)); setErr(null); document.getElementById("ag-name")?.focus(); }}>Customize</Button>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="rounded border border-line bg-ink/40 p-3" data-testid="hire-form">
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-mute">Or build your own</div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <FormField><Label htmlFor="ag-name">Name</Label><Input id="ag-name" data-testid="agent-name" value={f.name} maxLength={40} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Night shift BTC" /></FormField>
          <FormField><Label htmlFor="ag-kind">Agent type</Label>
            <Select id="ag-kind" data-testid="agent-kind" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as Kind })}><option value="trading">Trading (can deploy as paper signals)</option><option value="analyst">Non-trading (report only)</option></Select></FormField>
          <FormField><Label htmlFor="ag-market">Market</Label>
            <Select id="ag-market" value={f.market} onChange={(e) => { const m = e.target.value as "crypto" | "nse"; setF({ ...f, market: m, symbol: m === "crypto" ? "BTCUSDT" : "RELIANCE" }); }}><option value="crypto">Crypto</option><option value="nse">NSE stock</option></Select></FormField>
          <FormField><Label htmlFor="ag-symbol">{f.market === "crypto" ? "Asset" : "NSE ticker"}</Label>
            {f.market === "crypto"
              ? <Select id="ag-symbol" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })}>{syms.map((s) => <option key={s.symbol} value={s.symbol}>{s.name} ({s.symbol})</option>)}</Select>
              : <><Input id="ag-symbol" list="ag-nse" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} placeholder="RELIANCE" /><datalist id="ag-nse">{nse.map((t) => <option key={t} value={t.replace(".NS", "")} />)}</datalist></>}</FormField>
          <FormField className="sm:col-span-2"><Label htmlFor="ag-role">Role / job description{trading ? " (optional)" : ""}</Label>
            <Textarea id="ag-role" data-testid="agent-role" rows={2} maxLength={300} value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} placeholder={trading ? "Looks after my BTC swing research" : "Summarise the news and risk on this asset"} /></FormField>
          {trading ? (
            <>
              <FormField><Label htmlFor="ag-iv">Candles</Label>
                <Select id="ag-iv" value={f.market === "nse" ? "1d" : f.interval} disabled={f.market === "nse"} onChange={(e) => setF({ ...f, interval: e.target.value as Agent["interval"] })}><option value="1h">1 hour</option><option value="4h">4 hour</option><option value="1d">Daily</option></Select></FormField>
              <FormField><Label htmlFor="ag-style">Style</Label>
                <Select id="ag-style" value={f.style} onChange={(e) => setF({ ...f, style: e.target.value as Style })}><option value="any">Any</option><option value="momentum">Momentum</option><option value="mean-reversion">Mean reversion</option></Select></FormField>
              <FormField><Label htmlFor="ag-dd">Max drawdown, %</Label><Input id="ag-dd" type="number" min={1} max={90} value={f.maxDd} onChange={(e) => setF({ ...f, maxDd: e.target.value })} /></FormField>
              <FormField className="sm:col-span-2"><Label htmlFor="ag-goal">What should it look for?</Label>
                <Textarea id="ag-goal" data-testid="agent-goal" rows={2} maxLength={400} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} placeholder="Find a strategy that holds up in the locked final period" /></FormField>
            </>
          ) : null}
          <fieldset className={cn("sm:col-span-2", trading ? "lg:col-span-1" : "lg:col-span-2")}>
            <legend className="mb-1.5 text-xs font-medium text-mute">Tools it may use</legend>
            <div className="grid gap-1 sm:grid-cols-2">
              {TOOLS.map((t) => (
                <label key={t.id} className={cn("flex items-start gap-2 text-xs", trading ? "text-mute" : "cursor-pointer text-text/90")} title={t.hint}>
                  <input type="checkbox" className="mt-0.5" data-tool={t.id} checked={trading || f.tools.includes(t.id)} disabled={trading} onChange={() => toggle(t.id)} />{t.label}
                </label>))}
            </div>
            {trading && <p className="mt-1 text-[11px] text-mute">A trading agent uses all four through the swarm.</p>}
          </fieldset>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button onClick={hire} data-testid="agent-save">Hire</Button>
          <span className="min-h-5 text-sm text-loss" role="alert">{err}</span>
          <span className="ml-auto text-xs text-mute">{trading ? "Trading agents can be deployed as a paper watch after a run. Rex never places orders." : "Non-trading agents produce a report. They have no deploy option."}</span>
        </div>
      </div>
    </div>
  );
}
