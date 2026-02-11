"use client";

import { useEffect, useState } from "react";
import { api, Signal } from "@/lib/api";

export default function SignalsPage() {
    const [signals, setSignals] = useState<Signal[]>([]);
    const [tickers, setTickers] = useState<Record<string, { price: number; change_pct: number }>>({});
    const [error, setError] = useState("");

    useEffect(() => {
        const fetchAll = () => {
            api.getSignals(200).then(setSignals).catch((e) => setError(e.message));
            api.getTickers().then(setTickers).catch(() => { });
        };
        fetchAll();
        const iv = setInterval(fetchAll, 10000);
        return () => clearInterval(iv);
    }, []);

    // Sort: date descending, same day by surge_ratio descending
    const sorted = [...signals].sort((a, b) => {
        const dayA = a.timestamp.slice(0, 10);
        const dayB = b.timestamp.slice(0, 10);
        if (dayA !== dayB) return dayB.localeCompare(dayA);
        return b.surge_ratio - a.surge_ratio;
    });

    // Dedup: keep only first signal per symbol per day
    const seen = new Set<string>();
    const deduped = sorted.filter((s) => {
        const key = `${s.symbol}:${s.timestamp.slice(0, 10)}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });

    const accepted = signals.filter((s) => s.accepted);
    const rejected = signals.filter((s) => !s.accepted);

    return (
        <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>信号事件</h2>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{signals.length} 条</span>
                </div>
                <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
                    <span style={{ color: "var(--accent-green)", fontWeight: 500 }}>通过 {accepted.length}</span>
                    <span style={{ color: "var(--accent-red)", fontWeight: 500 }}>拒绝 {rejected.length}</span>
                </div>
            </div>

            {error && (
                <div style={{ fontSize: 11, color: "var(--accent-red)", background: "rgba(248,113,113,0.1)", padding: "8px 14px", borderRadius: "var(--radius-lg)", marginBottom: 16 }}>
                    ⚠ {error}
                </div>
            )}

            <div className="card">
                {deduped.length === 0 ? (
                    <div style={{ padding: "52px 0", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>暂无信号数据</div>
                ) : (
                    <table className="tbl">
                        <thead>
                            <tr>
                                <th>时间</th>
                                <th>币种</th>
                                <th className="right">暴涨倍数</th>
                                <th className="right">信号价格</th>
                                <th className="right">实时价格</th>
                                <th className="right">涨幅</th>
                                <th className="center">状态</th>
                                <th>拒绝原因</th>
                            </tr>
                        </thead>
                        <tbody>
                            {deduped.map((s, i) => {
                                const ticker = tickers[s.symbol];
                                const curPrice = ticker?.price;
                                const changePct = ticker?.change_pct;

                                return (
                                    <tr key={i}>
                                        <td className="font-mono" style={{ fontSize: 11, color: "var(--text-muted)" }}>
                                            {s.timestamp.slice(0, 19).replace("T", " ")}
                                        </td>
                                        <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{s.symbol}</td>
                                        <td className="right font-mono" style={{ color: "var(--accent-yellow)" }}>
                                            {s.surge_ratio.toFixed(1)}×
                                        </td>
                                        <td className="right font-mono">{s.price}</td>
                                        <td className="right font-mono">
                                            {curPrice != null ? curPrice : "—"}
                                        </td>
                                        <td className="right font-mono" style={{
                                            color: changePct == null ? "var(--text-muted)"
                                                : changePct >= 0 ? "var(--accent-green)" : "var(--accent-red)",
                                        }}>
                                            {changePct != null ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%` : "—"}
                                        </td>
                                        <td className="center">
                                            <span className={`badge ${s.accepted ? "badge-pass" : "badge-reject"}`}>
                                                {s.accepted ? "通过" : "拒绝"}
                                            </span>
                                        </td>
                                        <td style={{ fontSize: 11, color: "var(--text-muted)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                                            {s.reject_reason || "—"}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
