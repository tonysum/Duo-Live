"use client";

import { useEffect, useState } from "react";
import { api, LiveTrade, Kline } from "@/lib/api";
import TradeChart from "@/components/TradeChart";

export default function TradesPage() {
    const [trades, setTrades] = useState<LiveTrade[]>([]);
    const [selected, setSelected] = useState<LiveTrade | null>(null);
    const [klines, setKlines] = useState<Kline[]>([]);
    const [interval, setInterval_] = useState("15m");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    useEffect(() => {
        api.getTrades(200).then(setTrades).catch((e) => setError(e.message));
    }, []);

    useEffect(() => {
        if (!selected) return;
        setLoading(true);
        api.getKlines(selected.symbol, interval, 500)
            .then(setKlines)
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, [selected, interval]);

    const markers = selected
        ? [
            ...(selected.entry_time
                ? [{ type: "entry" as const, time: Math.floor(new Date(selected.entry_time).getTime() / 1000), price: selected.entry_price, label: `入场 ${selected.entry_price}` }]
                : []),
            ...(selected.exit_time
                ? [{ type: "exit" as const, time: Math.floor(new Date(selected.exit_time).getTime() / 1000), price: selected.exit_price, label: `出场 ${selected.exit_price}` }]
                : []),
        ]
        : [];

    const intervals = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"];

    return (
        <div style={{ display: "flex", gap: 0, margin: "-20px", height: "100vh" }}>
            {/* Left: Trade list */}
            <div style={{ width: 300, minWidth: 300, borderRight: "1px solid var(--border)", overflowY: "auto", background: "var(--bg-secondary)" }}>
                <div style={{
                    position: "sticky", top: 0, background: "var(--bg-secondary)",
                    borderBottom: "1px solid var(--border)", padding: "12px 16px", zIndex: 10,
                    fontSize: 13, fontWeight: 600
                }}>
                    交易历史
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>({trades.length})</span>
                </div>

                {trades.map((t, i) => {
                    const isSelected = selected === t;
                    return (
                        <div
                            key={i}
                            onClick={() => setSelected(t)}
                            style={{
                                padding: "10px 16px",
                                borderBottom: "1px solid var(--border-light)",
                                cursor: "pointer",
                                background: isSelected ? "var(--bg-hover)" : "transparent",
                                transition: "background 0.15s ease",
                            }}
                            onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)"; }}
                            onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
                        >
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                    <span style={{ fontWeight: 500, fontSize: 12, color: "var(--text-primary)" }}>{t.symbol}</span>
                                    <span className={`badge ${t.side === "SHORT" ? "badge-short" : "badge-long"}`}>{t.side}</span>
                                </div>
                                <span className={`font-mono ${t.pnl_usdt >= 0 ? "pnl-positive" : "pnl-negative"}`} style={{ fontSize: 12, fontWeight: 600 }}>
                                    {t.pnl_usdt >= 0 ? "+" : ""}{t.pnl_usdt.toFixed(2)}
                                </span>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 4 }}>
                                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                                    {t.exit_time?.slice(0, 16).replace("T", " ") || "—"}
                                </span>
                                <span className="font-mono" style={{ fontSize: 10, color: "var(--text-muted)" }}>
                                    {t.entry_price} → {t.exit_price}
                                </span>
                            </div>
                        </div>
                    );
                })}

                {error && <p style={{ padding: "12px 16px", fontSize: 11, color: "var(--accent-red)" }}>⚠ {error}</p>}
            </div>

            {/* Right: Chart */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", background: "var(--bg-primary)" }}>
                {selected ? (
                    <>
                        <div style={{
                            display: "flex", alignItems: "center", justifyContent: "space-between",
                            padding: "10px 16px", borderBottom: "1px solid var(--border)"
                        }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <span style={{ fontWeight: 600, fontSize: 15 }}>{selected.symbol}</span>
                                <span className={`badge ${selected.side === "SHORT" ? "badge-short" : "badge-long"}`}>{selected.side}</span>
                                <span className={`font-mono ${selected.pnl_usdt >= 0 ? "pnl-positive" : "pnl-negative"}`} style={{ fontSize: 14, fontWeight: 600 }}>
                                    {selected.pnl_usdt >= 0 ? "+" : ""}{selected.pnl_usdt.toFixed(2)} USDT
                                </span>
                            </div>
                            <div style={{ display: "flex", gap: 3 }}>
                                {intervals.map((iv) => (
                                    <button
                                        key={iv}
                                        onClick={() => setInterval_(iv)}
                                        style={{
                                            padding: "4px 10px", fontSize: 11, borderRadius: "var(--radius-md)",
                                            border: "none", cursor: "pointer",
                                            background: interval === iv ? "var(--bg-hover)" : "transparent",
                                            color: interval === iv ? "var(--text-primary)" : "var(--text-muted)",
                                            fontWeight: interval === iv ? 500 : 400,
                                            transition: "all 0.15s ease",
                                        }}
                                    >
                                        {iv}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div style={{ flex: 1, position: "relative" }}>
                            {loading ? (
                                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, color: "var(--text-muted)" }}>加载中...</div>
                            ) : klines.length > 0 ? (
                                <TradeChart klines={klines} markers={markers} entryPrice={selected.entry_price} exitPrice={selected.exit_price} />
                            ) : (
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: 12, color: "var(--text-muted)" }}>暂无K线数据</div>
                            )}
                        </div>

                        <div style={{
                            padding: "8px 16px", borderTop: "1px solid var(--border)",
                            display: "flex", gap: 20, fontSize: 11, color: "var(--text-secondary)"
                        }}>
                            <span>入场: <span className="font-mono">{selected.entry_price}</span> ({selected.entry_time?.slice(11, 19)})</span>
                            <span>出场: <span className="font-mono">{selected.exit_price}</span> ({selected.exit_time?.slice(11, 19)})</span>
                        </div>
                    </>
                ) : (
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", fontSize: 13, color: "var(--text-muted)" }}>
                        ← 选择一笔交易查看K线图
                    </div>
                )}
            </div>
        </div>
    );
}
