"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Radar, Siren, RefreshCw, CheckCircle2, Clock } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Input, Label, Select, Textarea } from "@/components/ui/input";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { PageHead } from "@/components/page-head";
import { TrackPanel } from "@/components/tracker-bits";
import { get, post } from "@/lib/api";
import { useGet } from "@/lib/hooks";
import type { MarketState, Templates, TrackList, TrackSession } from "@/lib/types";
import { cn, inr, pp, pretty, tone } from "@/lib/utils";

const axis = { stroke: "#8a8f98", fontSize: 11 };
const MARKER = { entry: "#8a8f98", exit: "#e6e7e9", stop: "#ef4444", tp: "#10b981" } as Record<string, string>;
const px = (x: number | null | undefined) => (x == null ? "n/a" : `₹${x.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`);

/** Very obvious closed-market banner (NSE is only live 09:15-15:30 IST, Mon-Fri; yfinance quotes ~15 min delayed). */
function MarketBanner({ m, asOf }: { m: MarketState | undefined; asOf?: string }) {
  if (!m) return null;
  if (m.open) return <div className="rounded-md border border-gain/40 bg-gain/10 px-4 py-2.5 text-sm" data-testid="market-banner" data-open="true"><span className="font-semibold text-gain">NSE open</span> · live prices, about 15 min delayed (free data) · session ends 15:30 IST.</div>;
  return (
    <div className="rounded-md border border-warn/50 bg-warn/10 px-4 py-3 text-sm" data-testid="market-banner" data-open="false">
      <span className="font-bold text-warn">NSE closed</span> - showing {asOf ? `last close (${asOf})` : "the last close"}; live during 9:15-15:30 IST, Mon-Fri, about 15 min delayed. {m.next_open ? `Next open ${m.next_open}.` : ""} <span className="text-mute">Prices will not move until then. Want a market that moves now? See the Crypto section (24/7).</span>
    </div>);
}

function MarketBadge({ m }: { m: MarketState | undefined }) {
  if (!m) return null;
  return <Badge tone={m.open ? "gain" : "neutral"} data-testid="market-badge"><Clock className="h-3 w-3" />NSE {m.label}</Badge>;
}

