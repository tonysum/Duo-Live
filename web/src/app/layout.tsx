import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

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
    <html lang="zh-CN">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased">
        <Sidebar />
        <main
          className="min-h-screen"
          style={{ marginLeft: "var(--sidebar-width)" }}
        >
          <div className="p-6">{children}</div>
        </main>
      </body>
    </html>
  );
}
