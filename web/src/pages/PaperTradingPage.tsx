import { useEffect, useState, useCallback } from "react"
import { api, PaperStats, PaperPosition, PaperPending, PaperTrade, PaperSignal, PaperStatus, PaperWsStatus } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
    Play,
    Square,
    Loader2,
    TrendingUp,
    TrendingDown,
    Wallet,
    Target,
    BarChart3,
    Clock,
    Wifi,
    WifiOff,
    Activity,
    ArrowUpRight,
    ArrowDownLeft,
    AlertCircle,
    Download,
} from "lucide-react"

function StatCard({ label, value, icon: Icon, color = "default" }: {
    label: string
    value: string | number
    icon: React.ElementType
    color?: "green" | "red" | "yellow" | "accent" | "default"
}) {
    const colorMap = {
        green: "text-emerald-600 dark:text-emerald-400",
        red: "text-red-600 dark:text-red-400",
        yellow: "text-amber-600 dark:text-amber-400",
        accent: "text-indigo-600 dark:text-indigo-400",
        default: "text-zinc-900 dark:text-zinc-100",
    }
    return (
        <div className={cn(
            "bg-white dark:bg-zinc-900/70",
            "border border-zinc-100 dark:border-zinc-800",
            "rounded-xl p-4 shadow-sm backdrop-blur-xl",
        )}>
            <div className="flex items-center gap-2 mb-2">
                <Icon className="w-3.5 h-3.5 text-zinc-400 dark:text-zinc-500" />
                <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                    {label}
                </span>
            </div>
            <div className={cn("text-xl font-bold font-mono", colorMap[color])}>
                {value}
            </div>
        </div>
    )
}

function StateBadge({ state }: { state: string }) {
    const styles: Record<string, string> = {
        searching: "bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400",
        cooling: "bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400",
        consolidating: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400",
        monitoring: "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400",
    }
    return (
        <span className={cn(
            "text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase",
            styles[state] || "bg-zinc-100 dark:bg-zinc-800 text-zinc-500"
        )}>
            {state}
        </span>
    )
}

