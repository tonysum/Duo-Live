const API_BASE = process.env.NEXT_PUBLIC_API_URL || (typeof window !== "undefined" ? `http://${window.location.hostname}:8899` : "http://localhost:8899");

// ── Types ──────────────────────────────────────────────────────────

export interface Status {
    mode: string;
    total_balance: number;
    available_balance: number;
    unrealized_pnl: number;
    daily_pnl: number;
    open_positions: number;
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
}


export interface LiveTrade {
    symbol: string;
    side: string;         // LONG / SHORT
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

// ── Fetch helpers ──────────────────────────────────────────────────

async function fetchWithRetry(url: string, opts?: RequestInit, retries = 1): Promise<Response> {
    for (let i = 0; i <= retries; i++) {
        try {
            const res = await fetch(url, opts);
            return res;
        } catch (err) {
            // ERR_EMPTY_RESPONSE / network error — retry once
            if (i < retries) {
                await new Promise(r => setTimeout(r, 500));
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
    getTrades: (limit = 50) => get<LiveTrade[]>(`/api/trades?limit=${limit}`),
    getSignals: (limit = 100) => get<Signal[]>(`/api/signals?limit=${limit}`),
    getConfig: () => get<Config>("/api/config"),
    getKlines: (symbol: string, interval = "5m", limit = 300) =>
        get<Kline[]>(`/api/klines/${symbol}?interval=${interval}&limit=${limit}`),
    getTicker: (symbol: string) =>
        get<{ symbol: string; price: number }>(`/api/ticker/${symbol}`),
    getExchangeInfo: (symbol: string) => get<Record<string, unknown>>(`/api/exchange-info/${symbol}`),
    placeOrder: (order: OrderRequest) => post<Record<string, unknown>>("/api/order", order),
    closePosition: (symbol: string) => post<Record<string, unknown>>(`/api/close/${symbol}`),

    // WebSocket URL
    wsUrl: `${API_BASE.replace("http", "ws")}/ws/live`,
};
