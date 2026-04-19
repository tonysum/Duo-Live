const API_BASE =
  import.meta.env.VITE_API_URL ||
  `http://${window.location.hostname}:8899`;

// ── Types ──────────────────────────────────────────────────────────
export interface Status {
  mode: string;
  total_balance: number;
  available_balance: number;
  unrealized_pnl: number;
  daily_pnl: number;
  open_positions: number;
  auto_trade_enabled: boolean;
  timestamp: string;
  /** ISO UTC; set when deploy.sh wrote data/deployed_at.txt */
  deployed_at?: string | null;
  uptime_since_deploy_sec?: number | null;
  uptime_since_deploy_label?: string;
  /** When this backend process entered the main trading loop */
  process_started_at?: string | null;
  uptime_since_restart_sec?: number | null;
  uptime_since_restart_label?: string;
}

export interface Position {
  symbol: string;
  side: string;
  entry_price: number;
  /** Binance mark price (实时标记价) */
  mark_price: number;
  quantity: number;
  unrealized_pnl: number;
  leverage: number;
  tp_pct: number;
  sl_pct: number;
  strength: string;
  /** ISO UTC，来自实盘 monitor 的入场成交时间（无追踪时为空） */
  entry_time?: string | null;
  liquidation_price: number;
  margin: number;
  margin_ratio: number;
  /** Strategy ID that owns this position (multi-strategy support) */
  strategy_id?: string;
}

export interface LiveTrade {
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  entry_time: string;
  exit_time: string;
  quantity: number;
  pnl_usdt: number;
  leverage: number;
  /** 相对入场价的价格收益率 %（与后端 R LONG/SHORT 定义一致） */
  return_pct?: number;
}

export interface Signal {
  timestamp: string;
  symbol: string;
  surge_ratio: number;
  price: string;
  accepted: boolean;
  reject_reason: string;
  strategy_id?: string;
}