export default function PaperTradingPage() {
    const [status, setStatus] = useState<PaperStatus | null>(null)
    const [stats, setStats] = useState<PaperStats | null>(null)
    const [positions, setPositions] = useState<PaperPosition[]>([])
    const [pending, setPending] = useState<PaperPending[]>([])
    const [trades, setTrades] = useState<PaperTrade[]>([])
    const [signals, setSignals] = useState<PaperSignal[]>([])
    const [wsStatus, setWsStatus] = useState<PaperWsStatus | null>(null)
    const [toggling, setToggling] = useState(false)
    const [error, setError] = useState("")

    const fetchData = useCallback(async () => {
        try {
            const [st, ss, pos, tr, sig, ws] = await Promise.all([
                api.paper.getStatus(),
                api.paper.getStats(),
                api.paper.getPositions(),
                api.paper.getTrades(),
                api.paper.getSignals(),
                api.paper.getWsStatus(),
            ])
            setStatus(st)
            setStats(ss)
            setPositions(pos.positions || [])
            setPending(pos.pending || [])
            setTrades(tr)
            setSignals(sig)
            setWsStatus(ws)
            setError("")
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Failed to fetch"
            setError(message)
        }
    }, [])

    useEffect(() => {
        fetchData()
        const iv = setInterval(fetchData, 10000)
        return () => clearInterval(iv)
    }, [fetchData])

    const handleToggle = async () => {
        setToggling(true)
        try {
            if (status?.running) {
                await api.paper.stop()
            } else {
                await api.paper.start()
            }
            await fetchData()
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : "Toggle failed"
            alert(message)
        } finally {
            setToggling(false)
        }
    }

    const isRunning = status?.running ?? false

    return (
        <Layout>
            <div className="space-y-6">
                {/* Header */}
                <div className="flex items-center justify-between">
                    <div>
                        <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
                            <Activity className="w-4 h-4" />
                            Paper Trading
                            <span className={cn(
                                "text-[10px] font-medium px-2 py-0.5 rounded-full uppercase",
                                isRunning
                                    ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                    : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500"
                            )}>
                                {isRunning ? "Running" : "Stopped"}
                            </span>
                        </h1>
                    </div>
                    <button
                        type="button"
                        onClick={handleToggle}
                        disabled={toggling}
                        className={cn(
                            "inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200",
                            "disabled:opacity-50 disabled:cursor-not-allowed",
                            isRunning
                                ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50"
                                : "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-200 dark:hover:bg-emerald-900/50"
                        )}
                    >
                        {toggling ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                        ) : isRunning ? (
                            <Square className="w-4 h-4" />
                        ) : (
                            <Play className="w-4 h-4" />
                        )}
                        {isRunning ? "Stop" : "Start"}
                    </button>
                </div>

                {error && (
                    <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
                        <AlertCircle className="w-4 h-4 text-red-500" />
                        <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
                    </div>
                )}

                {/* Stats Grid */}
                {stats && !("error" in stats) && (
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
                        <StatCard
                            label="Capital"
                            value={`$${stats.current_capital.toLocaleString()}`}
                            icon={Wallet}
                            color={stats.current_capital >= stats.initial_capital ? "green" : "red"}
                        />
                        <StatCard
                            label="Total P&L"
                            value={`${stats.total_pnl >= 0 ? "+" : ""}$${stats.total_pnl.toFixed(2)}`}
                            icon={stats.total_pnl >= 0 ? TrendingUp : TrendingDown}
                            color={stats.total_pnl >= 0 ? "green" : "red"}
                        />
                        <StatCard
                            label="Win Rate"
                            value={`${stats.win_rate}%`}
                            icon={Target}
                            color="accent"
                        />
                        <StatCard
                            label="Trades"
                            value={stats.total_trades}
                            icon={BarChart3}
                        />
                        <StatCard
                            label="Positions"
                            value={`${stats.open_positions}/${stats.max_positions}`}
                            icon={Activity}
                            color="yellow"
                        />
                        <StatCard
                            label="WS"
                            value={wsStatus ? `${wsStatus.connected}/${wsStatus.total}` : "—"}
                            icon={wsStatus && wsStatus.connected > 0 ? Wifi : WifiOff}
                            color={wsStatus && wsStatus.connected > 0 ? "green" : "red"}
                        />
                        <StatCard
                            label="Uptime"
                            value={status?.uptime_display || "—"}
                            icon={Clock}
                            color="accent"
                        />
                    </div>
                )}

                {/* Positions */}
                <div>
                    <h2 className="text-sm font-semibold text-zinc-900 dark:text-white mb-3 flex items-center gap-2">
                        💼 Positions ({positions.length}) & Pending ({pending.length})
                    </h2>
                    <div className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl overflow-hidden"
                    )}>
                        {positions.length === 0 && pending.length === 0 ? (
                            <div className="flex items-center justify-center py-12 text-sm text-zinc-400 dark:text-zinc-500">
                                No positions or pending orders
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                {positions.length > 0 && (
                                    <table className="w-full">
                                        <thead>
                                            <tr className="border-b border-zinc-100 dark:border-zinc-800">
                                                {["Symbol", "Dir", "Entry", "TP", "SL", "Size", "P&L", "Hold"].map(h => (
                                                    <th key={h} className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                                                        {h}
                                                    </th>
                                                ))}
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {positions.map((p, i) => (
                                                <tr key={`${p.symbol}-${i}`} className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors">
                                                    <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">{p.symbol}</td>
                                                    <td className="px-4 py-3">
                                                        <span className={cn(
                                                            "inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full",
                                                            p.direction === "long"
                                                                ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                                                : "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                                                        )}>
                                                            {p.direction === "long" ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownLeft className="w-3 h-3" />}
                                                            {p.direction.toUpperCase()}
                                                        </span>
                                                    </td>
                                                    <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">${p.entry_price.toFixed(4)}</td>
                                                    <td className="px-4 py-3 text-xs font-mono text-emerald-600 dark:text-emerald-400">${p.tp_price.toFixed(4)}</td>
                                                    <td className="px-4 py-3 text-xs font-mono text-red-600 dark:text-red-400">${p.sl_price.toFixed(4)}</td>
                                                    <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">${p.size_usdt}</td>
                                                    <td className="px-4 py-3">
                                                        <span className={cn("text-xs font-medium font-mono", p.unrealized_pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                                                            {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(2)}
                                                        </span>
                                                    </td>
                                                    <td className="px-4 py-3 text-xs text-zinc-500 font-mono">{p.hold_hours}h</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                                {pending.length > 0 && (
                                    <>
                                        <div className="px-4 py-2 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 bg-zinc-50 dark:bg-zinc-800/30 border-b border-zinc-100 dark:border-zinc-800">
                                            Pending Orders ({pending.length})
                                        </div>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 p-3">
                                            {pending.map((o, i) => (
                                                <div key={`${o.symbol}-${o.direction}-${i}`} className={cn(
                                                    "px-3 py-2 rounded-lg border",
                                                    "border-zinc-100 dark:border-zinc-800",
                                                    "bg-zinc-50 dark:bg-zinc-800/30"
                                                )}>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100">{o.symbol}</span>
                                                        <span className={cn(
                                                            "text-[10px] font-medium px-1.5 py-0.5 rounded-full",
                                                            o.direction === "long"
                                                                ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                                                : "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                                                        )}>
                                                            {o.direction.toUpperCase()}
                                                        </span>
                                                    </div>
                                                    <div className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1 font-mono">
                                                        Entry: ${o.entry_price.toFixed(4)} | ${o.size_usdt}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </>
                                )}
                            </div>
                        )}
                    </div>
                </div>

                {/* Trade History */}
                <div>
                    <div className="flex items-center justify-between mb-3">
                        <h2 className="text-sm font-semibold text-zinc-900 dark:text-white flex items-center gap-2">
                            📜 Trade History ({trades.length})
                        </h2>
                        {trades.length > 0 && (
                            <a
                                href={`${import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8899`}/api/paper/trades/csv`}
                                download
                                className={cn(
                                    "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200",
                                    "bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400",
                                    "hover:bg-indigo-200 dark:hover:bg-indigo-900/50"
                                )}
                            >
                                <Download className="w-3.5 h-3.5" />
                                导出CSV
                            </a>
                        )}
                    </div>
                    <div className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl overflow-hidden"
                    )}>
                        {trades.length === 0 ? (
                            <div className="flex items-center justify-center py-12 text-sm text-zinc-400 dark:text-zinc-500">
                                No completed trades yet
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead>
                                        <tr className="border-b border-zinc-100 dark:border-zinc-800">
                                            {["Symbol", "Dir", "Entry", "Exit", "P&L", "P&L%", "Hold", "Reason"].map(h => (
                                                <th key={h} className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                                                    {h}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[...trades].reverse().map((t, i) => (
                                            <tr key={i} className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors">
                                                <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">{t.symbol}</td>
                                                <td className="px-4 py-3">
                                                    <span className={cn(
                                                        "text-[10px] font-medium px-2 py-0.5 rounded-full",
                                                        t.direction === "long"
                                                            ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                                            : "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                                                    )}>
                                                        {t.direction.toUpperCase()}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">${t.entry_price.toFixed(4)}</td>
                                                <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">${t.exit_price.toFixed(4)}</td>
                                                <td className="px-4 py-3">
                                                    <span className={cn("text-xs font-medium font-mono", t.pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                                                        {t.pnl >= 0 ? "+" : ""}${t.pnl}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3">
                                                    <span className={cn("text-xs font-mono", t.pnl_pct >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                                                        {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct}%
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-xs text-zinc-500 font-mono">{t.hold_hours}h</td>
                                                <td className="px-4 py-3">
                                                    <span className={cn(
                                                        "text-[10px] font-medium px-2 py-0.5 rounded-full",
                                                        t.exit_reason === "tp" ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                                            : t.exit_reason === "sl" ? "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                                                                : "bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400"
                                                    )}>
                                                        {t.exit_reason === "tp" ? "🟢 TP" : t.exit_reason === "sl" ? "🔴 SL" : "⏰ Timeout"}
                                                    </span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </div>

                {/* Signals */}
                <div>
                    <h2 className="text-sm font-semibold text-zinc-900 dark:text-white mb-3 flex items-center gap-2">
                        📡 Signals ({signals.length})
                    </h2>
                    <div className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl overflow-hidden"
                    )}>
                        {signals.length === 0 ? (
                            <div className="flex items-center justify-center py-12 text-sm text-zinc-400 dark:text-zinc-500">
                                No signals detected yet
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead>
                                        <tr className="border-b border-zinc-100 dark:border-zinc-800">
                                            {["Time", "Symbol", "Dir", "Entry", "TP", "SL", "Price", "Level"].map(h => (
                                                <th key={h} className="text-left px-4 py-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                                                    {h}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[...signals].reverse().map((s, i) => (
                                            <tr key={i} className="border-b border-zinc-50 dark:border-zinc-800/50 hover:bg-zinc-50 dark:hover:bg-zinc-800/30 transition-colors">
                                                <td className="px-4 py-3 text-xs text-zinc-500">
                                                    {new Date(s.signal_time).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                                                </td>
                                                <td className="px-4 py-3 text-xs font-medium text-zinc-900 dark:text-zinc-100">{s.symbol}</td>
                                                <td className="px-4 py-3">
                                                    <span className={cn(
                                                        "text-[10px] font-medium px-2 py-0.5 rounded-full",
                                                        s.direction === "long"
                                                            ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400"
                                                            : "bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400"
                                                    )}>
                                                        {s.direction.toUpperCase()}
                                                    </span>
                                                </td>
                                                <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">${s.entry_price.toFixed(4)}</td>
                                                <td className="px-4 py-3 text-xs font-mono text-emerald-600 dark:text-emerald-400">${s.tp_price.toFixed(4)}</td>
                                                <td className="px-4 py-3 text-xs font-mono text-red-600 dark:text-red-400">${s.sl_price.toFixed(4)}</td>
                                                <td className="px-4 py-3 text-xs font-mono text-zinc-600 dark:text-zinc-400">
                                                    {s.current_price ? `$${s.current_price.toFixed(4)}` : "—"}
                                                </td>
                                                <td className="px-4 py-3 text-xs text-zinc-500">{s.state}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </div>

                {/* State Machine & WS Status */}
                {status?.state_info && Object.keys(status.state_info).length > 0 && (
                    <div>
                        <h2 className="text-sm font-semibold text-zinc-900 dark:text-white mb-3 flex items-center gap-2">
                            🔌 State Machine ({Object.keys(status.state_info).length} symbols)
                        </h2>
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                            {Object.entries(status.state_info).map(([sym, info]) => (
                                <div key={sym} className={cn(
                                    "px-3 py-2.5 rounded-lg border",
                                    "border-zinc-100 dark:border-zinc-800",
                                    "bg-white dark:bg-zinc-900/70",
                                    "hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
                                )}>
                                    <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">{sym.replace("USDT", "")}</span>
                                        <StateBadge state={info.state} />
                                    </div>
                                    {info.base_price && (
                                        <div className="text-[11px] text-zinc-500 font-mono">
                                            ${Number(info.base_price).toFixed(4)}
                                        </div>
                                    )}
                                    {wsStatus?.symbols?.[sym] && (
                                        <div className="flex items-center gap-1 mt-1">
                                            <div className={cn(
                                                "w-1.5 h-1.5 rounded-full",
                                                wsStatus.symbols[sym].status === "connected" ? "bg-emerald-500" :
                                                    wsStatus.symbols[sym].status === "connecting" ? "bg-amber-500 animate-pulse" : "bg-red-500"
                                            )} />
                                            <span className="text-[10px] text-zinc-400">
                                                {wsStatus.symbols[sym].rows || 0} rows
                                            </span>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Uptime */}
                {status?.start_time && (
                    <div className="text-right text-[11px] text-zinc-400 dark:text-zinc-500 flex items-center justify-end gap-1">
                        <Clock className="w-3 h-3" />
                        Started: {new Date(status.start_time).toLocaleString("zh-CN")}
                    </div>
                )}
            </div>
        </Layout >
    )
}
