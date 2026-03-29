import { useEffect, useState, useRef, useCallback, useMemo, lazy, Suspense } from "react"
import { api, Position, OpenOrder, Kline } from "@/lib/api"
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
    ChevronRight,
    ChevronDown,
    LineChart,
} from "lucide-react"

const TradeChart = lazy(() => import("@/components/kokonutui/trade-chart"))

const CHART_INTERVALS = ["5m", "15m", "1h", "4h", "1d"]

/** 单次拉取 K 线根数；全量进图，可横向拖拽查看 */
const POSITION_KLINE_LIMIT = 200
/** 初次加载/换周期时视口内大约显示的根数（柱更粗）；拖动画布可看更早已加载数据 */
const CHART_VISIBLE_BARS = 64

function formatMarkPrice(p: number) {
    if (!p || !Number.isFinite(p)) return "—"
    if (p >= 1000) return p.toFixed(2)
    if (p >= 1) return p.toFixed(4)
    return p.toFixed(6)
}

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

/** 后端 ISO 若无时区后缀，按 UTC 解析（避免被当成本地时区） */
function parseUtcEntryMs(iso: string): number {
    let s = iso.trim()
    if (!s) return NaN
    if (!s.includes("T") && s.length >= 10) {
        const rest = s.length > 10 ? s.slice(11).trim() : ""
        s = `${s.slice(0, 10)}T${rest || "00:00:00"}`
    }
    if (!/[zZ]$/.test(s) && !/[+-]\d{2}:?\d{2}$/.test(s)) {
        s = `${s}Z`
    }
    return new Date(s).getTime()
}

function fmtUtcCompact(iso: string | null | undefined) {
    if (!iso) return "—"
    const ms = parseUtcEntryMs(iso)
    if (!Number.isFinite(ms)) return `${iso.slice(0, 19).replace("T", " ")} UTC`
    const d = new Date(ms)
    const y = d.getUTCFullYear()
    const mo = String(d.getUTCMonth() + 1).padStart(2, "0")
    const da = String(d.getUTCDate()).padStart(2, "0")
    const hh = String(d.getUTCHours()).padStart(2, "0")
    const mi = String(d.getUTCMinutes()).padStart(2, "0")
    const ss = String(d.getUTCSeconds()).padStart(2, "0")
    return `${y}-${mo}-${da} ${hh}:${mi}:${ss} UTC`
}

/** K 线序列（时间升序）中，入场时刻所在蜡烛的 open time（秒） */
function barOpenTimeForEntry(entrySec: number, klines: Kline[]): number {
    if (!klines.length || !Number.isFinite(entrySec)) return 0
    let best = klines[0].time
    for (const k of klines) {
        if (k.time <= entrySec) best = k.time
        else break
    }
    return best
}

/** 图表标记上展示的 UTC 时间文案（紧凑，含秒便于与成交对齐） */
function formatEntryTimeInMarkerLabel(iso: string): string {
    const ms = parseUtcEntryMs(iso)
    if (!Number.isFinite(ms)) return ""
    const d = new Date(ms)
    const mo = String(d.getUTCMonth() + 1).padStart(2, "0")
    const da = String(d.getUTCDate()).padStart(2, "0")
    const hh = String(d.getUTCHours()).padStart(2, "0")
    const mi = String(d.getUTCMinutes()).padStart(2, "0")
    const ss = String(d.getUTCSeconds()).padStart(2, "0")
    return `${mo}-${da} ${hh}:${mi}:${ss} UTC`
}

/** 与 live_executor：`LONG` 止盈在上方；`SHORT` 止盈在下方 */
function tpSlFromPct(side: string, entry: number, tpPct: number, slPct: number) {
    if (!Number.isFinite(entry) || entry <= 0) return { tp: 0, sl: 0 }
    const L = side === "LONG"
    const tp = L ? entry * (1 + tpPct / 100) : entry * (1 - tpPct / 100)
    const sl = L ? entry * (1 - slPct / 100) : entry * (1 + slPct / 100)
    return { tp, sl }
}

