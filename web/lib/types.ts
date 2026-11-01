// Shapes mirror backend/examples/*.json (real API responses).
export interface Metrics {
  days: number; trades: number; total_return: number; cagr: number; win_rate: number | null; avg_trade: number | null;
  avg_trade_ci95: [number, number] | null; profit_factor: number | null; max_drawdown: number; sharpe: number; exposure: number;
}
export interface Bench { total_return: number; max_drawdown: number; sharpe: number; cagr: number }
export interface Trade {
  window: string; entry_date: string; exit_date: string; entry: number; exit: number; return_pct: number; reason: string; bars: number;
  tags: Record<string, string>;
}
export interface Window { start: string; end: string; metrics: Metrics; benchmark: Bench; trades: Trade[]; equity: number[]; benchmark_equity: number[]; dates: string[] }
export interface Result { train: Window; locked: Window; full: Window }
export interface Spec { ticker: string; template: string; params: Record<string, number | string>; exits: Record<string, number | null>; cost_bps: number; cost_model?: string; slippage_bps?: number | null; trade_value?: number | null }
export interface Backtest { saved_result_id?: string; spec: Spec; data: any; result: Result; notes: string[] }
export interface Refine {
  saved_result_id?: string;
  spec: Spec; data: any;
  original: { params: Record<string, any>; exits: Record<string, any>; result: Result };
  refined: { params: Record<string, any>; exits: Record<string, any>; result: Result; changed: boolean };
  verdict: { flag: "ok" | "overfit" | "inconclusive" | "no_change"; overfit: boolean; headline: string; notes: string[] };
  search: { trials: number; eligible: number; objective: string; tuned_on: string; locked: string; top_candidates: any[]; share_of_candidates_with_positive_train_sharpe?: number };
  overfit_stats?: OverfitStats;
}
export interface ParamMeta { name: string; label: string; type: "int" | "float" | "choice"; default: any; min?: number; max?: number; step?: number; choices?: any[] }
export interface Templates {
  templates: Record<string, { label: string; description: string; params: ParamMeta[] }>;
  exits: (ParamMeta & { optional?: boolean })[]; tickers: string[]; cost_bps_default: number; cost_note: string; locked_days: number; tag_groups: any;
}
export interface Stats { n: number; wins: number; win_rate: number; avg_pnl: number; avg_return_pct: number; total_pnl: number; avg_win_pct: number | null; avg_loss_pct: number | null; avg_hold_days: number }
export interface Pattern { kind: string; conditions: { group: string; value: string }[]; n: number; win_rate: number; avg_return_pct: number; avg_pnl: number; base_win_rate: number; text: string; wait_for: string | null }
export interface JSummary {
  source_used: string; overall: Stats; long: Stats; short: any; bad_patterns: Pattern[]; good_patterns: Pattern[]; patterns_tested: number;
  hold_behaviour: { kind: string; text: string; avg_hold_losers: number; avg_hold_winners: number } | null; notes: string[];
}
export interface Neighbor { cosine: number; ticker: string; entry_date: string; exit_date: string; pnl: number; return_pct: number; hold_days: number; same_symbol: boolean; is_sample: boolean; matching: string[]; tags: Record<string, string> }
export interface CheckResult {
  symbol: string; date: string; side: string; direction: string;
  fingerprint: { tags: Record<string, string>; metrics: Record<string, number>; as_of: string; descriptions: string[]; complete: boolean; gap_known: boolean };
  journal: { n: number; source_used: string; overall: Stats };
  similar: { n: number; candidates_above_threshold?: number; stats: Stats | null; neighbors: Neighbor[]; same_symbol?: number };
  mistake_pattern: { matched: Pattern | null; also_matched: Pattern[]; other_bad_patterns: Pattern[]; hold_behaviour: any; matched_good: Pattern[] } | null;
  recommendation: { action: "proceed" | "reduce_size" | "wait"; size_factor: number; confidence: string; reasons: string[]; wait_for: string | null; rules?: string[] };
  challenger?: Challenger;
  notes: string[];
}
export interface Challenger { veto: boolean; severity: "none" | "weak" | "strong"; score: number; veto_threshold: number; arguments: { weight: "weak" | "strong"; points: number; text: string }[]; verdict: string; final_action: string; overrides_recommendation: boolean; rules: string[] }
export interface Proof {
  source_used: string; is_sample: boolean; n_trades: number; decisions: Record<string, number>;
  off: { total_pnl: number; max_drawdown: number; win_rate: number; trades_taken: number };
  on: { total_pnl: number; max_drawdown: number; win_rate: number; trades_taken: number };
  skipped: { n: number; pnl_if_taken: number }; reduced: { n: number; pnl_full_size: number; pnl_half_size: number };
  series: { date: string; off: number; on: number }[]; notes: string[];
}