export interface Kline {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** R24 rolling strategy runtime snapshot from GET /api/config */
export interface RollingRuntimeParams {
  strategy_id: string;
  top_n: number;
  min_pct_chg: number;
  raw_min_pct_chg: number;
  raw_min_sell_surge: number;
  raw_max_signals_per_hour: number | null;
  enable_sell_surge_gate: boolean;
  sell_surge_threshold: number;
  sell_surge_max: number;
  scan_delay_minutes: number;
  min_listed_days: number;
  signal_cooldown_hours: number;
  scan_interval_hours: number;
  enable_main_profit_check: boolean;
  main_profit_thresholds: number[][];
  max_hold_days: number;
  tp_initial_pct: number;
  tp_reduced_pct: number;
  tp_hours_threshold: number;
  tp_after_add_pct: number;
  sl_threshold_pct: number;
  enable_trailing_stop: boolean;
  trailing_activation_pct: number;
  trailing_distance_pct: number;
  enable_add_position: boolean;
  add_position_threshold_pct: number;
  add_position_multiplier_pct: number;
}

export interface StrategyManifestItem {
  id: string;
  kind: string;
  enabled: boolean;
}

export interface Config {
  leverage: number;
  max_positions: number;
  max_entries_per_day: number;
  stop_loss_pct: number;
  strong_tp_pct: number;
  max_hold_hours: number;
  live_fixed_margin_usdt: number;
  daily_loss_limit_usdt: number;
  margin_mode: string;
  margin_pct: number;
  monitor_interval_seconds: number;
  paper_trading: boolean;
  rolling: RollingRuntimeParams;
  strategies: StrategyManifestItem[];
  /** 进程内每路策略的 Rolling 快照（多路时与 rolling 主快照同源为首路） */
  strategy_runtimes?: RollingRuntimeParams[];
}

export interface OrderRequest {
  symbol: string;
  side: string;
  order_type: string;
  margin_usdt?: number;
  quantity?: number;
  price?: number;
  tp_pct?: number;
  sl_pct?: number;
  leverage?: number;
  trading_password?: string;
}

export interface OpenOrder {
  id: number;
  symbol: string;
  type: string;
  side: string;
  position_side: string;
  price: number;
  stop_price: number;
  quantity: number;
  filled_qty: number;
  status: string;
  time: number;
  is_algo: boolean;
}

// ── Multi-Strategy Types ───────────────────────────────────────────
/** Strategy quota from /api/quotas */
export interface StrategyQuota {
  strategy_id: string;
  max_positions: number;
  current_positions: number;
  margin_per_position: number;
  daily_loss_limit: number;
  daily_realized_pnl: number;
  available_slots: number;
}

/** Response from /api/quotas */
export interface QuotasResponse {
  quotas: Record<string, StrategyQuota>;
  total_strategies: number;
  timestamp: string;
}

/** Strategy configuration parameters */
export interface StrategyConfig {
  max_positions: number;
  margin_per_position: number;
  daily_loss_limit: number;
  scan_interval_hours: number;
  top_n: number;
  min_pct_chg: number;
  tp_initial: number;
  sl_threshold: number;
}

/** Complete strategy from /api/strategies */
export interface Strategy {
  id: string;
  kind: string;
  enabled: boolean;
  config: StrategyConfig;
  quota: StrategyQuota;
}

/** Response from /api/strategies */
export interface StrategiesResponse {
  strategies: Strategy[];
  total: number;
  timestamp: string;
}


// ── Fetch helpers ──────────────────────────────────────────────────
async function fetchWithRetry(
  url: string,
  opts?: RequestInit,
  retries = 1
): Promise<Response> {
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch(url, opts);
      return res;
    } catch (err) {
      if (i < retries) {
        await new Promise((r) => setTimeout(r, 500));
        continue;
      }
      throw err;
    }
  }
  throw new Error("fetch failed");
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithRetry(`${API_BASE}${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetchWithRetry(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ── API Functions ───────────────────────────────────────────────────
export const api = {
  getStatus: () => get<Status>("/api/status"),
  getPositions: () => get<Position[]>("/api/positions"),
  getOrders: () => get<OpenOrder[]>("/api/orders"),
  getTrades: (limit = 50) => get<LiveTrade[]>(`/api/trades?limit=${limit}`),
  getSignals: (limit = 100) => get<Signal[]>(`/api/signals?limit=${limit}`),
  getTickers: () =>
    get<Record<string, { price: number; change_pct: number }>>("/api/tickers"),
  getConfig: () => get<Config>("/api/config"),
  getKlines: (symbol: string, interval = "5m", limit = 300) =>
    get<Kline[]>(`/api/klines/${symbol}?interval=${interval}&limit=${limit}`),
  getTicker: (symbol: string) =>
    get<{ symbol: string; price: number }>(`/api/ticker/${symbol}`),
  getExchangeInfo: (symbol: string) =>
    get<Record<string, unknown>>(`/api/exchange-info/${symbol}`),
  placeOrder: (order: OrderRequest) =>
    post<Record<string, unknown>>("/api/order", order),
  closePosition: (symbol: string) =>
    post<Record<string, unknown>>(`/api/close/${symbol}`),
  getAutoTrade: () =>
    get<{ enabled: boolean }>("/api/auto-trade"),
  setAutoTrade: (enabled: boolean) =>
    post<{ enabled: boolean; message: string }>("/api/auto-trade", { enabled }),
  updateConfig: (config: Partial<Config>) =>
    post<Config & { message: string }>("/api/config", config),
  getLogs: (lines = 200, level = "", search = "") =>
    get<{ lines: string[]; total: number; path: string }>(
      `/api/logs?lines=${lines}&level=${encodeURIComponent(level)}&search=${encodeURIComponent(search)}`
    ),
  // Multi-strategy endpoints
  getQuotas: () => get<QuotasResponse>("/api/quotas"),
  getStrategies: () => get<StrategiesResponse>("/api/strategies"),
  wsUrl: `${API_BASE.replace("http", "ws")}/ws/live`,
  /** `/ws/live` — optional `VITE_WS_TOKEN` must match server `WS_TOKEN` */
  wsLiveUrl: () => {
    const base = (
      import.meta.env.VITE_API_URL ||
      `http://${window.location.hostname}:8899`
    ).replace("http", "ws");
    const token = import.meta.env.VITE_WS_TOKEN;
    const q = token ? `?token=${encodeURIComponent(String(token))}` : "";
    return `${base}/ws/live${q}`;
  },
  wsLogsUrl: () => {
    const base = (
      import.meta.env.VITE_API_URL ||
      `http://${window.location.hostname}:8899`
    ).replace("http", "ws")
    const token = import.meta.env.VITE_WS_TOKEN
    return `${base}/ws/logs${token ? `?token=${token}` : ""}`
  },

};