/** 条件单 + 普通止损/止盈挂单（币安部分类型在非 algo 列表里也能出现） */
function tpSlFromOpenOrders(orders: OpenOrder[], symbol: string) {
    let tp = 0
    let sl = 0
    for (const o of orders) {
        if (o.symbol !== symbol) continue
        const sp = o.stop_price
        if (!sp || sp <= 0 || !Number.isFinite(sp)) continue
        const t = (o.type || "").toUpperCase()
        if (t.includes("TAKE_PROFIT")) {
            tp = sp
        } else if (t.includes("TRAILING")) {
            sl = sp
        } else if (t.includes("STOP")) {
            sl = sp
        }
    }
    return { tp, sl }
}

/** 由触发价反推价格收益率 %（与后端 TP/SL 距离定义一致） */
function pctFromTpSl(
    entry: number,
    side: string,
    tpPrice: number,
    slPrice: number
): { tpPct: number; slPct: number } {
    if (!Number.isFinite(entry) || entry <= 0) return { tpPct: 0, slPct: 0 }
    const L = side === "LONG"
    let tpPctout = 0
    let slPctout = 0
    if (tpPrice > 0) {
        tpPctout = L
            ? ((tpPrice - entry) / entry) * 100
            : ((entry - tpPrice) / entry) * 100
    }
    if (slPrice > 0) {
        slPctout = L
            ? ((entry - slPrice) / entry) * 100
            : ((slPrice - entry) / entry) * 100
    }
    return { tpPct: Math.abs(tpPctout), slPct: Math.abs(slPctout) }
}

function positionChartDerived(p: Position, orders: OpenOrder[]) {
    const entry = p.entry_price
    const { tp: algoTp, sl: algoSl } = tpSlFromOpenOrders(orders, p.symbol)
    const fromPct = tpSlFromPct(p.side, entry, p.tp_pct || 0, p.sl_pct || 0)
    const tpPrice = algoTp > 0 ? algoTp : (p.tp_pct || 0) > 0 ? fromPct.tp : 0
    const slPrice = algoSl > 0 ? algoSl : (p.sl_pct || 0) > 0 ? fromPct.sl : 0
    let tpPct = (p.tp_pct || 0) > 0 ? p.tp_pct! : 0
    let slPct = (p.sl_pct || 0) > 0 ? p.sl_pct! : 0
    const inferred = pctFromTpSl(entry, p.side, tpPrice, slPrice)
    if (tpPct <= 0 && tpPrice > 0) tpPct = inferred.tpPct
    if (slPct <= 0 && slPrice > 0) slPct = inferred.slPct
    return { tpPrice, slPrice, tpPct, slPct }
}

/** 用于稳定 chart 派生：该合约任一触发类挂单变化时更新 */
function ordersKeyForSymbol(orders: OpenOrder[], symbol: string): string {
    return orders
        .filter((o) => o.symbol === symbol)
        .map((o) => `${o.is_algo ? 1 : 0}:${o.type}:${o.stop_price}:${o.id}`)
        .sort()
        .join(";")
}

