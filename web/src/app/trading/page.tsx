"use client";

import { useEffect, useState, useCallback } from "react";
import { api, Kline, Position, Signal, OrderRequest } from "@/lib/api";
import TradeChart from "@/components/TradeChart";

export default function TradingPage() {
    const [symbol, setSymbol] = useState("BTCUSDT");
    const [searchInput, setSearchInput] = useState("BTCUSDT");
    const [klines, setKlines] = useState<Kline[]>([]);
    const [interval, setInterval_] = useState("1h");
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
        const sortAndDedup = (raw: Signal[]) => {
            const sorted = [...raw].sort((a, b) => {
                const dayA = a.timestamp.slice(0, 10);
                const dayB = b.timestamp.slice(0, 10);
                if (dayA !== dayB) return dayB.localeCompare(dayA);
                return b.surge_ratio - a.surge_ratio;
            });
            const seen = new Set<string>();
            return sorted.filter((s) => {
                const key = `${s.symbol}:${s.timestamp.slice(0, 10)}`;
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            });
        };
        api.getSignals(50).then(s => setSignals(sortAndDedup(s))).catch(() => { });
        const iv = setInterval(() => {
            api.getSignals(50).then(s => setSignals(sortAndDedup(s))).catch(() => { });
        }, 30000);
        return () => clearInterval(iv);
    }, []);

    const handleSearch = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") setSymbol(searchInput.toUpperCase());
    };

    const handleSignalClick = (sig: Signal) => {
        const sym = sig.symbol.toUpperCase();
        setSymbol(sym);
        setSearchInput(sym);
        setSide("SELL");
    };

    const handleSubmit = async () => {
        const qtyLabel = qtyMode === "margin"
            ? `保证金: ${margin} USDT × ${leverage}x`
            : `数量: ${quantity}`;
        if (!confirm(`确认 ${side === "SELL" ? "做空" : "做多"} ${symbol}？\n${qtyLabel}`)) return;

        setSubmitting(true);
        setResult("");
        try {
            const order: OrderRequest = {
                symbol, side, order_type: orderType,
                leverage: parseInt(leverage),
                trading_password: password,
            };
            if (qtyMode === "margin") order.margin_usdt = parseFloat(margin);
            else order.quantity = parseFloat(quantity);
            if (orderType === "LIMIT" && price) order.price = parseFloat(price);
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

    const intervals = ["5m", "15m", "1h", "4h", "1d", "1w", "1M"];

    const tabBtn = (active: boolean) => ({
        flex: 1, padding: "5px 0", fontSize: 11, borderRadius: "var(--radius-md)",
        border: "none", cursor: "pointer" as const,
        background: active ? "rgba(96,165,250,0.12)" : "transparent",
        color: active ? "var(--accent-blue)" : "var(--text-muted)",
        fontWeight: active ? 500 : 400 as any,
        transition: "all 0.15s",
    });

    const levBtn = (active: boolean) => ({
        flex: 1, padding: "5px 0", fontSize: 11, borderRadius: "var(--radius-md)",
        border: "none", cursor: "pointer" as const,
        background: active ? "var(--accent-blue)" : "var(--bg-card-solid)",
        color: active ? "#18181B" : "var(--text-muted)",
        transition: "all 0.15s",
    });

    return (
        <div style={{ display: "flex", flexDirection: "column", margin: "-20px", height: "100vh" }}>
            {/* Signal ribbon */}
            <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-secondary)", padding: "6px 12px", flexShrink: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, overflowX: "auto" }}>
                    <span style={{ fontSize: 10, color: "var(--text-muted)", whiteSpace: "nowrap", marginRight: 4 }}>信号</span>
                    {signals.length === 0 ? (
                        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>暂无信号</span>
                    ) : signals.map((sig, i) => {
                        const isActive = sig.symbol === symbol;
                        return (
                            <button
                                key={`${sig.symbol}-${sig.timestamp}-${i}`}
                                onClick={() => handleSignalClick(sig)}
                                style={{
                                    display: "flex", alignItems: "center", gap: 4,
                                    padding: "3px 8px", borderRadius: 4, fontSize: 11,
                                    whiteSpace: "nowrap", transition: "all 0.12s",
                                    border: isActive ? "1px solid rgba(96,165,250,0.4)" : "1px solid var(--border)",
                                    background: isActive ? "rgba(96,165,250,0.1)" : "var(--bg-card-solid)",
                                    color: isActive ? "var(--accent-blue)" : "var(--text-secondary)",
                                    cursor: "pointer",
                                }}
                            >
                                <span style={{ fontWeight: 500 }}>{sig.symbol.replace("USDT", "")}</span>
                                <span className="font-mono" style={{ fontSize: 10, color: sig.accepted ? "var(--accent-green)" : "var(--accent-yellow)" }}>
                                    {sig.surge_ratio.toFixed(1)}x
                                </span>
                                <span style={{
                                    fontSize: 9, padding: "0 3px", borderRadius: 2,
                                    background: sig.accepted ? "rgba(52,211,153,0.12)" : "rgba(248,113,113,0.12)",
                                    color: sig.accepted ? "var(--accent-green)" : "var(--accent-red)",
                                }}>{sig.accepted ? "✓" : "✗"}</span>
                                <span style={{ fontSize: 9, color: "var(--text-muted)" }}>{sig.timestamp?.slice(11, 16)}</span>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Main: chart + order panel */}
            <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
                {/* Chart area */}
                <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
                    {/* Chart header */}
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <input
                                type="text"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value.toUpperCase())}
                                onKeyDown={handleSearch}
                                placeholder="搜索币种..."
                                className="input"
                                style={{ width: 120, fontSize: 12 }}
                            />
                            {ticker && (
                                <span className="font-mono" style={{ fontSize: 16, fontWeight: 700 }}>
                                    ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                                </span>
                            )}
                        </div>
                        <div style={{ display: "flex", gap: 2 }}>
                            {intervals.map((iv) => (
                                <button
                                    key={iv}
                                    onClick={() => setInterval_(iv)}
                                    style={{
                                        padding: "3px 8px", fontSize: 11, borderRadius: "var(--radius-md)",
                                        border: "none", cursor: "pointer",
                                        background: interval === iv ? "var(--accent-blue)" : "transparent",
                                        color: interval === iv ? "#18181B" : "var(--text-muted)",
                                        transition: "all 0.15s",
                                    }}
                                >
                                    {iv}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Chart */}
                    <div style={{ flex: 1, position: "relative" }}>
                        {loading && klines.length === 0 ? (
                            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 12 }}>
                                加载中...
                            </div>
                        ) : (
                            <TradeChart key={`${symbol}-${interval}`} klines={klines} />
                        )}
                    </div>

                    {/* Current positions bar */}
                    {positions.length > 0 && (
                        <div style={{ borderTop: "1px solid var(--border)", padding: "6px 12px" }}>
                            <p style={{ fontSize: 11, color: "var(--text-muted)", margin: "0 0 4px" }}>当前持仓</p>
                            <div style={{ display: "flex", gap: 8, overflowX: "auto" }}>
                                {positions.map((p) => (
                                    <div key={p.symbol} style={{
                                        display: "flex", alignItems: "center", gap: 6,
                                        fontSize: 11, background: "var(--bg-card-solid)", borderRadius: "var(--radius-md)", padding: "4px 10px"
                                    }}>
                                        <span style={{ fontWeight: 500 }}>{p.symbol}</span>
                                        <span style={{ color: p.side === "SHORT" ? "var(--accent-red)" : "var(--accent-green)" }}>{p.side}</span>
                                        <span className={`font-mono ${p.unrealized_pnl >= 0 ? "pnl-positive" : "pnl-negative"}`}>
                                            {p.unrealized_pnl >= 0 ? "+" : ""}{p.unrealized_pnl.toFixed(4)}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* Order panel */}
                <div style={{ width: 300, minWidth: 300, maxWidth: 300, flexShrink: 0, borderLeft: "1px solid var(--border)", background: "var(--bg-secondary)", overflowY: "auto" }}>
                    <div style={{ padding: 14 }}>
                        {!unlocked ? (
                            /* Password lock */
                            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", paddingTop: 48, paddingBottom: 48 }}>
                                <div style={{ fontSize: 32, marginBottom: 12 }}>🔒</div>
                                <h3 style={{ margin: "0 0 4px", fontSize: 13, fontWeight: 500 }}>交易已锁定</h3>
                                <p style={{ margin: "0 0 20px", fontSize: 11, color: "var(--text-muted)" }}>输入密码以激活交易功能</p>
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => { setPassword(e.target.value); setPwError(""); }}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" && password) { setUnlocked(true); setPwError(""); }
                                    }}
                                    placeholder="交易密码"
                                    className="input"
                                    style={{ textAlign: "center", marginBottom: 10 }}
                                />
                                <button
                                    onClick={() => { if (password) { setUnlocked(true); setPwError(""); } else { setPwError("请输入密码"); } }}
                                    className="btn btn-blue"
                                    style={{ width: "100%", padding: "8px 0" }}
                                >
                                    解锁
                                </button>
                                {pwError && <p style={{ fontSize: 11, color: "var(--accent-red)", marginTop: 8 }}>{pwError}</p>}
                            </div>
                        ) : (
                            /* Order form */
                            <>
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                                    <h3 style={{ margin: 0, fontSize: 13, fontWeight: 500 }}>手动下单</h3>
                                    <button
                                        onClick={() => { setUnlocked(false); setPassword(""); }}
                                        style={{ fontSize: 11, color: "var(--text-muted)", background: "none", border: "none", cursor: "pointer" }}
                                    >
                                        🔓 锁定
                                    </button>
                                </div>

                                {/* Symbol display */}
                                <div style={{ marginBottom: 12, padding: "6px 10px", background: "var(--bg-card-solid)", borderRadius: "var(--radius-md)", border: "1px solid var(--border)", textAlign: "center" }}>
                                    <span style={{ fontSize: 15, fontWeight: 700 }}>{symbol}</span>
                                    {ticker && (
                                        <span className="font-mono" style={{ marginLeft: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                                            ${ticker.toFixed(ticker >= 100 ? 2 : 4)}
                                        </span>
                                    )}
                                </div>

                                {/* Side selector */}
                                <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
                                    <button
                                        onClick={() => setSide("BUY")}
                                        style={{
                                            flex: 1, padding: "8px 0", fontSize: 12, borderRadius: "var(--radius-md)",
                                            border: "none", cursor: "pointer", fontWeight: 500,
                                            background: side === "BUY" ? "var(--accent-green)" : "var(--bg-card-solid)",
                                            color: side === "BUY" ? "#18181B" : "var(--text-muted)",
                                            transition: "all 0.15s",
                                        }}
                                    >
                                        做多 LONG
                                    </button>
                                    <button
                                        onClick={() => setSide("SELL")}
                                        style={{
                                            flex: 1, padding: "8px 0", fontSize: 12, borderRadius: "var(--radius-md)",
                                            border: "none", cursor: "pointer", fontWeight: 500,
                                            background: side === "SELL" ? "var(--accent-red)" : "var(--bg-card-solid)",
                                            color: side === "SELL" ? "#18181B" : "var(--text-muted)",
                                            transition: "all 0.15s",
                                        }}
                                    >
                                        做空 SHORT
                                    </button>
                                </div>

                                {/* Order type */}
                                <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
                                    {(["MARKET", "LIMIT"] as const).map((t) => (
                                        <button key={t} onClick={() => setOrderType(t)} style={tabBtn(orderType === t)}>{t}</button>
                                    ))}
                                </div>

                                {/* Price (LIMIT) */}
                                {orderType === "LIMIT" && (
                                    <div style={{ marginBottom: 10 }}>
                                        <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>价格</label>
                                        <input type="number" value={price} onChange={(e) => setPrice(e.target.value)}
                                            placeholder={ticker ? ticker.toString() : "价格"} className="input" />
                                    </div>
                                )}

                                {/* Qty mode toggle */}
                                <div style={{ display: "flex", gap: 4, marginBottom: 10 }}>
                                    <button onClick={() => setQtyMode("margin")} style={tabBtn(qtyMode === "margin")}>保证金</button>
                                    <button onClick={() => setQtyMode("quantity")} style={tabBtn(qtyMode === "quantity")}>数量</button>
                                </div>

                                {/* Margin / Quantity */}
                                <div style={{ marginBottom: 10 }}>
                                    <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                                        {qtyMode === "margin" ? "保证金 (USDT)" : "数量"}
                                    </label>
                                    {qtyMode === "margin" ? (
                                        <input type="number" value={margin} onChange={(e) => setMargin(e.target.value)} className="input" />
                                    ) : (
                                        <input type="number" value={quantity} onChange={(e) => setQuantity(e.target.value)} placeholder="合约数量" className="input" />
                                    )}
                                </div>

                                {/* Leverage */}
                                <div style={{ marginBottom: 10 }}>
                                    <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>杠杆</label>
                                    <div style={{ display: "flex", gap: 4 }}>
                                        {["1", "2", "3", "5", "10"].map((l) => (
                                            <button key={l} onClick={() => setLeverage(l)} style={levBtn(leverage === l)}>{l}x</button>
                                        ))}
                                    </div>
                                </div>

                                {/* TP/SL */}
                                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
                                    <div>
                                        <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>止盈 %</label>
                                        <input type="number" value={tpPct} onChange={(e) => setTpPct(e.target.value)} placeholder="例: 33" className="input" />
                                    </div>
                                    <div>
                                        <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>止损 %</label>
                                        <input type="number" value={slPct} onChange={(e) => setSlPct(e.target.value)} placeholder="例: 18" className="input" />
                                    </div>
                                </div>

                                {/* Submit */}
                                <button
                                    onClick={handleSubmit}
                                    disabled={submitting || (qtyMode === "margin" ? !margin : !quantity)}
                                    className={`btn ${side === "SELL" ? "btn-red" : "btn-green"}`}
                                    style={{ width: "100%", padding: "10px 0", fontSize: 13 }}
                                >
                                    {submitting ? "下单中..." : `${side === "SELL" ? "做空" : "做多"} ${symbol}`}
                                </button>

                                {/* Result */}
                                {result && (
                                    <p style={{
                                        marginTop: 10, fontSize: 11, padding: "6px 10px", borderRadius: 4,
                                        background: result.startsWith("✅") ? "rgba(52,211,153,0.1)" : "rgba(248,113,113,0.1)",
                                        color: result.startsWith("✅") ? "var(--accent-green)" : "var(--accent-red)",
                                    }}>
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
