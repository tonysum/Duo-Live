"use client";

import { useEffect, useRef } from "react";
import { createChart, IChartApi, LineSeries } from "lightweight-charts";
import { EquityPoint } from "@/lib/api";

interface Props {
    data: EquityPoint[];
}

export default function EquityChart({ data }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    useEffect(() => {
        if (!containerRef.current || data.length === 0) return;

        // Clean up previous chart
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
                vertLine: { color: "#3b82f6", width: 1, style: 2 },
                horzLine: { color: "#3b82f6", width: 1, style: 2 },
            },
            rightPriceScale: { borderColor: "#1e293b" },
            timeScale: { borderColor: "#1e293b", timeVisible: true },
            width: containerRef.current.clientWidth,
            height: 300,
        });

        const series = chart.addSeries(LineSeries, {
            color: "#3b82f6",
            lineWidth: 2,
        });

        const chartData = data.map((d) => ({
            time: (new Date(d.timestamp).getTime() / 1000) as any,
            value: d.equity,
        }));

        series.setData(chartData);
        chart.timeScale().fitContent();
        chartRef.current = chart;

        const handleResize = () => {
            if (containerRef.current) {
                chart.applyOptions({ width: containerRef.current.clientWidth });
            }
        };
        window.addEventListener("resize", handleResize);

        return () => {
            window.removeEventListener("resize", handleResize);
            chart.remove();
            chartRef.current = null;
        };
    }, [data]);

    return <div ref={containerRef} className="w-full" />;
}
