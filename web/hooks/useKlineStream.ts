"use client"

import { useEffect, useRef } from "react"
import type { Kline } from "@/lib/api"

const BINANCE_WS = "wss://fstream.binance.com/ws"

/**
 * Hook that connects to Binance Futures kline WebSocket and
 * calls `onUpdate` with updated kline data in real-time.
 */
export function useKlineStream(
    symbol: string,
    interval: string,
    onUpdate: (kline: Kline) => void,
    enabled = true
) {
    const onUpdateRef = useRef(onUpdate)
    onUpdateRef.current = onUpdate

    useEffect(() => {
        if (!symbol || !interval || !enabled) return

        let ws: WebSocket | null = null
        let reconnectTimer: ReturnType<typeof setTimeout> | null = null
        let disposed = false

        function connect() {
            if (disposed) return

            const stream = `${symbol.toLowerCase()}@kline_${interval}`
            const socket = new WebSocket(`${BINANCE_WS}/${stream}`)
            ws = socket

            socket.onopen = () => {
                if (disposed) {
                    socket.close()
                    return
                }
            }

            socket.onmessage = (event) => {
                if (disposed) return
                try {
                    const msg = JSON.parse(event.data)
                    if (msg.e !== "kline") return

                    const k = msg.k
                    const kline: Kline = {
                        time: Math.floor(k.t / 1000), // open time in seconds
                        open: parseFloat(k.o),
                        high: parseFloat(k.h),
                        low: parseFloat(k.l),
                        close: parseFloat(k.c),
                        volume: parseFloat(k.v),
                    }
                    onUpdateRef.current(kline)
                } catch {
                    // ignore parse errors
                }
            }

            socket.onclose = () => {
                if (disposed) return
                // Auto-reconnect after 3 seconds
                reconnectTimer = setTimeout(connect, 3000)
            }

            socket.onerror = () => {
                if (disposed) return
                // onclose will fire after onerror, triggering reconnect
            }
        }

        connect()

        return () => {
            disposed = true
            if (reconnectTimer) {
                clearTimeout(reconnectTimer)
                reconnectTimer = null
            }
            if (ws) {
                // Only close if actually open or connecting
                if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                    ws.close()
                }
                ws = null
            }
        }
    }, [symbol, interval, enabled])
}
