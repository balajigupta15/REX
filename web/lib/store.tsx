"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Backtest, Refine } from "./types";

interface S { backtest: Backtest | null; refine: Refine | null; setBacktest: (b: Backtest | null) => void; setRefine: (r: Refine | null) => void }
const Ctx = createContext<S>({ backtest: null, refine: null, setBacktest: () => {}, setRefine: () => {} });

export function Providers({ children }: { children: ReactNode }) {
  const [backtest, setB] = useState<Backtest | null>(null);
  const [refine, setR] = useState<Refine | null>(null);
  useEffect(() => {
    try {
      const b = sessionStorage.getItem("rex.backtest"); if (b) setB(JSON.parse(b));
      const r = sessionStorage.getItem("rex.refine"); if (r) setR(JSON.parse(r));
    } catch {}
  }, []);
  const setBacktest = (b: Backtest | null) => { setB(b); try { b ? sessionStorage.setItem("rex.backtest", JSON.stringify(b)) : sessionStorage.removeItem("rex.backtest"); } catch {} };
  const setRefine = (r: Refine | null) => { setR(r); try { r ? sessionStorage.setItem("rex.refine", JSON.stringify(r)) : sessionStorage.removeItem("rex.refine"); } catch {} };
  return <Ctx.Provider value={{ backtest, refine, setBacktest, setRefine }}>{children}</Ctx.Provider>;
}
export const useRex = () => useContext(Ctx);
