import { AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import type { StrategyQuota } from "@/lib/api"

interface StrategyQuotaCardProps {
  quota: StrategyQuota
  compact?: boolean
}

export function StrategyQuotaCard({ quota, compact = false }: StrategyQuotaCardProps) {
  const utilizationPct = (quota.current_positions / quota.max_positions) * 100
  const isWarning = utilizationPct >= 80 && utilizationPct < 100
  const isCritical = utilizationPct >= 100
  
  // Check if approaching daily loss limit (within 20%)
  const lossWarning = quota.daily_realized_pnl < 0 && 
    Math.abs(quota.daily_realized_pnl) >= quota.daily_loss_limit * 0.8

  return (
    <div
      className={cn(
        "bg-white dark:bg-zinc-900/70",
        "border border-zinc-100 dark:border-zinc-800",
        "rounded-xl backdrop-blur-xl shadow-sm",
        compact ? "p-3" : "p-4"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className={cn(
          "font-semibold text-zinc-900 dark:text-zinc-100",
          compact ? "text-xs" : "text-sm"
        )}>
          {quota.strategy_id}
        </h3>
        {(isWarning || isCritical || lossWarning) && (
          <AlertCircle
            className={cn("w-4 h-4", {
              "text-yellow-500 dark:text-yellow-400": isWarning || lossWarning,
              "text-red-500 dark:text-red-400": isCritical,
            })}
          />
        )}
      </div>

      {/* Positions Progress */}
      <div className="space-y-1.5 mb-3">
        <div className="flex justify-between text-xs">
          <span className="text-zinc-500 dark:text-zinc-400">Positions</span>
          <span className="font-mono text-zinc-900 dark:text-zinc-100">
            {quota.current_positions} / {quota.max_positions}
          </span>
        </div>
        <div className="h-2 bg-zinc-100 dark:bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={cn("h-full transition-all duration-300", {
              "bg-zinc-400 dark:bg-zinc-600": !isWarning && !isCritical,
              "bg-yellow-500 dark:bg-yellow-600": isWarning,
              "bg-red-500 dark:bg-red-600": isCritical,
            })}
            style={{ width: `${Math.min(utilizationPct, 100)}%` }}
          />
        </div>
      </div>

      {/* Stats Grid */}
      <div className="space-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Available Slots</span>
          <span
            className={cn("font-mono font-medium", {
              "text-zinc-900 dark:text-zinc-100": quota.available_slots > 1,
              "text-yellow-600 dark:text-yellow-400": quota.available_slots === 1,
              "text-red-600 dark:text-red-400": quota.available_slots === 0,
            })}
          >
            {quota.available_slots}
          </span>
        </div>

        <div className="flex justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Margin/Position</span>
          <span className="font-mono text-zinc-900 dark:text-zinc-100">
            {quota.margin_per_position.toFixed(1)} USDT
          </span>
        </div>

        <div className="flex justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Daily PnL</span>
          <span
            className={cn("font-mono font-medium", {
              "text-emerald-600 dark:text-emerald-400": quota.daily_realized_pnl >= 0,
              "text-red-600 dark:text-red-400": quota.daily_realized_pnl < 0,
            })}
          >
            {quota.daily_realized_pnl >= 0 ? "+" : ""}
            {quota.daily_realized_pnl.toFixed(2)}
          </span>
        </div>

        <div className="flex justify-between">
          <span className="text-zinc-500 dark:text-zinc-400">Loss Limit</span>
          <span className="font-mono text-zinc-900 dark:text-zinc-100">
            {quota.daily_loss_limit.toFixed(0)} USDT
          </span>
        </div>
      </div>

      {/* Warning Messages */}
      {quota.available_slots === 0 && (
        <div className="mt-3 p-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
          <p className="text-xs text-red-600 dark:text-red-400 font-medium">
            No available slots
          </p>
        </div>
      )}
      
      {lossWarning && quota.available_slots > 0 && (
        <div className="mt-3 p-2 rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800">
          <p className="text-xs text-yellow-600 dark:text-yellow-400 font-medium">
            Approaching daily loss limit
          </p>
        </div>
      )}
    </div>
  )
}
