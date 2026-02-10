"use client";

import { useEffect, useRef } from "react";
import {
    createChart,
    IChartApi,
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

    useEffect(() => {
        if (!containerRef.current || klines.length === 0) return;

        if (chartRef.current) {
            chartRef.current.remove();
        }

        const chart = createChart(containerRef.current, {
            layout: {
                background: { color: "transparent" },
                textColor: "#94a3b8",
                fontSize: 11,
            },
            grid: {
                vertLines: { color: "#1e293b" },
                horzLines: { color: "#1e293b" },
            },
            crosshair: {
                vertLine: { color: "#3b82f666", width: 1, style: 2 },
                horzLine: { color: "#3b82f666", width: 1, style: 2 },
            },
            rightPriceScale: { borderColor: "#1e293b" },
            timeScale: { borderColor: "#1e293b", timeVisible: true },
            width: containerRef.current.clientWidth,
            height: containerRef.current.clientHeight || 500,
        });

        // Candlestick series
        const candleSeries = chart.addSeries(CandlestickSeries, {
            upColor: "#22c55e",
            downColor: "#ef4444",
            borderUpColor: "#22c55e",
            borderDownColor: "#ef4444",
            wickUpColor: "#22c55e80",
            wickDownColor: "#ef444480",
        });
        // Wider candlestick bars
        chart.timeScale().applyOptions({
            barSpacing: 9,
        });

        const candleData = klines.map((k) => ({
            time: k.time as any,
            open: k.open,
            high: k.high,
            low: k.low,
            close: k.close,
        }));
        candleSeries.setData(candleData);

        // Volume series
        const volumeSeries = chart.addSeries(HistogramSeries, {
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
        });
        volumeSeries.priceScale().applyOptions({
            scaleMargins: { top: 0.7, bottom: 0 },
        });
        const volData = klines.map((k) => ({
            time: k.time as any,
            value: k.volume,
            color: k.close >= k.open ? "#22c55e90" : "#ef444480",
        }));
        volumeSeries.setData(volData);

        // Entry/Exit markers
        if (markers.length > 0) {
            const lwMarkers = markers.map((m) => ({
                time: m.time as any,
                position: m.type === "entry" ? ("belowBar" as const) : ("aboveBar" as const),
                color: m.type === "entry" ? "#22c55e" : "#ef4444",
                shape: m.type === "entry" ? ("arrowUp" as const) : ("arrowDown" as const),
                text: m.label,
            }));
            createSeriesMarkers(candleSeries, lwMarkers.sort((a, b) => (a.time as number) - (b.time as number)));
        }

        // Price lines
        if (entryPrice) {
            candleSeries.createPriceLine({
                price: entryPrice,
                color: "#22c55e",
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: "入场",
            });
        }
        if (exitPrice) {
            candleSeries.createPriceLine({
                price: exitPrice,
                color: "#ef4444",
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: "出场",
            });
        }
        if (tpPrice) {
            candleSeries.createPriceLine({
                price: tpPrice,
                color: "#eab308",
                lineWidth: 1,
                lineStyle: 3,
                axisLabelVisible: true,
                title: "TP",
            });
        }
        if (slPrice) {
            candleSeries.createPriceLine({
                price: slPrice,
                color: "#f97316",
                lineWidth: 1,
                lineStyle: 3,
                axisLabelVisible: true,
                title: "SL",
            });
        }

        chart.timeScale().fitContent();
        chartRef.current = chart;

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
        };
    }, [klines, markers, tpPrice, slPrice, entryPrice, exitPrice]);

    return <div ref={containerRef} className="w-full h-full min-h-[500px]" />;
}
