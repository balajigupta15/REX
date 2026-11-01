"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Radar, Siren, RefreshCw, CheckCircle2, Activity } from "lucide-react";
import { Card, CardBody, CardDesc, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { FormField, Input, Label, Select } from "@/components/ui/input";
import { Alert, Skeleton, Table, Td, Th } from "@/components/ui/misc";
import { CryptoTrackPanel, NO_ORDERS } from "@/components/crypto-bits";
import { get, post } from "@/lib/api";
import { useGet } from "@/lib/hooks";
import { usd, usdSigned, IV_LABEL, unitOf, type CSymbols } from "@/lib/crypto";
import { cn, pp, pretty, tone } from "@/lib/utils";

const REFRESH_S = 3;
const axis = { stroke: "#8a8f98", fontSize: 11 };
const MARKER = { entry: "#8a8f98", exit: "#e6e7e9", stop: "#ef4444", tp: "#10b981" } as Record<string, string>;
const tip = { background: "#111113", border: "1px solid #232427", borderRadius: 4, fontSize: 12 };

function Tile({ label, value, sub, cls, id }: { label: string; value: string; sub?: string; cls?: string; id?: string }) {
  return (
    <div className="rounded-md border border-line bg-panel/80 p-4" data-testid={id}>
      <div className="text-xs uppercase tracking-wide text-mute">{label}</div>
      <div className={cn("num mt-1 text-2xl font-semibold", cls)}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-mute">{sub}</div>}
    </div>
  );
}

function CandleChart({ s }: { s: any }) {
  const pts: { d: string; close: number }[] = s.chart.points;
  if (!pts.length) return <p className="text-sm text-mute">No candle data yet.</p>;
  const have = new Set(pts.map((p) => p.d));
  const mk = (s.chart.markers as any[]).filter((m) => have.has(m.x));
  const ys = [...pts.map((p) => p.close), s.chart.stop_px, s.chart.tp_px, ...mk.map((m) => m.y)].filter((v): v is number => v != null);
  const lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.06 || 1;
  return (
    <div className="h-80" data-testid="track-chart">
      <ResponsiveContainer>
        <LineChart data={pts} margin={{ left: 0, right: 12, top: 10 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="d" tick={axis} minTickGap={50} />
          <YAxis tick={axis} domain={[lo - pad, hi + pad]} width={64} tickFormatter={(v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })} />
          <Tooltip contentStyle={tip} labelStyle={{ color: "#8a8f98" }} formatter={(v: any) => [usd(Number(v)), "Close"]} />
          {s.chart.stop_px != null && <ReferenceLine y={s.chart.stop_px} stroke={MARKER.stop} strokeDasharray="5 4" label={{ value: `stop ${usd(s.chart.stop_px)}`, fill: MARKER.stop, fontSize: 11, position: "insideBottomLeft" }} />}
          {s.chart.tp_px != null && <ReferenceLine y={s.chart.tp_px} stroke={MARKER.tp} strokeDasharray="5 4" label={{ value: `target ${usd(s.chart.tp_px)}`, fill: MARKER.tp, fontSize: 11, position: "insideTopLeft" }} />}
          <Line dataKey="close" stroke="#e6e7e9" dot={false} strokeWidth={1.8} isAnimationActive={false} />
          {mk.map((m, i) => <ReferenceDot key={i} x={m.x} y={m.y} r={6} fill={MARKER[m.kind] ?? MARKER.entry} stroke="#0b0d12" strokeWidth={2} ifOverflow="extendDomain" label={{ value: m.label, fill: MARKER[m.kind] ?? MARKER.entry, fontSize: 11, position: "top" }} />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** The live, moving price: one point per 3 s refresh, with the entry, stop and target lines. */
function TickChart({ s }: { s: any }) {
  const t: { t: string; p: number }[] = s.chart.ticks ?? [];
  if (t.length < 2) return <p className="text-sm text-mute" data-testid="tick-wait">Collecting live prices… the line starts moving after a few refreshes.</p>;
  const ys = [...t.map((x) => x.p), s.chart.stop_px, s.chart.tp_px, s.chart.entry_px].filter((v): v is number => v != null);
  const lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.1 || hi * 0.0005;
  return (
    <div className="h-56" data-testid="tick-chart">
      <ResponsiveContainer>
        <AreaChart data={t} margin={{ left: 0, right: 12, top: 8 }}>
          <defs><linearGradient id="tk" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#e6e7e9" stopOpacity={0.35} /><stop offset="100%" stopColor="#e6e7e9" stopOpacity={0} /></linearGradient></defs>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="t" tick={axis} minTickGap={60} />
          <YAxis tick={axis} domain={[lo - pad, hi + pad]} width={72} tickFormatter={(v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 })} />
          <Tooltip contentStyle={tip} labelStyle={{ color: "#8a8f98" }} formatter={(v: any) => [usd(Number(v)), "Live price"]} />
          {s.chart.entry_px != null && <ReferenceLine y={s.chart.entry_px} stroke={MARKER.entry} strokeDasharray="2 3" label={{ value: `entry ${usd(s.chart.entry_px)}`, fill: MARKER.entry, fontSize: 11, position: "insideTopRight" }} />}
          {s.chart.stop_px != null && <ReferenceLine y={s.chart.stop_px} stroke={MARKER.stop} strokeDasharray="5 4" />}
          {s.chart.tp_px != null && <ReferenceLine y={s.chart.tp_px} stroke={MARKER.tp} strokeDasharray="5 4" />}
          <Area dataKey="p" stroke="#e6e7e9" fill="url(#tk)" strokeWidth={1.8} dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function CloseForm({ s, onDone }: { s: any; onDone: (x: any) => void }) {
  const [price, setPrice] = useState(""); const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const logged = s.mode === "logged" && !!s.position;
  const submit = async () => {
    setBusy(true); setErr(null);
    try { onDone(await post<any>(`/crypto/tracker/sessions/${s.id}/close`, { exit_price: price === "" ? null : Number(price), note: note || null })); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  };
  return (
    <Card data-testid="close-form">
      <CardHeader><CardTitle>{logged ? "I closed it on my exchange" : "Stop tracking"}</CardTitle>
        <CardDesc>{logged ? "Enter the price you actually got on your exchange. Rex saves the outcome to crypto memory. Rex does not close anything for you." : "Ends the session and saves it to crypto memory."}</CardDesc></CardHeader>
      <CardBody className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[220px_1fr]">
          <FormField><Label>Exit price (USDT, optional)</Label><Input type="number" min={0} step="any" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="live price" data-testid="close-price" /></FormField>
          <FormField><Label>Note (optional)</Label><Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what you saw, why you exited" maxLength={500} data-testid="close-note" /></FormField>
        </div>
        {err && <Alert title="Could not close">{err}</Alert>}
        <Button onClick={submit} disabled={busy} variant={logged ? "default" : "outline"} data-testid="close-btn">{busy ? "Saving…" : logged ? "Mark closed and save to memory" : "Stop and save to memory"}</Button>
      </CardBody>
    </Card>
  );
}

function ClosedCard({ c }: { c: any }) {
  const sv = c.saved ?? {};
  return (
    <Card className="border-line" data-testid="closed-card">
      <CardHeader><div className="flex flex-wrap items-center gap-2"><CheckCircle2 className="h-4 w-4 text-rex" /><CardTitle>Closed and saved to crypto memory</CardTitle>
        <Badge tone={c.verdict === "WON" ? "gain" : c.verdict === "LOST" ? "loss" : "neutral"} data-testid="closed-verdict">{c.verdict}</Badge></div>
        <CardDesc>{c.closed_at} UTC{c.net_pct != null && <> · net <span className={tone(c.net_pct)}>{pp(c.net_pct)}</span> after costs</>}</CardDesc></CardHeader>
      <CardBody className="space-y-3 text-sm">
        {c.mfe_pct != null && <p className="text-mute">Price range while you held it: best {pp(c.mfe_pct, 1)}, worst {pp(c.mae_pct, 1)} from your entry.</p>}
        {c.exit_signal && <Alert tone="warn" title="The strategy's exit had fired">{c.exit_signal.text ?? `At ${c.exit_signal.date}`}{c.followed_strategy_exit ? " You closed on or after it." : " You held past it."}</Alert>}
        {sv.lesson && <p data-testid="closed-lesson" className="rounded-lg border border-line bg-panel2 p-2.5 text-xs"><span className="font-semibold text-rex">Lesson: </span>{sv.lesson}</p>}
        <ul className="list-inside list-disc space-y-1 text-xs text-mute">
          {sv.strategy_memory && <li>Crypto memory: {sv.strategy_memory.headline}</li>}
          <li>Stored in {sv.strategy_memory?.stored_in ?? "SQLite"}{sv.strategy_memory?.meko_mirror === "queued" ? " and mirrored to Meko" : ""}.</li>
        </ul>
        <div className="flex gap-3 text-xs"><Link className="underline" href="/crypto/memory">Open Last time</Link><Link className="underline" href="/crypto">Run a new backtest</Link></div>
      </CardBody>
    </Card>
  );
}

function Detail({ id }: { id: string }) {
  const [s, setS] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [spin, setSpin] = useState(false);
  const live = useRef(true); const inflight = useRef(false);
  const [okAt, setOkAt] = useState<number | null>(null); const [now, setNow] = useState(Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(i); }, []);
  const ago = okAt ? Math.max(0, Math.round((now - okAt) / 1000)) : null;
  const load = useCallback(async (refresh: boolean) => {
    if (inflight.current) return; inflight.current = true;
    setSpin(true);
    try { const x = await get<any>(`/crypto/tracker/sessions/${id}${refresh ? "?refresh=true" : ""}`); if (live.current) { setS(x); setErr(null); setOkAt(Date.now()); } }
    catch (e: any) { if (live.current) setErr(e.message); }
    finally { inflight.current = false; if (live.current) setSpin(false); }
  }, [id]);
  useEffect(() => { live.current = true; load(true); return () => { live.current = false; }; }, [load]);
  const active = s?.status === "active";
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => load(true), REFRESH_S * 1000);
    return () => clearInterval(t);
  }, [active, load]);

  if (err && !s) return <Alert title="Could not load this tracker session">{err} <Link className="underline" href="/crypto/tracker">Back to all sessions</Link></Alert>;
  if (!s) return <Skeleton className="h-96" />;
  const p = s.position, q = s.quote ?? {};
  const logged = s.mode === "logged";
  const unit = unitOf(s.interval);
  const coin = s.symbol.replace("USDT", "");
  return (
    <div className="space-y-5" data-testid="crypto-detail" data-status={s.status} data-mode={s.mode}>
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/crypto/tracker" className="text-xs text-mute underline">All sessions</Link>
        <h2 className="text-xl font-semibold tracking-tight">{coin}/USDT · {pretty(s.template)} · {IV_LABEL[s.interval]}</h2>
        <Badge tone={logged ? "rex" : "sky"}>{logged ? "your trade on your exchange" : "strategy paper track"}</Badge>
        <Badge tone={s.status === "active" ? "gain" : "neutral"}>{s.status}</Badge><Badge>{s.label}</Badge>
        <Badge tone={q.stale ? "warn" : "gain"} data-testid="rt-badge"><Activity className="h-3 w-3" />{q.stale ? "Not live (cached)" : `Real-time · ${REFRESH_S}s`}</Badge>
        <Button size="sm" variant="outline" className="ml-auto" onClick={() => load(true)} disabled={spin} data-testid="refresh-btn"><RefreshCw className={cn("h-3.5 w-3.5", spin && "animate-spin")} />Refresh now</Button>
      </div>
      {s.alert && s.status === "active" && (
        <div role="alert" data-testid="exit-alert" className="flex items-start gap-3 rounded-md border border-loss/60 bg-loss/15 p-4 text-loss">
          <Siren className="mt-0.5 h-6 w-6 shrink-0" />
          <div><div className="text-lg font-bold tracking-tight">Strategy says exit now</div>
            <div className="text-sm text-text/90">{s.exit_signal?.text}{logged ? " Rex only tracks: close it on your exchange if you decide to follow the strategy." : ""}</div></div>
        </div>)}
      {s.pending_close && <Alert tone="warn" title="Hold limit reached on the forming candle">The exit fires when the current {s.interval} candle closes{s.candle_closes_in_s != null ? ` (in about ${s.candle_closes_in_s}s)` : ""}, so Rex waits for its final price.</Alert>}
      {s.last_error && <Alert tone="warn" title="Last refresh problem">{s.last_error}</Alert>}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile id="tile-price" label={`${coin} live price`} value={usd(q.price)} sub={`${q.stale ? "STALE · " : "LIVE · "}${ago != null ? `updated ${ago}s ago` : "connecting…"} · ${q.time_utc ?? ""} UTC`} />
        {p ? (<>
          <Tile id="tile-pnl" label={logged ? "Your P&L (after costs)" : "Open position P&L"} value={pp(p.net_pct)} cls={tone(p.net_pct)} sub={`${p.pnl_abs != null ? usdSigned(p.pnl_abs) + " on " + p.qty + " " + coin + " · " : ""}entry ${usd(p.entry_px)} (${p.direction})`} />
          <Tile id="tile-stop" label="Distance to stop" value={p.stop_px == null ? "no stop" : p.dist_to_stop_pct != null && p.dist_to_stop_pct < 0 ? "Stop level passed" : pp(p.dist_to_stop_pct, 1)} sub={p.stop_px == null ? "stop-loss is off" : `stop at ${usd(p.stop_px)}${p.dist_to_stop_pct != null && p.dist_to_stop_pct < 0 ? ` · price is ${Math.abs(p.dist_to_stop_pct).toFixed(1)}% beyond it` : ""}`} />
          <Tile id="tile-tp" label="Distance to target" value={p.tp_px == null ? "no target" : pp(p.dist_to_tp_pct, 1)} sub={p.tp_px == null ? "take-profit is off" : `target at ${usd(p.tp_px)} · max hold ${p.hold_candles} ${unit}`} />
        </>) : (<>
          <Tile id="tile-pnl" label={s.status === "closed" ? "Result" : "Position"} value={s.status === "closed" && s.close?.net_pct != null ? pp(s.close.net_pct) : "flat"} cls={s.status === "closed" ? tone(s.close?.net_pct) : undefined}
            sub={s.status === "closed" ? "see the saved outcome below" : "watching for the strategy's entry signal"} />
          <Tile label="Paper trades" value={String(s.summary.trades)} sub={`${s.summary.wins} winners · ${pp(s.summary.realized_pct, 1)} total`} />
          <Tile label="Strategy" value={s.template.replace("_", " ")} sub={`stop ${s.exits.stop_pct ?? "off"}% · target ${s.exits.tp_pct ?? "off"}% · hold ${s.exits.hold_days} ${unit}`} />
        </>)}
      </div>
      <p className="text-xs text-mute" data-testid="data-labels">{s.data_labels.join(" · ")} Updated {s.updated} UTC{ago != null && active ? ` (${ago}s ago)` : ""}; auto-refresh every {REFRESH_S}s while active.</p>

      {s.entry_watch && !p && s.status === "active" && <Alert tone="info" title="No open position">{s.entry_watch.text}</Alert>}

      <Card><CardHeader><CardTitle>Live price (moving)</CardTitle>
        <CardDesc>One point per {REFRESH_S}-second refresh{p ? ", with your entry, stop and target lines" : ""}. Real-time Binance price, 24/7.</CardDesc></CardHeader>
        <CardBody><TickChart s={s} /></CardBody></Card>
      <Card><CardHeader><CardTitle>Price vs the strategy</CardTitle>
        <CardDesc>{IV_LABEL[s.interval]} candle closes{p ? ", your entry, and the strategy's stop and target" : ""}. Last point is the live (forming) candle.</CardDesc></CardHeader>
        <CardBody><CandleChart s={s} /></CardBody></Card>

      {s.status === "active" ? <CloseForm s={s} onDone={setS} /> : s.close && <ClosedCard c={s.close} />}

      <Card><CardHeader><CardTitle>Fill log ({s.fills.length})</CardTitle><CardDesc>{logged ? "The trade you logged and the close you reported. Rex records, it never trades." : "Entries and exits the strategy's rules would have made (same engine as the backtest)."}</CardDesc></CardHeader>
        <CardBody>{s.fills.length === 0 ? <p className="text-sm text-mute" data-testid="no-fills">No fills yet.</p> :
          <Table data-testid="fill-table"><thead><tr><Th>When (UTC)</Th><Th>Kind</Th><Th>Side</Th><Th>Price</Th><Th>Qty</Th><Th>Net</Th><Th>Why</Th></tr></thead><tbody>
            {s.fills.map((f: any) => <tr key={f.id}><Td>{f.ts_label}</Td><Td>{pretty(f.kind)}</Td><Td>{f.side}</Td><Td className="num">{usd(f.price)}</Td><Td className="num">{f.qty ?? ""}</Td>
              <Td className={cn("num", tone(f.pnl_pct))}>{f.pnl_pct == null ? "" : pp(f.pnl_pct)}</Td><Td className="max-w-xs whitespace-normal text-xs text-mute">{f.reason}</Td></tr>)}
          </tbody></Table>}</CardBody></Card>
      <p className="text-xs text-mute">{s.disclaimer}</p>
    </div>
  );
}

function Sessions() {
  const router = useRouter();
  const { data, error, reload } = useGet<{ sessions: any[]; loop: any }>("/crypto/tracker/sessions");
  const { data: t } = useGet<CSymbols>("/crypto/symbols");
  const [symbol, setSymbol] = useState("BTCUSDT"); const [interval, setInterval_] = useState("1d"); const [tpl, setTpl] = useState("breakout");
  useEffect(() => { const i = setInterval(reload, 10000); return () => clearInterval(i); }, [reload]);
  return (
    <div className="space-y-5">
      {error && <Alert title="Could not load crypto tracker sessions">{error}</Alert>}
      <Card data-testid="start-card"><CardHeader><div className="flex items-center gap-2"><Radar className="h-4 w-4 text-rex" /><CardTitle>Start tracking</CardTitle></div>
        <CardDesc>Pick a coin, candle size and strategy with its default rules, or start from the Results page to track the exact rules you backtested.</CardDesc></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <FormField><Label>Coin</Label><Select value={symbol} onChange={(e) => setSymbol(e.target.value)} data-testid="start-symbol">{(t?.symbols ?? [{ symbol: "BTCUSDT", name: "Bitcoin" }]).map((x) => <option key={x.symbol} value={x.symbol}>{x.name}</option>)}</Select></FormField>
            <FormField><Label>Candles</Label><Select value={interval} onChange={(e) => setInterval_(e.target.value)} data-testid="start-interval">{(t?.intervals ?? [{ interval: "1d" }]).map((x) => <option key={x.interval} value={x.interval}>{IV_LABEL[x.interval]}</option>)}</Select></FormField>
            <FormField><Label>Strategy</Label><Select value={tpl} onChange={(e) => setTpl(e.target.value)} data-testid="start-template">{t ? Object.entries(t.templates).map(([k, v]) => <option key={k} value={k}>{v.label}</option>) : <option value="breakout">Breakout</option>}</Select></FormField>
          </div>
          <CryptoTrackPanel symbol={symbol} interval={interval} template={tpl} cost_bps={t?.cost_bps_default ?? 10} variants={[{ label: "original", title: "Default rules", params: {}, exits: {} }]} />
        </CardBody></Card>
      <Card data-testid="sessions-card"><CardHeader><CardTitle>Your crypto tracker sessions</CardTitle>
        <CardDesc>{data ? `24/7 market, always open · background check every ${Math.round(data.loop.interval_s)}s${data.loop.running ? "" : " (paused)"} · times in UTC` : "Loading…"}</CardDesc></CardHeader>
        <CardBody>
          {!data && !error && <Skeleton className="h-24" />}
          {data && data.sessions.length === 0 && <p className="text-sm text-mute" data-testid="no-sessions">No sessions yet. Start one above, or press “Track this strategy live” on a backtest result.</p>}
          {data && data.sessions.length > 0 && (
            <Table data-testid="sessions-table"><thead><tr><Th>Coin</Th><Th>Strategy</Th><Th>Mode</Th><Th>Status</Th><Th>Price</Th><Th>P&L</Th><Th>Alert</Th><Th>Started (UTC)</Th></tr></thead><tbody>
              {data.sessions.map((r) => {
                const pl = r.status === "closed" ? r.closed_net_pct : r.open_pct;
                return (
                  <tr key={r.id} className="cursor-pointer hover:bg-panel2/50" onClick={() => router.push(`/crypto/tracker?id=${r.id}`)} data-testid="session-row">
                    <Td><Link className="font-medium underline-offset-2 hover:underline" href={`/crypto/tracker?id=${r.id}`}>{r.symbol.replace("USDT", "")}</Link> <span className="text-xs text-mute">{r.interval}</span></Td><Td>{pretty(r.template)} <span className="text-xs text-mute">({r.label})</span></Td>
                    <Td className="text-xs">{r.mode === "logged" ? "your trade" : "paper"}</Td><Td><Badge tone={r.status === "active" ? "gain" : "neutral"}>{r.verdict ?? r.status}</Badge></Td>
                    <Td className="num">{usd(r.price)}</Td><Td className={cn("num", tone(pl))}>{pl == null ? "flat" : pp(pl)}</Td>
                    <Td>{r.exit_now && r.status === "active" ? <Badge tone="loss">exit now</Badge> : ""}</Td><Td className="text-xs text-mute">{r.created}</Td></tr>);
              })}
            </tbody></Table>)}
          <p className="mt-3 text-xs text-mute">{NO_ORDERS}</p>
        </CardBody></Card>
    </div>
  );
}

function Inner() {
  const id = useSearchParams().get("id");
  return (
    <div>
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Crypto Tracker</h1>
      <p className="mb-5 max-w-3xl text-sm text-mute">Log the trade you took on your own exchange, or just follow a strategy. Rex tracks the live price against your entry, the strategy's stop and target, and tells you when its exit fires. Rex never places orders.</p>
      {id ? <Detail id={id} /> : <Sessions />}
    </div>
  );
}
export default function CryptoTrackerPage() { return <Suspense fallback={<Skeleton className="h-96" />}><Inner /></Suspense>; }
