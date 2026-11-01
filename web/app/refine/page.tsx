"use client";
import { SavedResult } from "@/components/saved-result";
import Link from "next/link";
import { ShieldCheck, ShieldAlert, ShieldQuestion, Minus, Lock } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Table, Td, Th } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { OverlayChart } from "@/components/charts";
import { Explain } from "@/components/explain";
import { TrackPanel } from "@/components/tracker-bits";
import { OverfitCard, WalkForwardPanel } from "@/components/quant-bits";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRex } from "@/lib/store";
import type { Metrics } from "@/lib/types";
import { num, pct, pctPlain, tone, cn } from "@/lib/utils";

const V = {
  ok: { label: "PASSED", cls: "border-gain/50 bg-gain/10 text-gain", Icon: ShieldCheck },
  overfit: { label: "OVERFIT", cls: "border-loss/50 bg-loss/10 text-loss", Icon: ShieldAlert },
  inconclusive: { label: "INCONCLUSIVE", cls: "border-warn/50 bg-warn/10 text-warn", Icon: ShieldQuestion },
  no_change: { label: "NO CHANGE", cls: "border-line bg-panel2 text-mute", Icon: Minus },
} as const;

const Row = ({ label, a, b, fmt, better = "high" }: { label: string; a: number | null; b: number | null; fmt: (x: number | null) => string; better?: "high" | "low" }) => {
  const win = a != null && b != null && a !== b && (better === "high" ? b > a : b < a);
  return <tr><Td className="text-mute">{label}</Td><Td className="num">{fmt(a)}</Td><Td className={cn("num font-medium", win && "text-gain")}>{fmt(b)}</Td></tr>;
};

function Compare({ title, o, r }: { title: string; o: Metrics; r: Metrics }) {
  return (
    <Card><CardHeader><CardTitle>{title}</CardTitle></CardHeader><CardBody>
      <Table><thead><tr><Th>Metric</Th><Th>Original</Th><Th>Refined</Th></tr></thead><tbody>
        <Row label="Total return" a={o.total_return} b={r.total_return} fmt={(x) => pct(x)} />
        <Row label="Sharpe" a={o.sharpe} b={r.sharpe} fmt={(x) => num(x)} />
        <Row label="Max drawdown" a={o.max_drawdown} b={r.max_drawdown} fmt={(x) => pct(x)} />
        <Row label="Win rate" a={o.win_rate} b={r.win_rate} fmt={(x) => (x == null ? "n/a" : pctPlain(x, 0))} />
        <Row label="Trades" a={o.trades} b={r.trades} fmt={(x) => String(x)} />
      </tbody></Table></CardBody></Card>
  );
}

