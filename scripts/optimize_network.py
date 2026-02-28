#!/usr/bin/env python3
"""网络配置优化脚本

自动优化 duo-live 的网络配置，减少网络错误。

使用方法:
    python scripts/optimize_network.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def optimize_binance_client():
    """优化 BinanceFuturesClient 的网络配置"""
    
    client_file = Path("live/binance_client.py")
    
    if not client_file.exists():
        print("❌ 找不到 live/binance_client.py")
        return False
    
    content = client_file.read_text(encoding='utf-8')
    
    # 检查是否已经优化过
    if "_MAX_RETRIES = 5" in content:
        print("✅ 网络配置已经是优化版本")
        return True
    
    print("🔧 优化网络配置...")
    
    # 替换重试次数
    content = content.replace(
        "_MAX_RETRIES = 3",
        "_MAX_RETRIES = 5  # 优化：增加重试次数"
    )
    
    # 替换重试间隔
    content = content.replace(
        "_RETRY_BACKOFF = [1, 2, 4]",
        "_RETRY_BACKOFF = [2, 4, 8, 16, 32]  # 优化：更长的等待时间"
    )
    
    # 替换默认超时
    content = content.replace(
        "timeout: float = 30.0",
        "timeout: float = 60.0  # 优化：增加超时时间"
    )
    
    # 保存文件
    client_file.write_text(content, encoding='utf-8')
    
    print("✅ BinanceFuturesClient 网络配置已优化")
    print("   - 重试次数: 3 → 5")
    print("   - 重试间隔: [1,2,4] → [2,4,8,16,32]")
    print("   - 超时时间: 30s → 60s")
    
    return True


def optimize_monitor_interval():
    """优化监控间隔，减少请求频率"""
    
    config_file = Path("live/live_config.py")
    
    if not config_file.exists():
        print("❌ 找不到 live/live_config.py")
        return False
    
    content = config_file.read_text(encoding='utf-8')
    
    # 检查是否已经优化过
    if "monitor_interval_seconds: int = 60" in content:
        print("✅ 监控间隔已经是优化版本")
        return True
    
    print("🔧 优化监控间隔...")
    
    # 替换监控间隔
    content = content.replace(
        "monitor_interval_seconds: int = 30",
        "monitor_interval_seconds: int = 60  # 优化：降低请求频率"
    )
    
    # 保存文件
    config_file.write_text(content, encoding='utf-8')
    
    print("✅ 监控间隔已优化")
    print("   - 监控间隔: 30s → 60s")
    
    return True


def create_network_monitor_script():
    """创建网络监控脚本"""
    
    script_dir = Path("scripts")
    script_dir.mkdir(exist_ok=True)
    
    monitor_script = script_dir / "monitor_network.sh"
    
    content = """#!/bin/bash
# 网络质量监控脚本

echo "开始监控网络质量..."
echo "按 Ctrl+C 停止"
echo ""

while true; do
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
    
    # 测试延迟
    echo -n "Ping 延迟: "
    ping -c 3 fapi.binance.com 2>/dev/null | grep "avg" | awk -F'/' '{print $5 " ms"}' || echo "失败"
    
    # 测试 API 响应
    echo -n "API 响应: "
    response_time=$(curl -o /dev/null -s -w '%{time_total}' https://fapi.binance.com/fapi/v1/time 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "${response_time}s"
    else
        echo "失败"
    fi
    
    echo ""
    sleep 60
done
"""
    
    monitor_script.write_text(content)
    monitor_script.chmod(0o755)
    
    print("✅ 网络监控脚本已创建: scripts/monitor_network.sh")
    print("   运行: ./scripts/monitor_network.sh")
    
    return True


def show_recommendations():
    """显示优化建议"""
    
    print("\n" + "=" * 60)
    print("📋 优化建议")
    print("=" * 60)
    
    print("\n1. 测试网络连接:")
    print("   ping fapi.binance.com")
    print("   curl -I https://fapi.binance.com/fapi/v1/ping")
    
    print("\n2. 运行网络监控:")
    print("   ./scripts/monitor_network.sh")
    
    print("\n3. 检查系统日志:")
    print("   grep '网络错误' logs/duo-live.log | tail -20")
    
    print("\n4. 如果问题持续，考虑:")
    print("   - 使用更稳定的 VPN")
    print("   - 更换服务器到网络质量更好的地区")
    print("   - 降低监控频率（已优化为60秒）")
    
    print("\n5. 重启服务使配置生效:")
    print("   pm2 restart duo-live-backend")
    
    print("\n" + "=" * 60)
    print("详细文档: docs/NETWORK_TROUBLESHOOTING.md")
    print("=" * 60 + "\n")


def main():
    """主函数"""
    print("=" * 60)
    print("duo-live 网络配置优化工具")
    print("=" * 60)
    print()
    
    success = True
    
    # 优化 BinanceFuturesClient
    if not optimize_binance_client():
        success = False
    
    print()
    
    # 优化监控间隔
    if not optimize_monitor_interval():
        success = False
    
    print()
    
    # 创建网络监控脚本
    if not create_network_monitor_script():
        success = False
    
    # 显示建议
    show_recommendations()
    
    if success:
        print("✅ 所有优化已完成！")
        print("⚠️  请重启服务使配置生效: pm2 restart duo-live-backend")
        return 0
    else:
        print("❌ 部分优化失败，请检查错误信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
