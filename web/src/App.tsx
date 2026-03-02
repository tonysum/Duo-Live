import { Routes, Route, Navigate } from "react-router-dom"
import { ThemeProvider } from "@/components/theme-provider"
import DashboardPage from "@/pages/DashboardPage"
import PositionsPage from "@/pages/PositionsPage"
import TradesPage from "@/pages/TradesPage"
import TradingPage from "@/pages/TradingPage"
import SignalsPage from "@/pages/SignalsPage"
import ChartPage from "@/pages/ChartPage"
import LogsPage from "@/pages/LogsPage"
import PaperTradingPage from "@/pages/PaperTradingPage"
import SettingsPage from "@/pages/SettingsPage"

export default function App() {
    return (
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
            <Routes>
                <Route path="/" element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/positions" element={<PositionsPage />} />
                <Route path="/trades" element={<TradesPage />} />
                <Route path="/trading" element={<TradingPage />} />
                <Route path="/signals" element={<SignalsPage />} />
                <Route path="/chart" element={<ChartPage />} />
                <Route path="/logs" element={<LogsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/paper" element={<PaperTradingPage />} />
            </Routes>
        </ThemeProvider>
    )
}
