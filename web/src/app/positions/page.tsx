"use client";

import { useEffect, useState } from "react";
import { api, Position } from "@/lib/api";

export default function PositionsPage() {
    const [positions, setPositions] = useState<Position[]>([]);
    const [closing, setClosing] = useState<string | null>(null);
    const [error, setError] = useState("");

    const fetchPositions = async () => {
        try {
            const p = await api.getPositions();
            setPositions(p);
            setError("");
        } catch (err: any) {
            setError(err.message);
        }
    };

    useEffect(() => {
        fetchPositions();
        const iv = setInterval(fetchPositions, 5000);
        return () => clearInterval(iv);
    }, []);

    const handleClose = async (symbol: string) => {
        if (!confirm(`确认平仓 ${symbol}？`)) return;
        setClosing(symbol);
        try {
            await api.closePosition(symbol);
            await fetchPositions();
        } catch (err: any) {
            alert(`平仓失败: ${err.message}`);
        } finally {
            setClosing(null);
        }
    };

    return (
        <div>
            <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-semibold">
                    当前持仓
                    <span className="ml-2 text-sm text-[var(--text-muted)]">
                        ({positions.length})
                    </span>
                </h2>
                {error && (
                    <span className="text-xs text-red-400 bg-red-400/10 px-3 py-1 rounded">
                        ⚠️ {error}
                    </span>
                )}
            </div>

            {positions.length === 0 ? (
                <div className="bg-[var(--bg-card)] rounded-xl p-12 border border-[var(--border)] text-center">
                    <p className="text-[var(--text-muted)]">暂无持仓</p>
                </div>
            ) : (
                <div className="grid gap-4">
                    {positions.map((p) => (
                        <div
                            key={p.symbol}
                            className="bg-[var(--bg-card)] rounded-xl p-5 border border-[var(--border)] hover:border-[var(--accent-blue)]/30 transition-colors"
                        >
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-4">
                                    <div>
                                        <span className="text-lg font-bold">{p.symbol}</span>
                                        <span
                                            className={`ml-2 text-xs px-1.5 py-0.5 rounded ${p.side === "SHORT"
                                                    ? "bg-red-500/20 text-red-400"
                                                    : "bg-green-500/20 text-green-400"
                                                }`}
                                        >
                                            {p.side}
                                        </span>
                                    </div>
                                </div>

                                <button
                                    onClick={() => handleClose(p.symbol)}
                                    disabled={closing === p.symbol}
                                    className="px-4 py-1.5 text-xs rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors disabled:opacity-50"
                                >
                                    {closing === p.symbol ? "平仓中..." : "平仓"}
                                </button>
                            </div>

                            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mt-4">
                                <div>
                                    <p className="text-xs text-[var(--text-muted)]">入场价</p>
                                    <p className="font-mono text-sm">{p.entry_price}</p>
                                </div>
                                <div>
                                    <p className="text-xs text-[var(--text-muted)]">数量</p>
                                    <p className="font-mono text-sm">{p.quantity}</p>
                                </div>
                                <div>
                                    <p className="text-xs text-[var(--text-muted)]">杠杆</p>
                                    <p className="font-mono text-sm">{p.leverage}x</p>
                                </div>
                                <div>
                                    <p className="text-xs text-[var(--text-muted)]">未实现盈亏</p>
                                    <p
                                        className={`font-mono text-sm ${p.unrealized_pnl >= 0
                                                ? "text-[var(--accent-green)]"
                                                : "text-[var(--accent-red)]"
                                            }`}
                                    >
                                        {p.unrealized_pnl >= 0 ? "+" : ""}
                                        {p.unrealized_pnl.toFixed(4)}
                                    </p>
                                </div>
                                {p.strength && (
                                    <div>
                                        <p className="text-xs text-[var(--text-muted)]">强度</p>
                                        <p className="text-sm">{p.strength}</p>
                                    </div>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