function RefinePage() {
  const { refine: f } = useRex();
  if (!f) return (
    <div><PageHead title="Refine" />
      <Alert tone="info" title="No refinement yet">Run a backtest, then press “Refine this strategy” on the <Link className="underline" href="/results">results page</Link>.</Alert></div>
  );
  const v = V[f.verdict.flag] ?? V.inconclusive;
  const o = f.original.result, r = f.refined.result;
  const diff = (a: Record<string, any>, b: Record<string, any>) => Object.keys({ ...a, ...b }).filter((k) => a[k] !== b[k]);
  const changed = [...diff(f.original.params, f.refined.params).map((k) => ["param", k, f.original.params[k], f.refined.params[k]]), ...diff(f.original.exits, f.refined.exits).map((k) => ["exit", k, f.original.exits[k], f.refined.exits[k]])];
  return (
    <div className="space-y-5">
      <PageHead title={`Refine: ${f.spec.ticker} · ${f.spec.template.replace("_", " ")}`} sub={`Tuned on ${f.search.tuned_on}. Locked ${f.search.locked}.`} right={<Link className="underline text-sm" href={`/lab${f.saved_result_id ? `?id=${f.saved_result_id}` : ""}`}>Test refined strategy in Lab</Link>} />
      <Tabs defaultValue="locked">
        <TabsList><TabsTrigger value="locked" data-testid="tab-locked">Locked-year test</TabsTrigger><TabsTrigger value="walkforward" data-testid="tab-walkforward">Walk-forward</TabsTrigger></TabsList>
        <TabsContent value="walkforward">
          <WalkForwardPanel ticker={f.spec.ticker} template={f.spec.template} params={f.original.params} exits={f.original.exits} cost_bps={f.spec.cost_bps}
            cost_model={f.spec.cost_model} slippage_bps={f.spec.slippage_bps} trade_value={f.spec.trade_value} />
        </TabsContent>
        <TabsContent value="locked" className="space-y-5">
      <div className={cn("flex flex-wrap items-center gap-4 rounded-md border p-5", v.cls)} data-testid="verdict" data-flag={f.verdict.flag}>
        <v.Icon className="h-9 w-9" />
        <div className="flex-1"><div className="text-xs font-medium uppercase tracking-widest opacity-80"><Lock className="mr-1 inline h-3 w-3" />Locked-year verdict</div>
          <div className="text-3xl font-bold tracking-tight">{v.label}</div></div>
        <p className="max-w-xl text-sm text-text/90">{f.verdict.headline}</p>
      </div>
      <Card><CardBody><ul className="list-inside list-disc space-y-1 text-sm text-text/90">{f.verdict.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        <Explain kind="refine" facts={{ spec: f.spec, verdict: f.verdict, original_locked: o.locked.metrics, refined_locked: r.locked.metrics, changed }} /></CardBody></Card>

      <Card><CardHeader><CardTitle>What changed</CardTitle><CardDesc>Best of {f.search.eligible} eligible candidates ({f.search.trials} combinations tried) by {f.search.objective}. Picking the best of many flatters the training result.</CardDesc></CardHeader>
        <CardBody>{changed.length === 0 ? <p className="text-sm text-mute">The search kept your original settings.</p> :
          <Table><thead><tr><Th>Kind</Th><Th>Setting</Th><Th>Original</Th><Th>Refined</Th></tr></thead><tbody>
            {changed.map(([kind, k, a, b]) => <tr key={`${kind}${k}`}><Td className="text-mute">{String(kind)}</Td><Td>{String(k)}</Td><Td className="num">{String(a ?? "off")}</Td><Td className="num font-medium text-rex">{String(b ?? "off")}</Td></tr>)}</tbody></Table>}</CardBody></Card>

      <OverfitCard stats={f.overfit_stats} />

      <div className="grid gap-4 md:grid-cols-2">
        <Compare title="Train (what the search saw)" o={o.train.metrics} r={r.train.metrics} />
        <Compare title="Locked final year (the honest test)" o={o.locked.metrics} r={r.locked.metrics} />
      </div>
      <Card><CardHeader><CardTitle>Locked year: original vs refined vs buy & hold</CardTitle></CardHeader><CardBody><OverlayChart a={o.locked} b={r.locked} /></CardBody></Card>

      <TrackPanel ticker={f.spec.ticker} template={f.spec.template} cost_bps={f.spec.cost_bps} cost_model={f.spec.cost_model} slippage_bps={f.spec.slippage_bps} trade_value={f.spec.trade_value} variants={[
        { label: "original", title: "Original strategy", params: f.original.params, exits: f.original.exits },
        ...(f.refined.changed ? [{ label: "refined" as const, title: `Refined strategy (${v.label})`, params: f.refined.params, exits: f.refined.exits }] : [])]} />

      <Card><CardHeader><CardTitle>Top candidates on training data</CardTitle><CardDesc>{pctPlain(f.search.share_of_candidates_with_positive_train_sharpe ?? undefined, 0)} of candidates had a positive training Sharpe.</CardDesc></CardHeader>
        <CardBody><Table><thead><tr><Th>Params</Th><Th>Exits</Th><Th>Train Sharpe</Th><Th>Train return</Th><Th>Trades</Th><Th>Win rate</Th></tr></thead><tbody>
          {f.search.top_candidates.slice(0, 8).map((c: any, i: number) => (
            <tr key={i}><Td className="num">{JSON.stringify(c.params)}</Td><Td className="num">{Object.entries(c.exits).map(([a, b]) => `${a}=${b ?? "off"}`).join(" ")}</Td>
              <Td className="num">{num(c.train_sharpe)}</Td><Td className={cn("num", tone(c.train_return))}>{pct(c.train_return)}</Td><Td className="num">{c.train_trades}</Td><Td className="num">{pctPlain(c.train_win_rate, 0)}</Td></tr>))}
        </tbody></Table></CardBody></Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function ResultRoute() { return <SavedResult market="nse" kind="refine"><RefinePage /></SavedResult>; }
