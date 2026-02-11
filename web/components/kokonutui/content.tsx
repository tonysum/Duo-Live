"use client"

import { useEffect, useState } from "react"
import { api, Status, Position, LiveTrade } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownLeft,
  Activity,
  Briefcase,
  AlertCircle,
  CreditCard,
  ArrowRight,
} from "lucide-react"
import Link from "next/link"

function StatCard({
  label,
  value,
  icon: Icon,
  trend,
  sub,
}: {
  label: string
  value: string
  icon: React.ElementType
  trend?: "up" | "down" | "neutral"
  sub?: string
}) {
  return (
    <div
      className={cn(
        "bg-white dark:bg-zinc-900/70",
        "border border-zinc-100 dark:border-zinc-800",
        "rounded-xl p-4",
        "backdrop-blur-xl"
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <div
          className={cn("p-1.5 rounded-lg", {
            "bg-emerald-100 dark:bg-emerald-900/30": trend === "up",
            "bg-red-100 dark:bg-red-900/30": trend === "down",
            "bg-zinc-100 dark:bg-zinc-800": trend === "neutral" || !trend,
          })}
        >
          <Icon
            className={cn("w-3.5 h-3.5", {
              "text-emerald-600 dark:text-emerald-400": trend === "up",
              "text-red-600 dark:text-red-400": trend === "down",
              "text-zinc-600 dark:text-zinc-400": trend === "neutral" || !trend,
            })}
          />
        </div>
      </div>
      <p
        className={cn("text-xl font-semibold font-mono", {
          "text-emerald-600 dark:text-emerald-400": trend === "up",
          "text-red-600 dark:text-red-400": trend === "down",
          "text-zinc-900 dark:text-zinc-50": trend === "neutral" || !trend,
        })}
      >
        {value}
      </p>
      {sub && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">{sub}</p>}
    </div>
  )
}

export default function Content() {
  const [status, setStatus] = useState<Status | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<LiveTrade[]>([])
  const [error, setError] = useState("")

  const fetchData = async () => {
    try {
      const [s, p, t] = await Promise.all([
        api.getStatus(),
        api.getPositions(),
        api.getTrades(10),
      ])
      setStatus(s)
      setPositions(p)
      setTrades(t)
      setError("")
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch data"
      setError(message)
    }
  }

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 10000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
            <Activity className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
            Overview
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
            Real-time trading dashboard
          </p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
          <AlertCircle className="w-4 h-4 text-red-500" />
          <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
        </div>
      )}

      {/* Stat cards row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Balance"
          value={status ? `$${status.total_balance.toFixed(2)}` : "---"}
          icon={Wallet}
          trend="neutral"
        />
        <StatCard
          label="Available"
          value={status ? `$${status.available_balance.toFixed(2)}` : "---"}
          icon={CreditCard}
          trend="neutral"
        />
        <StatCard
          label="Unrealized PnL"
          value={
            status
              ? `${status.unrealized_pnl >= 0 ? "+" : ""}$${status.unrealized_pnl.toFixed(2)}`
              : "---"
          }
          icon={status && status.unrealized_pnl >= 0 ? TrendingUp : TrendingDown}
          trend={status ? (status.unrealized_pnl >= 0 ? "up" : "down") : "neutral"}
        />
        <StatCard
          label="Daily PnL"
          value={
            status
              ? `${status.daily_pnl >= 0 ? "+" : ""}$${status.daily_pnl.toFixed(2)}`
              : "---"
          }
          icon={status && status.daily_pnl >= 0 ? TrendingUp : TrendingDown}
          trend={status ? (status.daily_pnl >= 0 ? "up" : "down") : "neutral"}
          sub={`${status?.open_positions || 0} open positions`}
        />
      </div>

      {/* Two column grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Current Positions */}
        <div className="bg-white dark:bg-[#0F0F12] rounded-xl flex flex-col border border-gray-200 dark:border-[#1F1F23]">
          <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Briefcase className="w-3.5 h-3.5 text-zinc-900 dark:text-zinc-50" />
              Current Positions
              <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
                ({positions.length})
              </span>
            </h2>
            <Link
              href="/positions"
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
            >
              View all
            </Link>
          </div>
          <div className="p-3 flex-1">
            {positions.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-zinc-400 dark:text-zinc-500">
                No open positions
              </div>
            ) : (
              <div className="space-y-1">
                {positions.map((p, i) => (
                  <div
                    key={`${p.symbol}-${i}`}
                    className={cn(
                      "group flex items-center justify-between",
                      "p-2.5 rounded-lg",
                      "hover:bg-zinc-100 dark:hover:bg-zinc-800/50",
                      "transition-all duration-200"
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={cn("p-1.5 rounded-lg", {
                          "bg-emerald-100 dark:bg-emerald-900/30": p.side === "LONG",
                          "bg-red-100 dark:bg-red-900/30": p.side === "SHORT",
                        })}
                      >
                        {p.side === "LONG" ? (
                          <ArrowUpRight className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        ) : (
                          <ArrowDownLeft className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
                        )}
                      </div>
                      <div>
                        <h3 className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {p.symbol}
                        </h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {p.side} {p.leverage}x
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <span
                        className={cn("text-xs font-medium font-mono", {
                          "text-emerald-600 dark:text-emerald-400": p.unrealized_pnl >= 0,
                          "text-red-600 dark:text-red-400": p.unrealized_pnl < 0,
                        })}
                      >
                        {p.unrealized_pnl >= 0 ? "+" : ""}
                        {p.unrealized_pnl.toFixed(2)}
                      </span>
                      <p className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
                        {p.entry_price.toFixed(4)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-2 border-t border-zinc-100 dark:border-zinc-800">
            <Link
              href="/positions"
              className={cn(
                "w-full flex items-center justify-center gap-2",
                "py-2 px-3 rounded-lg",
                "text-xs font-medium",
                "bg-zinc-900 dark:bg-zinc-50",
                "text-zinc-50 dark:text-zinc-900",
                "hover:bg-zinc-800 dark:hover:bg-zinc-200",
                "shadow-sm hover:shadow",
                "transition-all duration-200"
              )}
            >
              <span>Manage Positions</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {/* Recent Trades */}
        <div className="bg-white dark:bg-[#0F0F12] rounded-xl flex flex-col border border-gray-200 dark:border-[#1F1F23]">
          <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Activity className="w-3.5 h-3.5 text-zinc-900 dark:text-zinc-50" />
              Recent Trades
              <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
                ({trades.length})
              </span>
            </h2>
            <Link
              href="/trades"
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
            >
              View all
            </Link>
          </div>
          <div className="p-3 flex-1">
            {trades.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-zinc-400 dark:text-zinc-500">
                No trades yet
              </div>
            ) : (
              <div className="space-y-1">
                {trades.slice(0, 6).map((t, i) => (
                  <div
                    key={`${t.symbol}-${i}`}
                    className={cn(
                      "group flex items-center gap-3",
                      "p-2.5 rounded-lg",
                      "hover:bg-zinc-100 dark:hover:bg-zinc-800/50",
                      "transition-all duration-200"
                    )}
                  >
                    <div
                      className={cn(
                        "p-2 rounded-lg",
                        "bg-zinc-100 dark:bg-zinc-800",
                        "border border-zinc-200 dark:border-zinc-700"
                      )}
                    >
                      {t.side === "LONG" ? (
                        <ArrowUpRight className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                      ) : (
                        <ArrowDownLeft className="w-4 h-4 text-red-600 dark:text-red-400" />
                      )}
                    </div>
                    <div className="flex-1 flex items-center justify-between min-w-0">
                      <div className="space-y-0.5">
                        <h3 className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {t.symbol}
                        </h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {t.exit_time
                            ? t.exit_time.slice(0, 19).replace("T", " ")
                            : "---"}
                        </p>
                      </div>
                      <div className="flex items-center gap-1.5 pl-3">
                        <span
                          className={cn("text-xs font-medium font-mono", {
                            "text-emerald-600 dark:text-emerald-400": t.pnl_usdt >= 0,
                            "text-red-600 dark:text-red-400": t.pnl_usdt < 0,
                          })}
                        >
                          {t.pnl_usdt >= 0 ? "+" : ""}
                          {t.pnl_usdt.toFixed(2)}
                        </span>
                        {t.pnl_usdt >= 0 ? (
                          <ArrowUpRight className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        ) : (
                          <ArrowDownLeft className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-2 border-t border-zinc-100 dark:border-zinc-800">
            <Link
              href="/trades"
              className={cn(
                "w-full flex items-center justify-center gap-2",
                "py-2 px-3 rounded-lg",
                "text-xs font-medium",
                "bg-zinc-900 dark:bg-zinc-50",
                "text-zinc-50 dark:text-zinc-900",
                "hover:bg-zinc-800 dark:hover:bg-zinc-200",
                "shadow-sm hover:shadow",
                "transition-all duration-200"
              )}
            >
              <span>View All Trades</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  )
}
