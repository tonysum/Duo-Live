"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
    { href: "/", icon: "📊", label: "总览" },
    { href: "/positions", icon: "📌", label: "持仓" },
    { href: "/trades", icon: "📈", label: "交易" },
    { href: "/trading", icon: "🔄", label: "手动" },
    { href: "/signals", icon: "📡", label: "信号" },
];

export default function Sidebar() {
    const pathname = usePathname();

    return (
        <aside className="fixed top-0 left-0 h-screen flex flex-col bg-[var(--bg-secondary)] border-r border-[var(--border)]"
            style={{ width: "var(--sidebar-width)" }}>
            {/* Logo */}
            <div className="px-5 py-5 border-b border-[var(--border)]">
                <h1 className="text-lg font-bold tracking-tight">
                    <span className="text-[var(--accent-blue)]">Duo</span>
                    <span className="text-[var(--text-secondary)]">Live</span>
                </h1>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">Trading Dashboard</p>
            </div>

            {/* Navigation */}
            <nav className="flex-1 py-3">
                {NAV_ITEMS.map((item) => {
                    const active = pathname === item.href;
                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            className={`flex items-center gap-3 px-5 py-2.5 text-sm transition-all duration-150
                ${active
                                    ? "bg-[var(--accent-blue)]/10 text-[var(--accent-blue)] border-r-2 border-[var(--accent-blue)] font-medium"
                                    : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                                }`}
                        >
                            <span className="text-base">{item.icon}</span>
                            <span>{item.label}</span>
                        </Link>
                    );
                })}
            </nav>

            {/* Footer */}
            <div className="px-5 py-4 border-t border-[var(--border)]">
                <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-[var(--accent-green)] animate-pulse" />
                    <span className="text-xs text-[var(--text-muted)]">System Online</span>
                </div>
            </div>
        </aside>
    );
}
