import { useEffect, useState } from "react"
import {
  api,
  Status,
  Position,
  LiveTrade,
  Config,
  RollingRuntimeParams,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  ArrowUpRight,
  ArrowDownLeft,
  Activity,
  Briefcase,
  AlertCircle,
  CreditCard,
  ArrowRight,
  SlidersHorizontal,
} from "lucide-react"
import { Link } from "react-router-dom"

function ParamRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 text-xs py-1.5 border-b border-zinc-100 dark:border-zinc-800/80 last:border-0">
      <span className="text-zinc-500 dark:text-zinc-400 shrink-0">{label}</span>
      <span className="font-mono text-zinc-900 dark:text-zinc-100 text-right break-all">
        {value}
      </span>
    </div>
  )
}

/** 单路 Rolling 扫描 + 持仓管理参数（多策略时重复块） */
function RollingRuntimeBlock({ rolling }: { rolling: RollingRuntimeParams }) {
  const maxHoldH = rolling.max_hold_days * 24
  return (
    <div
      className={cn(
        "rounded-lg border border-zinc-100 dark:border-zinc-800",
        "bg-zinc-50/70 dark:bg-zinc-900/40 p-3 space-y-2"
      )}
    >
      <h3 className="text-[11px] font-semibold text-zinc-700 dark:text-zinc-200 tracking-wide">
        {rolling.strategy_id}
      </h3>
      <div>
        <h4 className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
          扫描
        </h4>
        <ParamRow label="Top N" value={String(rolling.top_n)} />
        <ParamRow
          label="24h 涨幅阈值"
          value={`≥ ${rolling.min_pct_chg}%`}
        />
        <ParamRow
          label="扫描间隔"
          value={`${rolling.scan_interval_hours}h`}
        />
        <ParamRow
          label="信号冷却"
          value={`${rolling.signal_cooldown_hours}h`}
        />
        <ParamRow
          label="新币过滤"
          value={`≥ ${rolling.min_listed_days}d`}
        />
        <ParamRow
          label="主盈利阶梯校验"
          value={rolling.enable_main_profit_check ? "开启" : "关闭"}
        />
        <ParamRow
          label="阶梯阈值"
          value={rolling.main_profit_thresholds
            .map(([a, b]) => `${a}→${b}`)
            .join(", ")}
        />
      </div>
      <div>
        <h4 className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1">
          持仓管理
        </h4>
        <ParamRow
          label="初始止盈"
          value={`${rolling.tp_initial_pct}%`}
        />
        <ParamRow
          label="衰减止盈"
          value={`${rolling.tp_reduced_pct}%（>${rolling.tp_hours_threshold}h）`}
        />
        <ParamRow
          label="加仓后止盈"
          value={`${rolling.tp_after_add_pct}%`}
        />
        <ParamRow label="止损" value={`${rolling.sl_threshold_pct}%`} />
        <ParamRow
          label="最长持仓"
          value={`${rolling.max_hold_days}d（${maxHoldH}h）`}
        />
        <ParamRow
          label="追踪止损"
          value={
            rolling.enable_trailing_stop
              ? `开 · 激活 ${rolling.trailing_activation_pct}% · 距离 ${rolling.trailing_distance_pct}%`
              : "关"
          }
        />
        <ParamRow
          label="逆势加仓"
          value={
            rolling.enable_add_position
              ? `开 · 阈值 ${rolling.add_position_threshold_pct}% · 倍数 ${rolling.add_position_multiplier_pct}%`
              : "关"
          }
        />
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  icon: Icon,
  trend,
  sub,
}: {
  label: string
  value: string
  icon: React.ElementType
  trend?: "up" | "down" | "neutral"
  sub?: string
}) {
  return (
    <div
      className={cn(
        "bg-white dark:bg-zinc-900/70",
        "border border-zinc-100 dark:border-zinc-800",
        "rounded-xl p-4",
        "backdrop-blur-xl"
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <div
          className={cn("p-1.5 rounded-lg", {
            "bg-emerald-100 dark:bg-emerald-900/30": trend === "up",
            "bg-red-100 dark:bg-red-900/30": trend === "down",
            "bg-zinc-100 dark:bg-zinc-800": trend === "neutral" || !trend,
          })}
        >
          <Icon
            className={cn("w-3.5 h-3.5", {
              "text-emerald-600 dark:text-emerald-400": trend === "up",
              "text-red-600 dark:text-red-400": trend === "down",
              "text-zinc-600 dark:text-zinc-400": trend === "neutral" || !trend,
            })}
          />
        </div>
      </div>
      <p
        className={cn("text-xl font-semibold font-mono", {
          "text-emerald-600 dark:text-emerald-400": trend === "up",
          "text-red-600 dark:text-red-400": trend === "down",
          "text-zinc-900 dark:text-zinc-50": trend === "neutral" || !trend,
        })}
      >
        {value}
      </p>
      {sub && <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">{sub}</p>}
    </div>
  )
}

export default function Content() {
  const [status, setStatus] = useState<Status | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<LiveTrade[]>([])
  const [runtimeCfg, setRuntimeCfg] = useState<Config | null>(null)
  const [error, setError] = useState("")

  const fetchData = async () => {
    try {
      const [s, p, t, cfg] = await Promise.all([
        api.getStatus(),
        api.getPositions(),
        api.getTrades(10),
        api.getConfig(),
      ])
      setStatus(s)
      setPositions(p)
      setTrades(t)
      setRuntimeCfg(cfg)
      setError("")
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch data"
      setError(message)
    }
  }

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 10000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
            <Activity className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
            Overview
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
            Real-time trading dashboard
          </p>
          {status && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 font-mono space-x-3">
              {status.uptime_since_deploy_label && status.deployed_at ? (
                <span title={status.deployed_at}>
                  自上次部署: {status.uptime_since_deploy_label}
                </span>
              ) : (
                <span title="在云服务器执行过 deploy.sh 后会写入 data/deployed_at.txt">
                  自上次部署: 未记录
                </span>
              )}
              {status.uptime_since_restart_label ? (
                <span title={status.process_started_at ?? ""}>
                  本进程: {status.uptime_since_restart_label}
                </span>
              ) : null}
            </p>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
          <AlertCircle className="w-4 h-4 text-red-500" />
          <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total Balance"
          value={status ? `$${status.total_balance.toFixed(2)}` : "---"}
          icon={Wallet}
          trend="neutral"
        />
        <StatCard
          label="Available"
          value={status ? `$${status.available_balance.toFixed(2)}` : "---"}
          icon={CreditCard}
          trend="neutral"
        />
        <StatCard
          label="Unrealized PnL"
          value={
            status
              ? `${status.unrealized_pnl >= 0 ? "+" : ""}$${status.unrealized_pnl.toFixed(2)}`
              : "---"
          }
          icon={status && status.unrealized_pnl >= 0 ? TrendingUp : TrendingDown}
          trend={status ? (status.unrealized_pnl >= 0 ? "up" : "down") : "neutral"}
        />
        <StatCard
          label="Daily PnL"
          value={
            status
              ? `${status.daily_pnl >= 0 ? "+" : ""}$${status.daily_pnl.toFixed(2)}`
              : "---"
          }
          icon={status && status.daily_pnl >= 0 ? TrendingUp : TrendingDown}
          trend={status ? (status.daily_pnl >= 0 ? "up" : "down") : "neutral"}
          sub={`${status?.open_positions || 0} open positions`}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Current Positions */}
        <div className="bg-white dark:bg-[#0F0F12] rounded-xl flex flex-col border border-gray-200 dark:border-[#1F1F23]">
          <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Briefcase className="w-3.5 h-3.5 text-zinc-900 dark:text-zinc-50" />
              Current Positions
              <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
                ({positions.length})
              </span>
            </h2>
            <Link
              to="/positions"
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
            >
              View all
            </Link>
          </div>
          <div className="p-3 flex-1">
            {positions.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-zinc-400 dark:text-zinc-500">
                No open positions
              </div>
            ) : (
              <div className="space-y-1">
                {positions.map((p, i) => (
                  <div
                    key={`${p.symbol}-${i}`}
                    className={cn(
                      "group flex items-center justify-between",
                      "p-2.5 rounded-lg",
                      "hover:bg-zinc-100 dark:hover:bg-zinc-800/50",
                      "transition-all duration-200"
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={cn("p-1.5 rounded-lg", {
                          "bg-emerald-100 dark:bg-emerald-900/30": p.side === "LONG",
                          "bg-red-100 dark:bg-red-900/30": p.side === "SHORT",
                        })}
                      >
                        {p.side === "LONG" ? (
                          <ArrowUpRight className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        ) : (
                          <ArrowDownLeft className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
                        )}
                      </div>
                      <div>
                        <h3 className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {p.symbol}
                        </h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {p.side} {p.leverage}x
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <span
                        className={cn("text-xs font-medium font-mono", {
                          "text-emerald-600 dark:text-emerald-400": p.unrealized_pnl >= 0,
                          "text-red-600 dark:text-red-400": p.unrealized_pnl < 0,
                        })}
                      >
                        {p.unrealized_pnl >= 0 ? "+" : ""}
                        {p.unrealized_pnl.toFixed(2)}
                      </span>
                      <p className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
                        {p.entry_price.toFixed(4)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-2 border-t border-zinc-100 dark:border-zinc-800">
            <Link
              to="/positions"
              className={cn(
                "w-full flex items-center justify-center gap-2",
                "py-2 px-3 rounded-lg",
                "text-xs font-medium",
                "bg-zinc-900 dark:bg-zinc-50",
                "text-zinc-50 dark:text-zinc-900",
                "hover:bg-zinc-800 dark:hover:bg-zinc-200",
                "shadow-sm hover:shadow",
                "transition-all duration-200"
              )}
            >
              <span>Manage Positions</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {/* Recent Trades */}
        <div className="bg-white dark:bg-[#0F0F12] rounded-xl flex flex-col border border-gray-200 dark:border-[#1F1F23]">
          <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Activity className="w-3.5 h-3.5 text-zinc-900 dark:text-zinc-50" />
              Recent Trades
              <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
                ({trades.length})
              </span>
            </h2>
            <Link
              to="/trades"
              className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors"
            >
              View all
            </Link>
          </div>
          <div className="p-3 flex-1">
            {trades.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-sm text-zinc-400 dark:text-zinc-500">
                No trades yet
              </div>
            ) : (
              <div className="space-y-1">
                {trades.slice(0, 6).map((t, i) => (
                  <div
                    key={`${t.symbol}-${i}`}
                    className={cn(
                      "group flex items-center gap-3",
                      "p-2.5 rounded-lg",
                      "hover:bg-zinc-100 dark:hover:bg-zinc-800/50",
                      "transition-all duration-200"
                    )}
                  >
                    <div
                      className={cn(
                        "p-2 rounded-lg",
                        "bg-zinc-100 dark:bg-zinc-800",
                        "border border-zinc-200 dark:border-zinc-700"
                      )}
                    >
                      {t.side === "LONG" ? (
                        <ArrowUpRight className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                      ) : (
                        <ArrowDownLeft className="w-4 h-4 text-red-600 dark:text-red-400" />
                      )}
                    </div>
                    <div className="flex-1 flex items-center justify-between min-w-0">
                      <div className="space-y-0.5">
                        <h3 className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {t.symbol}
                        </h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {t.exit_time
                            ? t.exit_time.slice(0, 19).replace("T", " ")
                            : "---"}
                        </p>
                      </div>
                      <div className="flex items-center gap-1.5 pl-3">
                        <span
                          className={cn("text-xs font-medium font-mono", {
                            "text-emerald-600 dark:text-emerald-400": t.pnl_usdt >= 0,
                            "text-red-600 dark:text-red-400": t.pnl_usdt < 0,
                          })}
                        >
                          {t.pnl_usdt >= 0 ? "+" : ""}
                          {t.pnl_usdt.toFixed(2)}
                        </span>
                        {t.pnl_usdt >= 0 ? (
                          <ArrowUpRight className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                        ) : (
                          <ArrowDownLeft className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-2 border-t border-zinc-100 dark:border-zinc-800">
            <Link
              to="/trades"
              className={cn(
                "w-full flex items-center justify-center gap-2",
                "py-2 px-3 rounded-lg",
                "text-xs font-medium",
                "bg-zinc-900 dark:bg-zinc-50",
                "text-zinc-50 dark:text-zinc-900",
                "hover:bg-zinc-800 dark:hover:bg-zinc-200",
                "shadow-sm hover:shadow",
                "transition-all duration-200"
              )}
            >
              <span>View All Trades</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>
      </div>

      {runtimeCfg && (
        <div className="bg-white dark:bg-[#0F0F12] rounded-xl border border-gray-200 dark:border-[#1F1F23] overflow-hidden">
          <div className="p-4 border-b border-zinc-100 dark:border-zinc-800 flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                <SlidersHorizontal className="w-3.5 h-3.5 text-zinc-900 dark:text-zinc-50" />
                运行时参数
              </h2>
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1">
                资金为账户级；Rolling 为每路策略合并全局
                <code className="text-[10px] mx-0.5">rolling</code>
                后的进程内快照（只读；
                <Link
                  to="/settings"
                  className="text-zinc-700 dark:text-zinc-300 underline-offset-2 hover:underline"
                >
                  Settings
                </Link>{" "}
                / data/config.json）。
              </p>
            </div>
          </div>
          <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
                资金与仓位上限
              </h3>
              <ParamRow label="杠杆" value={`${runtimeCfg.leverage}x`} />
              <ParamRow label="最大持仓数" value={String(runtimeCfg.max_positions)} />
              <ParamRow
                label="每日最大开仓"
                value={String(runtimeCfg.max_entries_per_day)}
              />
              <ParamRow
                label="保证金模式"
                value={runtimeCfg.margin_mode === "percent" ? "余额百分比" : "固定金额"}
              />
              {runtimeCfg.margin_mode === "percent" ? (
                <ParamRow label="保证金比例" value={`${runtimeCfg.margin_pct}%`} />
              ) : (
                <ParamRow
                  label="固定保证金"
                  value={`${runtimeCfg.live_fixed_margin_usdt} USDT`}
                />
              )}
              <ParamRow
                label="每日亏损限额"
                value={
                  runtimeCfg.daily_loss_limit_usdt <= 0
                    ? "不限"
                    : `${runtimeCfg.daily_loss_limit_usdt} USDT`
                }
              />
              <ParamRow
                label="持仓监控间隔"
                value={`${runtimeCfg.monitor_interval_seconds}s`}
              />
              <ParamRow
                label="配置声明 strategies"
                value={
                  runtimeCfg.strategies?.length
                    ? runtimeCfg.strategies
                        .map(
                          (s) =>
                            `${s.id}${s.enabled ? "" : "(关)"}[${s.kind}]`
                        )
                        .join(" · ")
                    : "—"
                }
              />
            </div>
            <div className="space-y-3 min-w-0">
              <h3 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Rolling（每路策略）
              </h3>
              {(runtimeCfg.strategy_runtimes &&
              runtimeCfg.strategy_runtimes.length > 0
                ? runtimeCfg.strategy_runtimes
                : [runtimeCfg.rolling]
              ).map((r, idx) => (
                <RollingRuntimeBlock
                  key={`${r.strategy_id}-${idx}`}
                  rolling={r}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
