import { useEffect, useRef } from "react"
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
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
  /** Override axis label next to price line (default: Entry) */
  entryLineTitle?: string
  tpLineTitle?: string
  slLineTitle?: string
  exitLineTitle?: string
  /** K 线横向间距（越大单根蜡烛越粗），默认 9 */
  barSpacing?: number
  /**
   * 传入时：全量 `klines` 进序列，但首次/数据替换后只把「最近 N 根」放进视口（柱更粗）；
   * 左右拖拽可浏览同批次已加载的更早 K 线。不传则沿用首次 fitContent 全览。
   */
  initialVisibleBars?: number
  onRealtimeUpdate?: (updater: (kline: Kline) => void) => void
}

export default function TradeChart({
  klines,
  markers = [],
  tpPrice,
  slPrice,
  entryPrice,
  exitPrice,
  entryLineTitle = "Entry",
  tpLineTitle = "TP",
  slLineTitle = "SL",
  exitLineTitle = "Exit",
  barSpacing = 9,
  initialVisibleBars,
  onRealtimeUpdate,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const initialFitDone = useRef(false)
  const klinesLayoutKeyRef = useRef("")

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
        barSpacing,
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
    klinesLayoutKeyRef.current = ""

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
        timeScale: {
          borderColor: newGridColor,
          timeVisible: true,
          barSpacing,
        },
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
  }, [barSpacing])

  // Update data
  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleRef.current
    const volumeSeries = volumeRef.current
    if (!chart || !candleSeries || !volumeSeries || klines.length === 0) return

    const candleData = klines.map((k) => ({
      time: k.time as Time,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
    }))
    candleSeries.setData(candleData)

    const volData = klines.map((k) => ({
      time: k.time as Time,
      value: k.volume,
      color: k.close >= k.open ? "#34D39990" : "#F8717180",
    }))
    volumeSeries.setData(volData)

    // Markers
    if (markers.length > 0) {
      const lwMarkers = markers.map((m) => ({
        time: m.time as Time,
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
      for (const line of candleSeries.priceLines()) {
        candleSeries.removePriceLine(line)
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
        title: entryLineTitle,
      })
    }
    if (exitPrice) {
      candleSeries.createPriceLine({
        price: exitPrice,
        color: "#F87171",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: exitLineTitle,
      })
    }
    if (tpPrice) {
      candleSeries.createPriceLine({
        price: tpPrice,
        color: "#eab308",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: tpLineTitle,
      })
    }
    if (slPrice) {
      candleSeries.createPriceLine({
        price: slPrice,
        color: "#f97316",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: slLineTitle,
      })
    }

    const layoutKey =
      klines.length > 0
        ? `${klines.length}:${klines[0]!.time}:${klines[klines.length - 1]!.time}`
        : ""
    const layoutChanged =
      layoutKey !== klinesLayoutKeyRef.current && layoutKey.length > 0
    if (layoutChanged) {
      klinesLayoutKeyRef.current = layoutKey
    }

    if (initialVisibleBars && klines.length > 0 && layoutChanged) {
      const n = Math.min(initialVisibleBars, klines.length)
      const from = klines.length - n
      const to = klines.length - 1
      requestAnimationFrame(() => {
        chart.timeScale().setVisibleLogicalRange({ from, to })
      })
    } else if (!initialVisibleBars && !initialFitDone.current) {
      chart.timeScale().fitContent()
      initialFitDone.current = true
    }
  }, [
    klines,
    markers,
    tpPrice,
    slPrice,
    entryPrice,
    exitPrice,
    entryLineTitle,
    tpLineTitle,
    slLineTitle,
    exitLineTitle,
    initialVisibleBars,
  ])

  // Expose real-time update function
  useEffect(() => {
    if (!onRealtimeUpdate || !candleRef.current || !volumeRef.current) return

    const candleSeries = candleRef.current
    const volumeSeries = volumeRef.current

    onRealtimeUpdate((kline: Kline) => {
      candleSeries.update({
        time: kline.time as Time,
        open: kline.open,
        high: kline.high,
        low: kline.low,
        close: kline.close,
      })
      volumeSeries.update({
        time: kline.time as Time,
        value: kline.volume,
        color: kline.close >= kline.open ? "#34D39990" : "#F8717180",
      })
    })
  }, [onRealtimeUpdate, klines])

  return <div ref={containerRef} className="w-full h-full" />
}
