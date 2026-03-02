"use client"

import { useEffect, useRef } from "react"
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
} from "lightweight-charts"
import type { Kline } from "@/lib/api"

interface Marker {
  type: "entry" | "exit"
  time: number
  price: number
  label: string
}

interface Props {
  klines: Kline[]
  markers?: Marker[]
  tpPrice?: number
  slPrice?: number
  entryPrice?: number
  exitPrice?: number
  onRealtimeUpdate?: (updater: (kline: Kline) => void) => void
}

export default function TradeChart({
  klines,
  markers = [],
  tpPrice,
  slPrice,
  entryPrice,
  exitPrice,
  onRealtimeUpdate,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const candleRef = useRef<ISeriesApi<any> | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const volumeRef = useRef<ISeriesApi<any> | null>(null)
  const initialFitDone = useRef(false)

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return

    const isDark = document.documentElement.classList.contains("dark")
    const gridColor = isDark ? "#27272A" : "#E4E4E7"
    const textColor = isDark ? "#A1A1AA" : "#71717A"

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: "transparent" },
        textColor,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      crosshair: {
        vertLine: { color: "#60A5FA66", width: 1, style: 2 },
        horzLine: { color: "#60A5FA66", width: 1, style: 2 },
      },
      rightPriceScale: { borderColor: gridColor },
      timeScale: {
        borderColor: gridColor,
        timeVisible: true,
        barSpacing: 9,
      },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight || 500,
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#34D399",
      downColor: "#F87171",
      borderUpColor: "#34D399",
      borderDownColor: "#F87171",
      wickUpColor: "#34D39980",
      wickDownColor: "#F8717180",
      priceFormat: { type: "price", precision: 6, minMove: 0.000001 },
    })

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    }, 1)
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.1, bottom: 0 },
    })

    chartRef.current = chart
    candleRef.current = candleSeries
    volumeRef.current = volumeSeries
    initialFitDone.current = false

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight || 500,
        })
      }
    }
    window.addEventListener("resize", handleResize)

    // Watch for theme changes and update chart colors
    const observer = new MutationObserver(() => {
      const nowDark = document.documentElement.classList.contains("dark")
      const newGridColor = nowDark ? "#27272A" : "#E4E4E7"
      const newTextColor = nowDark ? "#A1A1AA" : "#71717A"
      chart.applyOptions({
        layout: { textColor: newTextColor },
        grid: {
          vertLines: { color: newGridColor },
          horzLines: { color: newGridColor },
        },
        rightPriceScale: { borderColor: newGridColor },
        timeScale: { borderColor: newGridColor },
      })
    })
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })

    return () => {
      observer.disconnect()
      window.removeEventListener("resize", handleResize)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
    }
  }, [])

  // Update data
  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleRef.current
    const volumeSeries = volumeRef.current
    if (!chart || !candleSeries || !volumeSeries || klines.length === 0) return

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const candleData = klines.map((k) => ({
      time: k.time as any,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    candleSeries.setData(candleData)

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const volData = klines.map((k) => ({
      time: k.time as any,
      value: k.volume,
      color: k.close >= k.open ? "#34D39990" : "#F8717180",
    }))
    volumeSeries.setData(volData)

    // Markers
    if (markers.length > 0) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const lwMarkers = markers.map((m) => ({
        time: m.time as any,
        position:
          m.type === "entry"
            ? ("belowBar" as const)
            : ("aboveBar" as const),
        color: m.type === "entry" ? "#34D399" : "#F87171",
        shape:
          m.type === "entry"
            ? ("arrowUp" as const)
            : ("arrowDown" as const),
        text: m.label,
      }))
      createSeriesMarkers(
        candleSeries,
        lwMarkers.sort((a, b) => (a.time as number) - (b.time as number))
      )
    }

    // Price lines
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const lines: any[] = []
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((candleSeries as any).priceLines) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        for (const line of (candleSeries as any).priceLines()) {
          lines.push(line)
        }
        for (const line of lines) {
          candleSeries.removePriceLine(line)
        }
      }
    } catch {
      // ignore
    }

    if (entryPrice) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: "#34D399",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Entry",
      })
    }
    if (exitPrice) {
      candleSeries.createPriceLine({
        price: exitPrice,
        color: "#F87171",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "Exit",
      })
    }
    if (tpPrice) {
      candleSeries.createPriceLine({
        price: tpPrice,
        color: "#eab308",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: "TP",
      })
    }
    if (slPrice) {
      candleSeries.createPriceLine({
        price: slPrice,
        color: "#f97316",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: "SL",
      })
    }

    if (!initialFitDone.current) {
      chart.timeScale().fitContent()
      initialFitDone.current = true
    }
  }, [klines, markers, tpPrice, slPrice, entryPrice, exitPrice])

  // Expose real-time update function
  useEffect(() => {
    if (!onRealtimeUpdate || !candleRef.current || !volumeRef.current) return

    const candleSeries = candleRef.current
    const volumeSeries = volumeRef.current

    onRealtimeUpdate((kline: Kline) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      candleSeries.update({
        time: kline.time as any,
        open: kline.open,
        high: kline.high,
        low: kline.low,
        close: kline.close,
      })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      volumeSeries.update({
        time: kline.time as any,
        value: kline.volume,
        color: kline.close >= kline.open ? "#34D39990" : "#F8717180",
      })
    })
  }, [onRealtimeUpdate, klines])

  return <div ref={containerRef} className="w-full h-full" />
}
