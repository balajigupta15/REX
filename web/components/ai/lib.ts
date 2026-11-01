import { ApiError, get, post } from "@/lib/api";

export interface AiErr { code: string; message: string; detail?: string | null; retryable?: boolean }
/** Readable message from any thrown value (the API wraps structured errors as {error:{code,message,detail}}). */
export function errText(e: unknown): string {
  if (e instanceof ApiError) {
    try { const j = JSON.parse(e.message); if (j?.error?.message) return j.error.message + (j.error.detail ? ` (${String(j.error.detail).slice(0, 160)})` : ""); } catch { /* plain text */ }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
export const aiGet = <T = any>(p: string) => get<T>(`/ai${p}`);
export const aiPost = <T = any>(p: string, body?: unknown) => post<T>(`/ai${p}`, body);

export interface Step { idx: number; role: string; tool: string; status: "running" | "ok" | "error" | "skipped"; summary?: string | null; inputs?: any; output?: any; error?: AiErr | null; started?: number | null; ended?: number | null }
export interface Experiment { round: number; hypothesis: string; hypothesis_text?: string; version_id: string | null; title: string; source: string | null; train_sharpe: number; train_return: number; train_trades: number; locked_return: number; locked_sharpe: number; locked_trades: number; locked_bh: number; verdict: string; status: "kept" | "dropped"; reason: string }
export interface Risk { verdict: "VETO" | "CAUTION" | "APPROVE"; vetoes: string[]; cautions: string[]; passes: string[] }
export interface Debate { source: string; bull: string[]; bear: string[] }
export interface Metrics { trades: number; total_return: number; sharpe: number; max_drawdown: number; win_rate: number | null }
export interface Final {
  decision: string; headline: string; version_id: string | null; title?: string; rules?: string[]; mode: string; source?: string; model?: string | null; verdict?: { label: string; reasons: string[] }; risk?: Risk; debate?: Debate;
  metrics?: { train: Metrics; locked: Metrics; benchmark_locked: any }; periods?: { train: string[]; locked: string[] }; chart?: any;
  dsr?: { dsr: number | null } | null; trials?: number; capped?: boolean; lesson: string; memory_written?: string[]; meko_status?: string; cost?: string; disclaimer: string; limits?: string[];
}
export interface Run {
  id: string; goal: string; status: "queued" | "running" | "done" | "failed" | "interrupted"; mode: "gemini" | "offline"; target: { market: string; symbol: string; interval: string };
  steps: Step[]; experiments: Experiment[]; debate: Debate | null; risk: Risk | null; final: Final | null; error: AiErr | null; max_steps: number; notices: string[];
}
export interface Status { round_options?: number[]; mode: "gemini" | "offline"; gemini: { enabled: boolean; last_error?: string | null }; examples: { text: string; market: string }[]; symbols: { symbol: string; name: string }[]; nse: string[]; intervals: string[] }
export interface Version {
  id: string; session_id: string; n: number; title: string; explanation: string; rules: string[]; code: string; exits: any; source: string; model: string | null; symbol: string; interval: string; market: string;
  result: { train: any; locked: any; full: any }; verdict: { label: string; reasons: string[] };
}
export const pc = (x: number | null | undefined, d = 1) => (x == null || Number.isNaN(x) ? "n/a" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`);
export const n2 = (x: number | null | undefined) => (x == null || Number.isNaN(x) ? "n/a" : x.toFixed(2));
