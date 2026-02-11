"use client"

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import Image from "next/image"
import { Bell, ChevronRight, Activity } from "lucide-react"
import Profile01 from "./profile-01"
import Link from "next/link"
import { ThemeToggle } from "../theme-toggle"
import { usePathname } from "next/navigation"
import { useState, useEffect, useCallback } from "react"
import { api } from "@/lib/api"

interface BreadcrumbItem {
  label: string
  href?: string
}

const pageTitles: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/positions": "Positions",
  "/trades": "Trades",
  "/trading": "Manual Trade",
  "/signals": "Signals",
  "/chart": "Chart",
  "/settings": "Settings",
}

export default function TopNav() {
  const pathname = usePathname()
  const currentPage = pageTitles[pathname] || "Dashboard"
  const [autoTrade, setAutoTrade] = useState(false)
  const [toggling, setToggling] = useState(false)

  const breadcrumbs: BreadcrumbItem[] = [
    { label: "Duo Live", href: "/dashboard" },
    { label: currentPage },
  ]

  // Sync auto-trade state from backend
  const syncState = useCallback(async () => {
    try {
      const res = await api.getAutoTrade()
      setAutoTrade(res.enabled)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    syncState()
    const iv = setInterval(syncState, 10000)
    return () => clearInterval(iv)
  }, [syncState])

  const handleToggle = async () => {
    setToggling(true)
    try {
      const res = await api.setAutoTrade(!autoTrade)
      setAutoTrade(res.enabled)
    } catch {
      // ignore
    } finally {
      setToggling(false)
    }
  }

  return (
    <nav className="px-3 sm:px-6 flex items-center justify-between bg-white dark:bg-[#0F0F12] border-b border-gray-200 dark:border-[#1F1F23] h-full">
      <div className="font-medium text-sm hidden sm:flex items-center space-x-1 truncate max-w-[300px]">
        {breadcrumbs.map((item, index) => (
          <div key={item.label} className="flex items-center">
            {index > 0 && <ChevronRight className="h-4 w-4 text-gray-500 dark:text-gray-400 mx-1" />}
            {item.href ? (
              <Link
                href={item.href}
                className="text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
              >
                {item.label}
              </Link>
            ) : (
              <span className="text-gray-900 dark:text-gray-100">{item.label}</span>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2 sm:gap-4 ml-auto sm:ml-0">
        {/* Auto-trade toggle */}
        <button
          type="button"
          disabled={toggling}
          onClick={handleToggle}
          className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full transition-all duration-200 cursor-pointer ${toggling ? "opacity-50 cursor-not-allowed" : ""
            } ${autoTrade
              ? "bg-emerald-50 dark:bg-emerald-900/20"
              : "bg-gray-100 dark:bg-gray-800"
            }`}
        >
          <Activity className={`h-3.5 w-3.5 ${autoTrade
            ? "text-emerald-600 dark:text-emerald-400 animate-pulse"
            : "text-gray-400 dark:text-gray-500"
            }`} />
          <span className={`text-xs font-medium ${autoTrade
            ? "text-emerald-600 dark:text-emerald-400"
            : "text-gray-400 dark:text-gray-500"
            }`}>
            {autoTrade ? "自动交易" : "自动交易"}
          </span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold leading-none ${autoTrade
            ? "bg-emerald-500 text-white"
            : "bg-gray-300 dark:bg-gray-600 text-gray-500 dark:text-gray-400"
            }`}>
            {autoTrade ? "ON" : "OFF"}
          </span>
        </button>

        <button
          type="button"
          className="p-1.5 sm:p-2 hover:bg-gray-100 dark:hover:bg-[#1F1F23] rounded-full transition-colors"
        >
          <Bell className="h-4 w-4 sm:h-5 sm:w-5 text-gray-600 dark:text-gray-300" />
        </button>

        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger className="focus:outline-none">
            <div className="w-7 h-7 sm:w-8 sm:h-8 rounded-full overflow-hidden bg-zinc-900 ring-2 ring-gray-200 dark:ring-zinc-800 cursor-pointer">
              <Image
                src="/Duo Avatar.jpg"
                alt="User avatar"
                width={32}
                height={32}
                className="w-full h-full object-cover scale-[1.15]"
              />
            </div>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            sideOffset={8}
            className="w-[280px] sm:w-80 bg-background border-border rounded-lg shadow-lg"
          >
            <Profile01 avatar="/Duo Avatar.jpg" />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  )
}
