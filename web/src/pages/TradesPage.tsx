import { useEffect, useState, lazy, Suspense } from "react"
import { api, LiveTrade, Kline } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
    BarChart2,
    ArrowUpRight,
    ArrowDownLeft,
    AlertCircle,
} from "lucide-react"

const TradeChart = lazy(() => import("@/components/kokonutui/trade-chart"))

const INTERVALS = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"]

function tradeKey(t: LiveTrade): string {
    return `${t.symbol}|${t.entry_time}|${t.exit_time}`
}

/** 展示 ISO UTC，无则 — */
function fmtUtc(iso: string | undefined): string {
    if (!iso) return "—"
    return iso.slice(0, 19).replace("T", " ")
}

/** 价格收益率 %（与后端 return_pct 一致；旧接口无字段时前端推算） */
function priceReturnPct(t: LiveTrade): number {
    if (t.return_pct != null && !Number.isNaN(t.return_pct)) {
        return t.return_pct
    }
    if (t.entry_price <= 0 || t.exit_price <= 0) return 0
    return t.side === "LONG"
        ? ((t.exit_price - t.entry_price) / t.entry_price) * 100
        : ((t.entry_price - t.exit_price) / t.entry_price) * 100
}

export default function TradesPage() {
    const [trades, setTrades] = useState<LiveTrade[]>([])
    const [selectedKey, setSelectedKey] = useState<string | null>(null)
    const selected =
        trades.find((t) => tradeKey(t) === selectedKey) ?? null
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

    const closedTrades = trades.filter((t) => t.exit_time && t.entry_time)

    const totalPnl = trades.reduce((s, t) => s + t.pnl_usdt, 0)
    const wins = trades.filter((t) => t.pnl_usdt > 0).length
    const winRate = trades.length > 0 ? (wins / trades.length) * 100 : 0
    const avgHoldMs =
        closedTrades.length > 0
            ? closedTrades.reduce((s, t) => {
                const ms =
                    new Date(t.exit_time!).getTime() -
                    new Date(t.entry_time!).getTime()
                return s + ms
            }, 0) / closedTrades.length
            : 0
    const avgHoldH = avgHoldMs / 3_600_000
    const selectedReturnPct = selected ? priceReturnPct(selected) : 0

    return (
        <Layout>
            {/* Full-height flex column: fixed header items at top, list+chart fills rest */}
            <div className="flex flex-col gap-3 h-full">
                <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
                    <BarChart2 className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
                    Trade History
                    <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                        ({trades.length})
                    </span>
                </h1>

                {/* ── Summary stats ── */}
                {trades.length > 0 && (
                    <div className="grid grid-cols-3 gap-3">
                        {/* Total PnL */}
                        <div className="bg-white dark:bg-zinc-900/70 border border-zinc-100 dark:border-zinc-800 rounded-xl px-4 py-3 shadow-sm">
                            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mb-0.5">Total PnL</p>
                            <p
                                className={cn("text-base font-semibold font-mono", {
                                    "text-emerald-600 dark:text-emerald-400": totalPnl >= 0,
                                    "text-red-600 dark:text-red-400": totalPnl < 0,
                                })}
                            >
                                {totalPnl >= 0 ? "+" : ""}
                                {totalPnl.toFixed(2)} USDT
                            </p>
                        </div>
                        {/* Win Rate */}
                        <div className="bg-white dark:bg-zinc-900/70 border border-zinc-100 dark:border-zinc-800 rounded-xl px-4 py-3 shadow-sm">
                            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mb-0.5">
                                Win Rate
                            </p>
                            <p className="text-base font-semibold font-mono text-zinc-900 dark:text-zinc-100">
                                {winRate.toFixed(1)}%
                                <span className="text-[11px] font-normal text-zinc-400 ml-1">
                                    ({wins}/{trades.length})
                                </span>
                            </p>
                        </div>
                        {/* Avg Hold Time */}
                        <div className="bg-white dark:bg-zinc-900/70 border border-zinc-100 dark:border-zinc-800 rounded-xl px-4 py-3 shadow-sm">
                            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mb-0.5">
                                Avg Hold
                            </p>
                            <p className="text-base font-semibold font-mono text-zinc-900 dark:text-zinc-100">
                                {avgHoldH >= 24
                                    ? `${(avgHoldH / 24).toFixed(1)}d`
                                    : `${avgHoldH.toFixed(1)}h`}
                            </p>
                        </div>
                    </div>
                )}
                {error && (
                    <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
                        <AlertCircle className="w-4 h-4 text-red-500" />
                        <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
                    </div>
                )}

                <div className="flex flex-col lg:flex-row gap-4 flex-1 min-h-0">
                    {/* Trade table */}
                    <div
                        className={cn(
                            "w-full lg:w-[min(100%,36rem)] shrink-0",
                            "bg-white dark:bg-zinc-900/70",
                            "border border-zinc-100 dark:border-zinc-800",
                            "rounded-xl shadow-sm backdrop-blur-xl",
                            "flex flex-col min-h-0 max-h-[70vh] lg:max-h-none"
                        )}
                    >
                        <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 shrink-0">
                            <h2 className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                                Trades ({trades.length})
                            </h2>
                            <p className="text-[10px] text-zinc-400 dark:text-zinc-500 mt-0.5">
                                时间均为 UTC；收益率=相对入场价的价格涨跌%（含方向）
                            </p>
                        </div>
                        <div className="overflow-auto flex-1 min-h-0">
                            {trades.length === 0 ? (
                                <div className="p-6 text-center text-sm text-zinc-400">
                                    No trades in window
                                </div>
                            ) : (
                                <table className="w-full text-left border-collapse">
                                    <thead>
                                        <tr className="border-b border-zinc-100 dark:border-zinc-800 sticky top-0 bg-white dark:bg-zinc-900/95 z-[1]">
                                            {[
                                                "合约",
                                                "入场",
                                                "出场",
                                                "盈亏 USDT",
                                                "收益率",
                                            ].map((h) => (
                                                <th
                                                    key={h}
                                                    className="px-2 py-2 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400 whitespace-nowrap"
                                                >
                                                    {h}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {trades.map((t, i) => {
                                            const key = tradeKey(t)
                                            const isSel = selectedKey === key
                                            const ret = priceReturnPct(t)
                                            return (
                                                <tr
                                                    key={`${key}-${i}`}
                                                    role="button"
                                                    tabIndex={0}
                                                    onClick={() =>
                                                        setSelectedKey(key)
                                                    }
                                                    onKeyDown={(e) => {
                                                        if (
                                                            e.key === "Enter" ||
                                                            e.key === " "
                                                        ) {
                                                            e.preventDefault()
                                                            setSelectedKey(key)
                                                        }
                                                    }}
                                                    className={cn(
                                                        "border-b border-zinc-50 dark:border-zinc-800/80 cursor-pointer transition-colors",
                                                        isSel
                                                            ? "bg-zinc-100 dark:bg-zinc-800/70"
                                                            : "hover:bg-zinc-50 dark:hover:bg-zinc-800/30"
                                                    )}
                                                >
                                                    <td className="px-2 py-2 align-top">
                                                        <div className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                                                            {t.symbol}
                                                        </div>
                                                        <span
                                                            className={cn(
                                                                "inline-block text-[10px] font-medium px-1.5 py-0.5 rounded-full mt-0.5",
                                                                {
                                                                    "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                                                                        t.side ===
                                                                        "LONG",
                                                                    "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                                                                        t.side ===
                                                                        "SHORT",
                                                                }
                                                            )}
                                                        >
                                                            {t.side}
                                                        </span>
                                                        <div className="text-[10px] text-zinc-400 font-mono mt-1 tabular-nums">
                                                            {t.entry_price} →{" "}
                                                            {t.exit_price}
                                                        </div>
                                                    </td>
                                                    <td className="px-2 py-2 text-[10px] text-zinc-600 dark:text-zinc-300 font-mono whitespace-nowrap align-top">
                                                        {fmtUtc(t.entry_time)}
                                                    </td>
                                                    <td className="px-2 py-2 text-[10px] text-zinc-600 dark:text-zinc-300 font-mono whitespace-nowrap align-top">
                                                        {fmtUtc(t.exit_time)}
                                                    </td>
                                                    <td
                                                        className={cn(
                                                            "px-2 py-2 text-xs font-semibold font-mono whitespace-nowrap align-top",
                                                            {
                                                                "text-emerald-600 dark:text-emerald-400":
                                                                    t.pnl_usdt >= 0,
                                                                "text-red-600 dark:text-red-400":
                                                                    t.pnl_usdt < 0,
                                                            }
                                                        )}
                                                    >
                                                        {t.pnl_usdt >= 0 ? "+" : ""}
                                                        {t.pnl_usdt.toFixed(2)}
                                                    </td>
                                                    <td
                                                        className={cn(
                                                            "px-2 py-2 text-xs font-mono whitespace-nowrap align-top",
                                                            {
                                                                "text-emerald-600 dark:text-emerald-400":
                                                                    ret >= 0,
                                                                "text-red-600 dark:text-red-400":
                                                                    ret < 0,
                                                            }
                                                        )}
                                                    >
                                                        {ret >= 0 ? "+" : ""}
                                                        {ret.toFixed(2)}%
                                                    </td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            )}
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
                                        <span
                                            className={cn(
                                                "text-xs font-mono",
                                                {
                                                    "text-emerald-600 dark:text-emerald-400":
                                                        selectedReturnPct >= 0,
                                                    "text-red-600 dark:text-red-400":
                                                        selectedReturnPct < 0,
                                                }
                                            )}
                                        >
                                            ({selectedReturnPct >= 0 ? "+" : ""}
                                            {selectedReturnPct.toFixed(2)}%)
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
                                        <Suspense fallback={<div className="flex items-center justify-center h-full text-sm text-zinc-400">Loading chart...</div>}>
                                            <TradeChart
                                                klines={klines}
                                                markers={markers}
                                                entryPrice={selected.entry_price}
                                                exitPrice={selected.exit_price}
                                            />
                                        </Suspense>
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
                                        Entry: {selected.entry_price} ({
                                            (() => {
                                                if (!selected.entry_time) return "N/A"
                                                const entryDay = selected.entry_time.slice(0, 10)
                                                const exitDay = selected.exit_time?.slice(0, 10)
                                                return entryDay === exitDay
                                                    ? selected.entry_time.slice(11, 19)
                                                    : selected.entry_time.slice(0, 19).replace("T", " ")
                                            })()
                                        })
                                    </span>
                                    <span className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono flex items-center gap-1">
                                        <ArrowDownLeft className="w-3 h-3 text-red-500" />
                                        Exit: {selected.exit_price} ({
                                            (() => {
                                                if (!selected.exit_time) return "N/A"
                                                const entryDay = selected.entry_time?.slice(0, 10)
                                                const exitDay = selected.exit_time.slice(0, 10)
                                                return entryDay === exitDay
                                                    ? selected.exit_time.slice(11, 19)
                                                    : selected.exit_time.slice(0, 19).replace("T", " ")
                                            })()
                                        })
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
