"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { api, Kline } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import dynamic from "next/dynamic"
import { LineChart } from "lucide-react"
import { useKlineStream } from "@/hooks/useKlineStream"

const TradeChart = dynamic(() => import("@/components/kokonutui/trade-chart"), {
  ssr: false,
})

const INTERVALS = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"]

export default function ChartPage() {
  const [symbol, setSymbol] = useState("BTCUSDT")
  const [searchInput, setSearchInput] = useState("BTCUSDT")
  const [klines, setKlines] = useState<Kline[]>([])
  const [interval, setInterval_] = useState("1h")
  const [ticker, setTicker] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  // Real-time updater ref
  const updaterRef = useRef<((kline: Kline) => void) | null>(null)

  const loadChart = useCallback(async () => {
    setLoading(true)
    try {
      const [k, t] = await Promise.all([
        api.getKlines(symbol, interval, 500),
        api.getTicker(symbol).catch(() => null),
      ])
      setKlines(k)
      if (t) setTicker(t.price)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [symbol, interval])

  useEffect(() => {
    loadChart()
  }, [loadChart])

  // WebSocket real-time kline updates
  useKlineStream(symbol, interval, (kline) => {
    // Update ticker price
    setTicker(kline.close)
    // Push update to chart via updater
    if (updaterRef.current) {
      updaterRef.current(kline)
    }
  })

  const handleSearch = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") setSymbol(searchInput.toUpperCase())
  }

  return (
    <Layout>
      <div className="space-y-4" style={{ height: "calc(100vh - 140px)" }}>
        <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
          <LineChart className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
          Chart
          <span className="relative flex h-2 w-2 ml-1">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
        </h1>

        <div
          className={cn(
            "flex-1 flex flex-col",
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl shadow-sm backdrop-blur-xl",
            "overflow-hidden"
          )}
          style={{ height: "calc(100% - 48px)" }}
        >
          {/* Header */}
          <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-3">
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value.toUpperCase())}
                onKeyDown={handleSearch}
                placeholder="Symbol..."
                className="w-28 px-2.5 py-1.5 rounded-md text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400 dark:focus:ring-zinc-600"
              />
              {ticker && (
                <span className="text-sm font-semibold font-mono text-zinc-900 dark:text-zinc-100">
                  ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                </span>
              )}
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

          {/* Chart */}
          <div className="flex-1 p-2 min-h-[400px]">
            {loading && klines.length === 0 ? (
              <div className="flex items-center justify-center h-full text-sm text-zinc-400">
                Loading...
              </div>
            ) : (
              <TradeChart
                klines={klines}
                onRealtimeUpdate={(updater) => {
                  updaterRef.current = updater
                }}
              />
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}
