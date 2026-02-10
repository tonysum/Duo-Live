"use client";

import { useEffect, useState, useCallback } from "react";
import { api, Kline, Position, Signal, OrderRequest } from "@/lib/api";
import TradeChart from "@/components/TradeChart";

export default function TradingPage() {
    const [symbol, setSymbol] = useState("BTCUSDT");
    const [searchInput, setSearchInput] = useState("BTCUSDT");
    const [klines, setKlines] = useState<Kline[]>([]);
    const [interval, setInterval_] = useState("15m");
    const [positions, setPositions] = useState<Position[]>([]);
    const [ticker, setTicker] = useState<number | null>(null);
    const [loading, setLoading] = useState(false);

    // Signals
    const [signals, setSignals] = useState<Signal[]>([]);

    // Order form
    const [side, setSide] = useState<"SELL" | "BUY">("SELL");
    const [orderType, setOrderType] = useState<"MARKET" | "LIMIT">("MARKET");
    const [price, setPrice] = useState("");
    const [qtyMode, setQtyMode] = useState<"margin" | "quantity">("margin");
    const [margin, setMargin] = useState("5");
    const [quantity, setQuantity] = useState("");
    const [leverage, setLeverage] = useState("3");
    const [tpPct, setTpPct] = useState("");
    const [slPct, setSlPct] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<string>("");

    // Password lock
    const [unlocked, setUnlocked] = useState(false);
    const [password, setPassword] = useState("");
    const [pwError, setPwError] = useState("");

    const loadChart = useCallback(async () => {
        setLoading(true);
        try {
            const [k, t, p] = await Promise.all([
                api.getKlines(symbol, interval, 500),
                api.getTicker(symbol).catch(() => null),
                api.getPositions().catch(() => []),
            ]);
            setKlines(k);
            if (t) setTicker(t.price);
            setPositions(p);
        } catch {
            // ignore
        } finally {
            setLoading(false);
        }
    }, [symbol, interval]);

    useEffect(() => {
        loadChart();
        const iv = setInterval(loadChart, 15000);
        return () => clearInterval(iv);
    }, [loadChart]);

    // Load signals
    useEffect(() => {
        api.getSignals(50).then(s => setSignals(s.sort((a, b) => b.surge_ratio - a.surge_ratio))).catch(() => { });
        const iv = setInterval(() => {
            api.getSignals(50).then(s => setSignals(s.sort((a, b) => b.surge_ratio - a.surge_ratio))).catch(() => { });
        }, 30000);
        return () => clearInterval(iv);
    }, []);

    const handleSearch = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            setSymbol(searchInput.toUpperCase());
        }
    };

    const handleSignalClick = (sig: Signal) => {
        const sym = sig.symbol.toUpperCase();
        setSymbol(sym);
        setSearchInput(sym);
        setSide("SELL"); // surge short strategy
    };

    const handleSubmit = async () => {
        const qtyLabel = qtyMode === "margin"
            ? `保证金: ${margin} USDT × ${leverage}x`
            : `数量: ${quantity}`;
        if (!confirm(`确认 ${side === "SELL" ? "做空" : "做多"} ${symbol}？\n${qtyLabel}`))
            return;

        setSubmitting(true);
        setResult("");
        try {
            const order: OrderRequest = {
                symbol,
                side,
                order_type: orderType,
                leverage: parseInt(leverage),
                trading_password: password,
            };
            if (qtyMode === "margin") {
                order.margin_usdt = parseFloat(margin);
            } else {
                order.quantity = parseFloat(quantity);
            }
            if (orderType === "LIMIT" && price) {
                order.price = parseFloat(price);
            }
            if (tpPct) order.tp_pct = parseFloat(tpPct);
            if (slPct) order.sl_pct = parseFloat(slPct);

            const res = await api.placeOrder(order);
            setResult(`✅ 下单成功 (ID: ${(res as any).order_id})`);
            loadChart();
        } catch (err: any) {
            setResult(`❌ ${err.message}`);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="flex flex-col -mx-6 -mt-6" style={{ height: "calc(100vh - 0px)" }}>
            {/* Signal bar — horizontal scrollable list */}
            {signals.length > 0 && (
                <div className="border-b border-[var(--border)] bg-[var(--bg-secondary)] px-4 py-2 flex-shrink-0">
                    <div className="flex items-center gap-2 overflow-x-auto">
                        <span className="text-[10px] text-[var(--text-muted)] whitespace-nowrap mr-1">📡 信号</span>
                        {signals.map((sig, i) => {
                            const isActive = sig.symbol === symbol;
                            return (
                                <button
                                    key={`${sig.symbol}-${sig.timestamp}-${i}`}
                                    onClick={() => handleSignalClick(sig)}
                                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs whitespace-nowrap transition-all
                                        ${isActive
                                            ? "bg-[var(--accent-blue)]/20 border border-[var(--accent-blue)]/50 text-[var(--accent-blue)]"
                                            : "bg-[var(--bg-card)] border border-[var(--border)] hover:border-[var(--accent-blue)]/30 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                                        }`}
                                >
                                    <span className="font-medium">{sig.symbol.replace("USDT", "")}</span>
                                    <span className={`font-mono text-[10px] ${sig.accepted ? "text-green-400" : "text-orange-400"}`}>
                                        {sig.surge_ratio.toFixed(1)}x
                                    </span>
                                    {sig.accepted ? (
                                        <span className="text-[9px] px-1 rounded bg-green-500/20 text-green-400">✓</span>
                                    ) : (
                                        <span className="text-[9px] px-1 rounded bg-orange-500/15 text-orange-400" title={sig.reject_reason}>✗</span>
                                    )}
                                    <span className="text-[9px] text-[var(--text-muted)]">
                                        {sig.timestamp?.slice(11, 16)}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Main content: chart + order panel */}
            <div className="flex flex-1 min-h-0">
                {/* Chart area */}
                <div className="flex-1 flex flex-col min-w-0">
                    {/* Chart header */}
                    <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
                        <div className="flex items-center gap-3">
                            <input
                                type="text"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value.toUpperCase())}
                                onKeyDown={handleSearch}
                                placeholder="搜索币种..."
                                className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-sm w-36
                           focus:border-[var(--accent-blue)] focus:outline-none"
                            />
                            {ticker && (
                                <span className="text-lg font-bold font-mono">
                                    ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                                </span>
                            )}
                        </div>

                        <div className="flex gap-1">
                            {["5m", "15m", "1h", "4h", "1d", "1w", "1M"].map((iv) => (
                                <button
                                    key={iv}
                                    onClick={() => setInterval_(iv)}
                                    className={`px-2 py-1 text-xs rounded transition-colors ${interval === iv
                                        ? "bg-[var(--accent-blue)] text-white"
                                        : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]"
                                        }`}
                                >
                                    {iv}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Chart */}
                    <div className="flex-1 relative">
                        {loading && klines.length === 0 ? (
                            <div className="absolute inset-0 flex items-center justify-center text-[var(--text-muted)]">
                                加载中...
                            </div>
                        ) : (
                            <TradeChart klines={klines} />
                        )}
                    </div>

                    {/* Current positions */}
                    {positions.length > 0 && (
                        <div className="border-t border-[var(--border)] px-4 py-2">
                            <p className="text-xs text-[var(--text-muted)] mb-1">当前持仓</p>
                            <div className="flex gap-4 overflow-x-auto">
                                {positions.map((p) => (
                                    <div
                                        key={p.symbol}
                                        className="flex items-center gap-2 text-xs bg-[var(--bg-card)] rounded px-3 py-1.5"
                                    >
                                        <span className="font-medium">{p.symbol}</span>
                                        <span
                                            className={`${p.side === "SHORT" ? "text-red-400" : "text-green-400"
                                                }`}
                                        >
                                            {p.side}
                                        </span>
                                        <span
                                            className={`font-mono ${p.unrealized_pnl >= 0
                                                ? "text-[var(--accent-green)]"
                                                : "text-[var(--accent-red)]"
                                                }`}
                                        >
                                            {p.unrealized_pnl >= 0 ? "+" : ""}
                                            {p.unrealized_pnl.toFixed(4)}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Order panel — fixed width, never shrinks */}
                <div className="border-l border-[var(--border)] bg-[var(--bg-secondary)] overflow-y-auto"
                    style={{ width: 320, minWidth: 320, maxWidth: 320, flexShrink: 0 }}>
                    <div className="p-4">
                        {!unlocked ? (
                            /* Password lock screen */
                            <div className="flex flex-col items-center justify-center py-12">
                                <div className="text-4xl mb-4">🔒</div>
                                <h3 className="text-sm font-medium mb-1">交易已锁定</h3>
                                <p className="text-xs text-[var(--text-muted)] mb-6">输入密码以激活交易功能</p>
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => { setPassword(e.target.value); setPwError(""); }}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" && password) {
                                            setUnlocked(true);
                                            setPwError("");
                                        }
                                    }}
                                    placeholder="交易密码"
                                    className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-center
                                       focus:border-[var(--accent-blue)] focus:outline-none mb-3"
                                />
                                <button
                                    onClick={() => {
                                        if (password) {
                                            setUnlocked(true);
                                            setPwError("");
                                        } else {
                                            setPwError("请输入密码");
                                        }
                                    }}
                                    className="w-full py-2 bg-[var(--accent-blue)] text-white text-sm rounded-lg hover:bg-[var(--accent-blue)]/80 transition-colors"
                                >
                                    解锁
                                </button>
                                {pwError && (
                                    <p className="text-xs text-red-400 mt-2">{pwError}</p>
                                )}
                            </div>
                        ) : (
                            /* Order form */
                            <>
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="text-sm font-medium">手动下单</h3>
                                    <button
                                        onClick={() => { setUnlocked(false); setPassword(""); }}
                                        className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
                                        title="锁定交易"
                                    >
                                        🔓 锁定
                                    </button>
                                </div>

                                {/* Active symbol display */}
                                <div className="mb-4 p-2 bg-[var(--bg-card)] rounded-lg border border-[var(--border)] text-center">
                                    <span className="text-lg font-bold">{symbol}</span>
                                    {ticker && (
                                        <span className="ml-2 text-sm font-mono text-[var(--text-secondary)]">
                                            ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                                        </span>
                                    )}
                                </div>

                                {/* Side selector */}
                                <div className="flex gap-1 mb-4">
                                    <button
                                        onClick={() => setSide("BUY")}
                                        className={`flex-1 py-2 text-sm rounded-lg font-medium transition-colors ${side === "BUY"
                                            ? "bg-green-500 text-white"
                                            : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                                            }`}
                                    >
                                        做多 LONG
                                    </button>
                                    <button
                                        onClick={() => setSide("SELL")}
                                        className={`flex-1 py-2 text-sm rounded-lg font-medium transition-colors ${side === "SELL"
                                            ? "bg-red-500 text-white"
                                            : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                                            }`}
                                    >
                                        做空 SHORT
                                    </button>
                                </div>

                                {/* Order type */}
                                <div className="flex gap-1 mb-4">
                                    {(["MARKET", "LIMIT"] as const).map((t) => (
                                        <button
                                            key={t}
                                            onClick={() => setOrderType(t)}
                                            className={`flex-1 py-1.5 text-xs rounded transition-colors ${orderType === t
                                                ? "bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]"
                                                : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                                }`}
                                        >
                                            {t}
                                        </button>
                                    ))}
                                </div>

                                {/* Price (LIMIT only) */}
                                {orderType === "LIMIT" && (
                                    <div className="mb-3">
                                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                                            价格
                                        </label>
                                        <input
                                            type="number"
                                            value={price}
                                            onChange={(e) => setPrice(e.target.value)}
                                            placeholder={ticker ? ticker.toString() : "价格"}
                                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                                     focus:border-[var(--accent-blue)] focus:outline-none"
                                        />
                                    </div>
                                )}

                                {/* Quantity mode toggle */}
                                <div className="flex gap-1 mb-3">
                                    <button
                                        onClick={() => setQtyMode("margin")}
                                        className={`flex-1 py-1 text-xs rounded transition-colors ${qtyMode === "margin"
                                            ? "bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]"
                                            : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                            }`}
                                    >
                                        保证金
                                    </button>
                                    <button
                                        onClick={() => setQtyMode("quantity")}
                                        className={`flex-1 py-1 text-xs rounded transition-colors ${qtyMode === "quantity"
                                            ? "bg-[var(--accent-blue)]/20 text-[var(--accent-blue)]"
                                            : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                            }`}
                                    >
                                        数量
                                    </button>
                                </div>

                                {/* Margin / Quantity input */}
                                {qtyMode === "margin" ? (
                                    <div className="mb-3">
                                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                                            保证金 (USDT)
                                        </label>
                                        <input
                                            type="number"
                                            value={margin}
                                            onChange={(e) => setMargin(e.target.value)}
                                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                                       focus:border-[var(--accent-blue)] focus:outline-none"
                                        />
                                    </div>
                                ) : (
                                    <div className="mb-3">
                                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                                            数量
                                        </label>
                                        <input
                                            type="number"
                                            value={quantity}
                                            onChange={(e) => setQuantity(e.target.value)}
                                            placeholder="合约数量"
                                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                                       focus:border-[var(--accent-blue)] focus:outline-none"
                                        />
                                    </div>
                                )}

                                {/* Leverage */}
                                <div className="mb-3">
                                    <label className="text-xs text-[var(--text-muted)] block mb-1">
                                        杠杆
                                    </label>
                                    <div className="flex gap-1">
                                        {["1", "2", "3", "5", "10"].map((l) => (
                                            <button
                                                key={l}
                                                onClick={() => setLeverage(l)}
                                                className={`flex-1 py-1.5 text-xs rounded transition-colors ${leverage === l
                                                    ? "bg-[var(--accent-blue)] text-white"
                                                    : "bg-[var(--bg-card)] text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
                                                    }`}
                                            >
                                                {l}x
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* TP/SL */}
                                <div className="grid grid-cols-2 gap-2 mb-4">
                                    <div>
                                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                                            止盈 %
                                        </label>
                                        <input
                                            type="number"
                                            value={tpPct}
                                            onChange={(e) => setTpPct(e.target.value)}
                                            placeholder="例: 33"
                                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                                     focus:border-[var(--accent-blue)] focus:outline-none"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-xs text-[var(--text-muted)] block mb-1">
                                            止损 %
                                        </label>
                                        <input
                                            type="number"
                                            value={slPct}
                                            onChange={(e) => setSlPct(e.target.value)}
                                            placeholder="例: 18"
                                            className="w-full bg-[var(--bg-card)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm
                                     focus:border-[var(--accent-blue)] focus:outline-none"
                                        />
                                    </div>
                                </div>

                                {/* Submit */}
                                <button
                                    onClick={handleSubmit}
                                    disabled={submitting || (qtyMode === "margin" ? !margin : !quantity)}
                                    className={`w-full py-2.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${side === "SELL"
                                        ? "bg-red-500 hover:bg-red-600 text-white"
                                        : "bg-green-500 hover:bg-green-600 text-white"
                                        }`}
                                >
                                    {submitting
                                        ? "下单中..."
                                        : `${side === "SELL" ? "做空" : "做多"} ${symbol}`}
                                </button>

                                {/* Result */}
                                {result && (
                                    <p
                                        className={`mt-3 text-xs p-2 rounded ${result.startsWith("✅")
                                            ? "bg-green-500/10 text-green-400"
                                            : "bg-red-500/10 text-red-400"
                                            }`}
                                    >
                                        {result}
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