function Tile({ label, value, sub, cls, id }: { label: string; value: string; sub?: string; cls?: string; id?: string }) {
  return (
    <div className="rounded-md border border-line bg-panel/80 p-4" data-testid={id}>
      <div className="text-xs uppercase tracking-wide text-mute">{label}</div>
      <div className={cn("num mt-1 text-2xl font-semibold", cls)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-mute">{sub}</div>}
    </div>
  );
}

function TrackChart({ s }: { s: TrackSession }) {
  const pts = s.chart.points;
  const have = new Set(pts.map((p) => p.d));
  const mk = s.chart.markers.filter((m) => have.has(m.x));
  const ys = [...pts.map((p) => p.close), s.chart.stop_px, s.chart.tp_px, ...mk.map((m) => m.y)].filter((v): v is number => v != null);
  const lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.06 || 1;
  return (
    <div className="h-80" data-testid="track-chart">
      <ResponsiveContainer>
        <LineChart data={pts} margin={{ left: 0, right: 12, top: 10 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="d" tick={axis} minTickGap={50} />
          <YAxis tick={axis} domain={[lo - pad, hi + pad]} width={56} tickFormatter={(v) => Number(v).toFixed(0)} />
          <Tooltip contentStyle={{ background: "#111113", border: "1px solid #232427", borderRadius: 4, fontSize: 12 }} labelStyle={{ color: "#8a8f98" }} formatter={(v: any) => [px(Number(v)), "Price"]} />
          {s.chart.stop_px != null && <ReferenceLine y={s.chart.stop_px} stroke={MARKER.stop} strokeDasharray="5 4" label={{ value: `stop ${px(s.chart.stop_px)}`, fill: MARKER.stop, fontSize: 11, position: "insideBottomLeft" }} />}
          {s.chart.tp_px != null && <ReferenceLine y={s.chart.tp_px} stroke={MARKER.tp} strokeDasharray="5 4" label={{ value: `target ${px(s.chart.tp_px)}`, fill: MARKER.tp, fontSize: 11, position: "insideTopLeft" }} />}
          <Line dataKey="close" stroke="#e6e7e9" dot={false} strokeWidth={1.8} isAnimationActive={false} />
          {mk.map((m, i) => <ReferenceDot key={i} x={m.x} y={m.y} r={6} fill={MARKER[m.kind] ?? MARKER.entry} stroke="#0b0d12" strokeWidth={2} ifOverflow="extendDomain" label={{ value: m.label, fill: MARKER[m.kind] ?? MARKER.entry, fontSize: 11, position: "top" }} />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function CloseForm({ s, onDone }: { s: TrackSession; onDone: (x: TrackSession) => void }) {
  const [price, setPrice] = useState(""); const [note, setNote] = useState(""); const [journal, setJournal] = useState(true);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const logged = s.mode === "logged" && !!s.position;
  const submit = async () => {
    setBusy(true); setErr(null);
    try { onDone(await post<TrackSession>(`/paper/${s.id}/stop`, { exit_price: price === "" ? null : Number(price), note: note || null, add_to_journal: journal })); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };
  return (
    <Card data-testid="close-form">
      <CardHeader><CardTitle>{logged ? "I closed it in my broker" : "Stop tracking"}</CardTitle>
        <CardDesc>{logged ? "Enter the price you actually got in your broker app. Rex saves the outcome and how price moved to memory." : "Ends the session and saves it to strategy memory."}</CardDesc></CardHeader>
      <CardBody className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[200px_1fr]">
          <FormField><Label>Exit price ₹ (optional)</Label><Input type="number" min={0} step="0.05" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="latest price" data-testid="close-price" /></FormField>
          <FormField><Label>Note (optional)</Label><Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what you saw, why you exited" maxLength={500} data-testid="close-note" /></FormField>
        </div>
        {logged && (
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={journal} onChange={(e) => setJournal(e.target.checked)} data-testid="close-journal" className="h-4 w-4 accent-[#e6e7e9]" />
            Add this trade to my journal (feeds the Pre-Trade Check)</label>)}
        {err && <Alert title="Could not close">{err}</Alert>}
        <Button onClick={submit} disabled={busy} variant={logged ? "default" : "outline"} data-testid="close-btn">{busy ? "Saving…" : logged ? "Mark closed and save to memory" : "Stop and save to memory"}</Button>
      </CardBody>
    </Card>
  );
}

function ClosedCard({ c }: { c: NonNullable<TrackSession["close"]> }) {
  const sv = c.saved;
  return (
    <Card className="border-line" data-testid="closed-card">
      <CardHeader><div className="flex flex-wrap items-center gap-2"><CheckCircle2 className="h-4 w-4 text-rex" /><CardTitle>Closed and saved to memory</CardTitle>
        <Badge tone={c.verdict === "WON" ? "gain" : c.verdict === "LOST" ? "loss" : "neutral"} data-testid="closed-verdict">{c.verdict}</Badge></div>
        <CardDesc>{c.closed_at}{c.net_pct != null && <> · net <span className={tone(c.net_pct)}>{pp(c.net_pct)}</span>{c.pnl_abs != null && <> ({inr(c.pnl_abs)})</>} after costs · {c.days_held} day(s) held</>}</CardDesc></CardHeader>
      <CardBody className="space-y-3 text-sm">
        {c.mfe_pct != null && <p className="text-mute">Price range while you held it: best {pp(c.mfe_pct, 1)}, worst {pp(c.mae_pct, 1)} from your entry.</p>}
        {c.exit_signal && <Alert tone="warn" title="The strategy's exit had fired">{c.exit_signal.text ?? `On ${c.exit_signal.date}`}{c.followed_strategy_exit ? " You closed on or after it." : " You held past it."}</Alert>}
        <p data-testid="closed-lesson" className="rounded-lg border border-line bg-panel2 p-2.5 text-xs"><span className="font-semibold text-rex">Lesson: </span>{sv.lesson}</p>
        <ul className="list-inside list-disc space-y-1 text-xs text-mute">
          <li>Strategy memory: {sv.strategy_memory.headline}</li>
          {sv.journal?.added && <li>Added to your journal ({inr(sv.journal.pnl)}). {sv.journal.note}</li>}
          {sv.journal && !sv.journal.added && sv.journal.reason && <li>Journal: {sv.journal.reason}</li>}
          {!sv.journal && <li>Not added to your journal.</li>}
          <li>Stored in {sv.strategy_memory.stored_in}{sv.strategy_memory.meko_mirror === "queued" ? " and mirrored to Meko" : ""}.</li>
        </ul>
        <div className="flex gap-3 text-xs"><Link className="underline" href="/memory">Open Memory</Link><Link className="underline" href="/build">Run a new backtest</Link></div>
      </CardBody>
    </Card>
  );
}

function Detail({ id }: { id: string }) {
  const [s, setS] = useState<TrackSession | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [spin, setSpin] = useState(false);
  const live = useRef(true);
  const load = useCallback(async (refresh: boolean) => {
    setSpin(true);
    try { const x = await get<TrackSession>(`/paper/${id}${refresh ? "?refresh=1" : ""}`); if (live.current) { setS(x); setErr(null); } }
    catch (e: any) { if (live.current) setErr(e.message); }
    finally { if (live.current) setSpin(false); }
  }, [id]);
  useEffect(() => { live.current = true; load(true); return () => { live.current = false; }; }, [load]);
  const secs = Math.max(10, Math.round(s?.refresh_seconds ?? 60));
  const active = s?.status === "active";
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => load(true), secs * 1000);
    return () => clearInterval(t);
  }, [active, secs, load]);

  if (err && !s) return <Alert title="Could not load this tracker session">{err} <Link className="underline" href="/paper">Back to all sessions</Link></Alert>;
  if (!s) return <Skeleton className="h-96" />;
  const p = s.position, q = s.quote;
  const logged = s.mode === "logged";
  return (
    <div className="space-y-5" data-testid="paper-detail" data-status={s.status} data-mode={s.mode}>
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/paper" className="text-xs text-mute underline">All sessions</Link>
        <h2 className="text-xl font-semibold tracking-tight">{s.ticker.replace(".NS", "")} · {pretty(s.template)}</h2>
        <Badge tone={logged ? "rex" : "sky"}>{logged ? "your trade in your broker" : "strategy paper track"}</Badge>
        <Badge tone={s.status === "active" ? "gain" : "neutral"}>{s.status}</Badge><Badge>{s.label}</Badge><MarketBadge m={s.market} />
        <Button size="sm" variant="outline" className="ml-auto" onClick={() => load(true)} disabled={spin} data-testid="refresh-btn"><RefreshCw className={cn("h-3.5 w-3.5", spin && "animate-spin")} />Refresh now</Button>
      </div>
      {s.exit_signal?.fired && s.status === "active" && (
        <div role="alert" data-testid="exit-alert" className="flex items-start gap-3 rounded-md border border-loss/60 bg-loss/15 p-4 text-loss">
          <Siren className="mt-0.5 h-6 w-6 shrink-0" />
          <div><div className="text-lg font-bold tracking-tight">Strategy says exit now</div>
            <div className="text-sm text-text/90">{s.exit_signal?.text}{logged ? " Rex only tracks: close it in your broker if you decide to follow the strategy." : ""}</div></div>
        </div>)}
      <MarketBanner m={s.market} asOf={q.price_time} />
      {s.pending_close && <Alert tone="warn" title="Hold limit reached today">The exit fires at today's close, so Rex waits for the final price.</Alert>}
      {s.last_error && <Alert tone="warn" title="Last refresh problem">{s.last_error}</Alert>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile id="tile-price" label="Live price" value={px(q.price)} sub={`${q.price_time}${q.stale ? " · stale" : ""}`} />
        {p ? (<>
          <Tile id="tile-pnl" label={logged ? "Your P&L (after costs)" : "Open position P&L"} value={pp(p.net_pct)} cls={tone(p.net_pct)} sub={`${inr(p.pnl_abs)} on ${p.qty} @ ${px(p.entry_px)} (${p.direction})`} />
          <Tile id="tile-stop" label="Distance to stop" value={p.stop_px == null ? "no stop" : p.dist_to_stop_pct != null && p.dist_to_stop_pct < 0 ? "Stop level passed" : pp(p.dist_to_stop_pct, 1)} sub={p.stop_px == null ? "stop-loss is off" : `stop at ${px(p.stop_px)}${p.dist_to_stop_pct != null && p.dist_to_stop_pct < 0 ? ` · price is ${Math.abs(p.dist_to_stop_pct).toFixed(1)}% beyond it` : ""}`} />
          <Tile id="tile-tp" label="Distance to target" value={p.tp_px == null ? "no target" : pp(p.dist_to_tp_pct, 1)} sub={p.tp_px == null ? "take-profit is off" : `target at ${px(p.tp_px)} · max hold ${p.hold_days}d`} />
        </>) : (<>
          <Tile id="tile-pnl" label={s.status === "closed" ? "Result" : "Position"} value={s.status === "closed" && s.close?.net_pct != null ? pp(s.close.net_pct) : "flat"} cls={s.status === "closed" ? tone(s.close?.net_pct) : undefined}
            sub={s.status === "closed" ? "see the saved outcome below" : "watching for the strategy's entry signal"} />
          <Tile label="Paper trades" value={String(s.summary.trades)} sub={`${s.summary.wins} winners · ${pp(s.summary.realized_pct, 1)} total`} />
          <Tile label="Strategy" value={s.template.replace("_", " ")} sub={`stop ${s.exits.stop_pct ?? "off"}% · target ${s.exits.tp_pct ?? "off"}% · hold ${s.exits.hold_days}d`} />
        </>)}
      </div>
      <p className="text-xs text-mute" data-testid="data-labels">{s.data_labels.join(" · ")} · Source: {q.source}. Updated {s.updated}; auto-refresh every {secs}s while active.{q.note ? ` ${q.note}` : ""}</p>

      {s.entry_watch && !p && s.status === "active" && <Alert tone="info" title="No open position">{typeof s.entry_watch === "string" ? s.entry_watch : s.entry_watch.text ?? "Waiting for the strategy's entry signal on completed daily bars."}</Alert>}

      <Card><CardHeader><CardTitle>Price vs the strategy</CardTitle>
        <CardDesc>Daily closes{p ? ", your entry, and the strategy's stop and target" : ""}. Last point is the live price.</CardDesc></CardHeader>
        <CardBody><TrackChart s={s} /></CardBody></Card>

      {s.status === "active" ? <CloseForm s={s} onDone={setS} /> : s.close && <ClosedCard c={s.close} />}

      <Card><CardHeader><CardTitle>Fill log ({s.fills.length})</CardTitle><CardDesc>{logged ? "The trade you logged and the close you reported. Rex records, it never trades." : `Entries and exits the strategy's rules would have made (${s.cost_label ?? `${s.cost_bps} bps costs`}).`}</CardDesc></CardHeader>
        <CardBody>{s.fills.length === 0 ? <p className="text-sm text-mute" data-testid="no-fills">No fills yet.</p> :
          <Table data-testid="fill-table"><thead><tr><Th>When</Th><Th>Kind</Th><Th>Side</Th><Th>Price</Th><Th>Qty</Th><Th>Net</Th><Th>Why</Th></tr></thead><tbody>
            {s.fills.map((f) => <tr key={f.id}><Td>{f.ts_label}</Td><Td>{pretty(f.kind)}</Td><Td>{f.side}</Td><Td className="num">{px(f.price)}</Td><Td className="num">{f.qty}</Td>
              <Td className={cn("num", tone(f.pnl_pct))}>{f.pnl_pct == null ? "" : pp(f.pnl_pct)}</Td><Td className="max-w-xs whitespace-normal text-xs text-mute">{f.reason}</Td></tr>)}
          </tbody></Table>}</CardBody></Card>
      <p className="text-xs text-mute">{s.disclaimer}</p>
    </div>
  );
}

function Sessions() {
  const router = useRouter();
  const { data, error, reload } = useGet<TrackList>("/paper/sessions");
  const { data: t } = useGet<Templates>("/strategies/templates");
  const [ticker, setTicker] = useState("RELIANCE.NS"); const [tpl, setTpl] = useState("breakout");
  useEffect(() => { const i = setInterval(reload, 30000); return () => clearInterval(i); }, [reload]);
  return (
    <div className="space-y-5">
      {error && <Alert title="Could not load tracker sessions">{error}</Alert>}
      <Card data-testid="start-card"><CardHeader><div className="flex items-center gap-2"><Radar className="h-4 w-4 text-rex" /><CardTitle>Start tracking</CardTitle></div>
        <CardDesc>Pick a stock and strategy with its default rules, or start from the Results page to track the exact rules you backtested.</CardDesc></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <FormField><Label>Stock</Label><Input list="tk" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} data-testid="start-ticker" /><datalist id="tk">{t?.tickers.map((x) => <option key={x} value={x} />)}</datalist></FormField>
            <FormField><Label>Strategy</Label><Select value={tpl} onChange={(e) => setTpl(e.target.value)} data-testid="start-template">{t ? Object.entries(t.templates).map(([k, v]) => <option key={k} value={k}>{v.label}</option>) : <option value="breakout">Breakout</option>}</Select></FormField>
          </div>
          <TrackPanel ticker={ticker} template={tpl} cost_bps={t?.cost_bps_default ?? 15} variants={[{ label: "original", title: "Default rules", params: {}, exits: {} }]} />
        </CardBody></Card>
      {data && <MarketBanner m={data.market} />}
      <Card data-testid="sessions-card"><CardHeader><CardTitle>Your tracker sessions</CardTitle>
        <CardDesc>{data ? `${data.market.label} · ${data.market.ist_now}${data.market.open ? "" : ` · next open ${data.market.next_open}`} · background check every ${Math.round(data.loop.interval_s)}s${data.loop.running ? "" : " (paused)"}` : "Loading…"}</CardDesc></CardHeader>
        <CardBody>
          {!data && !error && <Skeleton className="h-24" />}
          {data && data.sessions.length === 0 && <p className="text-sm text-mute" data-testid="no-sessions">No sessions yet. Start one above, or press “Track this strategy live” on a backtest result.</p>}
          {data && data.sessions.length > 0 && (
            <Table data-testid="sessions-table"><thead><tr><Th>Stock</Th><Th>Strategy</Th><Th>Mode</Th><Th>Status</Th><Th>Price</Th><Th>P&L</Th><Th>Alert</Th><Th>Started</Th></tr></thead><tbody>
              {data.sessions.map((r) => {
                const pl = r.status === "closed" ? r.closed_net_pct : r.open_pct;
                return (
                  <tr key={r.id} className="cursor-pointer hover:bg-panel2/50" onClick={() => router.push(`/paper?id=${r.id}`)} data-testid="session-row">
                    <Td><Link className="font-medium underline-offset-2 hover:underline" href={`/paper?id=${r.id}`}>{r.ticker.replace(".NS", "")}</Link></Td><Td>{pretty(r.template)} <span className="text-xs text-mute">({r.label})</span></Td>
                    <Td className="text-xs">{r.mode === "logged" ? "your trade" : "paper"}</Td><Td><Badge tone={r.status === "active" ? "gain" : "neutral"}>{r.verdict ?? r.status}</Badge></Td>
                    <Td className="num">{px(r.price)}</Td><Td className={cn("num", tone(pl))}>{pl == null ? "flat" : pp(pl)}</Td>
                    <Td>{r.exit_now && r.status === "active" ? <Badge tone="loss">exit now</Badge> : ""}</Td><Td className="text-xs text-mute">{r.created}</Td></tr>);
              })}
            </tbody></Table>)}
          {data && <p className="mt-3 text-xs text-mute">{data.labels.join(" · ")}. {data.market.holidays_note}</p>}
        </CardBody></Card>
    </div>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  return (
    <div>
      <PageHead title="Live Strategy Tracker" sub="Log the trade you took in your own broker app, or just follow a strategy. Rex tracks the price against your entry, the strategy's stop and target, and tells you when its exit fires. Rex never places orders." />
      {id ? <Detail id={id} /> : <Sessions />}
    </div>
  );
}
export default function PaperPage() { return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>; }
