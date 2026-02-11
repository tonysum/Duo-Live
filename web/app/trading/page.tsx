"use client"

import { useEffect, useState, useCallback } from "react"
import { api, Kline, Position, Signal, OrderRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import dynamic from "next/dynamic"
import {
  Crosshair,
  Lock,
  Unlock,
  AlertCircle,
  ArrowUpRight,
  ArrowDownLeft,
  Loader2,
} from "lucide-react"

const TradeChart = dynamic(() => import("@/components/kokonutui/trade-chart"), {
  ssr: false,
})

const INTERVALS = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"]
const LEVERAGES = ["1", "2", "3", "5", "10"]

export default function TradingPage() {
  const [symbol, setSymbol] = useState("BTCUSDT")
  const [searchInput, setSearchInput] = useState("BTCUSDT")
  const [klines, setKlines] = useState<Kline[]>([])
  const [interval, setInterval_] = useState("1h")
  const [positions, setPositions] = useState<Position[]>([])
  const [ticker, setTicker] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [signals, setSignals] = useState<Signal[]>([])

  // Order form
  const [side, setSide] = useState<"SELL" | "BUY">("SELL")
  const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET")
  const [price, setPrice] = useState("")
  const [qtyMode, setQtyMode] = useState<"margin" | "quantity">("margin")
  const [margin, setMargin] = useState("5")
  const [quantity, setQuantity] = useState("")
  const [leverage, setLeverage] = useState("3")
  const [tpPct, setTpPct] = useState("")
  const [slPct, setSlPct] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<string>("")

  // Password lock
  const [unlocked, setUnlocked] = useState(false)
  const [password, setPassword] = useState("")
  const [pwError, setPwError] = useState("")

  const loadChart = useCallback(async () => {
    setLoading(true)
    try {
      const [k, t, p] = await Promise.all([
        api.getKlines(symbol, interval, 500),
        api.getTicker(symbol).catch(() => null),
        api.getPositions().catch(() => []),
      ])
      setKlines(k)
      if (t) setTicker(t.price)
      setPositions(p)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [symbol, interval])

  useEffect(() => {
    loadChart()
    const iv = setInterval(loadChart, 15000)
    return () => clearInterval(iv)
  }, [loadChart])

  // Load signals
  useEffect(() => {
    const sortAndDedup = (raw: Signal[]) => {
      const sorted = [...raw].sort((a, b) => {
        const dayA = a.timestamp.slice(0, 10)
        const dayB = b.timestamp.slice(0, 10)
        if (dayA !== dayB) return dayB.localeCompare(dayA)
        return b.surge_ratio - a.surge_ratio
      })
      const seen = new Set<string>()
      return sorted.filter((s) => {
        const key = `${s.symbol}:${s.timestamp.slice(0, 10)}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
    }
    api
      .getSignals(50)
      .then((s) => setSignals(sortAndDedup(s)))
      .catch(() => {})
    const iv = setInterval(() => {
      api
        .getSignals(50)
        .then((s) => setSignals(sortAndDedup(s)))
        .catch(() => {})
    }, 30000)
    return () => clearInterval(iv)
  }, [])

  const handleSearch = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") setSymbol(searchInput.toUpperCase())
  }

  const handleSignalClick = (sig: Signal) => {
    const sym = sig.symbol.toUpperCase()
    setSymbol(sym)
    setSearchInput(sym)
    setSide("SELL")
  }

  const handleSubmit = async () => {
    const qtyLabel =
      qtyMode === "margin"
        ? `Margin: ${margin} USDT x ${leverage}x`
        : `Qty: ${quantity}`
    if (
      !confirm(
        `Confirm ${side === "SELL" ? "SHORT" : "LONG"} ${symbol}?\n${qtyLabel}`
      )
    )
      return

    setSubmitting(true)
    setResult("")
    try {
      const order: OrderRequest = {
        symbol,
        side,
        order_type: orderType,
        leverage: parseInt(leverage),
        trading_password: password,
      }
      if (qtyMode === "margin") order.margin_usdt = parseFloat(margin)
      else order.quantity = parseFloat(quantity)
      if (orderType === "LIMIT" && price) order.price = parseFloat(price)
      if (tpPct) order.tp_pct = parseFloat(tpPct)
      if (slPct) order.sl_pct = parseFloat(slPct)

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await api.placeOrder(order) as any
      setResult(`Order placed (ID: ${res.order_id})`)
      loadChart()
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Order failed"
      setResult(`Failed: ${message}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Layout>
      <div className="space-y-4">
        <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
          <Crosshair className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
          Manual Trade
        </h1>

        {/* Signal ribbon */}
        <div
          className={cn(
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl p-3",
            "overflow-x-auto"
          )}
        >
          <div className="flex items-center gap-2 min-w-max">
            <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400 shrink-0">
              Signals
            </span>
            {signals.length === 0 ? (
              <span className="text-[11px] text-zinc-400">No signals</span>
            ) : (
              signals.map((sig, i) => {
                const isActive = sig.symbol === symbol
                return (
                  <button
                    key={`${sig.symbol}-${i}`}
                    type="button"
                    onClick={() => handleSignalClick(sig)}
                    className={cn(
                      "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-all whitespace-nowrap",
                      isActive
                        ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                        : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
                    )}
                  >
                    {sig.symbol.replace("USDT", "")}
                    <span className="text-amber-500 dark:text-amber-400">
                      {sig.surge_ratio.toFixed(1)}x
                    </span>
                    <span
                      className={cn("text-[10px]", {
                        "text-emerald-500": sig.accepted,
                        "text-red-500": !sig.accepted,
                      })}
                    >
                      {sig.accepted ? "Pass" : "Rej"}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        </div>

        {/* Main layout */}
        <div
          className="flex flex-col lg:flex-row gap-4"
          style={{ height: "calc(100vh - 280px)" }}
        >
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
            {/* Chart header */}
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
            <div className="flex-1 p-2 min-h-[300px]">
              {loading && klines.length === 0 ? (
                <div className="flex items-center justify-center h-full text-sm text-zinc-400">
                  Loading...
                </div>
              ) : (
                <TradeChart klines={klines} />
              )}
            </div>

            {/* Positions bar */}
            {positions.length > 0 && (
              <div className="p-3 border-t border-zinc-100 dark:border-zinc-800 flex items-center gap-4 overflow-x-auto">
                <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400 shrink-0">
                  Positions
                </span>
                {positions.map((p, idx) => (
                  <div
                    key={`${p.symbol}-${idx}`}
                    className="flex items-center gap-2 shrink-0"
                  >
                    <span className="text-[11px] font-medium text-zinc-900 dark:text-zinc-100">
                      {p.symbol}
                    </span>
                    <span
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded-full font-medium",
                        {
                          "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                            p.side === "LONG",
                          "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                            p.side === "SHORT",
                        }
                      )}
                    >
                      {p.side}
                    </span>
                    <span
                      className={cn("text-[11px] font-mono font-medium", {
                        "text-emerald-600 dark:text-emerald-400":
                          p.unrealized_pnl >= 0,
                        "text-red-600 dark:text-red-400":
                          p.unrealized_pnl < 0,
                      })}
                    >
                      {p.unrealized_pnl >= 0 ? "+" : ""}
                      {p.unrealized_pnl.toFixed(4)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Order panel */}
          <div
            className={cn(
              "w-full lg:w-80 shrink-0",
              "bg-white dark:bg-zinc-900/70",
              "border border-zinc-100 dark:border-zinc-800",
              "rounded-xl shadow-sm backdrop-blur-xl",
              "overflow-y-auto"
            )}
          >
            <div className="p-4">
              {!unlocked ? (
                /* Password lock */
                <div className="flex flex-col items-center gap-3 py-8">
                  <div className="p-3 rounded-full bg-zinc-100 dark:bg-zinc-800">
                    <Lock className="w-5 h-5 text-zinc-500 dark:text-zinc-400" />
                  </div>
                  <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                    Trading Locked
                  </h3>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400 text-center">
                    Enter password to unlock
                  </p>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value)
                      setPwError("")
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && password) {
                        setUnlocked(true)
                        setPwError("")
                      }
                    }}
                    placeholder="Trading password"
                    className="w-full px-3 py-2 rounded-lg text-xs text-center bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (password) {
                        setUnlocked(true)
                        setPwError("")
                      } else {
                        setPwError("Please enter password")
                      }
                    }}
                    className={cn(
                      "w-full py-2 rounded-lg text-xs font-medium",
                      "bg-zinc-900 dark:bg-zinc-50",
                      "text-zinc-50 dark:text-zinc-900",
                      "hover:bg-zinc-800 dark:hover:bg-zinc-200",
                      "transition-colors"
                    )}
                  >
                    Unlock
                  </button>
                  {pwError && (
                    <span className="text-xs text-red-500">{pwError}</span>
                  )}
                </div>
              ) : (
                /* Order form */
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      Place Order
                    </h3>
                    <button
                      type="button"
                      onClick={() => {
                        setUnlocked(false)
                        setPassword("")
                      }}
                      className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                    >
                      <Unlock className="w-3.5 h-3.5 text-zinc-400" />
                    </button>
                  </div>

                  {/* Symbol + price */}
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                      {symbol}
                    </span>
                    {ticker && (
                      <span className="text-xs font-mono text-zinc-500 dark:text-zinc-400">
                        ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                      </span>
                    )}
                  </div>

                  {/* Side selector */}
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setSide("BUY")}
                      className={cn(
                        "py-2 rounded-lg text-xs font-medium transition-colors",
                        side === "BUY"
                          ? "bg-emerald-500 text-zinc-900"
                          : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      )}
                    >
                      <ArrowUpRight className="w-3.5 h-3.5 inline mr-1" />
                      LONG
                    </button>
                    <button
                      type="button"
                      onClick={() => setSide("SELL")}
                      className={cn(
                        "py-2 rounded-lg text-xs font-medium transition-colors",
                        side === "SELL"
                          ? "bg-red-500 text-zinc-900"
                          : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      )}
                    >
                      <ArrowDownLeft className="w-3.5 h-3.5 inline mr-1" />
                      SHORT
                    </button>
                  </div>

                  {/* Order type */}
                  <div className="grid grid-cols-2 gap-2">
                    {(["MARKET", "LIMIT"] as const).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setOrderType(t)}
                        className={cn(
                          "py-1.5 rounded-md text-[11px] font-medium transition-colors",
                          orderType === t
                            ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                            : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                        )}
                      >
                        {t}
                      </button>
                    ))}
                  </div>

                  {/* Limit price */}
                  {orderType === "LIMIT" && (
                    <div className="space-y-1.5">
                      <label className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                        Price
                      </label>
                      <input
                        type="number"
                        value={price}
                        onChange={(e) => setPrice(e.target.value)}
                        placeholder={ticker ? ticker.toString() : "Price"}
                        className="w-full px-3 py-2 rounded-lg text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                      />
                    </div>
                  )}

                  {/* Qty mode */}
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setQtyMode("margin")}
                      className={cn(
                        "py-1.5 rounded-md text-[11px] font-medium transition-colors",
                        qtyMode === "margin"
                          ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                          : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      )}
                    >
                      Margin
                    </button>
                    <button
                      type="button"
                      onClick={() => setQtyMode("quantity")}
                      className={cn(
                        "py-1.5 rounded-md text-[11px] font-medium transition-colors",
                        qtyMode === "quantity"
                          ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                          : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                      )}
                    >
                      Quantity
                    </button>
                  </div>

                  {/* Margin / Quantity input */}
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                      {qtyMode === "margin" ? "Margin (USDT)" : "Quantity"}
                    </label>
                    {qtyMode === "margin" ? (
                      <input
                        type="number"
                        value={margin}
                        onChange={(e) => setMargin(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                      />
                    ) : (
                      <input
                        type="number"
                        value={quantity}
                        onChange={(e) => setQuantity(e.target.value)}
                        placeholder="Contract quantity"
                        className="w-full px-3 py-2 rounded-lg text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                      />
                    )}
                  </div>

                  {/* Leverage */}
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                      Leverage
                    </label>
                    <div className="grid grid-cols-5 gap-1.5">
                      {LEVERAGES.map((l) => (
                        <button
                          key={l}
                          type="button"
                          onClick={() => setLeverage(l)}
                          className={cn(
                            "py-1.5 rounded-md text-[11px] font-medium transition-colors",
                            leverage === l
                              ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                              : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400"
                          )}
                        >
                          {l}x
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* TP/SL */}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <label className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                        TP %
                      </label>
                      <input
                        type="number"
                        value={tpPct}
                        onChange={(e) => setTpPct(e.target.value)}
                        placeholder="e.g. 33"
                        className="w-full px-3 py-2 rounded-lg text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                        SL %
                      </label>
                      <input
                        type="number"
                        value={slPct}
                        onChange={(e) => setSlPct(e.target.value)}
                        placeholder="e.g. 18"
                        className="w-full px-3 py-2 rounded-lg text-xs bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 outline-none focus:ring-1 focus:ring-zinc-400"
                      />
                    </div>
                  </div>

                  {/* Submit */}
                  <button
                    type="button"
                    onClick={handleSubmit}
                    disabled={submitting}
                    className={cn(
                      "w-full py-2.5 rounded-lg text-xs font-medium transition-all",
                      "disabled:opacity-40 disabled:cursor-not-allowed",
                      side === "BUY"
                        ? "bg-emerald-500 hover:bg-emerald-600 text-zinc-900"
                        : "bg-red-500 hover:bg-red-600 text-zinc-900"
                    )}
                  >
                    {submitting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin inline mr-1" />
                    ) : null}
                    {submitting
                      ? "Placing..."
                      : `${side === "SELL" ? "SHORT" : "LONG"} ${symbol}`}
                  </button>

                  {/* Result */}
                  {result && (
                    <div
                      className={cn(
                        "p-2.5 rounded-lg text-xs",
                        result.includes("Failed")
                          ? "bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400"
                          : "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400"
                      )}
                    >
                      {result}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  )
}
