"use client"

import { useEffect, useState } from "react"
import { api, Position, OpenOrder } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
  Briefcase,
  ArrowUpRight,
  ArrowDownLeft,
  AlertCircle,
  X,
  Loader2,
  ClipboardList,
} from "lucide-react"

function formatTime(ts: number) {
  if (!ts) return "—"
  const d = new Date(ts)
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function orderTypeLabel(type: string) {
  const map: Record<string, string> = {
    LIMIT: "限价",
    MARKET: "市价",
    STOP: "止损限价",
    STOP_MARKET: "止损",
    TAKE_PROFIT: "止盈限价",
    TAKE_PROFIT_MARKET: "止盈",
    TRAILING_STOP_MARKET: "追踪止损",
  }
  return map[type] || type
}

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<OpenOrder[]>([])
  const [closing, setClosing] = useState<string | null>(null)
  const [error, setError] = useState("")

  const fetchData = async () => {
    try {
      const [p, o] = await Promise.all([api.getPositions(), api.getOrders()])
      setPositions(p)
      setOrders(o)
      setError("")
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch"
      setError(message)
    }
  }

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 5000)
    return () => clearInterval(iv)
  }, [])

  const handleClose = async (symbol: string) => {
    if (!confirm(`Confirm close ${symbol}?`)) return
    setClosing(symbol)
    try {
      await api.closePosition(symbol)
      await fetchData()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Close failed"
      alert(`Close failed: ${message}`)
    } finally {
      setClosing(null)
    }
  }

  const totalPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0)

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
              <Briefcase className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
              Current Positions
              <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                ({positions.length})
              </span>
            </h1>
          </div>
          {positions.length > 0 && (
            <span
              className={cn("text-sm font-semibold font-mono", {
                "text-emerald-600 dark:text-emerald-400": totalPnl >= 0,
                "text-red-600 dark:text-red-400": totalPnl < 0,
              })}
            >
              Total: {totalPnl >= 0 ? "+" : ""}
              {totalPnl.toFixed(2)} USDT
            </span>
          )}
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <AlertCircle className="w-4 h-4 text-red-500" />
            <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
          </div>
        )}

        {/* Positions table */}
        <div
          className={cn(
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl shadow-sm backdrop-blur-xl",
            "overflow-hidden"
          )}
        >
          {positions.length === 0 ? (
            <div className="flex items-center justify-center py-16 text-sm text-zinc-400 dark:text-zinc-500">
              No open positions
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-100 dark:border-zinc-800">
                    {["Symbol", "Side", "Qty", "Entry", "Leverage", "Liq. Price", "Margin", "Margin %", "Unrealized PnL", "Action"].map(
                      (h) => (
                        <th
                          key={h}
                          className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr
                      key={p.symbol}
                      className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors"
                    >
                      <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">
                        {p.symbol}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full",
                            {
                              "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                                p.side === "LONG",
                              "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                                p.side === "SHORT",
                            }
                          )}
                        >
                          {p.side === "LONG" ? (
                            <ArrowUpRight className="w-3 h-3" />
                          ) : (
                            <ArrowDownLeft className="w-3 h-3" />
                          )}
                          {p.side}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {p.quantity}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {p.entry_price.toFixed(4)}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {p.leverage}x
                      </td>
                      <td className="px-4 py-3 text-xs text-orange-600 dark:text-orange-400 font-mono">
                        {p.liquidation_price > 0 ? p.liquidation_price.toFixed(4) : "—"}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {p.margin > 0 ? p.margin.toFixed(2) : "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn("text-xs font-medium font-mono", {
                            // High margin_ratio = near liquidation = danger
                            "text-red-600 dark:text-red-400": p.margin_ratio >= 80,
                            "text-yellow-600 dark:text-yellow-400": p.margin_ratio >= 50 && p.margin_ratio < 80,
                            "text-emerald-600 dark:text-emerald-400": p.margin_ratio < 50,
                          })}
                        >
                          {p.margin_ratio > 0 ? `${p.margin_ratio.toFixed(1)}%` : "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn("text-xs font-medium font-mono", {
                            "text-emerald-600 dark:text-emerald-400": p.unrealized_pnl >= 0,
                            "text-red-600 dark:text-red-400": p.unrealized_pnl < 0,
                          })}
                        >
                          {p.unrealized_pnl >= 0 ? "+" : ""}
                          {p.unrealized_pnl.toFixed(4)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          onClick={() => handleClose(p.symbol)}
                          disabled={closing === p.symbol}
                          className={cn(
                            "inline-flex items-center gap-1",
                            "px-2.5 py-1 rounded-md",
                            "text-[11px] font-medium",
                            "bg-red-100 dark:bg-red-900/30",
                            "text-red-600 dark:text-red-400",
                            "hover:bg-red-200 dark:hover:bg-red-900/50",
                            "transition-colors duration-200",
                            "disabled:opacity-40 disabled:cursor-not-allowed"
                          )}
                        >
                          {closing === p.symbol ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <X className="w-3 h-3" />
                          )}
                          {closing === p.symbol ? "Closing..." : "Close"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Open Orders section */}
        <div>
          <h2 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2 mb-3">
            <ClipboardList className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
            Open Orders
            <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
              ({orders.length})
            </span>
          </h2>
        </div>

        <div
          className={cn(
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl shadow-sm backdrop-blur-xl",
            "overflow-hidden"
          )}
        >
          {orders.length === 0 ? (
            <div className="flex items-center justify-center py-12 text-sm text-zinc-400 dark:text-zinc-500">
              No open orders
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-zinc-100 dark:border-zinc-800">
                    {["Symbol", "Type", "Side", "Price / Trigger", "Qty", "Filled", "Status", "Time"].map(
                      (h) => (
                        <th
                          key={h}
                          className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      )
                    )}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr
                      key={`${o.id}-${o.is_algo}`}
                      className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors"
                    >
                      <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">
                        {o.symbol}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "text-[10px] font-medium px-2 py-0.5 rounded-full",
                            o.is_algo
                              ? "bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400"
                              : "bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400"
                          )}
                        >
                          {orderTypeLabel(o.type)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "text-[10px] font-medium px-2 py-0.5 rounded-full",
                            o.side === "BUY"
                              ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                              : "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                          )}
                        >
                          {o.side}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {o.stop_price > 0 ? (
                          <span className="text-orange-600 dark:text-orange-400">
                            ⚡ {o.stop_price}
                          </span>
                        ) : o.price > 0 ? (
                          o.price
                        ) : (
                          "市价"
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {o.quantity > 0 ? o.quantity : "全仓"}
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                        {o.filled_qty}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "text-[10px] font-medium px-2 py-0.5 rounded-full",
                            "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400"
                          )}
                        >
                          {o.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-zinc-500 dark:text-zinc-500">
                        {formatTime(o.time)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Layout>
  )
}
