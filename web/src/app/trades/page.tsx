"use client";

import { useEffect, useState } from "react";
import { api, PaperTrade, Kline } from "@/lib/api";
import TradeChart from "@/components/TradeChart";

export default function TradesPage() {
    const [trades, setTrades] = useState<PaperTrade[]>([]);
    const [selected, setSelected] = useState<PaperTrade | null>(null);
    const [klines, setKlines] = useState<Kline[]>([]);
    const [interval, setInterval_] = useState("15m");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        api
            .getTrades(200)
            .then(setTrades)
            .catch((e) => setError(e.message));
    }, []);

    // Load klines when selecting a trade or changing interval
    useEffect(() => {
        if (!selected) return;
        setLoading(true);
        api
            .getKlines(selected.symbol, interval, 500)
            .then(setKlines)
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, [selected, interval]);

    // Compute markers and price lines for selected trade
    const markers = selected
        ? [
            ...(selected.entry_time
                ? [
                    {
                        type: "entry" as const,
                        time: Math.floor(new Date(selected.entry_time).getTime() / 1000),
                        price: parseFloat(selected.entry_price),
                        label: `入场 ${selected.entry_price}`,
                    },
                ]
                : []),
            ...(selected.exit_time
                ? [
                    {
                        type: "exit" as const,
                        time: Math.floor(new Date(selected.exit_time).getTime() / 1000),
                        price: parseFloat(selected.exit_price),
                        label: `出场 ${selected.exit_price}`,
                    },
                ]
                : []),
        ]
        : [];

    const entryPrice = selected ? parseFloat(selected.entry_price) : undefined;
    const exitPrice =
        selected && selected.exit_price ? parseFloat(selected.exit_price) : undefined;

    // Estimate TP/SL from entry price and tp_pct / config
    const tpPrice =
        selected && entryPrice && selected.tp_pct_used
            ? entryPrice * (1 - selected.tp_pct_used / 100) // short position
            : undefined;
    const slPrice =
        selected && entryPrice
            ? entryPrice * (1 + 0.18) // 18% SL for short
            : undefined;

    return (
        <div className="flex gap-0 -mx-6 -mt-6" style={{ height: "calc(100vh - 0px)" }}>
            {/* Left: Trade List */}
            <div className="w-[340px] min-w-[340px] border-r border-[var(--border)] overflow-y-auto bg-[var(--bg-secondary)]">
                <div className="sticky top-0 bg-[var(--bg-secondary)] border-b border-[var(--border)] px-4 py-3 z-10">
                    <h3 className="text-sm font-medium">交易历史 ({trades.length})</h3>
                </div>

                {trades.map((t, i) => {
                    const pnl = parseFloat(t.pnl);
                    const pnlPct = parseFloat(t.pnl_pct);
                    const isSelected = selected === t;

                    return (
                        <div
                            key={i}
                            onClick={() => setSelected(t)}
                            className={`px-4 py-3 border-b border-[var(--border)]/50 cursor-pointer transition-colors
                ${isSelected ? "bg-[var(--accent-blue)]/10 border-l-2 border-l-[var(--accent-blue)]" : "hover:bg-[var(--bg-hover)]"}`}
                        >
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <span className="font-medium text-sm">{t.symbol}</span>
                                    <span
                                        className={`text-[10px] px-1 py-0.5 rounded ${t.side === "short"
                                                ? "bg-red-500/20 text-red-400"
                                                : "bg-green-500/20 text-green-400"
                                            }`}
                                    >
                                        {t.side.toUpperCase()}
                                    </span>
                                </div>
                                <span
                                    className={`text-sm font-mono font-medium ${pnl >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"
                                        }`}
                                >
                                    {pnl >= 0 ? "+" : ""}
                                    {pnlPct.toFixed(1)}%
                                </span>
                            </div>

                            <div className="flex items-center justify-between mt-1">
                                <span className="text-[10px] text-[var(--text-muted)]">
                                    {t.exit_time?.slice(0, 16) || "—"}
                                </span>
                                <span className="text-[10px] text-[var(--text-muted)]">
                                    {t.exit_reason}
                                </span>
                            </div>
                        </div>
                    );
                })}

                {error && (
                    <p className="px-4 py-3 text-xs text-red-400">⚠️ {error}</p>
                )}
            </div>

            {/* Right: Chart */}
            <div className="flex-1 flex flex-col bg-[var(--bg-primary)]">
                {selected ? (
                    <>
                        {/* Chart header */}
                        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
                            <div className="flex items-center gap-3">
                                <span className="font-bold">{selected.symbol}</span>
                                <span
                                    className={`text-xs px-1.5 py-0.5 rounded ${selected.side === "short"
                                            ? "bg-red-500/20 text-red-400"
                                            : "bg-green-500/20 text-green-400"
                                        }`}
                                >
                                    {selected.side.toUpperCase()}
                                </span>
                                <span className="text-xs text-[var(--text-muted)]">
                                    {selected.hold_hours.toFixed(1)}h |{" "}
                                    TP {selected.tp_pct_used}% |{" "}
                                    {selected.strength}
                                </span>
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
                            {loading ? (
                                <div className="absolute inset-0 flex items-center justify-center text-[var(--text-muted)]">
                                    加载中...
                                </div>
                            ) : klines.length > 0 ? (
                                <TradeChart
                                    klines={klines}
                                    markers={markers}
                                    entryPrice={entryPrice}
                                    exitPrice={exitPrice}
                                    tpPrice={tpPrice}
                                    slPrice={slPrice}
                                />
                            ) : (
                                <div className="flex items-center justify-center h-full text-[var(--text-muted)]">
                                    暂无K线数据
                                </div>
                            )}
                        </div>

                        {/* Trade details bar */}
                        <div className="px-4 py-2 border-t border-[var(--border)] flex gap-6 text-xs text-[var(--text-secondary)]">
                            <span>入场: {selected.entry_price}</span>
                            <span>出场: {selected.exit_price}</span>
                            <span
                                className={
                                    parseFloat(selected.pnl) >= 0
                                        ? "text-[var(--accent-green)]"
                                        : "text-[var(--accent-red)]"
                                }
                            >
                                盈亏: {selected.pnl} ({selected.pnl_pct}%)
                            </span>
                            <span>原因: {selected.exit_reason}</span>
                        </div>
                    </>
                ) : (
                    <div className="flex items-center justify-center h-full text-[var(--text-muted)]">
                        ← 选择一笔交易查看K线图
                    </div>
                )}
            </div>
        </div>
    );
}
