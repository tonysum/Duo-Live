"use client"

import { useEffect, useState } from "react"
import { api, Position } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
  Briefcase,
  ArrowUpRight,
  ArrowDownLeft,
  AlertCircle,
  X,
  Loader2,
} from "lucide-react"

export default function PositionsPage() {
  const [positions, setPositions] = useState<Position[]>([])
  const [closing, setClosing] = useState<string | null>(null)
  const [error, setError] = useState("")

  const fetchPositions = async () => {
    try {
      const p = await api.getPositions()
      setPositions(p)
      setError("")
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch"
      setError(message)
    }
  }

  useEffect(() => {
    fetchPositions()
    const iv = setInterval(fetchPositions, 5000)
    return () => clearInterval(iv)
  }, [])

  const handleClose = async (symbol: string) => {
    if (!confirm(`Confirm close ${symbol}?`)) return
    setClosing(symbol)
    try {
      await api.closePosition(symbol)
      await fetchPositions()
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
                    {["Symbol", "Side", "Qty", "Entry", "Leverage", "Unrealized PnL", "Action"].map(
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
      </div>
    </Layout>
  )
}
