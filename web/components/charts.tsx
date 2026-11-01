"use client";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend, BarChart, Bar, Cell, LabelList, ReferenceLine } from "recharts";
import type { Window, Brief } from "@/lib/types";

const axis = { stroke: "#8a8f98", fontSize: 11 };
const tip = { contentStyle: { background: "#111113", border: "1px solid #232427", borderRadius: 4, fontSize: 12 }, labelStyle: { color: "#8a8f98" } };

export function EquityChart({ w, labelA = "Strategy" }: { w: Window; labelA?: string }) {
  const rows = w.dates.map((d, i) => ({ d, s: w.equity[i], b: w.benchmark_equity[i] }));
  let peak = 0; const dd = rows.map((r) => { peak = Math.max(peak, r.s); return { d: r.d, dd: (r.s / peak - 1) * 100 }; });
  return (
    <div>
      <div className="h-72" data-testid="equity-chart">
        <ResponsiveContainer>
          <LineChart data={rows} syncId="eq" margin={{ left: 0, right: 8, top: 8 }}>
            <CartesianGrid stroke="#1c1d20" vertical={false} />
            <XAxis dataKey="d" tick={axis} minTickGap={50} />
            <YAxis tick={axis} domain={["auto", "auto"]} width={44} tickFormatter={(v) => v.toFixed(2)} />
            <Tooltip {...tip} formatter={(v: any) => Number(v).toFixed(3)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line dataKey="s" name={labelA} stroke="#e6e7e9" dot={false} strokeWidth={1.5} isAnimationActive={false} />
            <Line dataKey="b" name="Buy & hold" stroke="#5f636b" dot={false} strokeWidth={1.25} strokeDasharray="4 3" isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="h-24">
        <ResponsiveContainer>
          <AreaChart data={dd} syncId="eq" margin={{ left: 0, right: 8 }}>
            <XAxis dataKey="d" hide />
            <YAxis tick={axis} width={44} tickFormatter={(v) => `${v.toFixed(0)}%`} />
            <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
            <Area dataKey="dd" name="Strategy drawdown" stroke="#ef4444" fill="none" fillOpacity={0} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function OverlayChart({ a, b }: { a: Window; b: Window }) {
  const rows = a.dates.map((d, i) => ({ d, o: a.equity[i], r: b.equity[i], b: a.benchmark_equity[i] }));
  return (
    <div className="h-72" data-testid="overlay-chart">
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="d" tick={axis} minTickGap={40} />
          <YAxis tick={axis} domain={["auto", "auto"]} width={44} tickFormatter={(v) => v.toFixed(2)} />
          <Tooltip {...tip} formatter={(v: any) => Number(v).toFixed(3)} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line dataKey="o" name="Original" stroke="#8a8f98" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line dataKey="r" name="Refined" stroke="#e6e7e9" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line dataKey="b" name="Buy & hold" stroke="#5f636b" dot={false} strokeWidth={1.25} strokeDasharray="4 3" isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ProofChart({ series }: { series: { date: string; off: number; on: number }[] }) {
  return (
    <div className="h-80" data-testid="proof-chart">
      <ResponsiveContainer>
        <LineChart data={series} margin={{ left: 8, right: 8, top: 8 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="date" tick={axis} minTickGap={50} />
          <YAxis tick={axis} width={64} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
          <Tooltip {...tip} formatter={(v: any) => `₹${Math.round(Number(v)).toLocaleString("en-IN")}`} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line dataKey="off" name="Memory OFF (take every trade)" stroke="#8a8f98" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line dataKey="on" name="Memory ON (follow Rex's check)" stroke="#e6e7e9" dot={false} strokeWidth={1.5} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function HoldChart({ losers, winners }: { losers: number; winners: number }) {
  const rows = [{ k: "Winners", v: winners, c: "#10b981" }, { k: "Losers", v: losers, c: "#ef4444" }];
  return (
    <div className="h-56" data-testid="hold-chart">
      <ResponsiveContainer>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 48 }}>
          <CartesianGrid stroke="#1c1d20" horizontal={false} />
          <XAxis type="number" tick={axis} />
          <YAxis type="category" dataKey="k" tick={axis} width={64} />
          <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(1)} days`} />
          <Bar dataKey="v" radius={[0, 2, 2, 0]} isAnimationActive={false}>
            {rows.map((r) => <Cell key={r.k} fill={r.c} />)}
            <LabelList dataKey="v" position="right" formatter={(v: any) => `${Number(v).toFixed(1)}d`} fill="#e6e7e9" fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function WinLossChart({ win, loss }: { win: number; loss: number }) {
  const rows = [{ k: "Avg win", v: win, c: "#10b981" }, { k: "Avg loss", v: loss, c: "#ef4444" }];
  return (
    <div className="h-56" data-testid="winloss-chart">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 16 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="k" tick={axis} />
          <YAxis tick={axis} width={40} tickFormatter={(v) => `${v}%`} />
          <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(2)}%`} />
          <Bar dataKey="v" radius={[2, 2, 0, 0]} isAnimationActive={false}>
            {rows.map((r) => <Cell key={r.k} fill={r.c} />)}
            <LabelList dataKey="v" position="top" formatter={(v: any) => `${Number(v).toFixed(1)}%`} fill="#e6e7e9" fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function PriceChart({ b }: { b: Brief }) {
  const lv = [...b.levels.support.slice(0, 2).map((l) => ({ ...l, k: "S" })), ...b.levels.resistance.slice(0, 2).map((l) => ({ ...l, k: "R" }))];
  return (
    <div className="h-80" data-testid="price-chart">
      <ResponsiveContainer>
        <LineChart data={b.series} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="date" tick={axis} minTickGap={60} />
          <YAxis tick={axis} domain={["auto", "auto"]} width={52} tickFormatter={(v) => Math.round(v).toLocaleString("en-IN")} />
          <Tooltip {...tip} formatter={(v: any) => Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 })} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {lv.map((l) => <ReferenceLine key={l.k + l.level} y={l.level} stroke={l.k === "S" ? "#10b981" : "#ef4444"} strokeDasharray="5 4" strokeOpacity={0.7} label={{ value: `${l.k === "S" ? "support" : "resistance"} ${Math.round(l.level)}`, fill: l.k === "S" ? "#10b981" : "#ef4444", fontSize: 10, position: "insideTopLeft" }} />)}
          <Line dataKey="close" name="Weekly close" stroke="#e6e7e9" dot={false} strokeWidth={1.5} isAnimationActive={false} />
          <Line dataKey="sma50" name="50-day avg" stroke="#8a8f98" dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
          <Line dataKey="sma200" name="200-day avg" stroke="#5f636b" dot={false} strokeWidth={1.2} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function RiskChart({ b }: { b: Brief }) {
  return (
    <div data-testid="risk-chart">
      <div className="h-36">
        <ResponsiveContainer>
          <AreaChart data={b.series} syncId="risk" margin={{ left: 0, right: 8 }}>
            <XAxis dataKey="date" hide />
            <YAxis tick={axis} width={52} tickFormatter={(v) => `${v.toFixed(0)}%`} />
            <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
            <Area dataKey="drawdown_pct" name="Drawdown from peak" stroke="#ef4444" fill="none" fillOpacity={0} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="h-36">
        <ResponsiveContainer>
          <LineChart data={b.series} syncId="risk" margin={{ left: 0, right: 8 }}>
            <CartesianGrid stroke="#1c1d20" vertical={false} />
            <XAxis dataKey="date" tick={axis} minTickGap={60} />
            <YAxis tick={axis} width={52} tickFormatter={(v) => `${v.toFixed(0)}%`} />
            <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(1)}% a year`} />
            <Line dataKey="vol20" name="20-day volatility (annualised)" stroke="#8a8f98" dot={false} strokeWidth={1.5} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function EarningsChart({ rows }: { rows: { date: string; reaction_pct: number }[] }) {
  const d = [...rows].reverse();
  return (
    <div className="h-56" data-testid="earnings-chart">
      <ResponsiveContainer>
        <BarChart data={d} margin={{ left: 0, right: 8, top: 8 }}>
          <CartesianGrid stroke="#1c1d20" vertical={false} />
          <XAxis dataKey="date" tick={axis} minTickGap={30} />
          <YAxis tick={axis} width={40} tickFormatter={(v) => `${v}%`} />
          <Tooltip {...tip} formatter={(v: any) => `${Number(v).toFixed(2)}%`} />
          <ReferenceLine y={0} stroke="#32353a" />
          <Bar dataKey="reaction_pct" name="2-day reaction" isAnimationActive={false}>{d.map((r, i) => <Cell key={i} fill={r.reaction_pct >= 0 ? "#10b981" : "#ef4444"} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
