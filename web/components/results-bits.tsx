"use client";
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Metric } from "./metric";
import { Badge } from "./ui/badge";
import { Table, Td, Th } from "./ui/misc";
import type { Trade, Window } from "@/lib/types";
import { num, pct, pctPlain, pp, tone, cn } from "@/lib/utils";

export function MetricsGrid({ w, unit = "days" }: { w: Window; unit?: string }) {
  const m = w.metrics, b = w.benchmark;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="metrics">
      <Metric label="Total return" value={pct(m.total_return)} valueClass={tone(m.total_return)} sub={`Buy & hold ${pct(b.total_return)}`} />
      <Metric label="Max drawdown" value={pct(m.max_drawdown)} valueClass="text-loss" sub={`Buy & hold ${pct(b.max_drawdown)}`} />
      <Metric label="Sharpe" value={num(m.sharpe)} sub={`Buy & hold ${num(b.sharpe)}`} />
      <Metric label="Trades" value={m.trades} sub={`Exposure ${pctPlain(m.exposure, 0)} of ${unit}`} />
      <Metric label="Win rate" value={m.win_rate == null ? "n/a" : pctPlain(m.win_rate, 0)} sub={m.trades < 20 ? "few trades: weak evidence" : undefined} />
      <Metric label="Profit factor" value={num(m.profit_factor)} />
      <Metric label="Avg trade" value={m.avg_trade == null ? "n/a" : pct(m.avg_trade, 2)} valueClass={tone(m.avg_trade)}
        sub={m.avg_trade_ci95 ? `95% CI ${pct(m.avg_trade_ci95[0], 1)} to ${pct(m.avg_trade_ci95[1], 1)}` : undefined} />
      <Metric label="CAGR" value={pct(m.cagr)} valueClass={tone(m.cagr)} sub={`${m.days} ${unit}`} />
    </div>
  );
}

type K = "entry_date" | "return_pct" | "bars";
export function TradeTable({ trades, limit = 15, unit = "Bars" }: { trades: Trade[]; limit?: number; unit?: string }) {
  const [k, setK] = useState<K>("entry_date"); const [asc, setAsc] = useState(false); const [all, setAll] = useState(false);
  const rows = useMemo(() => [...trades].sort((a, b) => (a[k] > b[k] ? 1 : -1) * (asc ? 1 : -1)), [trades, k, asc]);
  const head = (key: K, label: string) => (
    <Th className="cursor-pointer select-none" onClick={() => { if (k === key) setAsc(!asc); else { setK(key); setAsc(false); } }}>
      <span className="inline-flex items-center gap-1">{label}{k === key && (asc ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</span></Th>
  );
  if (!trades.length) return <p className="text-sm text-mute">No trades in this window.</p>;
  return (
    <div data-testid="trade-table">
      <Table>
        <thead><tr>{head("entry_date", "Entry")}<Th>Exit</Th><Th>Reason</Th>{head("bars", unit)}{head("return_pct", "Return")}<Th>Context at entry</Th></tr></thead>
        <tbody>{(all ? rows : rows.slice(0, limit)).map((t, i) => (
          <tr key={i}><Td className="num">{t.entry_date}</Td><Td className="num">{t.exit_date}</Td><Td>{t.reason}</Td><Td className="num">{t.bars}</Td>
            <Td className={cn("num font-medium", tone(t.return_pct))}>{pp(t.return_pct)}</Td>
            <Td><div className="flex gap-1">{Object.entries(t.tags || {}).slice(0, 3).map(([a, b]) => <Badge key={a}>{a} {b}</Badge>)}</div></Td></tr>))}
        </tbody>
      </Table>
      {trades.length > limit && <button className="mt-2 text-xs text-rex underline" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${trades.length}`}</button>}
    </div>
  );
}
