"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Backtest, Refine } from "./types";

/** Crypto section state. Separate sessionStorage keys, so it can never mix with the NSE section. */
export interface CBacktest extends Omit<Backtest, "spec"> { spec: Backtest["spec"] & { symbol: string; interval: string; market: string }; memory?: any }
export interface CRefine extends Omit<Refine, "spec"> { spec: Backtest["spec"] & { symbol: string; interval: string; market: string }; memory?: any }
interface S { backtest: CBacktest | null; refine: CRefine | null; setBacktest: (b: CBacktest | null) => void; setRefine: (r: CRefine | null) => void }
const Ctx = createContext<S>({ backtest: null, refine: null, setBacktest: () => {}, setRefine: () => {} });

export function CryptoProvider({ children }: { children: ReactNode }) {
  const [backtest, setB] = useState<CBacktest | null>(null);
  const [refine, setR] = useState<CRefine | null>(null);
  useEffect(() => {
    try {
      const b = sessionStorage.getItem("rex.crypto.backtest"); if (b) setB(JSON.parse(b));
      const r = sessionStorage.getItem("rex.crypto.refine"); if (r) setR(JSON.parse(r));
    } catch {}
  }, []);
  const setBacktest = (b: CBacktest | null) => { setB(b); try { b ? sessionStorage.setItem("rex.crypto.backtest", JSON.stringify(b)) : sessionStorage.removeItem("rex.crypto.backtest"); } catch {} };
  const setRefine = (r: CRefine | null) => { setR(r); try { r ? sessionStorage.setItem("rex.crypto.refine", JSON.stringify(r)) : sessionStorage.removeItem("rex.crypto.refine"); } catch {} };
  return <Ctx.Provider value={{ backtest, refine, setBacktest, setRefine }}>{children}</Ctx.Provider>;
}
export const useCrypto = () => useContext(Ctx);

export const usd = (x: number | null | undefined, d = 2) => (x == null ? "n/a" : `$${x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
export const usdSigned = (x: number | null | undefined) => (x == null ? "n/a" : `${x < 0 ? "-" : x > 0 ? "+" : ""}$${Math.abs(x).toLocaleString("en-US", { maximumFractionDigits: 2 })}`);
export const IV_LABEL: Record<string, string> = { "1d": "Daily", "4h": "4-hour", "1h": "1-hour" };
export const unitOf = (iv: string) => (iv === "1d" ? "days" : "candles");

export interface CSymbols {
  symbols: { symbol: string; name: string; yf: string }[];
  intervals: { interval: string; label: string; periods_per_year: number; locked: string }[];
  cost_bps_default: number;
  templates: Record<string, { label: string; description: string; params: any[] }>;
  exits: any[];
}
export interface CQuote { symbol: string; price: number; provider: string; live: boolean; stale: boolean; label: string; market: string; time_utc: string; error?: string }