export interface Level { level: number; touches: number; last_touch: string; distance_pct: number }
export interface Move { date: string; return_pct: number; volume_ratio: number | null; close: number }
export interface Brief {
  ticker: string; symbol: string; as_of: string; close: number; bars: number; years: number; disclaimer: string; summary: string[];
  data: { source: string; first: string; last: string };
  trend: { label: string; total_return_pct: number; cagr_pct: number; ret_1m: number | null; ret_3m: number | null; ret_6m: number | null; ret_12m: number | null; above_50dma: boolean; above_200dma: boolean; golden_cross: boolean; sma50: number; sma200: number; high_52w: number; low_52w: number; pct_from_high: number; pct_from_low: number };
  volatility: { annualised_pct: number; last_20d_pct: number; percentile_of_history: number; max_drawdown_pct: number; max_dd_peak: string; max_dd_trough: string; current_drawdown_pct: number; avg_daily_range_pct: number; worst_day_pct: number; best_day_pct: number };
  biggest_moves: { up: Move[]; down: Move[] };
  levels: { close: number; support: Level[]; resistance: Level[]; method: string };
  earnings: { available: boolean; note: string; fetched?: string; source?: string; reactions: { date: string; eps_estimate: number | null; eps_reported: number | null; surprise_pct: number | null; reaction_pct: number }[];
    summary: { n: number; avg_abs_move_pct: number; max_abs_move_pct: number; up: number; down: number; avg_after_beat_pct: number | null; n_beats: number; avg_after_miss_pct: number | null; n_misses: number } | null };
  series: { date: string; close: number; sma50: number | null; sma200: number | null; drawdown_pct: number; vol20: number | null }[];
}
export interface ScoutRow { rank: number; ticker: string; symbol: string; score: number; label: "strong" | "constructive" | "neutral" | "weak"; close: number; as_of: string; reasons: string[];
  components: { trend: number; momentum: number; volume: number; stretch_penalty: number };
  metrics: { ret_3m_pct: number; ret_6m_pct: number; ret_5d_pct: number; volume_ratio_5d_60d: number; pct_above_50dma: number; pct_vs_200dma: number } }
export interface Scout { as_of: string; universe: number; ranked: ScoutRow[]; errors: { ticker: string; error: string }[]; rules: string[]; disclaimer: string }

export interface ParsedStrategy { ticker: string; template: string; params: Record<string, any>; exits: Record<string, number | null> }
export interface ParseResult { strategy: ParsedStrategy | null; confidence: number; assumptions: string[]; unsupported: string[]; parser: "gemini" | "rules";
  fields_from_text: string[]; summary: string | null; cached: boolean; model?: string; gemini_error?: string | null; fallback_reason?: string }

// ---- Live Strategy Tracker (/paper/*) and strategy-run memory (/memory/strategy-runs)
export interface MarketState { open: boolean; state: string; label: string; ist_now: string; ist_date: string; next_open: string; holidays_note: string }
export interface TrackQuote { price: number; price_time: string; source: string; stale: boolean; delay_note: string; note: string | null; last_close: number; last_close_date: string;
  day_open: number | null; day_high: number | null; day_low: number | null; market: MarketState; fetched_at: string }
export interface TrackPosition { direction: "long" | "short"; entry_px: number; entry_date: string; qty: number; price: number; gross_pct: number; net_pct: number; pnl_abs: number; per_share: number;
  stop_px: number | null; tp_px: number | null; dist_to_stop_pct: number | null; dist_to_tp_pct: number | null; origin: string; hold_days: number }
export interface ExitSignal { fired: boolean; reason: string | null; px: number | null; at_close: boolean; text: string | null }
export interface TrackFill { id: number; date: string; ts_label: string; kind: string; side: string; price: number; qty: number; reason: string; pnl_pct: number | null }
export interface TrackClose { mode: string; label: string; note: string | null; closed_at: string; side: string | null; entry: number | null; exit: number | null; qty: number | null;
  net_pct: number | null; gross_pct: number | null; days_held: number | null; pnl_abs: number | null; exit_reason: string; exit_signal: any; followed_strategy_exit: boolean;
  mfe_pct: number | null; mae_pct: number | null; verdict: string;
  saved: { strategy_memory: { run_id: string; verdict: string; headline: string; stored_in: string; meko_mirror: string }; journal: null | { added: boolean; pnl?: number; ret_pct?: number; real_trades_in_journal?: number; note?: string; reason?: string }; lesson: string } }
