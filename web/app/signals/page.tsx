"use client"

import { useEffect, useState, useMemo } from "react"
import { api, Signal } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
  Radio,
  AlertCircle,
  CheckCircle2,
  XCircle,
  ChevronLeft,
  ChevronRight,
} from "lucide-react"

const PAGE_SIZE = 20

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [tickers, setTickers] = useState<Record<string, { price: number; change_pct: number }>>({})
  const [error, setError] = useState("")
  const [page, setPage] = useState(0)

  useEffect(() => {
    const fetchAll = () => {
      api
        .getSignals(2000)
        .then(setSignals)
        .catch((e) => setError(e.message))
      api.getTickers().then(setTickers).catch(() => { })
    }
    fetchAll()
    const iv = setInterval(fetchAll, 10000)
    return () => clearInterval(iv)
  }, [])

  // Sort & dedup
  const deduped = useMemo(() => {
    const sorted = [...signals].sort((a, b) => {
      const dayA = a.timestamp.slice(0, 10)
      const dayB = b.timestamp.slice(0, 10)
      if (dayA !== dayB) return dayB.localeCompare(dayA)
      // Same day: accepted first, then by surge_ratio
      if (a.accepted !== b.accepted) return a.accepted ? -1 : 1
      return b.surge_ratio - a.surge_ratio
    })

    const seen = new Set<string>()
    return sorted.filter((s) => {
      const key = `${s.symbol}:${s.timestamp.slice(0, 10)}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [signals])

  const totalPages = Math.max(1, Math.ceil(deduped.length / PAGE_SIZE))
  const paged = useMemo(
    () => deduped.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [deduped, page]
  )

  // Reset page when data changes significantly
  useEffect(() => {
    if (page >= totalPages) setPage(Math.max(0, totalPages - 1))
  }, [totalPages, page])

  const accepted = signals.filter((s) => s.accepted)
  const rejected = signals.filter((s) => !s.accepted)

  return (
    <Layout>
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
              <Radio className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
              Signal Events
              <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                ({deduped.length})
              </span>
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="w-3 h-3" />
              Accepted {accepted.length}
            </span>
            <span className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400">
              <XCircle className="w-3 h-3" />
              Rejected {rejected.length}
            </span>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <AlertCircle className="w-4 h-4 text-red-500" />
            <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
          </div>
        )}

        {/* Signal table */}
        <div
          className={cn(
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl shadow-sm backdrop-blur-xl",
            "overflow-hidden"
          )}
        >
          {deduped.length === 0 ? (
            <div className="flex items-center justify-center py-16 text-sm text-zinc-400 dark:text-zinc-500">
              No signal data
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-zinc-100 dark:border-zinc-800">
                      {[
                        "Time",
                        "Symbol",
                        "Surge",
                        "Signal Price",
                        "Live Price",
                        "Change",
                        "Status",
                        "Reject Reason",
                      ].map((h) => (
                        <th
                          key={h}
                          className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((s, i) => {
                      const ticker = tickers[s.symbol]
                      const curPrice = ticker?.price
                      const changePct = ticker?.change_pct

                      return (
                        <tr
                          key={`${s.symbol}-${i}`}
                          className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors"
                        >
                          <td className="px-4 py-3 text-[11px] text-zinc-500 dark:text-zinc-400 font-mono whitespace-nowrap">
                            {s.timestamp.slice(0, 19).replace("T", " ")}
                          </td>
                          <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">
                            {s.symbol}
                          </td>
                          <td className="px-4 py-3">
                            <span className="text-xs font-medium font-mono text-amber-600 dark:text-amber-400">
                              {s.surge_ratio.toFixed(1)}x
                            </span>
                          </td>
                          <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                            {s.price}
                          </td>
                          <td className="px-4 py-3 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                            {curPrice != null ? curPrice : "---"}
                          </td>
                          <td className="px-4 py-3">
                            <span
                              className={cn("text-xs font-medium font-mono", {
                                "text-emerald-600 dark:text-emerald-400":
                                  changePct != null && changePct >= 0,
                                "text-red-600 dark:text-red-400":
                                  changePct != null && changePct < 0,
                                "text-zinc-400": changePct == null,
                              })}
                            >
                              {changePct != null
                                ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`
                                : "---"}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <span
                              className={cn(
                                "inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full",
                                {
                                  "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                                    s.accepted,
                                  "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                                    !s.accepted,
                                }
                              )}
                            >
                              {s.accepted ? (
                                <CheckCircle2 className="w-3 h-3" />
                              ) : (
                                <XCircle className="w-3 h-3" />
                              )}
                              {s.accepted ? "Accepted" : "Rejected"}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-[11px] text-zinc-500 dark:text-zinc-400 max-w-[200px] truncate">
                            {s.reject_reason || "---"}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between px-4 py-3 border-t border-zinc-100 dark:border-zinc-800">
                  <span className="text-[11px] text-zinc-400">
                    {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, deduped.length)} of {deduped.length}
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.max(0, p - 1))}
                      disabled={page === 0}
                      className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-30 transition-colors"
                    >
                      <ChevronLeft className="w-4 h-4 text-zinc-500" />
                    </button>
                    {Array.from({ length: totalPages }, (_, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => setPage(i)}
                        className={cn(
                          "w-7 h-7 rounded text-[11px] font-medium transition-colors",
                          page === i
                            ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                            : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                        )}
                      >
                        {i + 1}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                      disabled={page >= totalPages - 1}
                      className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-30 transition-colors"
                    >
                      <ChevronRight className="w-4 h-4 text-zinc-500" />
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Layout>
  )
}
