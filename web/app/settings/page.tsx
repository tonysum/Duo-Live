"use client"

import { useEffect, useState } from "react"
import { api, Config } from "@/lib/api"
import { cn } from "@/lib/utils"
import Layout from "@/components/kokonutui/layout"
import {
    Settings,
    AlertCircle,
    Save,
    Loader2,
    RotateCcw,
} from "lucide-react"

interface FieldConfig {
    key: keyof Config
    label: string
    description: string
    type: "int" | "float"
    min?: number
    max?: number
    step?: number
    unit?: string
}

const FIELDS: FieldConfig[] = [
    {
        key: "leverage",
        label: "杠杆倍数",
        description: "合约杠杆（整数）",
        type: "int",
        min: 1,
        max: 125,
        unit: "x",
    },
    {
        key: "max_positions",
        label: "最大持仓数",
        description: "同时持有的最大仓位数量",
        type: "int",
        min: 1,
        max: 20,
    },
    {
        key: "max_entries_per_day",
        label: "每日最大开仓次数",
        description: "每天允许的最大开仓次数",
        type: "int",
        min: 1,
        max: 50,
    },
    {
        key: "live_fixed_margin_usdt",
        label: "固定保证金",
        description: "每笔交易的固定保证金金额",
        type: "float",
        min: 1,
        max: 10000,
        step: 0.5,
        unit: "USDT",
    },
    {
        key: "daily_loss_limit_usdt",
        label: "每日亏损限额",
        description: "每日最大亏损限制（0 = 不限）",
        type: "float",
        min: 0,
        max: 10000,
        step: 1,
        unit: "USDT",
    },
    {
        key: "margin_pct",
        label: "保证金比例",
        description: "百分比模式下，每笔使用可用余额的百分比",
        type: "float",
        min: 0.1,
        max: 100,
        step: 0.5,
        unit: "%",
    },
]

