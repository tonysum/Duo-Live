import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "DuoLive Trading Dashboard",
  description: "Real-time crypto trading monitoring and control",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className={inter.className}>
      <body className="antialiased">
        <Sidebar />
        <main
          style={{ marginLeft: "var(--sidebar-width)", minHeight: "100vh" }}
        >
          <div style={{ padding: 20 }}>{children}</div>
        </main>
      </body>
    </html>
  );
}
