#!/bin/bash
# ============================================================
#  duo-live 守护脚本 — 进程退出后自动重启
#  用法:  ./run_forever.sh [--live]
#  停止:  Ctrl+C 两次 (第一次停进程，第二次停脚本)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/restart.log"
MODE="${1:---live}"   # 默认 --live

MAX_RESTARTS=50       # 最大连续重启次数
RESTART_DELAY=10      # 重启间隔 (秒)
restart_count=0

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cleanup() {
    log "🛑 收到退出信号，停止守护"
    exit 0
}
trap cleanup SIGINT SIGTERM

log "=========================================="
log "🚀 duo-live 守护脚本启动 (mode: $MODE)"
log "=========================================="

while [ $restart_count -lt $MAX_RESTARTS ]; do
    restart_count=$((restart_count + 1))
    log "▶️  第 $restart_count 次启动..."

    cd "$SCRIPT_DIR" && uv run python -m live run $MODE
    EXIT_CODE=$?

    log "⚠️  进程退出 (code: $EXIT_CODE)"

    if [ $EXIT_CODE -eq 0 ]; then
        log "✅ 正常退出，不再重启"
        break
    fi

    log "⏳ ${RESTART_DELAY}s 后重启..."
    sleep $RESTART_DELAY
done

if [ $restart_count -ge $MAX_RESTARTS ]; then
    log "🚨 连续重启 $MAX_RESTARTS 次，停止守护"
fi