export default function SettingsPage() {
    const [config, setConfig] = useState<Config | null>(null)
    const [draft, setDraft] = useState<Partial<Config>>({})
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState("")
    const [success, setSuccess] = useState("")

    useEffect(() => {
        api
            .getConfig()
            .then((c) => {
                setConfig(c)
                setDraft({
                    leverage: c.leverage,
                    max_positions: c.max_positions,
                    max_entries_per_day: c.max_entries_per_day,
                    live_fixed_margin_usdt: c.live_fixed_margin_usdt,
                    daily_loss_limit_usdt: c.daily_loss_limit_usdt,
                    margin_mode: c.margin_mode,
                    margin_pct: c.margin_pct,
                })
            })
            .catch((err) => {
                setError(err instanceof Error ? err.message : "Failed to load config")
            })
    }, [])

    const handleChange = (key: keyof Config, value: string) => {
        const field = FIELDS.find((f) => f.key === key)
        if (!field) return
        const num = field.type === "int" ? parseInt(value, 10) : parseFloat(value)
        if (!isNaN(num)) {
            setDraft((prev) => ({ ...prev, [key]: num }))
        }
    }

    const hasChanges =
        config &&
        FIELDS.some(
            (f) =>
                draft[f.key] !== undefined && draft[f.key] !== config[f.key]
        )

    const handleSave = async () => {
        setSaving(true)
        setError("")
        setSuccess("")
        try {
            const res = await api.updateConfig(draft)
            setConfig((prev) => (prev ? { ...prev, ...res } : prev))
            setSuccess("配置已保存 ✓")
            setTimeout(() => setSuccess(""), 3000)
        } catch (err) {
            setError(err instanceof Error ? err.message : "Save failed")
        } finally {
            setSaving(false)
        }
    }

    const handleReset = () => {
        if (!config) return
        setDraft({
            leverage: config.leverage,
            max_positions: config.max_positions,
            max_entries_per_day: config.max_entries_per_day,
            live_fixed_margin_usdt: config.live_fixed_margin_usdt,
            daily_loss_limit_usdt: config.daily_loss_limit_usdt,
            margin_mode: config.margin_mode,
            margin_pct: config.margin_pct,
        })
        setSuccess("")
    }

    return (
        <Layout>
            <div className="space-y-6 max-w-2xl">
                {/* Header */}
                <div>
                    <h1 className="text-lg font-bold text-zinc-900 dark:text-white flex items-center gap-2">
                        <Settings className="w-4 h-4 text-zinc-900 dark:text-zinc-50" />
                        Settings
                    </h1>
                    <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
                        资金与风控参数（修改后实时生效，重启保留）
                    </p>
                </div>

                {error && (
                    <div className="flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
                        <AlertCircle className="w-4 h-4 text-red-500" />
                        <span className="text-sm text-red-600 dark:text-red-400">{error}</span>
                    </div>
                )}

                {success && (
                    <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800">
                        <Save className="w-4 h-4 text-emerald-500" />
                        <span className="text-sm text-emerald-600 dark:text-emerald-400">{success}</span>
                    </div>
                )}

                {/* Margin mode toggle */}
                <div
                    className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl",
                        "px-5 py-4"
                    )}
                >
                    <div className="space-y-0.5 mb-3">
                        <label className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                            保证金模式
                        </label>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">
                            固定金额或按可用余额百分比计算
                        </p>
                    </div>
                    <div className="flex items-center gap-1 p-1 bg-zinc-100 dark:bg-zinc-800 rounded-lg w-fit">
                        {(["fixed", "percent"] as const).map((mode) => (
                            <button
                                key={mode}
                                type="button"
                                onClick={() => setDraft((prev) => ({ ...prev, margin_mode: mode }))}
                                className={cn(
                                    "px-4 py-1.5 rounded-md text-xs font-medium transition-all duration-200",
                                    draft.margin_mode === mode
                                        ? "bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100 shadow-sm"
                                        : "text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300"
                                )}
                            >
                                {mode === "fixed" ? "固定金额" : "余额百分比"}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Config fields */}
                <div
                    className={cn(
                        "bg-white dark:bg-zinc-900/70",
                        "border border-zinc-100 dark:border-zinc-800",
                        "rounded-xl shadow-sm backdrop-blur-xl",
                        "divide-y divide-zinc-100 dark:divide-zinc-800"
                    )}
                >
                    {FIELDS.filter((field) => {
                        if (field.key === "live_fixed_margin_usdt") return draft.margin_mode === "fixed"
                        if (field.key === "margin_pct") return draft.margin_mode === "percent"
                        return true
                    }).map((field) => (
                        <div
                            key={field.key}
                            className="px-5 py-4 space-y-2"
                        >
                            <div className="space-y-0.5">
                                <label className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                                    {field.label}
                                </label>
                                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                                    {field.description}
                                </p>
                            </div>
                            <div className="flex items-center gap-2">
                                <input
                                    type="number"
                                    value={draft[field.key] ?? ""}
                                    onChange={(e) => handleChange(field.key, e.target.value)}
                                    min={field.min}
                                    max={field.max}
                                    step={field.step ?? 1}
                                    className={cn(
                                        "w-32 px-3 py-1.5 text-sm font-mono",
                                        "bg-zinc-50 dark:bg-zinc-800",
                                        "border border-zinc-200 dark:border-zinc-700",
                                        "rounded-lg",
                                        "text-zinc-900 dark:text-zinc-100",
                                        "focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500",
                                        "transition-colors"
                                    )}
                                />
                                {field.unit && (
                                    <span className="text-xs text-zinc-500 dark:text-zinc-400">
                                        {field.unit}
                                    </span>
                                )}
                            </div>
                        </div>
                    ))}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-3">
                    <button
                        type="button"
                        onClick={handleSave}
                        disabled={saving || !hasChanges}
                        className={cn(
                            "inline-flex items-center gap-2",
                            "px-4 py-2 rounded-lg",
                            "text-sm font-medium",
                            "bg-emerald-600 hover:bg-emerald-700",
                            "text-white",
                            "shadow-sm hover:shadow",
                            "transition-all duration-200",
                            "disabled:opacity-40 disabled:cursor-not-allowed"
                        )}
                    >
                        {saving ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                            <Save className="w-3.5 h-3.5" />
                        )}
                        {saving ? "保存中..." : "保存设置"}
                    </button>
                    <button
                        type="button"
                        onClick={handleReset}
                        disabled={!hasChanges}
                        className={cn(
                            "inline-flex items-center gap-2",
                            "px-4 py-2 rounded-lg",
                            "text-sm font-medium",
                            "bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200 dark:hover:bg-zinc-700",
                            "text-zinc-700 dark:text-zinc-300",
                            "transition-all duration-200",
                            "disabled:opacity-40 disabled:cursor-not-allowed"
                        )}
                    >
                        <RotateCcw className="w-3.5 h-3.5" />
                        重置
                    </button>
                </div>
            </div>
        </Layout>
    )
}
