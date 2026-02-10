"use client";

import { useEffect, useState } from "react";
import { api, Status, Position, PaperTrade, EquityPoint } from "@/lib/api";
import EquityChart from "@/components/EquityChart";

function StatCard({
  title,
  value,
  subtitle,
  color = "text-[var(--text-primary)]",
}: {
  title: string;
  value: string;
  subtitle?: string;
  color?: string;
}) {
  return (
    <div className="bg-[var(--bg-card)] rounded-xl p-5 border border-[var(--border)] hover:border-[var(--accent-blue)]/30 transition-colors">
      <p className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-1">
        {title}
      </p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {subtitle && (
        <p className="text-xs text-[var(--text-secondary)] mt-1">{subtitle}</p>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [error, setError] = useState("");

  const fetchData = async () => {
    try {
      const [s, p, t, e] = await Promise.all([
        api.getStatus(),
        api.getPositions(),
        api.getTrades(10),
        api.getEquity(),
      ]);
      setStatus(s);
      setPositions(p);
      setTrades(t);
      setEquity(e);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to fetch data");
    }
  };

  useEffect(() => {
    fetchData();
    const iv = setInterval(fetchData, 10000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">
          仪表盘
          {status && (
            <span
              className={`ml-3 text-xs px-2 py-0.5 rounded-full ${status.mode === "live"
                  ? "bg-red-500/20 text-red-400"
                  : "bg-blue-500/20 text-blue-400"
                }`}
            >
              {status.mode === "live" ? "🔴 实盘" : "🔵 模拟"}
            </span>
          )}
        </h2>
        {error && (
          <span className="text-xs text-red-400 bg-red-400/10 px-3 py-1 rounded">
            ⚠️ {error}
          </span>
        )}
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard
          title="账户余额"
          value={status ? `$${status.total_balance.toFixed(2)}` : "—"}
        />
        <StatCard
          title="可用余额"
          value={status ? `$${status.available_balance.toFixed(2)}` : "—"}
        />
        <StatCard
          title="未实现盈亏"
          value={
            status ? `$${status.unrealized_pnl.toFixed(2)}` : "—"
          }
          color={
            status && status.unrealized_pnl >= 0
              ? "text-[var(--accent-green)]"
              : "text-[var(--accent-red)]"
          }
        />
        <StatCard
          title="今日盈亏"
          value={status ? `$${status.daily_pnl.toFixed(2)}` : "—"}
          color={
            status && status.daily_pnl >= 0
              ? "text-[var(--accent-green)]"
              : "text-[var(--accent-red)]"
          }
          subtitle={`持仓 ${status?.open_positions || 0} 笔`}
        />
      </div>

      {/* Equity Chart */}
      <div className="bg-[var(--bg-card)] rounded-xl p-5 border border-[var(--border)] mb-6">
        <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
          资金曲线
        </h3>
        {equity.length > 0 ? (
          <EquityChart data={equity} />
        ) : (
          <div className="h-[300px] flex items-center justify-center text-[var(--text-muted)]">
            暂无数据
          </div>
        )}
      </div>

      {/* Recent Trades */}
      <div className="bg-[var(--bg-card)] rounded-xl p-5 border border-[var(--border)]">
        <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-3">
          最近交易
        </h3>
        {trades.length === 0 ? (
          <p className="text-[var(--text-muted)] text-sm">暂无交易记录</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-[var(--text-muted)] text-xs border-b border-[var(--border)]">
                  <th className="text-left py-2 pr-4">币种</th>
                  <th className="text-left py-2 pr-4">方向</th>
                  <th className="text-right py-2 pr-4">入场价</th>
                  <th className="text-right py-2 pr-4">出场价</th>
                  <th className="text-right py-2 pr-4">盈亏</th>
                  <th className="text-right py-2 pr-4">盈亏%</th>
                  <th className="text-left py-2">退出原因</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const pnl = parseFloat(t.pnl);
                  return (
                    <tr
                      key={i}
                      className="border-b border-[var(--border)]/50 hover:bg-[var(--bg-hover)] transition-colors"
                    >
                      <td className="py-2 pr-4 font-medium">{t.symbol}</td>
                      <td className="py-2 pr-4">
                        <span
                          className={`text-xs px-1.5 py-0.5 rounded ${t.side === "short"
                              ? "bg-red-500/20 text-red-400"
                              : "bg-green-500/20 text-green-400"
                            }`}
                        >
                          {t.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs">
                        {t.entry_price}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs">
                        {t.exit_price}
                      </td>
                      <td
                        className={`py-2 pr-4 text-right font-mono text-xs ${pnl >= 0
                            ? "text-[var(--accent-green)]"
                            : "text-[var(--accent-red)]"
                          }`}
                      >
                        {pnl >= 0 ? "+" : ""}
                        {pnl.toFixed(2)}
                      </td>
                      <td
                        className={`py-2 pr-4 text-right font-mono text-xs ${pnl >= 0
                            ? "text-[var(--accent-green)]"
                            : "text-[var(--accent-red)]"
                          }`}
                      >
                        {t.pnl_pct}%
                      </td>
                      <td className="py-2 text-xs text-[var(--text-muted)]">
                        {t.exit_reason}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
