"use client";

import { useEffect, useState } from "react";
import { api, Signal } from "@/lib/api";

export default function SignalsPage() {
    const [signals, setSignals] = useState<Signal[]>([]);
    const [error, setError] = useState("");

    useEffect(() => {
        api
            .getSignals(200)
            .then(setSignals)
            .catch((e) => setError(e.message));

        const iv = setInterval(() => {
            api.getSignals(200).then(setSignals).catch(() => { });
        }, 10000);
        return () => clearInterval(iv);
    }, []);

    const accepted = signals.filter((s) => s.accepted);
    const rejected = signals.filter((s) => !s.accepted);

    return (
        <div>
            <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-semibold">
                    信号事件
                    <span className="ml-2 text-sm text-[var(--text-muted)]">
                        ({signals.length})
                    </span>
                </h2>
                <div className="flex gap-3 text-xs">
                    <span className="text-green-400">
                        ✅ 通过 {accepted.length}
                    </span>
                    <span className="text-red-400">
                        ❌ 拒绝 {rejected.length}
                    </span>
                </div>
            </div>

            {error && (
                <p className="text-xs text-red-400 bg-red-400/10 px-3 py-2 rounded mb-4">
                    ⚠️ {error}
                </p>
            )}

            <div className="bg-[var(--bg-card)] rounded-xl border border-[var(--border)] overflow-hidden">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="text-[var(--text-muted)] text-xs border-b border-[var(--border)] bg-[var(--bg-secondary)]">
                            <th className="text-left px-4 py-2.5">时间</th>
                            <th className="text-left px-4 py-2.5">币种</th>
                            <th className="text-right px-4 py-2.5">暴涨倍数</th>
                            <th className="text-right px-4 py-2.5">价格</th>
                            <th className="text-center px-4 py-2.5">状态</th>
                            <th className="text-left px-4 py-2.5">拒绝原因</th>
                        </tr>
                    </thead>
                    <tbody>
                        {signals.map((s, i) => (
                            <tr
                                key={i}
                                className="border-b border-[var(--border)]/30 hover:bg-[var(--bg-hover)] transition-colors"
                            >
                                <td className="px-4 py-2 text-xs text-[var(--text-secondary)] font-mono">
                                    {s.timestamp.slice(0, 19).replace("T", " ")}
                                </td>
                                <td className="px-4 py-2 font-medium">{s.symbol}</td>
                                <td className="px-4 py-2 text-right font-mono">
                                    <span className="text-[var(--accent-yellow)]">
                                        {s.surge_ratio.toFixed(1)}x
                                    </span>
                                </td>
                                <td className="px-4 py-2 text-right font-mono text-xs">
                                    {s.price}
                                </td>
                                <td className="px-4 py-2 text-center">
                                    <span
                                        className={`text-xs px-2 py-0.5 rounded-full ${s.accepted
                                                ? "bg-green-500/20 text-green-400"
                                                : "bg-red-500/20 text-red-400"
                                            }`}
                                    >
                                        {s.accepted ? "通过" : "拒绝"}
                                    </span>
                                </td>
                                <td className="px-4 py-2 text-xs text-[var(--text-muted)] max-w-[200px] truncate">
                                    {s.reject_reason || "—"}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>

                {signals.length === 0 && (
                    <div className="py-12 text-center text-[var(--text-muted)]">
                        暂无信号数据
                    </div>
                )}
            </div>
        </div>
    );
}
