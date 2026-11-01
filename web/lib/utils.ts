import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...i: ClassValue[]) => twMerge(clsx(i));
export const pct = (x: number | null | undefined, d = 1) => (x == null || Number.isNaN(x) ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`);
export const pctPlain = (x: number | null | undefined, d = 1) => (x == null ? "n/a" : `${(x * 100).toFixed(d)}%`);
export const pp = (x: number | null | undefined, d = 2) => (x == null ? "n/a" : `${x >= 0 ? "+" : ""}${x.toFixed(d)}%`); // value already in %
export const num = (x: number | null | undefined, d = 2) => (x == null ? "n/a" : x.toFixed(d));
export const inr = (x: number | null | undefined) =>
  x == null ? "n/a" : `${x < 0 ? "-" : x > 0 ? "+" : ""}₹${Math.abs(Math.round(x)).toLocaleString("en-IN")}`;
export const tone = (x: number | null | undefined) => (x == null ? "text-mute" : x > 0 ? "text-gain" : x < 0 ? "text-loss" : "text-text");
export const pretty = (s: string) => s.replace(/_/g, " ");
