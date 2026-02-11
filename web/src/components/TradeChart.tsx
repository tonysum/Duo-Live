"use client";

import { useEffect, useRef } from "react";
import {
    createChart,
    IChartApi,
    ISeriesApi,
    CandlestickSeries,
    HistogramSeries,
    createSeriesMarkers,
} from "lightweight-charts";
import { Kline } from "@/lib/api";

interface Marker {
    type: "entry" | "exit";
    time: number;
    price: number;
    label: string;
}

interface Props {
    klines: Kline[];
    markers?: Marker[];
    tpPrice?: number;
    slPrice?: number;
    entryPrice?: number;
    exitPrice?: number;
}

export default function TradeChart({
    klines,
    markers = [],
    tpPrice,
    slPrice,
    entryPrice,
    exitPrice,
}: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const candleRef = useRef<ISeriesApi<any> | null>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const volumeRef = useRef<ISeriesApi<any> | null>(null);
    const initialFitDone = useRef(false);

    // ── Create chart once ──────────────────────────────────────────
    useEffect(() => {
        if (!containerRef.current) return;

        const chart = createChart(containerRef.current, {
            layout: {
                background: { color: "transparent" },
                textColor: "#A1A1AA",
                fontSize: 11,
            },
            grid: {
                vertLines: { color: "#27272A" },
                horzLines: { color: "#27272A" },
            },
            crosshair: {
                vertLine: { color: "#60A5FA66", width: 1, style: 2 },
                horzLine: { color: "#60A5FA66", width: 1, style: 2 },
            },
            rightPriceScale: { borderColor: "#27272A" },
            timeScale: { borderColor: "#27272A", timeVisible: true, barSpacing: 9 },
            width: containerRef.current.clientWidth,
            height: containerRef.current.clientHeight || 500,
        });

        const candleSeries = chart.addSeries(CandlestickSeries, {
            upColor: "#34D399",
            downColor: "#F87171",
            borderUpColor: "#34D399",
            borderDownColor: "#F87171",
            wickUpColor: "#34D39980",
            wickDownColor: "#F8717180",
            priceFormat: { type: "price", precision: 6, minMove: 0.000001 },
        });

        const volumeSeries = chart.addSeries(HistogramSeries, {
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
        });
        volumeSeries.priceScale().applyOptions({
            scaleMargins: { top: 0.55, bottom: 0 },
        });

        chartRef.current = chart;
        candleRef.current = candleSeries;
        volumeRef.current = volumeSeries;
        initialFitDone.current = false;

        const handleResize = () => {
            if (containerRef.current) {
                chart.applyOptions({
                    width: containerRef.current.clientWidth,
                    height: containerRef.current.clientHeight || 500,
                });
            }
        };
        window.addEventListener("resize", handleResize);

        return () => {
            window.removeEventListener("resize", handleResize);
            chart.remove();
            chartRef.current = null;
            candleRef.current = null;
            volumeRef.current = null;
        };
    }, []); // chart created once, never destroyed on data change

    // ── Update data without resetting zoom/pan ─────────────────────
    useEffect(() => {
        const chart = chartRef.current;
        const candleSeries = candleRef.current;
        const volumeSeries = volumeRef.current;
        if (!chart || !candleSeries || !volumeSeries || klines.length === 0) return;

        // Update candle data
        const candleData = klines.map((k) => ({
            time: k.time as any,
            open: k.open,
            high: k.high,
            low: k.low,
            close: k.close,
        }));
        candleSeries.setData(candleData);

        // Update volume data
        const volData = klines.map((k) => ({
            time: k.time as any,
            value: k.volume,
            color: k.close >= k.open ? "#34D39990" : "#F8717180",
        }));
        volumeSeries.setData(volData);

        // Markers
        if (markers.length > 0) {
            const lwMarkers = markers.map((m) => ({
                time: m.time as any,
                position: m.type === "entry" ? ("belowBar" as const) : ("aboveBar" as const),
                color: m.type === "entry" ? "#34D399" : "#F87171",
                shape: m.type === "entry" ? ("arrowUp" as const) : ("arrowDown" as const),
                text: m.label,
            }));
            createSeriesMarkers(candleSeries, lwMarkers.sort((a, b) => (a.time as number) - (b.time as number)));
        }

        // Price lines — clear old ones and recreate
        // (lightweight-charts doesn't have a removePriceLine-all API,
        //  but createPriceLine is additive, so we need to remove old ones)
        // We recreate the series price lines by removing and re-adding
        // Actually, we can use the priceLine objects:
        const existingLines = (candleSeries as any)._priceLines;
        // Simplest: remove all price lines and re-add
        try {
            // Collect and remove existing price lines
            const lines: any[] = [];
            if ((candleSeries as any).priceLines) {
                for (const line of (candleSeries as any).priceLines()) {
                    lines.push(line);
                }
                for (const line of lines) {
                    candleSeries.removePriceLine(line);
                }
            }
        } catch {
            // ignore if API not available
        }

        if (entryPrice) {
            candleSeries.createPriceLine({
                price: entryPrice, color: "#34D399",
                lineWidth: 1, lineStyle: 2,
                axisLabelVisible: true, title: "入场",
            });
        }
        if (exitPrice) {
            candleSeries.createPriceLine({
                price: exitPrice, color: "#F87171",
                lineWidth: 1, lineStyle: 2,
                axisLabelVisible: true, title: "出场",
            });
        }
        if (tpPrice) {
            candleSeries.createPriceLine({
                price: tpPrice, color: "#eab308",
                lineWidth: 1, lineStyle: 3,
                axisLabelVisible: true, title: "TP",
            });
        }
        if (slPrice) {
            candleSeries.createPriceLine({
                price: slPrice, color: "#f97316",
                lineWidth: 1, lineStyle: 3,
                axisLabelVisible: true, title: "SL",
            });
        }

        // Only fitContent on the first load, not on subsequent refreshes
        if (!initialFitDone.current) {
            chart.timeScale().fitContent();
            initialFitDone.current = true;
        }
    }, [klines, markers, tpPrice, slPrice, entryPrice, exitPrice]);

    return <div ref={containerRef} className="w-full h-full min-h-[500px]" />;
}
