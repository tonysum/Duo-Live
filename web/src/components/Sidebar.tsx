"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
    { href: "/", label: "总览", icon: "◈" },
    { href: "/positions", label: "持仓", icon: "◎" },
    { href: "/trades", label: "交易", icon: "◇" },
    { href: "/trading", label: "手动", icon: "▸" },
    { href: "/signals", label: "信号", icon: "◆" },
];

export default function Sidebar() {
    const pathname = usePathname();

    return (
        <aside
            style={{
                position: "fixed",
                top: 0,
                left: 0,
                height: "100vh",
                width: "var(--sidebar-width)",
                display: "flex",
                flexDirection: "column",
                background: "var(--bg-secondary)",
                borderRight: "1px solid var(--border)",
                zIndex: 50,
            }}
        >
            {/* Logo */}
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", height: 56, display: "flex", alignItems: "center" }}>
                <h1 style={{ margin: 0, fontSize: 17, fontWeight: 700, letterSpacing: "-0.03em" }}>
                    <span style={{ color: "var(--text-primary)" }}>Duo</span>
                    <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>Live</span>
                </h1>
            </div>

            {/* Nav section label */}
            <div style={{ padding: "16px 20px 6px", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                交易面板
            </div>

            {/* Navigation */}
            <nav style={{ flex: 1, padding: "0 8px" }}>
                {NAV_ITEMS.map((item) => {
                    const active = pathname === item.href;
                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 10,
                                padding: "8px 12px",
                                margin: "2px 0",
                                fontSize: 13,
                                color: active ? "var(--text-primary)" : "var(--text-muted)",
                                fontWeight: active ? 500 : 400,
                                textDecoration: "none",
                                borderRadius: "var(--radius-lg)",
                                background: active ? "var(--bg-hover)" : "transparent",
                                transition: "all 0.15s ease",
                            }}
                            onMouseEnter={(e) => {
                                if (!active) {
                                    e.currentTarget.style.background = "var(--bg-hover)";
                                    e.currentTarget.style.color = "var(--text-secondary)";
                                }
                            }}
                            onMouseLeave={(e) => {
                                if (!active) {
                                    e.currentTarget.style.background = "transparent";
                                    e.currentTarget.style.color = "var(--text-muted)";
                                }
                            }}
                        >
                            <span style={{ fontSize: 12, opacity: active ? 1 : 0.6 }}>{item.icon}</span>
                            {item.label}
                        </Link>
                    );
                })}
            </nav>

            {/* Footer */}
            <div style={{ padding: "12px 20px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{
                    width: 7, height: 7, borderRadius: "50%",
                    background: "var(--accent-green)",
                    boxShadow: "0 0 6px rgba(52, 211, 153, 0.4)",
                }} />
                <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 500 }}>运行中</span>
            </div>
        </aside>
    );
}