export default function PositionsPage() {
    const [positions, setPositions] = useState<Position[]>([])
    const [orders, setOrders] = useState<OpenOrder[]>([])
    const [closing, setClosing] = useState<string | null>(null)
    const [error, setError] = useState("")
    /** WebSocket 已连接且收到过 payload 时为 true；断线时用 REST 兜底 */
    const [liveWsOk, setLiveWsOk] = useState(false)
    /** Open Orders 区块默认折叠 */
    const [ordersExpanded, setOrdersExpanded] = useState(false)
    const wsAttemptRef = useRef(0)
    const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
    const [klines, setKlines] = useState<Kline[]>([])
    const [chartInterval, setChartInterval] = useState("1h")
    const [chartLoading, setChartLoading] = useState(false)

    const selectedPosition = useMemo(
        () => positions.find((p) => p.symbol === selectedSymbol) ?? null,
        [positions, selectedSymbol]
    )

    /** 仅随入场/止盈止损参数或条件单变化，避免每次 WS 推送都换新对象导致图表重绘 */
    const chartModelKey = useMemo(() => {
        if (!selectedSymbol) return ""
        const p = positions.find((x) => x.symbol === selectedSymbol)
        if (!p) return ""
        const ok = ordersKeyForSymbol(orders, selectedSymbol)
        const et = p.entry_time ?? ""
        return `${selectedSymbol}|${p.side}|${p.entry_price}|${p.tp_pct}|${p.sl_pct}|${et}|${ok}`
    }, [positions, selectedSymbol, orders])

    type EntryMarker = {
        type: "entry"
        time: number
        price: number
        label: string
    }

    const chartBundle = useMemo(() => {
        if (!chartModelKey || !selectedSymbol) {
            return { model: null as ReturnType<typeof positionChartDerived> | null }
        }
        const p = positions.find((x) => x.symbol === selectedSymbol)
        if (!p) {
            return { model: null }
        }
        const model = positionChartDerived(p, orders)
        return { model }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- 故意只在 chartModelKey 变化时重算，与 WS 数组引用脱钩
    }, [chartModelKey])

    const chartModel = chartBundle.model

    /** 时间与已加载 K 线对齐；标签含 UTC 入场时间 + 价格 */
    const chartEntryMarkers = useMemo((): EntryMarker[] => {
        if (!selectedSymbol || klines.length === 0) return []
        const p = positions.find((x) => x.symbol === selectedSymbol)
        if (!p?.entry_time) return []
        const ms = parseUtcEntryMs(p.entry_time)
        if (!Number.isFinite(ms)) return []
        const entrySec = Math.floor(ms / 1000)
        const barTime = barOpenTimeForEntry(entrySec, klines)
        if (!barTime) return []
        const timePart = formatEntryTimeInMarkerLabel(p.entry_time)
        const pricePart = formatMarkPrice(p.entry_price)
        return [
            {
                type: "entry",
                time: barTime,
                price: p.entry_price,
                label: `入场 ${timePart} @ ${pricePart}`,
            },
        ]
    }, [selectedSymbol, positions, klines])

    useEffect(() => {
        if (!selectedSymbol) return
        if (!positions.some((p) => p.symbol === selectedSymbol)) {
            setSelectedSymbol(null)
        }
    }, [positions, selectedSymbol])

    useEffect(() => {
        if (!selectedSymbol) {
            setKlines([])
            return
        }
        let cancelled = false
        setChartLoading(true)
        api.getKlines(selectedSymbol, chartInterval, POSITION_KLINE_LIMIT)
            .then((data) => {
                if (!cancelled) setKlines(data)
            })
            .catch((err: unknown) => {
                if (!cancelled) {
                    const message = err instanceof Error ? err.message : "Klines failed"
                    setError(message)
                }
            })
            .finally(() => {
                if (!cancelled) setChartLoading(false)
            })
        return () => {
            cancelled = true
        }
    }, [selectedSymbol, chartInterval])

    const fetchOrders = useCallback(async () => {
        try {
            const o = await api.getOrders()
            setOrders(o)
            setError("")
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to fetch"
            setError(message)
        }
    }, [])

    const fetchPositionsRest = useCallback(async () => {
        try {
            const p = await api.getPositions()
            setPositions(p)
            setError("")
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to fetch"
            setError(message)
        }
    }, [])

    // 首屏 + 平仓后刷新
    const fetchAll = useCallback(async () => {
        try {
            const [p, o] = await Promise.all([api.getPositions(), api.getOrders()])
            setPositions(p)
            setOrders(o)
            setError("")
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to fetch"
            setError(message)
        }
    }, [])

    useEffect(() => {
        void fetchAll()
    }, [fetchAll])

    // 挂单仍用 REST 周期拉取（未在 /ws/live 里带 orders）
    useEffect(() => {
        const iv = setInterval(fetchOrders, 5000)
        return () => clearInterval(iv)
    }, [fetchOrders])

    // 持仓：/ws/live 推送；断线时每 5s REST 兜底
    useEffect(() => {
        if (liveWsOk) return
        const iv = setInterval(fetchPositionsRest, 5000)
        return () => clearInterval(iv)
    }, [liveWsOk, fetchPositionsRest])

    useEffect(() => {
        let alive = true
        let ws: WebSocket | null = null
        let reconnectTimer: ReturnType<typeof setTimeout> | undefined

        const connect = () => {
            if (!alive) return
            wsAttemptRef.current += 1
            const url = api.wsLiveUrl()
            try {
                ws = new WebSocket(url)
            } catch {
                setLiveWsOk(false)
                reconnectTimer = setTimeout(connect, 4000)
                return
            }

            ws.onopen = () => {
                if (!alive) return
                wsAttemptRef.current = 0
                setLiveWsOk(true)
            }

            ws.onmessage = (ev) => {
                if (!alive) return
                try {
                    const data = JSON.parse(ev.data as string)
                    if (data.type === "status" && Array.isArray(data.positions)) {
                        setPositions(data.positions as Position[])
                    }
                } catch {
                    /* ignore */
                }
            }

            ws.onerror = () => {
                setLiveWsOk(false)
            }

            ws.onclose = () => {
                setLiveWsOk(false)
                if (!alive) return
                const delay = Math.min(3000 * wsAttemptRef.current, 30_000)
                reconnectTimer = setTimeout(connect, delay)
            }
        }

        connect()

        return () => {
            alive = false
            if (reconnectTimer !== undefined) clearTimeout(reconnectTimer)
            ws?.close()
        }
    }, [])

    const handleClose = async (symbol: string) => {
        if (!confirm(`Confirm close ${symbol}?`)) return
        setClosing(symbol)
        try {
            await api.closePosition(symbol)
            await fetchAll()
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Close failed"
            alert(`Close failed: ${message}`)
        } finally {
            setClosing(null)
        }
    }

    const totalPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0)

    const entryLineTitle = selectedPosition
        ? `入场 ${formatMarkPrice(selectedPosition.entry_price)}`
        : "Entry"
    const tpLineTitle =
        chartModel && chartModel.tpPrice > 0
            ? chartModel.tpPct > 0
                ? `止盈 ${chartModel.tpPct.toFixed(1)}%`
                : "止盈"
            : "TP"
    const slLineTitle =
        chartModel && chartModel.slPrice > 0
            ? chartModel.slPct > 0
                ? `止损 ${chartModel.slPct.toFixed(1)}%`
                : "止损"
            : "SL"

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

                {/* Positions table + K-line chart */}
                {positions.length === 0 ? (
                    <div
                        className={cn(
                            "bg-white dark:bg-zinc-900/70",
                            "border border-zinc-100 dark:border-zinc-800",
                            "rounded-xl shadow-sm backdrop-blur-xl",
                            "overflow-hidden"
                        )}
                    >
                        <div className="flex items-center justify-center py-16 text-sm text-zinc-400 dark:text-zinc-500">
                            No open positions
                        </div>
                    </div>
                ) : (
                    <div className="flex flex-col lg:flex-row gap-4 items-stretch">
                        <div
                            className={cn(
                                "w-full lg:flex-1 min-w-0",
                                "bg-white dark:bg-zinc-900/70",
                                "border border-zinc-100 dark:border-zinc-800",
                                "rounded-xl shadow-sm backdrop-blur-xl",
                                "overflow-hidden"
                            )}
                        >
                            <div className="px-4 py-2 border-b border-zinc-100 dark:border-zinc-800 bg-zinc-50/80 dark:bg-zinc-800/40">
                                <p className="text-[10px] text-zinc-500 dark:text-zinc-400">
                                    点击一行在右侧查看 K 线；绿/黄/橙线分别为入场、止盈、止损（轴标签含目标收益率 %）
                                </p>
                            </div>
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                <thead>
                                    <tr className="border-b border-zinc-100 dark:border-zinc-800">
                                        {[
                                            "Symbol",
                                            "Side",
                                            "Qty",
                                            "Entry",
                                            "Leverage",
                                            "Liq. Price",
                                            "Margin",
                                            "Margin %",
                                            "Mark Price",
                                            "Unrealized PnL",
                                            "Action",
                                        ].map(
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
                                    {positions.map((p) => {
                                        const isSel = selectedSymbol === p.symbol
                                        return (
                                        <tr
                                            key={p.symbol}
                                            role="button"
                                            tabIndex={0}
                                            onClick={() => setSelectedSymbol(p.symbol)}
                                            onKeyDown={(e) => {
                                                if (e.key === "Enter" || e.key === " ") {
                                                    e.preventDefault()
                                                    setSelectedSymbol(p.symbol)
                                                }
                                            }}
                                            className={cn(
                                                "border-b border-zinc-50 dark:border-zinc-800/50 transition-colors cursor-pointer",
                                                isSel
                                                    ? "bg-emerald-50/80 dark:bg-emerald-950/30"
                                                    : "hover:bg-zinc-50 dark:hover:bg-zinc-800/30"
                                            )}
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
                                                        "text-red-600 dark:text-red-400": p.margin_ratio >= 80,
                                                        "text-yellow-600 dark:text-yellow-400": p.margin_ratio >= 50 && p.margin_ratio < 80,
                                                        "text-emerald-600 dark:text-emerald-400": p.margin_ratio < 50,
                                                    })}
                                                >
                                                    {p.margin_ratio > 0 ? `${p.margin_ratio.toFixed(1)}%` : "—"}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3 text-xs text-cyan-600 dark:text-cyan-400 font-mono tabular-nums">
                                                {formatMarkPrice(p.mark_price ?? 0)}
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
                                                    {p.margin > 0 && (
                                                        <span className="ml-1 text-[10px] opacity-70">
                                                            ({(p.unrealized_pnl / p.margin * 100) >= 0 ? "+" : ""}
                                                            {(p.unrealized_pnl / p.margin * 100).toFixed(2)}%)
                                                        </span>
                                                    )}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3">
                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        void handleClose(p.symbol)
                                                    }}
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
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                        </div>

                        <div
                            className={cn(
                                "w-full lg:w-[min(100%,40rem)] shrink-0 flex flex-col min-h-0",
                                "bg-white dark:bg-zinc-900/70",
                                "border border-zinc-100 dark:border-zinc-800",
                                "rounded-xl shadow-sm backdrop-blur-xl",
                                "overflow-hidden"
                            )}
                        >
                            {selectedPosition && chartModel ? (
                                <>
                                    <div className="p-3 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between flex-wrap gap-2">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <LineChart className="w-4 h-4 shrink-0 text-zinc-600 dark:text-zinc-300" />
                                            <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                                                {selectedPosition.symbol}
                                            </span>
                                            <span
                                                className={cn(
                                                    "text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0",
                                                    {
                                                        "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400":
                                                            selectedPosition.side === "LONG",
                                                        "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400":
                                                            selectedPosition.side === "SHORT",
                                                    }
                                                )}
                                            >
                                                {selectedPosition.side}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-1 flex-wrap">
                                            {CHART_INTERVALS.map((iv) => (
                                                <button
                                                    key={iv}
                                                    type="button"
                                                    onClick={() => setChartInterval(iv)}
                                                    className={cn(
                                                        "px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors",
                                                        chartInterval === iv
                                                            ? "bg-zinc-900 dark:bg-zinc-50 text-zinc-50 dark:text-zinc-900"
                                                            : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                                                    )}
                                                >
                                                    {iv}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="flex-1 p-2 min-h-[300px] min-w-0">
                                        {chartLoading ? (
                                            <div className="flex items-center justify-center h-full min-h-[280px] text-sm text-zinc-400">
                                                Loading…
                                            </div>
                                        ) : klines.length > 0 ? (
                                            <Suspense
                                                fallback={
                                                    <div className="flex items-center justify-center h-full min-h-[280px] text-sm text-zinc-400">
                                                        Loading chart…
                                                    </div>
                                                }
                                            >
                                                <div className="h-[min(420px,50vh)] w-full min-h-[280px]">
                                                    <TradeChart
                                                        klines={klines}
                                                        markers={chartEntryMarkers}
                                                        entryPrice={selectedPosition.entry_price}
                                                        tpPrice={
                                                            chartModel.tpPrice > 0
                                                                ? chartModel.tpPrice
                                                                : undefined
                                                        }
                                                        slPrice={
                                                            chartModel.slPrice > 0
                                                                ? chartModel.slPrice
                                                                : undefined
                                                        }
                                                        entryLineTitle={entryLineTitle}
                                                        tpLineTitle={tpLineTitle}
                                                        slLineTitle={slLineTitle}
                                                        barSpacing={22}
                                                        initialVisibleBars={CHART_VISIBLE_BARS}
                                                    />
                                                </div>
                                            </Suspense>
                                        ) : (
                                            <div className="flex items-center justify-center h-full min-h-[280px] text-sm text-zinc-400">
                                                No kline data
                                            </div>
                                        )}
                                    </div>
                                    <div className="p-3 border-t border-zinc-100 dark:border-zinc-800 flex flex-col gap-1.5 text-[11px] text-zinc-600 dark:text-zinc-400 font-mono">
                                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                                            <span>
                                                入场时间（UTC）{" "}
                                                <span className="text-zinc-900 dark:text-zinc-200">
                                                    {fmtUtcCompact(selectedPosition.entry_time)}
                                                </span>
                                            </span>
                                            <span>
                                                入场价{" "}
                                                <span className="text-emerald-600 dark:text-emerald-400">
                                                    {formatMarkPrice(selectedPosition.entry_price)}
                                                </span>
                                            </span>
                                        </div>
                                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                                            {chartModel.tpPrice > 0 ? (
                                                <span>
                                                    止盈触发{" "}
                                                    <span className="text-amber-600 dark:text-amber-400">
                                                        {formatMarkPrice(chartModel.tpPrice)}
                                                    </span>
                                                    {chartModel.tpPct > 0 && (
                                                        <span className="text-zinc-500 ml-1">
                                                            ({chartModel.tpPct.toFixed(2)}%)
                                                        </span>
                                                    )}
                                                </span>
                                            ) : (
                                                <span className="text-zinc-500">止盈：暂无（无挂单或未识别）</span>
                                            )}
                                            {chartModel.slPrice > 0 ? (
                                                <span>
                                                    止损触发{" "}
                                                    <span className="text-orange-600 dark:text-orange-400">
                                                        {formatMarkPrice(chartModel.slPrice)}
                                                    </span>
                                                    {chartModel.slPct > 0 && (
                                                        <span className="text-zinc-500 ml-1">
                                                            ({chartModel.slPct.toFixed(2)}%)
                                                        </span>
                                                    )}
                                                </span>
                                            ) : (
                                                <span className="text-zinc-500">止损：暂无（无挂单或未识别）</span>
                                            )}
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <div className="flex flex-1 flex-col items-center justify-center min-h-[280px] px-4 text-center text-sm text-zinc-400 dark:text-zinc-500">
                                    <LineChart className="w-8 h-8 mb-2 opacity-50" />
                                    点击左侧某一行持仓，查看该合约 K 线与入场 / 止盈 / 止损横线
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {/* Open Orders section — 默认折叠 */}
                <div
                    className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl",
                        "overflow-hidden"
                    )}
                >
                    <button
                        type="button"
                        onClick={() => setOrdersExpanded((e) => !e)}
                        aria-expanded={ordersExpanded}
                        className={cn(
                            "w-full flex items-center justify-between gap-2 px-4 py-3 text-left",
                            "text-lg font-bold text-zinc-900 dark:text-white",
                            "hover:bg-zinc-50 dark:hover:bg-zinc-800/40 transition-colors duration-200",
                            ordersExpanded && "border-b border-zinc-100 dark:border-zinc-800"
                        )}
                    >
                        <span className="flex items-center gap-2 min-w-0">
                            <ClipboardList className="w-4 h-4 shrink-0 text-zinc-900 dark:text-zinc-50" />
                            <span>Open Orders</span>
                            <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                                ({orders.length})
                            </span>
                        </span>
                        {ordersExpanded ? (
                            <ChevronDown className="w-5 h-5 shrink-0 text-zinc-500 dark:text-zinc-400" aria-hidden />
                        ) : (
                            <ChevronRight className="w-5 h-5 shrink-0 text-zinc-500 dark:text-zinc-400" aria-hidden />
                        )}
                    </button>

                    {ordersExpanded &&
                        (orders.length === 0 ? (
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
                        ))}
                </div>
            </div>
        </Layout>
    )
}
