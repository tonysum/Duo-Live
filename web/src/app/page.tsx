"use client";

import { useEffect, useState } from "react";
import { api, Status, Position, LiveTrade } from "@/lib/api";

export default function DashboardPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [error, setError] = useState("");

  const fetchData = async () => {
    try {
      const [s, p, t] = await Promise.all([
        api.getStatus(),
        api.getPositions(),
        api.getTrades(10),
      ]);
      setStatus(s);
      setPositions(p);
      setTrades(t);
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
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>总览</h2>
          <span className="badge badge-reject" style={{ fontSize: 10 }}>实盘</span>
        </div>
        {error && (
          <span style={{ fontSize: 11, color: "var(--accent-red)", background: "rgba(248,113,113,0.1)", padding: "4px 10px", borderRadius: 9999 }}>
            ⚠ {error}
          </span>
        )}
      </div>

      {/* Stat Row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 1, background: "var(--border)", borderRadius: "var(--radius-xl)", overflow: "hidden", marginBottom: 20 }}>
        <StatCell label="账户余额" value={status ? `$${status.total_balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"} />
        <StatCell label="可用余额" value={status ? `$${status.available_balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"} />
        <StatCell
          label="未实现盈亏"
          value={status ? `${status.unrealized_pnl >= 0 ? "+" : ""}${status.unrealized_pnl.toFixed(2)}` : "—"}
          valueColor={status ? (status.unrealized_pnl >= 0 ? "var(--accent-green)" : "var(--accent-red)") : undefined}
        />
        <StatCell
          label="今日盈亏"
          value={status ? `${status.daily_pnl >= 0 ? "+" : ""}${status.daily_pnl.toFixed(2)}` : "—"}
          valueColor={status ? (status.daily_pnl >= 0 ? "var(--accent-green)" : "var(--accent-red)") : undefined}
          sub={`持仓 ${status?.open_positions || 0} 笔`}
        />
      </div>

      {/* 2-column grid for positions & trades */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Positions */}
        <div className="card">
          <div className="card-header">
            <span>当前持仓</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>{positions.length} 笔</span>
          </div>
          {positions.length === 0 ? (
            <div style={{ padding: "40px 0", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>暂无持仓</div>
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
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{p.symbol}</td>
                    <td>
                      <span className={`badge ${p.side === "SHORT" ? "badge-short" : "badge-long"}`}>{p.side}</span>
                    </td>
                    <td className="right font-mono">{p.quantity}</td>
                    <td className="right font-mono">{p.entry_price.toFixed(4)}</td>
                    <td className="right font-mono">{p.leverage}×</td>
                    <td className={`right font-mono ${p.unrealized_pnl >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                      {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Recent Trades */}
        <div className="card">
          <div className="card-header">
            <span>最近交易</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>{trades.length} 笔</span>
          </div>
          {trades.length === 0 ? (
            <div style={{ padding: "40px 0", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>暂无交易记录</div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>币种</th>
                  <th>方向</th>
                  <th className="right">入场价</th>
                  <th className="right">出场价</th>
                  <th className="right">盈亏</th>
                  <th>平仓时间</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{t.symbol}</td>
                    <td>
                      <span className={`badge ${t.side === "SHORT" ? "badge-short" : "badge-long"}`}>{t.side}</span>
                    </td>
                    <td className="right font-mono">{t.entry_price}</td>
                    <td className="right font-mono">{t.exit_price}</td>
                    <td className={`right font-mono ${t.pnl_usdt >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                      {t.pnl_usdt >= 0 ? "+" : ""}{t.pnl_usdt.toFixed(2)}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {t.exit_time ? t.exit_time.slice(0, 19).replace("T", " ") : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function StatCell({ label, value, valueColor, sub }: {
  label: string;
  value: string;
  valueColor?: string;
  sub?: string;
}) {
  return (
    <div className="stat-cell">
      <div className="label">{label}</div>
      <div className="value" style={valueColor ? { color: valueColor } : undefined}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}
