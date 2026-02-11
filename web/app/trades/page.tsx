"use client"

import { useEffect, useState } from "react"
import { api, LiveTrade, Kline } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import dynamic from "next/dynamic"
import {
  BarChart2,
  ArrowUpRight,
  ArrowDownLeft,
  AlertCircle,
} from "lucide-react"

const TradeChart = dynamic(() => import("@/components/kokonutui/trade-chart"), {
  ssr: false,
})

const INTERVALS = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"]

export default function TradesPage() {
  const [trades, setTrades] = useState<LiveTrade[]>([])
  const [selected, setSelected] = useState<LiveTrade | null>(null)
  const [klines, setKlines] = useState<Kline[]>([])
  const [interval, setInterval_] = useState("1h")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    api.getTrades(200).then(setTrades).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!selected) return
    setLoading(true)
    api
      .getKlines(selected.symbol, interval, 500)
      .then(setKlines)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [selected, interval])

  const markers = selected
    ? [
      ...(selected.entry_time
        ? [
          {
            type: "entry" as const,
            time: Math.floor(
              new Date(selected.entry_time).getTime() / 1000
            ),
            price: selected.entry_price,
            label: `Entry ${selected.entry_price}`,
          },
        ]
        : []),
      ...(selected.exit_time
        ? [
          {
            type: "exit" as const,
            time: Math.floor(
              new Date(selected.exit_time).getTime() / 1000
            ),
            price: selected.exit_price,
            label: `Exit ${selected.exit_price}`,
          },
        ]
        : []),
    ]
    : []

  return (
    <Layout>
      <div className="space-y-4">
        <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
          <BarChart2 className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
          Trade History
          <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
            ({trades.length})
          </span>
        </h1>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <AlertCircle className="w-4 h-4 text-red-500" />
            <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
          </div>
        )}

        <div className="flex flex-col lg:flex-row gap-4" style={{ height: "calc(100vh - 200px)" }}>
          {/* Trade list */}
          <div
            className={cn(
              "w-full lg:w-80 shrink-0",
              "bg-white dark:bg-zinc-900/70",
              "border border-zinc-100 dark:border-zinc-800",
              "rounded-xl shadow-sm backdrop-blur-xl",
              "overflow-y-auto"
            )}
          >
            <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 sticky top-0 bg-white dark:bg-zinc-900/70 backdrop-blur-xl z-10">
              <h2 className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                Trades ({trades.length})
              </h2>
            </div>
            <div className="p-1">
              {trades.map((t, i) => {
                const isSelected = selected === t
                return (
                  <button
                    key={`${t.symbol}-${i}`}
                    type="button"
                    onClick={() => setSelected(t)}
                    className={cn(
                      "w-full text-left p-3 rounded-lg transition-all duration-150",
                      isSelected
                        ? "bg-zinc-100 dark:bg-zinc-800/70"
                        : "hover:bg-zinc-50 dark:hover:bg-zinc-800/30"
                    )}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {t.symbol}
                        </span>
                        <span
                          className={cn(
                            "text-[10px] font-medium px-1.5 py-0.5 rounded-full",
                            {
                              "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                                t.side === "LONG",
                              "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                                t.side === "SHORT",
                            }
                          )}
                        >
                          {t.side}
                        </span>
                      </div>
                      <span
                        className={cn("text-xs font-semibold font-mono", {
                          "text-emerald-600 dark:text-emerald-400": t.pnl_usdt >= 0,
                          "text-red-600 dark:text-red-400": t.pnl_usdt < 0,
                        })}
                      >
                        {t.pnl_usdt >= 0 ? "+" : ""}
                        {t.pnl_usdt.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                        {t.exit_time?.slice(0, 16).replace("T", " ") || "---"}
                      </span>
                      <span className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
                        {t.entry_price} {">"} {t.exit_price}
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Chart area */}
          <div
            className={cn(
              "flex-1 flex flex-col min-w-0",
              "bg-white dark:bg-zinc-900/70",
              "border border-zinc-100 dark:border-zinc-800",
              "rounded-xl shadow-sm backdrop-blur-xl",
              "overflow-hidden"
            )}
          >
            {selected ? (
              <>
                {/* Chart header */}
                <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between flex-wrap gap-2">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      {selected.symbol}
                    </span>
                    <span
                      className={cn(
                        "text-[10px] font-medium px-2 py-0.5 rounded-full",
                        {
                          "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                            selected.side === "LONG",
                          "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                            selected.side === "SHORT",
                        }
                      )}
                    >
                      {selected.side}
                    </span>
                    <span
                      className={cn("text-sm font-semibold font-mono", {
                        "text-emerald-600 dark:text-emerald-400":
                          selected.pnl_usdt >= 0,
                        "text-red-600 dark:text-red-400":
                          selected.pnl_usdt < 0,
                      })}
                    >
                      {selected.pnl_usdt >= 0 ? "+" : ""}
                      {selected.pnl_usdt.toFixed(2)} USDT
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {INTERVALS.map((iv) => (
                      <button
                        key={iv}
                        type="button"
                        onClick={() => setInterval_(iv)}
                        className={cn(
                          "px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors",
                          interval === iv
                            ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                            : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        )}
                      >
                        {iv}
                      </button>
                    ))}
                  </div>
                </div>
                {/* Chart body */}
                <div className="flex-1 p-2 min-h-[300px]">
                  {loading ? (
                    <div className="flex items-center justify-center h-full text-sm text-zinc-400">
                      Loading...
                    </div>
                  ) : klines.length > 0 ? (
                    <TradeChart
                      klines={klines}
                      markers={markers}
                      entryPrice={selected.entry_price}
                      exitPrice={selected.exit_price}
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-sm text-zinc-400">
                      No kline data
                    </div>
                  )}
                </div>
                {/* Chart footer */}
                <div className="p-3 border-t border-zinc-100 dark:border-zinc-800 flex items-center gap-4">
                  <span className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono flex items-center gap-1">
                    <ArrowUpRight className="w-3 h-3 text-emerald-500" />
                    Entry: {selected.entry_price} ({selected.entry_time?.slice(11, 19)})
                  </span>
                  <span className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono flex items-center gap-1">
                    <ArrowDownLeft className="w-3 h-3 text-red-500" />
                    Exit: {selected.exit_price} ({selected.exit_time?.slice(11, 19)})
                  </span>
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center h-full text-sm text-zinc-400 dark:text-zinc-500">
                Select a trade to view chart
              </div>
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}
