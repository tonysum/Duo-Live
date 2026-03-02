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
}

export interface Position {
  symbol: string;
  side: string;
  entry_price: number;
  quantity: number;
  unrealized_pnl: number;
  leverage: number;
  tp_pct: number;
  strength: string;
  liquidation_price: number;
  margin: number;
  margin_ratio: number;
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
}

export interface Signal {
  timestamp: string;
  symbol: string;
  surge_ratio: number;
  price: string;
  accepted: boolean;
  reject_reason: string;
}

export interface Kline {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Config {
  leverage: number;
  max_positions: number;
  max_entries_per_day: number;
  stop_loss_pct: number;
  strong_tp_pct: number;
  medium_tp_pct: number;
  weak_tp_pct: number;
  max_hold_hours: number;
  surge_threshold: number;
  live_fixed_margin_usdt: number;
  daily_loss_limit_usdt: number;
  margin_mode: string;
  margin_pct: number;
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

// ── Paper Trading Types ─────────────────────────────────────────────
export interface PaperStats {
  initial_capital: number;
  current_capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
  avg_hold_hours: number;
  open_positions: number;
  pending_orders: number;
  max_positions: number;
}

export interface PaperPosition {
  symbol: string;
  direction: string;
  entry_price: number;
  tp_price: number;
  sl_price: number;
  entry_time: string;
  size_usdt: number;
  unrealized_pnl: number;
  hold_hours: number;
}

export interface PaperPending {
  symbol: string;
  direction: string;
  entry_price: number;
  created_at: string;
  size_usdt: number;
}

export interface PaperTrade {
  symbol: string;
  direction: string;
  entry_price: number;
  exit_price: number;
  entry_time: string;
  exit_time: string;
  hold_hours: number;
  size_usdt: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string;
}

export interface PaperSignal {
  symbol: string;
  direction: string;
  entry_price: number;
  tp_price: number;
  sl_price: number;
  signal_time: string;
  current_price: number | null;
  state: string;
}

export interface PaperStatus {
  running: boolean;
  start_time: string | null;
  symbols: string[];
  state_info: Record<string, { state: string; base_price: number | null; alerts_count: number }>;
  total_alerts: number;
}

export interface PaperWsStatus {
  connected: number;
  total: number;
  symbols: Record<string, {
    status: string;
    last_data: string | null;
    stale_seconds: number | null;
    reconnects: number;
    error: string | null;
    rows: number;
  }>;
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
  wsUrl: `${API_BASE.replace("http", "ws")}/ws/live`,
  wsLogsUrl: () => {
    const base = (
      import.meta.env.VITE_API_URL ||
      `http://${window.location.hostname}:8899`
    ).replace("http", "ws")
    const token = import.meta.env.VITE_WS_TOKEN
    return `${base}/ws/logs${token ? `?token=${token}` : ""}`
  },

  // Paper Trading API
  paper: {
    getStatus: () => get<PaperStatus>("/api/paper/status"),
    getStats: () => get<PaperStats>("/api/paper/stats"),
    getPositions: () => get<{ positions: PaperPosition[]; pending: PaperPending[] }>("/api/paper/positions"),
    getTrades: () => get<PaperTrade[]>("/api/paper/trades"),
    getSignals: () => get<PaperSignal[]>("/api/paper/signals"),
    getWsStatus: () => get<PaperWsStatus>("/api/paper/ws-status"),
    start: (symbols?: string[]) => post<{ status: string; message: string }>("/api/paper/start", symbols ? { symbols } : undefined),
    stop: () => post<{ status: string; message: string }>("/api/paper/stop"),
  },
};