export interface TrackSession { cost_label?: string;
  id: string; status: "active" | "closed"; mode: "logged" | "paper"; ticker: string; template: string; params: Record<string, any>; exits: Record<string, any>; cost_bps: number; label: string;
  created: string; updated: string; phase: string; quote: TrackQuote; market: MarketState; position: TrackPosition | null; exit_signal: ExitSignal | null; first_exit_signal: any; pending_close: boolean; pending_signal: any;
  summary: { trades: number; realized_pct: number; wins: number; closed_trades: any[] }; last_error: string | null;
  trade: null | { side: string; direction: string; entry_price: number; qty: number; entered_at: string; entry_date: string; notes: string[] };
  close: TrackClose | null; refresh_seconds: number; disclaimer: string; data_labels: string[]; entry_watch?: any;
  fills: TrackFill[];
  chart: { points: { d: string; close: number }[]; markers: { x: string; y: number; kind: string; label: string }[]; stop_px: number | null; tp_px: number | null };
  ticks: { ts: number; ts_label: string; price: number }[];
}
export interface TrackRow { id: string; status: string; mode: string; ticker: string; template: string; label: string; created: string; updated: string; phase: string; price: number | null; price_time: string | null;
  market_label: string; open_pct: number | null; open_pnl: number | null; side: string | null; exit_now: boolean; realized_pct: number; paper_trades: number; closed_net_pct: number | null; verdict: string | null }
export interface TrackList { sessions: TrackRow[]; loop: { running: boolean; interval_s: number; last_run: string | null; last_error: string | null; runs: number }; market: MarketState; labels: string[] }
export interface StrategyRun { id: string; run_date: string; run_date_label: string; kind: string; source: string; ticker: string; template: string; verdict: string; stats: Record<string, any>;
  anchor_date: string | null; fwd5: number | null; fwd20: number | null; days_since: number | null; since_pct: number | null; headline: string; lesson: string; match: string; replayed: boolean; session_id: string | null; outcome: any }
export interface StrategyRuns { count: number; total: number; runs: StrategyRun[]; last_time: StrategyRun | null; backend: string; note: string }

// ---- Quant suite
export interface OverfitStats {
  pbo: { available: boolean; reason?: string; pbo?: number; level?: "low" | "moderate" | "high"; words?: string; splits?: number; candidates?: number; blocks?: number; logit_hist?: number[]; degradation?: number | null };
  dsr: { available: boolean; reason?: string; dsr?: number; verdict?: string; words?: string; n_trials?: number; sharpe_annual?: number; expected_max_sharpe_annual?: number };
}
export interface WFWindow { window: number; train_start: string; train_end: string; test_start: string; test_end: string; params: Record<string, any>; exits: Record<string, any>; changed: boolean; eligible: number;
  train_sharpe: number; train_return: number; train_trades: number; test_return: number; test_sharpe: number; test_trades: number; test_max_drawdown: number; original_test_return: number; buy_hold_test_return: number }
export interface WalkForward {
  spec: any; data: any; cost: string; mode: "rolling" | "anchored"; train_bars: number; test_bars: number; windows: WFWindow[];
  summary: { metrics: Metrics; benchmark: Bench; original: { total_return: number; sharpe: number }; efficiency: number | null; windows_profitable: number; windows_total: number };
  verdict: { flag: "holds" | "fails" | "inconclusive"; headline: string; notes: string[] };
  chart: { dates: string[]; equity: number[]; original_equity: number[]; benchmark_equity: number[]; boundaries: string[] };
  oos: { start: string; end: string; bars: number };
}
export interface LabRow { symbol: string; short: string; name: string; asset_class: "stock" | "crypto"; total_return: number; cagr: number; sharpe: number; max_drawdown: number; trades: number; win_rate: number | null;
  buy_hold_return: number; train_sharpe: number; locked_return: number; locked_sharpe: number; locked_trades: number; verdict: "holds" | "fades" | "loses" | "few_trades"; verdict_text: string; bars: number; data_source: string }
export interface Lab { template: string; params: Record<string, any>; exits: Record<string, any>; rows: LabRow[]; errors: { symbol: string; error: string }[];
  summary: { assets: number; holds: number; fades: number; loses: number; few_trades: number; median_sharpe: number; beats_buy_hold: number }; notes: string[]; cost: string; cost_model: string }
export interface NewsItem { title: string; url: string; publisher: string; published: string | null; via: string }
export interface NewsOut { symbol: string; items: NewsItem[]; sources: string[]; errors: string[]; disabled?: boolean; note: string }
