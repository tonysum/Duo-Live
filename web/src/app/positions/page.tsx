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

    const totalPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0);

    return (
        <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>当前持仓</h2>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{positions.length} 笔</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    {error && (
                        <span style={{ fontSize: 11, color: "var(--accent-red)", background: "rgba(248,113,113,0.1)", padding: "4px 10px", borderRadius: 9999 }}>
                            ⚠ {error}
                        </span>
                    )}
                    {positions.length > 0 && (
                        <span className={`font-mono ${totalPnl >= 0 ? "pnl-positive" : "pnl-negative"}`} style={{ fontSize: 14, fontWeight: 600 }}>
                            合计 {totalPnl >= 0 ? "+" : ""}{totalPnl.toFixed(2)} USDT
                        </span>
                    )}
                </div>
            </div>

            <div className="card">
                {positions.length === 0 ? (
                    <div style={{ padding: "52px 0", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>暂无持仓</div>
                ) : (
                    <table className="tbl">
                        <thead>
                            <tr>
                                <th>币种</th>
                                <th>方向</th>
                                <th className="right">数量</th>
                                <th className="right">入场价</th>
                                <th className="right">杠杆</th>
                                <th className="right">未实现盈亏</th>
                                <th className="center">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            {positions.map((p) => (
                                <tr key={p.symbol}>
                                    <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{p.symbol}</td>
                                    <td>
                                        <span className={`badge ${p.side === "SHORT" ? "badge-short" : "badge-long"}`}>{p.side}</span>
                                    </td>
                                    <td className="right font-mono">{p.quantity}</td>
                                    <td className="right font-mono">{p.entry_price.toFixed(4)}</td>
                                    <td className="right font-mono">{p.leverage}×</td>
                                    <td className={`right font-mono ${p.unrealized_pnl >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                                        {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(4)}
                                    </td>
                                    <td className="center">
                                        <button
                                            onClick={() => handleClose(p.symbol)}
                                            disabled={closing === p.symbol}
                                            className="badge badge-reject"
                                            style={{ cursor: "pointer", border: "none", padding: "3px 10px", opacity: closing === p.symbol ? 0.4 : 1, transition: "opacity 0.15s" }}
                                        >
                                            {closing === p.symbol ? "平仓中..." : "平仓"}
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
