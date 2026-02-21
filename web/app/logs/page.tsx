"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import { api } from "@/lib/api"
import Layout from "@/components/kokonutui/layout"
import { ScrollText, Search, Wifi, WifiOff, Trash2, Download, ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"

// ── Log level coloring ──────────────────────────────────────────────
const LEVEL_COLORS: Record<string, string> = {
    DEBUG: "text-zinc-400 dark:text-zinc-500",
    INFO: "text-zinc-200 dark:text-zinc-300",
    WARNING: "text-yellow-400 dark:text-yellow-300",
    ERROR: "text-red-400 dark:text-red-300",
    CRITICAL: "text-red-500 dark:text-red-400",
}

function getLevel(line: string): string {
    if (line.includes("[CRITICAL]")) return "CRITICAL"
    if (line.includes("[ERROR]")) return "ERROR"
    if (line.includes("[WARNING]")) return "WARNING"
    if (line.includes("[DEBUG]")) return "DEBUG"
    if (line.includes("[INFO]")) return "INFO"
    return "INFO"
}

// Highlight emoji/icons for key events
function lineClass(line: string): string {
    const lv = getLevel(line)
    return LEVEL_COLORS[lv] ?? LEVEL_COLORS.INFO
}

const LEVELS = ["", "DEBUG", "INFO", "WARNING", "ERROR"] as const

export default function LogsPage() {
    const [lines, setLines] = useState<string[]>([])
    const [search, setSearch] = useState("")
    const [levelFilter, setLevelFilter] = useState<string>("")
    const [isLive, setIsLive] = useState(true)
    const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "disconnected">("connecting")
    const [autoScroll, setAutoScroll] = useState(true)
    const bottomRef = useRef<HTMLDivElement>(null)
    const containerRef = useRef<HTMLDivElement>(null)
    const wsRef = useRef<WebSocket | null>(null)

    // Auto-scroll
    useEffect(() => {
        if (autoScroll && bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: "smooth" })
        }
    }, [lines, autoScroll])

    // Detect manual scroll up → disable auto-scroll
    const handleScroll = useCallback(() => {
        const el = containerRef.current
        if (!el) return
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
        setAutoScroll(atBottom)
    }, [])

    // WebSocket live mode
    useEffect(() => {
        if (!isLive) {
            wsRef.current?.close()
            return
        }

        // Evaluate URL at runtime so we get the real server hostname (not SSR localhost)
        const wsUrl = api.wsLogsUrl()
        let ws: WebSocket
        let reconnectTimer: ReturnType<typeof setTimeout>
        // alive flag: prevents StrictMode double-invoke from triggering a ghost reconnect
        // when cleanup runs on a still-CONNECTING socket
        let alive = true

        function connect() {
            if (!alive) return
            setWsStatus("connecting")
            ws = new WebSocket(wsUrl)
            wsRef.current = ws

            ws.onopen = () => { if (alive) setWsStatus("connected") }

            ws.onmessage = (evt) => {
                if (!alive) return
                try {
                    const data = JSON.parse(evt.data)
                    if (data.type === "init") {
                        setLines(data.lines ?? [])
                    } else if (data.type === "append") {
                        setLines(prev => [...prev, ...(data.lines ?? [])])
                    }
                } catch { /* ignore */ }
            }

            ws.onclose = () => {
                if (!alive) return
                setWsStatus("disconnected")
                reconnectTimer = setTimeout(connect, 3000)
            }

            ws.onerror = () => {
                if (ws.readyState !== WebSocket.CLOSED) ws.close()
            }
        }

        connect()
        return () => {
            alive = false
            clearTimeout(reconnectTimer)
            if (ws) {
                ws.onopen = null
                ws.onmessage = null
                ws.onclose = null
                ws.onerror = null
                // Only call close() once actually connected; avoids "closed before established"
                if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CLOSING) {
                    ws.close()
                }
            }
            setWsStatus("disconnected")
        }
    }, [isLive])

    // Static fetch (not live)
    const fetchStatic = useCallback(async () => {
        try {
            const res = await api.getLogs(500, levelFilter, search)
            setLines(res.lines)
        } catch (err) {
            console.error(err)
        }
    }, [levelFilter, search])

    useEffect(() => {
        if (!isLive) fetchStatic()
    }, [isLive, fetchStatic])

    // Client-side filtering (for live mode — server already filters in static mode)
    const displayed = lines.filter(line => {
        if (levelFilter && !line.includes(`[${levelFilter}]`)) return false
        if (search && !line.toLowerCase().includes(search.toLowerCase())) return false
        return true
    })

    // Download current log buffer as text
    const handleDownload = () => {
        const blob = new Blob([displayed.join("\n")], { type: "text/plain" })
        const url = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = url
        a.download = `duo-live-${new Date().toISOString().slice(0, 16)}.log`
        a.click()
        URL.revokeObjectURL(url)
    }

    return (
        <Layout>
            <div className="flex flex-col h-full gap-3">
                {/* Header */}
                <div className="flex items-center justify-between flex-shrink-0">
                    <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
                        <ScrollText className="w-4 h-4" />
                        系统日志
                        <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                            ({displayed.length} 行)
                        </span>
                    </h1>

                    {/* WS status indicator */}
                    <div className="flex items-center gap-2">
                        {isLive && (
                            <span className={cn("flex items-center gap-1.5 text-xs font-medium", {
                                "text-emerald-500": wsStatus === "connected",
                                "text-yellow-500": wsStatus === "connecting",
                                "text-red-500": wsStatus === "disconnected",
                            })}>
                                {wsStatus === "connected"
                                    ? <Wifi className="w-3.5 h-3.5" />
                                    : <WifiOff className="w-3.5 h-3.5" />
                                }
                                {wsStatus === "connected" ? "实时" : wsStatus === "connecting" ? "连接中…" : "断线"}
                            </span>
                        )}
                    </div>
                </div>

                {/* Toolbar */}
                <div className="flex flex-wrap items-center gap-2 flex-shrink-0">
                    {/* Live toggle */}
                    <button
                        onClick={() => setIsLive(v => !v)}
                        className={cn(
                            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                            isLive
                                ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300"
                                : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
                        )}
                    >
                        <span className={cn("w-1.5 h-1.5 rounded-full", isLive ? "bg-emerald-500 animate-pulse" : "bg-zinc-400")} />
                        {isLive ? "实时跟踪" : "静态查看"}
                    </button>

                    {/* Level filter */}
                    <div className="relative">
                        <select
                            value={levelFilter}
                            onChange={e => setLevelFilter(e.target.value)}
                            className={cn(
                                "appearance-none pl-3 pr-8 py-1.5 rounded-lg text-xs font-medium",
                                "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300",
                                "border border-zinc-200 dark:border-zinc-700",
                                "focus:outline-none focus:ring-1 focus:ring-zinc-400"
                            )}
                        >
                            {LEVELS.map(l => (
                                <option key={l} value={l}>{l || "全部级别"}</option>
                            ))}
                        </select>
                        <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-400 pointer-events-none" />
                    </div>

                    {/* Search */}
                    <div className="relative flex-1 min-w-[160px]">
                        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400" />
                        <input
                            type="text"
                            placeholder="关键词过滤…"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            className={cn(
                                "w-full pl-8 pr-3 py-1.5 rounded-lg text-xs",
                                "bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300",
                                "border border-zinc-200 dark:border-zinc-700",
                                "placeholder-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
                            )}
                        />
                    </div>

                    <div className="ml-auto flex items-center gap-2">
                        {/* Auto-scroll indicator */}
                        <button
                            onClick={() => {
                                setAutoScroll(true)
                                bottomRef.current?.scrollIntoView({ behavior: "smooth" })
                            }}
                            title="跳到底部"
                            className={cn(
                                "flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs transition-colors",
                                autoScroll
                                    ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400"
                                    : "bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-200"
                            )}
                        >
                            <ChevronDown className="w-3 h-3" />
                            {autoScroll ? "跟随" : "跳底"}
                        </button>

                        {/* Clear */}
                        <button
                            onClick={() => setLines([])}
                            title="清空显示"
                            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                        >
                            <Trash2 className="w-3.5 h-3.5" />
                        </button>

                        {/* Download */}
                        <button
                            onClick={handleDownload}
                            title="下载日志"
                            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                        >
                            <Download className="w-3.5 h-3.5" />
                        </button>
                    </div>
                </div>

                {/* Log area */}
                <div
                    ref={containerRef}
                    onScroll={handleScroll}
                    className={cn(
                        "flex-1 overflow-y-auto rounded-xl min-h-0",
                        "bg-zinc-950 dark:bg-black",
                        "border border-zinc-800",
                        "font-mono text-[11px] leading-relaxed",
                        "p-3"
                    )}
                >
                    {displayed.length === 0 ? (
                        <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                            {wsStatus === "connecting" ? "连接中…" : "暂无日志"}
                        </div>
                    ) : (
                        displayed.map((line, i) => (
                            <div
                                key={i}
                                className={cn(
                                    "whitespace-pre-wrap break-all py-0.5 hover:bg-white/5 rounded px-1 transition-colors",
                                    lineClass(line)
                                )}
                            >
                                {line}
                            </div>
                        ))
                    )}
                    <div ref={bottomRef} />
                </div>
            </div>
        </Layout>
    )
}
