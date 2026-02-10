"use client";

import { useEffect, useState, useCallback } from "react";
import { api, Kline, Position, OrderRequest } from "@/lib/api";
import TradeChart from "@/components/TradeChart";

export default function TradingPage() {
    const [symbol, setSymbol] = useState("BTCUSDT");
    const [searchInput, setSearchInput] = useState("BTCUSDT");
    const [klines, setKlines] = useState<Kline[]>([]);
    const [interval, setInterval_] = useState("15m");
    const [positions, setPositions] = useState<Position[]>([]);
    const [ticker, setTicker] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);

    // Order form
    const [side, setSide] = useState<"SELL" | "BUY">("SELL");
    const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET");
    const [price, setPrice] = useState("");
    const [margin, setMargin] = useState("5");
    const [leverage, setLeverage] = useState("3");
    const [tpPct, setTpPct] = useState("");
    const [slPct, setSlPct] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<string>("");

    const loadChart = useCallback(async () => {
        setLoading(true);
        try {
            const [k, t, p] = await Promise.all([
                api.getKlines(symbol, interval, 500),
                api.getTicker(symbol).catch(() => null),
                api.getPositions().catch(() => []),
            ]);
            setKlines(k);
            if (t) setTicker(t.price);
            setPositions(p);
        } catch {
            // ignore
        } finally {
            setLoading(false);
        }
    }, [symbol, interval]);

    useEffect(() => {
        loadChart();
        const iv = setInterval(loadChart, 15000);
        return () => clearInterval(iv);
    }, [loadChart]);

    const handleSearch = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            setSymbol(searchInput.toUpperCase());
        }
    };

    const handleSubmit = async () => {
        if (!confirm(`确认 ${side === "SELL" ? "做空" : "做多"} ${symbol}？\n保证金: ${margin} USDT`))
            return;

        setSubmitting(true);
        setResult("");
        try {
            const order: OrderRequest = {
                symbol,
                side,
                order_type: orderType,
                margin_usdt: parseFloat(margin),
                leverage: parseInt(leverage),
            };
            if (orderType === "LIMIT" && price) {
                order.price = parseFloat(price);
            }
            if (tpPct) order.tp_pct = parseFloat(tpPct);
            if (slPct) order.sl_pct = parseFloat(slPct);

            const res = await api.placeOrder(order);
            setResult(`✅ 下单成功 (ID: ${(res as any).order_id})`);
            loadChart();
        } catch (err: any) {
            setResult(`❌ ${err.message}`);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="flex gap-0 -mx-6 -mt-6" style={{ height: "calc(100vh - 0px)" }}>
            {/* Chart area */}
            <div className="flex-1 flex flex-col">
                {/* Chart header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
                    <div className="flex items-center gap-3">
                        <input
                            type="text"
                            value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value.toUpperCase())}
                            onKeyDown={handleSearch}
                            placeholder="搜索币种..."
                            className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-sm w-36
                       focus:border-[var(--accent-blue)] focus:outline-none"
                        />
                        {ticker && (
                            <span className="text-lg font-bold font-mono">
                                ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                            </span>
                        )}
                    </div>

                    <div className="flex gap-1">
                        {["5m", "15m", "1h", "4h"].map((iv) => (
                            <button
                                key={iv}
                                onClick={() => setInterval_(iv)}
                                className={`px-2 py-1 text-xs rounded transition-colors ${interval === iv
                                        ? "bg-[var(--accent-blue)] text-white"
                                        : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]"
                                    }`}
                            >
                                {iv}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <div className="flex-1 relative">
                    {loading && klines.length === 0 ? (
                        <div className="absolute inset-0 flex items-center justify-center text-[var(--text-muted)]">
                            加载中...
                        </div>
                    ) : (
                        <TradeChart klines={klines} />
                    )}
                </div>

                {/* Current positions */}
                {positions.length > 0 && (
                    <div className="border-t border-[var(--border)] px-4 py-2">
                        <p className="text-xs text-[var(--text-muted)] mb-1">当前持仓</p>
                        <div className="flex gap-4 overflow-x-auto">
                            {positions.map((p) => (
                                <div
                                    key={p.symbol}
                                    className="flex items-center gap-2 text-xs bg-[var(--bg-card)] rounded px-3 py-1.5"
                                >
                                    <span className="font-medium">{p.symbol}</span>
                                    <span
                                        className={`${p.side === "SHORT" ? "text-red-400" : "text-green-400"
                                            }`}
                                    >
                                        {p.side}
                                    </span>
                                    <span
                                        className={`font-mono ${p.unrealized_pnl >= 0
                                                ? "text-[var(--accent-green)]"
                                                : "text-[var(--accent-red)]"
                                            }`}
                                    >
                                        {p.unrealized_pnl >= 0 ? "+" : ""}
                                        {p.unrealized_pnl.toFixed(4)}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* Order form */}
            <div className="w-[280px] min-w-[280px] border-l border-[var(--border)] bg-[var(--bg-secondary)] overflow-y-auto">
                <div className="p-4">
                    <h3 className="text-sm font-medium mb-4">手动下单</h3>

                    {/* Side selector */}
                    <div className="flex gap-1 mb-4">
                        <button
                            onClick={() => setSide("BUY")}
                            className={`flex-1 py-2 text-sm rounded-lg font-medium transition-colors ${side === "BUY"
                                    ? "bg-green-500 text-white"
                                    : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                                }`}
                        >
                            做多 LONG
                        </button>
                        <button
                            onClick={() => setSide("SELL")}
                            className={`flex-1 py-2 text-sm rounded-lg font-medium transition-colors ${side === "SELL"
                                    ? "bg-red-500 text-white"
                                    : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                                }`}
                        >
                            做空 SHORT
                        </button>
                    </div>

                    {/* Order type */}
                    <div className="flex gap-1 mb-4">
                        {(["MARKET", "LIMIT"] as const).map((t) => (
                            <button
                                key={t}
                                onClick={() => setOrderType(t)}
                                className={`flex-1 py-1.5 text-xs rounded transition-colors ${orderType === t
                                        ? "bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]"
                                        : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                    }`}
                            >
                                {t}
                            </button>
                        ))}
                    </div>

                    {/* Price (LIMIT only) */}
                    {orderType === "LIMIT" && (
                        <div className="mb-3">
                            <label className="text-xs text-[var(--text-muted)] block mb-1">
                                价格
                            </label>
                            <input
                                type="number"
                                value={price}
                                onChange={(e) => setPrice(e.target.value)}
                                placeholder={ticker ? ticker.toString() : "价格"}
                                className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                         focus:border-[var(--accent-blue)] focus:outline-none"
                            />
                        </div>
                    )}

                    {/* Margin */}
                    <div className="mb-3">
                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                            保证金 (USDT)
                        </label>
                        <input
                            type="number"
                            value={margin}
                            onChange={(e) => setMargin(e.target.value)}
                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                       focus:border-[var(--accent-blue)] focus:outline-none"
                        />
                    </div>

                    {/* Leverage */}
                    <div className="mb-3">
                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                            杠杆
                        </label>
                        <div className="flex gap-1">
                            {["1", "2", "3", "5", "10"].map((l) => (
                                <button
                                    key={l}
                                    onClick={() => setLeverage(l)}
                                    className={`flex-1 py-1.5 text-xs rounded transition-colors ${leverage === l
                                            ? "bg-[var(--accent-blue)] text-white"
                                            : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                        }`}
                                >
                                    {l}x
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* TP/SL */}
                    <div className="grid grid-cols-2 gap-2 mb-4">
                        <div>
                            <label className="text-xs text-[var(--text-muted)] block mb-1">
                                止盈 %
                            </label>
                            <input
                                type="number"
                                value={tpPct}
                                onChange={(e) => setTpPct(e.target.value)}
                                placeholder="例: 33"
                                className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                         focus:border-[var(--accent-blue)] focus:outline-none"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-[var(--text-muted)] block mb-1">
                                止损 %
                            </label>
                            <input
                                type="number"
                                value={slPct}
                                onChange={(e) => setSlPct(e.target.value)}
                                placeholder="例: 18"
                                className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                         focus:border-[var(--accent-blue)] focus:outline-none"
                            />
                        </div>
                    </div>

                    {/* Submit */}
                    <button
                        onClick={handleSubmit}
                        disabled={submitting || !margin}
                        className={`w-full py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${side === "SELL"
                                ? "bg-red-500 hover:bg-red-600 text-white"
                                : "bg-green-500 hover:bg-green-600 text-white"
                            }`}
                    >
                        {submitting
                            ? "下单中..."
                            : `${side === "SELL" ? "做空" : "做多"} ${symbol}`}
                    </button>

                    {/* Result */}
                    {result && (
                        <p
                            className={`mt-3 text-xs p-2 rounded ${result.startsWith("✅")
                                    ? "bg-green-500/10 text-green-400"
                                    : "bg-red-500/10 text-red-400"
                                }`}
                        >
                            {result}
                        </p>
                    )}
                </div>
            </div>
        </div>
    );
}
