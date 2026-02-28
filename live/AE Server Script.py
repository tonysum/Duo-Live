#!/usr/bin/env python3
"""
AE Server - Auto Exchange 自动交易软件（服务器版本）
基于 ae.py 改造，去除 Tkinter GUI，添加 Flask Web API

核心功能：
- 无GUI后台运行
- Flask Web监控界面
- 完整的API接口（查看+操作）
- 支持远程控制（手动平仓、修改止盈止损等）

作者：量化交易助手
版本：v2.0 (Server Edition)
创建时间：2026-02-12
"""

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import threading
import time
import json
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
import os
import configparser
import signal
import sys
import glob
import smtplib
import uuid  # ✨ 用于生成持仓唯一ID
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# ==================== 配置日志 ====================
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f"ae_server_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 数据库路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 持仓记录文件
POSITIONS_RECORD_FILE = os.path.join(SCRIPT_DIR, "positions_record.json")

# ==================== 邮件报警配置 ====================
ALERT_EMAIL = "13910306825@163.com"  # 报警接收邮箱

def generate_daily_report() -> str:
    """生成每日交易报告"""
    try:
        report_lines = []
        report_lines.append("=" * 60)
        report_lines.append("📊 AE交易系统 - 每日交易报告")
        report_lines.append("=" * 60)
        report_lines.append(f"📅 报告日期: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        report_lines.append(f"⏰ 生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        report_lines.append("")

        # 检查strategy是否已初始化
        if strategy is None:
            report_lines.append("⚠️ 策略引擎未初始化，无法获取详细数据")
            report_lines.append("")
        else:
            # 1. 账户信息
            report_lines.append("💰 账户信息")
            report_lines.append("-" * 30)
            try:
                account_info = strategy.server_get_account_info()
                if account_info:
                    report_lines.append(f"总余额: ${account_info['total_balance']:.2f}")
                    report_lines.append(f"可用余额: ${account_info['available_balance']:.2f}")
                    report_lines.append(f"未实现盈亏: ${account_info['unrealized_pnl']:.2f}")
                    report_lines.append(f"维持保证金: ${account_info['maintenance_margin']:.2f}")
                else:
                    report_lines.append("❌ 无法获取账户信息")
            except Exception as e:
                report_lines.append(f"❌ 获取账户信息失败: {e}")
            report_lines.append("")

            # 2. 持仓情况
            report_lines.append("📈 当前持仓")
            report_lines.append("-" * 30)
            try:
                if strategy and strategy.positions:
                    for pos in strategy.positions:
                        direction = "多头" if pos.get('direction') == 'long' else "空头"

                        # 实时计算当前盈亏
                        try:
                            ticker = strategy.client.futures_symbol_ticker(symbol=pos['symbol'])
                            current_price = float(ticker['price'])
                            entry_price = pos['entry_price']
                            quantity = abs(pos.get('quantity', 0))

                            if pos.get('direction') == 'long':
                                pnl_pct = (current_price - entry_price) / entry_price
                            else:
                                pnl_pct = (entry_price - current_price) / entry_price

                            position_value = quantity * entry_price
                            pnl_value = pnl_pct * position_value * strategy.leverage
                            pnl_display = f"${pnl_value:.2f} ({pnl_pct*100:.2f}%)"
                        except Exception as e:
                            pnl_display = f"计算失败: {e}"

                        pnl_color = "🟢" if pnl_value > 0 else "🔴"
                        report_lines.append(f"{pos['symbol']}: {direction} | "
                                          f"数量:{quantity:.0f} | "
                                          f"价格:${entry_price:.6f} | "
                                          f"{pnl_color}盈亏:{pnl_display}")
                else:
                    report_lines.append("无持仓")
            except Exception as e:
                report_lines.append(f"❌ 获取持仓信息失败: {e}")
            report_lines.append("")

            # 3. 过去24小时统计
            report_lines.append("📊 过去24小时统计")
            report_lines.append("-" * 30)
            try:
                # 获取24小时前的收入记录
                yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
                start_timestamp = int(yesterday.timestamp() * 1000)

                income_history = strategy.client.futures_income_history(
                    startTime=start_timestamp,
                    incomeType='REALIZED_PNL'
                )

                if income_history:
                    total_24h_pnl = sum(float(record['income']) for record in income_history)
                    trade_count = len(income_history)

                    report_lines.append(f"已实现盈亏: ${total_24h_pnl:.2f}")
                    report_lines.append(f"交易次数: {trade_count}")

                    # 统计盈利/亏损次数
                    profitable_trades = len([r for r in income_history if float(r['income']) > 0])
                    loss_trades = len([r for r in income_history if float(r['income']) <= 0])

                    report_lines.append(f"盈利交易: {profitable_trades}")
                    report_lines.append(f"亏损交易: {loss_trades}")
                    report_lines.append(f"胜率: {profitable_trades/trade_count*100:.1f}%" if trade_count > 0 else "胜率: 0%")
                else:
                    report_lines.append("过去24小时无交易记录")
            except Exception as e:
                report_lines.append(f"❌ 获取交易统计失败: {e}")
            report_lines.append("")

            # 4. 最近的仓位变动记录
            report_lines.append("📋 最近仓位变动")
            report_lines.append("-" * 30)
            try:
                # 读取最近的仓位变动日志
                position_log_file = os.path.join(log_dir, 'position_changes.log')
                if os.path.exists(position_log_file):
                    with open(position_log_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()

                    # 获取最近24小时的记录
                    recent_changes = []
                    for line in reversed(lines):
                        if '时间:' in line:
                            try:
                                # 解析时间
                                time_str = line.split('时间:')[1].strip()
                                log_time = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S UTC')
                                if log_time > yesterday:
                                    recent_changes.append(line.strip())
                            except:
                                continue

                    if recent_changes:
                        for change in recent_changes[:10]:  # 最多显示10条
                            if '✅' in change and ('手动平仓' in change or '自动平仓' in change):
                                report_lines.append(change.replace('✅', '•'))
                    else:
                        report_lines.append("过去24小时无仓位变动")
                else:
                    report_lines.append("仓位变动日志文件不存在")
            except Exception as e:
                report_lines.append(f"❌ 读取仓位变动日志失败: {e}")
            report_lines.append("")

            # 5. 系统状态
            report_lines.append("🔧 系统状态")
            report_lines.append("-" * 30)
            try:
                uptime_hours = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600 if start_time else 0
                report_lines.append(f"系统运行时间: {uptime_hours:.1f} 小时")
                report_lines.append(f"持仓监控状态: {'正常' if is_running else '已停止'}")
                report_lines.append(f"当前持仓数量: {len(strategy.positions) if strategy else 0}")
            except Exception as e:
                report_lines.append(f"❌ 获取系统状态失败: {e}")

            # 6. 详细交易记录
            report_lines.append("")
            report_lines.append("📋 详细交易记录")
            report_lines.append("-" * 30)

            try:
                # 获取过去24小时的所有交易记录
                yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
                start_timestamp = int(yesterday.timestamp() * 1000)

                income_history = strategy.client.futures_income_history(
                    startTime=start_timestamp,
                    limit=100  # 获取更多记录
                )

                if income_history:
                    report_lines.append(f"共 {len(income_history)} 笔交易:")
                    report_lines.append("")

                    for i, record in enumerate(income_history[:20], 1):  # 最多显示20笔
                        income = float(record['income'])
                        timestamp = datetime.fromtimestamp(record['time'] / 1000, tz=timezone.utc)
                        symbol = record.get('symbol', 'Unknown')
                        income_type = record.get('incomeType', 'Unknown')

                        pnl_str = f"+${income:.2f}" if income > 0 else f"${income:.2f}"
                        color = "🟢" if income > 0 else "🔴"

                        report_lines.append(f"{i:2d}. {symbol} | {timestamp.strftime('%m-%d %H:%M')} | "
                                          f"{income_type} | {color}{pnl_str}")

                    if len(income_history) > 20:
                        report_lines.append(f"... 还有 {len(income_history) - 20} 笔交易")
                else:
                    report_lines.append("过去24小时无交易记录")

            except Exception as e:
                report_lines.append(f"❌ 获取交易记录失败: {e}")

            # 7. 持仓详细信息
            report_lines.append("")
            report_lines.append("📊 当前持仓详情")
            report_lines.append("-" * 30)

            try:
                if strategy and strategy.positions:
                    for pos in strategy.positions:
                        direction = "多头" if pos.get('direction') == 'long' else "空头"
                        entry_time_str = pos.get('entry_time', 'Unknown')
                        entry_price = pos.get('entry_price', 'Unknown')
                        quantity = abs(pos.get('quantity', 0))
                        symbol = pos.get('symbol', 'Unknown')

                        # 计算当前盈亏
                        try:
                            ticker = strategy.client.futures_symbol_ticker(symbol=symbol)
                            current_price = float(ticker['price'])

                            if direction == '多头':
                                pnl_pct = (current_price - entry_price) / entry_price
                            else:
                                pnl_pct = (entry_price - current_price) / entry_price

                            position_value = quantity * entry_price
                            pnl_value = pnl_pct * position_value * strategy.leverage
                            pnl_display = f"${pnl_value:.2f} ({pnl_pct*100:.2f}%)"
                        except Exception as e:
                            pnl_display = f"计算失败: {e}"

                        report_lines.append(f"交易对: {symbol}")
                        report_lines.append(f"  方向: {direction}")
                        report_lines.append(f"  建仓时间: {entry_time_str}")
                        report_lines.append(f"  建仓价格: ${entry_price}")
                        report_lines.append(f"  持仓数量: {quantity:.0f}")
                        report_lines.append(f"  当前价格: ${current_price:.6f}" if 'current_price' in locals() else "  当前价格: 获取失败")
                        report_lines.append(f"  当前盈亏: {pnl_display}")
                        report_lines.append("")
                else:
                    report_lines.append("当前无持仓")

            except Exception as e:
                report_lines.append(f"❌ 获取持仓详情失败: {e}")

        report_lines.append("")
        report_lines.append("---")
        report_lines.append("此报告由AE交易系统自动生成")
        report_lines.append(f"服务器: {os.uname().nodename if hasattr(os, 'uname') else 'Unknown'}")

        return "\n".join(report_lines)

    except Exception as e:
        return f"生成报告失败: {e}"

def send_daily_report():
    """发送每日交易报告邮件"""
    try:
        report_content = generate_daily_report()

        # 保存报告到文件
        report_file = f"daily_report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt"
        report_path = os.path.join(log_dir, report_file)

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)

        # 发送邮件
        subject = f"每日交易报告 - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        message = f"请查看附件中的每日交易报告。\n\n报告生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"

        # 创建带附件的邮件
        msg = MIMEMultipart()
        msg['From'] = os.getenv('SMTP_EMAIL')
        msg['To'] = ALERT_EMAIL
        msg['Subject'] = f"[AE交易系统] {subject}"

        # 邮件正文
        body = MIMEText(message, 'plain', 'utf-8')
        msg.attach(body)

        # 添加附件
        with open(report_path, 'r', encoding='utf-8') as f:
            attachment = MIMEText(f.read(), 'plain', 'utf-8')
            attachment.add_header('Content-Disposition', 'attachment', filename=report_file)
            msg.attach(attachment)

        # 发送邮件
        sender_email = os.getenv('SMTP_EMAIL')
        sender_password = os.getenv('SMTP_PASSWORD')

        if not sender_email or not sender_password:
            logging.error("❌ 未配置邮件发送账号")
            return

        server = smtplib.SMTP_SSL('smtp.163.com', 465)
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, ALERT_EMAIL, msg.as_string())
        server.quit()

        logging.info(f"✅ 每日交易报告已发送到 {ALERT_EMAIL}")
        print(f"✅ 每日交易报告已发送到 {ALERT_EMAIL}")

    except Exception as e:
        logging.error(f"❌ 发送每日报告失败: {e}")

def send_email_alert(subject: str, message: str):
    """发送邮件报警"""
    try:
        # 使用163邮箱SMTP服务（免费，需要授权码）
        # 注意：需要在环境变量中配置邮箱和授权码
        sender_email = os.getenv('SMTP_EMAIL')  # 发件邮箱
        sender_password = os.getenv('SMTP_PASSWORD')  # 授权码（不是邮箱密码）
        
        if not sender_email or not sender_password:
            logging.warning("⚠️ 未配置邮件发送账号，跳过邮件报警")
            return
        
        # 创建邮件
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = ALERT_EMAIL
        msg['Subject'] = f"[AE交易系统] {subject}"
        
        # 邮件正文
        body = f"""
AE自动交易系统报警

时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}

{message}

---
此邮件由AE交易系统自动发送
服务器: {os.uname().nodename if hasattr(os, 'uname') else 'Unknown'}
"""
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # 发送邮件
        with smtplib.SMTP_SSL('smtp.163.com', 465, timeout=10) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        
        logging.info(f"✅ 邮件报警已发送: {subject}")
        
    except Exception as e:
        logging.error(f"❌ 发送邮件报警失败: {e}")

class YesterdayDataCache:
    """昨日数据缓存类（避免重复API调用）"""
    
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.cache_date = None
        logging.info("📦 初始化昨日数据缓存")
    
    def get_yesterday_avg_sell_api(self, symbol: str) -> Optional[float]:
        """获取昨日平均小时卖量（带缓存）- API版本"""
        try:
            # 检查缓存是否过期
            today = datetime.now(timezone.utc).date()
            if self.cache_date != today:
                if self.cache_date:
                    logging.info(f"🔄 清空昨日缓存（日期变更: {self.cache_date} -> {today}）")
                self.cache = {}
                self.cache_date = today
            
            # 从缓存读取
            if symbol in self.cache:
                return self.cache[symbol]
            
            # 从API获取昨日日K线
            yesterday = today - timedelta(days=1)
            yesterday_start = int(datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
            yesterday_end = int(datetime.combine(yesterday, datetime.max.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
            
            klines = self.client.futures_klines(
                symbol=symbol,
                interval='1d',
                startTime=yesterday_start,
                endTime=yesterday_end,
                limit=1
            )
            
            if not klines:
                return None
            
            # 计算昨日平均小时卖量
            volume = float(klines[0][5])  # 总成交量
            active_buy_volume = float(klines[0][9])  # 主动买入量
            total_sell = volume - active_buy_volume
            avg_hour_sell = total_sell / 24.0
            
            # 缓存结果
            self.cache[symbol] = avg_hour_sell
            
            return avg_hour_sell
        
        except Exception as e:
            logging.error(f"❌ 获取 {symbol} 昨日数据失败: {e}")
            return None


# 备用交易对列表（API获取失败时使用）
BACKUP_SYMBOL_LIST = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
    'ADAUSDT', 'DOGEUSDT', 'MATICUSDT', 'DOTUSDT', 'AVAXUSDT',
    'SHIBUSDT', 'LTCUSDT', 'LINKUSDT', 'ATOMUSDT', 'UNIUSDT',
    'ETCUSDT', 'XLMUSDT', 'NEARUSDT', 'ALGOUSDT', 'ICPUSDT',
    'APTUSDT', 'FILUSDT', 'LDOUSDT', 'ARBUSDT', 'OPUSDT',
    'SUIUSDT', 'INJUSDT', 'TIAUSDT', 'ORDIUSDT', 'RUNEUSDT',
]


def load_config():
    """从配置文件加载配置"""
    config = configparser.ConfigParser()
    config_file = os.path.join(SCRIPT_DIR, "config.ini")
    
    if not os.path.exists(config_file):
        logging.error("❌ 配置文件 config.ini 不存在！")
        logging.error("请复制 config.ini.example 为 config.ini 并填入API密钥")
        raise FileNotFoundError("config.ini not found")
    
    config.read(config_file, encoding='utf-8')
    return config


class AutoExchangeStrategy:
    """自动交易策略核心类"""
    
    def __init__(self, config: configparser.ConfigParser):
        """初始化策略参数"""
        # 加载配置
        self.config = config
        
        # 🔐 安全改进：优先从环境变量读取API密钥
        api_key = os.getenv('BINANCE_API_KEY')
        api_secret = os.getenv('BINANCE_API_SECRET')
        
        # 如果环境变量未设置，降级从配置文件读取
        if api_key and api_secret:
            logging.info("✅ 从环境变量加载API密钥")
        else:
            logging.warning("⚠️ 环境变量未设置，从config.ini读取API密钥")
            try:
                api_key = config.get('BINANCE', 'api_key')
                api_secret = config.get('BINANCE', 'api_secret')
            except:
                logging.error("❌ 无法获取API密钥！请设置环境变量或配置config.ini")
                raise ValueError("API密钥未配置")
        
        logging.info("🔄 初始化币安客户端...")
        
        # 创建客户端（忽略初始化时的现货API ping错误）
        client_ready = False
        for attempt in range(3):
            try:
                # 临时捕获所有异常，因为Client()可能在__init__中调用ping
                self.client = Client(api_key, api_secret, tld='com', testnet=False)
                self.client.FUTURES_RECV_WINDOW = 10000
                client_ready = True
                break
            except Exception as e:
                error_msg = str(e)
                # 如果是SSL/ping相关错误，这可能只影响现货API
                if 'SSL' in error_msg or 'ping' in error_msg or 'api.binance.com' in error_msg:
                    logging.warning(f"⚠️ 现货API连接失败（可忽略，我们只用期货API）: {error_msg[:80]}...")
                    # 强制创建客户端实例
                    try:
                        import binance
                        self.client = object.__new__(Client)
                        self.client.API_KEY = api_key
                        self.client.API_SECRET = api_secret
                        self.client.FUTURES_RECV_WINDOW = 10000
                        # 初始化session
                        import requests
                        self.client.session = requests.Session()
                        self.client.session.headers.update({
                            'Accept': 'application/json',
                            'User-Agent': 'Mozilla/5.0',
                            'X-MBX-APIKEY': api_key
                        })
                        client_ready = True
                        logging.info("✅ 已绕过现货API测试，创建期货专用客户端")
                        break
                    except Exception as bypass_error:
                        if attempt < 2:
                            logging.warning(f"⚠️ 尝试 {attempt+1}/3 失败，2秒后重试...")
                            time.sleep(2)
                        else:
                            logging.error(f"❌ 客户端创建失败: {bypass_error}")
                            raise
                else:
                    if attempt < 2:
                        logging.warning(f"⚠️ 初始化失败 ({attempt+1}/3): {error_msg[:80]}")
                        time.sleep(2)
                    else:
                        raise
        
        if not client_ready:
            raise RuntimeError("无法创建币安客户端")
        
        # 测试期货API（这才是我们真正使用的）
        try:
            self.client.futures_ping()
            logging.info("✅ 期货API连接测试成功")
        except Exception as e:
            logging.warning(f"⚠️ 期货API ping失败: {e}")
            logging.warning("⚠️ 将在实际调用时重试")
        
        # 🆕 初始化昨日数据缓存（替代数据库）
        self.yesterday_cache = YesterdayDataCache(self.client)
        logging.info("✅ 昨日数据缓存初始化完成（API模式）")
        
        # 核心参数（从配置文件读取）
        self.leverage = config.getfloat('STRATEGY', 'leverage', fallback=3.0)
        self.position_size_ratio = config.getfloat('STRATEGY', 'position_size_ratio', fallback=0.09)
        self.max_positions = config.getint('STRATEGY', 'max_positions', fallback=10)
        self.max_daily_entries = config.getint('STRATEGY', 'max_daily_entries', fallback=6)
        
        # 信号阈值
        self.sell_surge_threshold = config.getfloat('SIGNAL', 'sell_surge_threshold', fallback=10)
        self.sell_surge_max = config.getfloat('SIGNAL', 'sell_surge_max', fallback=14008)

        # 🆕 当日买量倍数风控（从hm1l.py移植）
        self.enable_intraday_buy_ratio_filter = True  # ✅ 启用：当日买量倍数风控
        # 当日买量倍数：信号发生前12小时，每小时买量相对前一小时的最大比值
        # 📊 根据实际回测数据优化：
        #   - 5-7x 表现最佳（止盈率16.7%，止损率20.8%）✅
        #   - 10-15x 表现差（止盈率6.2%，止损率56.2%）❌
        #   - >15x 表现差（止盈率10.0%，止损率50.0%）❌
        self.intraday_buy_ratio_danger_ranges = [
            (4.81, 6.61),  # 危险区间1：4.81-6.61倍（过滤多空博弈信号）
            (9.45, 11.1),  # 危险区间2：9.45-11.1倍（过滤高波动信号）
        ]
        
        # 动态止盈参数
        self.strong_coin_tp_pct = config.getfloat('RISK', 'strong_coin_tp_pct', fallback=33.0)
        self.medium_coin_tp_pct = config.getfloat('RISK', 'medium_coin_tp_pct', fallback=21.0)
        self.weak_coin_tp_pct = config.getfloat('RISK', 'weak_coin_tp_pct', fallback=10.0)
        
        # 2小时判断参数
        self.dynamic_tp_2h_ratio = 0.6  # 强势K线占比60%
        self.dynamic_tp_2h_growth_threshold = 0.055  # 单根跌幅5.5%
        
        # 12小时判断参数
        self.dynamic_tp_12h_ratio = 0.6  # 强势K线占比60%
        self.dynamic_tp_12h_growth_threshold = 0.075  # 单根跌幅7.5%
        
        # 🚨 12小时及早平仓参数（新增）
        self.enable_12h_early_stop = True  # 是否启用12小时及早平仓
        self.early_stop_12h_threshold = 0.037  # 12小时涨幅阈值（3.7%）
        
        # 止损参数
        self.stop_loss_pct = config.getfloat('RISK', 'stop_loss_pct', fallback=18.0)
        self.max_gain_24h_threshold = config.getfloat('RISK', 'max_gain_24h_threshold', fallback=6.3) / 100
        self.max_hold_hours = config.getfloat('RISK', 'max_hold_hours', fallback=72)
        
        # 持仓管理
        self.positions = []  # 当前持仓列表
        self.daily_entries = 0  # 今日建仓数
        self.last_entry_date = None  # 上次建仓日期
        self.last_entry_hour = None  # 上次建仓小时（用于每小时限制）
        
        # 🔒 并发控制锁（防止重复建仓）
        import threading
        self.position_locks = {}  # symbol -> Lock
        self.position_lock_master = threading.Lock()  # 保护locks字典本身
        
        # 账户余额
        self.account_balance = 0.0
        
        # 加载现有持仓
        self.server_load_existing_positions()
        
        logging.info("✅ 策略引擎初始化完成")
        logging.info(f"   杠杆: {self.leverage}x, 单仓: {self.position_size_ratio*100:.0f}%, 最大持仓: {self.max_positions}")
        logging.info(f"   止盈: {self.strong_coin_tp_pct}/{self.medium_coin_tp_pct}/{self.weak_coin_tp_pct}%, 止损: {self.stop_loss_pct}%")
    
    def server_load_existing_positions(self):
        """启动时从交易所加载现有持仓（并从文件恢复真实建仓时间）- 服务器版本"""
        try:
            logging.info("🔍 加载交易所现有持仓...")
            
            # 先读取持仓记录文件
            positions_record = self.server_load_positions_record()
            
            # 🔧 API调用重试机制
            positions_info = None
            max_retries = 5
            retry_delay = 3  # 秒
            
            for attempt in range(1, max_retries + 1):
                try:
                    positions_info = self.client.futures_position_information()
                    logging.info(f"✅ 第{attempt}次尝试获取持仓信息成功")
                    break
                except Exception as e:
                    if attempt < max_retries:
                        logging.warning(f"⚠️ 第{attempt}次获取持仓信息失败: {e}，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        logging.error(f"❌ 尝试{max_retries}次后仍无法获取持仓信息: {e}")
                        raise
            
            if positions_info is None:
                raise Exception("无法从交易所获取持仓信息")
            
            loaded_count = 0
            for pos in positions_info:
                position_amt = float(pos['positionAmt'])
                
                # 只加载有持仓的交易对（做空持仓为负数）
                if position_amt < 0:
                    symbol = pos['symbol']
                    entry_price = float(pos['entryPrice'])
                    quantity = abs(position_amt)
                    
                    # 估算持仓价值（假设使用默认杠杆和仓位比例）
                    position_value = (quantity * entry_price) / self.leverage
                    
                    # 尝试从记录文件获取真实建仓时间
                    if symbol in positions_record:
                        signal_datetime = positions_record[symbol].get('signal_datetime')
                        entry_time_iso = positions_record[symbol]['entry_time']
                        tp_pct = positions_record[symbol].get('tp_pct', self.strong_coin_tp_pct)
                        tp_2h_checked = positions_record[symbol].get('tp_2h_checked', False)
                        tp_12h_checked = positions_record[symbol].get('tp_12h_checked', False)
                        # 🔧 修复：从记录文件恢复动态止盈标记
                        dynamic_tp_strong = positions_record[symbol].get('dynamic_tp_strong', False)
                        dynamic_tp_medium = positions_record[symbol].get('dynamic_tp_medium', False)
                        dynamic_tp_weak = positions_record[symbol].get('dynamic_tp_weak', False)
                        is_consecutive_confirmed = positions_record[symbol].get('is_consecutive_confirmed', False)
                        logging.info(f"✅ {symbol} 从记录文件恢复建仓时间: {entry_time_iso}")
                        
                        # 🔧 修复：即使从文件恢复，也要检查是否已超过窗口
                        try:
                            entry_time_dt = datetime.fromisoformat(entry_time_iso)
                            elapsed_hours = (datetime.now(timezone.utc) - entry_time_dt).total_seconds() / 3600
                            
                            # 如果持仓时间已超过检查窗口，强制标记为已检查
                            if elapsed_hours >= 2.5 and not tp_2h_checked:
                                tp_2h_checked = True
                                logging.info(f"  • {symbol} 持仓{elapsed_hours:.1f}h，已超过2h窗口，强制标记为已检查")
                            
                            if elapsed_hours >= 12.5 and not tp_12h_checked:
                                tp_12h_checked = True
                                logging.info(f"  • {symbol} 持仓{elapsed_hours:.1f}h，已超过12h窗口，强制标记为已检查")
                        except Exception as e:
                            logging.warning(f"  • {symbol} 计算持仓时间失败: {e}")
                    else:
                        # 如果文件中没有记录，查询交易历史
                        signal_datetime = None
                        entry_time_iso = self.server_get_entry_time_from_trades(symbol)
                        tp_pct = self.strong_coin_tp_pct
                        tp_2h_checked = False
                        tp_12h_checked = False
                        logging.warning(f"⚠️ {symbol} 记录文件中无数据，从交易历史查询")
                    
                    # 🔧 修复：计算持仓时间，如果已超过检查窗口，直接标记为已检查
                    try:
                        entry_time_dt = datetime.fromisoformat(entry_time_iso)
                        elapsed_hours = (datetime.now(timezone.utc) - entry_time_dt).total_seconds() / 3600
                        
                        # 如果持仓时间已超过检查窗口，标记为已检查（避免永远显示"未检查"）
                        if elapsed_hours >= 2.5:
                            tp_2h_checked = True
                            logging.info(f"  • {symbol} 持仓{elapsed_hours:.1f}h，已超过2h窗口，标记为已检查")
                        
                        if elapsed_hours >= 12.5:
                            tp_12h_checked = True
                            logging.info(f"  • {symbol} 持仓{elapsed_hours:.1f}h，已超过12h窗口，标记为已检查")
                    except Exception as e:
                        logging.warning(f"  • {symbol} 计算持仓时间失败: {e}")
                    
                    # 创建持仓记录
                    position = {
                        'symbol': symbol,
                        'signal_datetime': signal_datetime,  # 🔥 新增：信号时间
                        'entry_price': entry_price,
                        'entry_time': entry_time_iso,
                        'quantity': quantity,
                        'position_value': position_value,
                        'surge_ratio': 0.0,  # 未知
                        'leverage': self.leverage,
                        'tp_pct': tp_pct,
                        'tp_2h_checked': tp_2h_checked,
                        'tp_12h_checked': tp_12h_checked,
                        # 🔧 修复：添加动态止盈标记（从文件恢复或初始化为False）
                        'dynamic_tp_strong': dynamic_tp_strong if 'dynamic_tp_strong' in locals() else False,
                        'dynamic_tp_medium': dynamic_tp_medium if 'dynamic_tp_medium' in locals() else False,
                        'dynamic_tp_weak': dynamic_tp_weak if 'dynamic_tp_weak' in locals() else False,
                        'is_consecutive_confirmed': is_consecutive_confirmed if 'is_consecutive_confirmed' in locals() else False,
                        'status': 'normal',
                        'order_id': 0,
                        'loaded_from_exchange': True  # 标记为从交易所加载
                    }
                    
                    self.positions.append(position)
                    loaded_count += 1
                    
                    logging.info(f"✅ 加载持仓: {symbol} 开仓价:{entry_price:.6f} 数量:{quantity:.0f}")
            
            if loaded_count > 0:
                logging.info(f"🎉 成功加载 {loaded_count} 个现有持仓")
            else:
                logging.info("📭 无现有持仓")
                
        except Exception as e:
            logging.error(f"❌ 加载现有持仓失败: {e}")
    
    def server_load_positions_record(self) -> Dict:
        """从文件加载持仓记录（兼容旧版本数据，自动补充缺失的ID字段）- 服务器版本"""
        try:
            if os.path.exists(POSITIONS_RECORD_FILE):
                with open(POSITIONS_RECORD_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # ✨ 兼容性处理：为旧记录补充position_id
                modified = False
                for symbol, position in data.items():
                    if 'position_id' not in position or not position['position_id']:
                        position['position_id'] = str(uuid.uuid4())
                        modified = True
                        logging.info(f"🔄 {symbol} 旧持仓记录已补充ID: {position['position_id'][:8]}")
                    
                    # 补充tp_order_id和sl_order_id字段（如果不存在）
                    if 'tp_order_id' not in position:
                        position['tp_order_id'] = None
                        modified = True
                    if 'sl_order_id' not in position:
                        position['sl_order_id'] = None
                        modified = True
                
                # 如果有修改，保存回文件
                if modified:
                    with open(POSITIONS_RECORD_FILE, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logging.info("💾 已保存补充ID后的持仓记录")
                
                return data
            else:
                logging.info("📄 持仓记录文件不存在，将创建新文件")
                return {}
        except Exception as e:
            logging.error(f"❌ 读取持仓记录文件失败: {e}")
            return {}
    
    def server_load_position_record(self, symbol: str):
        """从文件加载单个持仓记录 - 服务器版本
        
        Args:
            symbol: 交易对符号
            
        Returns:
            持仓记录字典，如果不存在返回None
        """
        all_records = self.server_load_positions_record()
        return all_records.get(symbol)
    
    def server_save_positions_record(self):
        """保存持仓记录到文件 - 服务器版本"""
        try:
            record = {}
            for position in self.positions:
                symbol = position['symbol']
                record[symbol] = {
                    'symbol': symbol,  # ✅ 新增：保存symbol字段，避免后续使用时缺失
                    'signal_datetime': position.get('signal_datetime'),  # 🔥 信号时间
                    'entry_time': position['entry_time'],
                    'entry_price': position['entry_price'],
                    'quantity': position['quantity'],
                    'tp_pct': position.get('tp_pct', self.strong_coin_tp_pct),
                    'tp_2h_checked': position.get('tp_2h_checked', False),
                    'tp_12h_checked': position.get('tp_12h_checked', False),
                    # 🔧 修复：保存动态止盈判断标记
                    'dynamic_tp_strong': position.get('dynamic_tp_strong', False),
                    'dynamic_tp_medium': position.get('dynamic_tp_medium', False),
                    'dynamic_tp_weak': position.get('dynamic_tp_weak', False),
                    'is_consecutive_confirmed': position.get('is_consecutive_confirmed', False),
                    'tp_history': position.get('tp_history', []),  # 🔥 新增：止盈修改历史
                    'last_update': datetime.now(timezone.utc).isoformat()
                }
            
            with open(POSITIONS_RECORD_FILE, 'w', encoding='utf-8') as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            
            logging.debug(f"💾 已保存 {len(record)} 个持仓记录")
        except Exception as e:
            logging.error(f"❌ 保存持仓记录失败: {e}")
    
    def server_get_entry_time_from_trades(self, symbol: str) -> str:
        """从交易历史查询建仓时间（备用方案）- 服务器版本"""
        try:
            trades = self.client.futures_account_trades(symbol=symbol, limit=50)
            if trades:
                # 找到最早的建仓交易
                sorted_trades = sorted(trades, key=lambda x: x['time'])
                entry_time = datetime.fromtimestamp(sorted_trades[0]['time'] / 1000, tz=timezone.utc)
                logging.info(f"📅 {symbol} 从交易历史查询到建仓时间: {entry_time.isoformat()}")
                return entry_time.isoformat()
            else:
                # 如果查询失败，使用当前时间
                logging.warning(f"⚠️ {symbol} 交易历史为空，使用当前时间")
                return datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logging.error(f"❌ {symbol} 查询交易历史失败: {e}")
            return datetime.now(timezone.utc).isoformat()
    
    def _server_check_consecutive_surge(self, position: Dict) -> bool:
        """检查该持仓在建仓时是否为连续2小时卖量暴涨（API版本）- 服务器版本
        
        判断逻辑（基于hm1l.py的逻辑）：
        1. 获取信号发生时间（第1小时）
        2. 建仓时间 = 信号时间 + 1小时（第2小时）
        3. 检查信号小时和建仓小时是否都有卖量>=10倍
        4. 如果是，返回True（连续确认）
        
        Args:
            position: 持仓信息
        
        Returns:
            bool: 是否为连续2小时确认
        """
        symbol = position.get('symbol', 'Unknown')
        try:
            signal_datetime_str = position.get('signal_datetime')
            
            if not signal_datetime_str:
                logging.debug(f"❌ {symbol} 无signal_datetime，无法判断连续确认")
                return False
            
            # 解析信号时间（第1小时）
            if isinstance(signal_datetime_str, str):
                try:
                    signal_dt = datetime.strptime(signal_datetime_str, '%Y-%m-%d %H:%M:%S UTC')
                    signal_dt = signal_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        signal_dt = datetime.fromisoformat(signal_datetime_str.replace('Z', '+00:00'))
                    except:
                        signal_dt = datetime.strptime(signal_datetime_str, '%Y-%m-%d %H:%M')
                        signal_dt = signal_dt.replace(tzinfo=timezone.utc)
            else:
                signal_dt = signal_datetime_str
            
            # 确保时区
            if signal_dt.tzinfo is None:
                signal_dt = signal_dt.replace(tzinfo=timezone.utc)
            
            # 建仓时间 = 信号时间 + 1小时（第2小时）
            entry_dt = signal_dt + timedelta(hours=1)
            
            # 步骤1：获取昨日平均小时卖量（从缓存）
            yesterday_avg_hour_sell = self.yesterday_cache.get_yesterday_avg_sell_api(symbol)
            if not yesterday_avg_hour_sell or yesterday_avg_hour_sell <= 0:
                logging.debug(f"❌ {symbol} 昨日数据缺失，无法判断连续确认")
                return False
            
            # 步骤2：从API获取信号小时和建仓小时的K线数据
            signal_hour_ms = int(signal_dt.timestamp() * 1000)
            entry_hour_ms = int(entry_dt.timestamp() * 1000)
            
            # 获取2小时的K线数据
            klines = self.client.futures_klines(
                symbol=symbol,
                interval='1h',
                startTime=signal_hour_ms,
                endTime=entry_hour_ms,
                limit=2
            )
            
            if len(klines) < 2:
                logging.debug(f"❌ {symbol} 小时数据不足（{len(klines)}条），无法判断连续确认")
                return False
            
            # 计算每小时的卖量倍数
            threshold = self.sell_surge_threshold  # 10倍
            ratios = []
            hour_times = []
            
            for kline in klines:
                hour_volume = float(kline[5])  # 总成交量
                hour_active_buy = float(kline[9])  # 主动买入量
                hour_sell_volume = hour_volume - hour_active_buy
                ratio = hour_sell_volume / yesterday_avg_hour_sell
                ratios.append(ratio)
                hour_times.append(datetime.fromtimestamp(int(kline[0])/1000, tz=timezone.utc).strftime('%H:%M'))
            
            # 判断两个小时都>=10倍
            if len(ratios) >= 2 and all(r >= threshold for r in ratios[-2:]):
                logging.info(
                    f"✅ {symbol} 确认为连续2小时卖量暴涨：\n"
                    f"  • 信号小时({hour_times[-2]}): {ratios[-2]:.2f}x\n"
                    f"  • 建仓小时({hour_times[-1]}): {ratios[-1]:.2f}x\n"
                    f"  • 阈值: {threshold}x"
                )
                return True
            else:
                logging.debug(f"❌ {symbol} 非连续确认（倍数: 信号{ratios[-2]:.2f}x, 建仓{ratios[-1]:.2f}x < {threshold}x）")
                return False
        
        except Exception as e:
            logging.warning(f"⚠️ {symbol} 检查连续确认失败: {e}")
            import traceback
            logging.debug(f"异常堆栈:\n{traceback.format_exc()}")
            return False

    def server_calculate_intraday_buy_surge_ratio(self, symbol: str, signal_datetime: str) -> float:
        """
        计算当日买量倍数：信号发生前12小时，每小时买量相对前一小时的最大比值

        这个指标反映了短期买量的爆发性，用于过滤多空博弈信号

        Args:
            symbol: 交易对
            signal_datetime: 信号时间 'YYYY-MM-DD HH:MM:SS UTC'

        Returns:
            float: 当日买量倍数（最大的小时间买量比值），如果数据不足返回0
        """
        try:
            # 解析信号时间
            signal_dt = datetime.strptime(signal_datetime, '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)

            # 计算时间范围：信号前12小时
            start_time = signal_dt - timedelta(hours=12)
            end_time = signal_dt

            logging.debug(f"📊 {symbol} 查询当日买量倍数，时间范围: {start_time} ~ {end_time}")

            # 获取小时K线数据
            klines = self.client.futures_klines(
                symbol=symbol,
                interval='1h',
                startTime=int(start_time.timestamp() * 1000),
                endTime=int(end_time.timestamp() * 1000),
                limit=12  # 获取最近12小时的数据
            )

            if not klines or len(klines) < 2:
                logging.debug(f"⚠️ {symbol} 数据不足（<2小时），无法计算当日买量倍数")
                return 0.0

            # 计算每小时的主动买量比值
            max_ratio = 0.0
            for i in range(1, len(klines)):
                prev_kline = klines[i-1]
                curr_kline = klines[i]

                prev_buy_vol = float(prev_kline[9])  # taker_buy_volume
                curr_buy_vol = float(curr_kline[9])  # taker_buy_volume

                if prev_buy_vol > 0:
                    ratio = curr_buy_vol / prev_buy_vol
                    max_ratio = max(max_ratio, ratio)

            if max_ratio > 0:
                logging.debug(f"📊 {symbol} 当日买量倍数: {max_ratio:.2f}倍（信号前12小时最大小时间比值）")
            else:
                logging.debug(f"⚠️ {symbol} 未计算出有效的当日买量倍数（max_ratio=0）")

            return max_ratio

        except Exception as e:
            logging.warning(f"⚠️ 计算当日买量倍数失败 {symbol}: {e}")
            return 0.0

    def server_get_account_balance(self) -> float:
        """获取账户USDT余额 - 服务器版本"""
        try:
            account = self.client.futures_account()
            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    balance = float(asset['walletBalance'])
                    logging.info(f"💰 账户余额: ${balance:.2f} USDT")
                    return balance
            return 0.0
        except Exception as e:
            logging.error(f"❌ 获取账户余额失败: {e}")
            return 0.0
    
    def server_get_account_info(self) -> Optional[Dict]:
        """获取账户详细信息（余额、可用余额、未实现盈亏、今日盈亏）- 服务器版本"""
        try:
            # 获取账户信息
            account_info = self.client.futures_account()
            
            # 总余额
            total_balance = float(account_info['totalWalletBalance'])
            
            # 可用余额
            available_balance = float(account_info['availableBalance'])
            
            # 未实现盈亏
            unrealized_pnl = float(account_info['totalUnrealizedProfit'])
            
            # 今日盈亏（通过收入记录计算）
            daily_pnl = self.server_get_daily_pnl()
            
            # 维持保证金（可选字段，可能不存在）
            maintenance_margin = float(account_info.get('totalMaintMargin', 0))

            return {
                'total_balance': total_balance,
                'available_balance': available_balance,
                'unrealized_pnl': unrealized_pnl,
                'maintenance_margin': maintenance_margin,
                'daily_pnl': daily_pnl
            }
        except Exception as e:
            logging.error(f"❌ 获取账户详细信息失败: {e}")
            return None
    
    def server_get_daily_pnl(self) -> float:
        """获取今日盈亏（UTC 0点至今的已实现盈亏）- 服务器版本"""
        try:
            # 获取今日UTC 0:00的时间戳
            now_utc = datetime.now(timezone.utc)
            today_start = datetime(now_utc.year, now_utc.month, now_utc.day, 0, 0, 0, tzinfo=timezone.utc)
            start_timestamp = int(today_start.timestamp() * 1000)
            
            # 查询今日收入记录
            income_history = self.client.futures_income_history(
                startTime=start_timestamp,
                incomeType='REALIZED_PNL'
            )
            
            # 累计今日已实现盈亏
            daily_pnl = sum(float(record['income']) for record in income_history)
            
            return daily_pnl
        except Exception as e:
            logging.warning(f"⚠️ 获取今日盈亏失败: {e}")
            return 0.0
    
    def _server_get_active_symbols(self) -> List[str]:
        """获取活跃交易对列表（API方式）- 服务器版本"""
        try:
            # 获取所有U本位期货交易对
            exchange_info = self.client.futures_exchange_info()
            symbols = []
            
            for s in exchange_info['symbols']:
                symbol = s['symbol']
                # 只筛选USDT永续合约，并且状态为TRADING
                if symbol.endswith('USDT') and s['status'] == 'TRADING' and s['contractType'] == 'PERPETUAL':
                    symbols.append(symbol)
            
            logging.info(f"✅ 获取到 {len(symbols)} 个活跃USDT合约")
            return sorted(symbols)
        
        except Exception as e:
            logging.error(f"❌ 获取交易对列表失败: {e}，使用备用列表")
            return BACKUP_SYMBOL_LIST
    
    def server_scan_sell_surge_signals(self) -> List[Dict]:
        """扫描卖量暴涨信号（API实时版本）- 服务器版本"""
        try:
            logging.info("🔍 开始扫描卖量暴涨信号（API模式）...")
            signals = []
            
            # 获取当前UTC时间
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
            
            # 获取交易对列表
            symbols = self._server_get_active_symbols()
            logging.info(f"📊 开始扫描 {len(symbols)} 个交易对...")
            
            # 逐个检查交易对
            for symbol in symbols:
                try:
                    # 1. 从缓存获取昨日平均小时卖量
                    yesterday_avg_hour_sell = self.yesterday_cache.get_yesterday_avg_sell_api(symbol)
                    if not yesterday_avg_hour_sell or yesterday_avg_hour_sell <= 0:
                        continue
                    
                    # 2. 获取上一个完整小时的K线（刚刚完成的小时）
                    check_hour = current_hour - timedelta(hours=1)
                    check_hour_ms = int(check_hour.timestamp() * 1000)
                    
                    # 请求上一小时的K线数据
                    klines = self.client.futures_klines(
                        symbol=symbol,
                        interval='1h',
                        startTime=check_hour_ms,
                        limit=2  # 获取上一小时和当前小时
                    )
                    
                    if not klines or len(klines) < 1:
                        continue
                    
                    # 上一小时数据
                    hour_kline = klines[0]
                    hour_volume = float(hour_kline[5])  # 总成交量
                    hour_active_buy = float(hour_kline[9])  # 主动买入量
                    hour_sell_volume = hour_volume - hour_active_buy
                    hour_close = float(hour_kline[4])
                    
                    # 计算暴涨倍数
                    surge_ratio = hour_sell_volume / yesterday_avg_hour_sell
                    
                    # 3. 检查是否满足阈值
                    if self.sell_surge_threshold <= surge_ratio <= self.sell_surge_max:
                        # 获取信号价格（使用下一小时开盘价，如果存在）
                        if len(klines) >= 2:
                            signal_price = float(klines[1][1])  # 下一小时开盘价
                            logging.info(f"📊 {symbol} 信号价格: 使用下一小时开盘价 {signal_price:.6f}")
                        else:
                            signal_price = hour_close
                            logging.info(f"📊 {symbol} 信号价格: 下一小时未生成，使用当前小时收盘价 {signal_price:.6f}")

                        # 🆕 检查当日买量倍数风控
                        signal_time_utc = datetime.fromtimestamp(int(hour_kline[0]) / 1000, tz=timezone.utc)
                        signal_time_str = signal_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')

                        intraday_buy_ratio = 0.0
                        if self.enable_intraday_buy_ratio_filter:
                            try:
                                intraday_buy_ratio = self.server_calculate_intraday_buy_surge_ratio(symbol, signal_time_str)
                            except Exception as e:
                                logging.debug(f"计算当日买量倍数失败 {symbol}: {e}")

                        # 🔥 风控：当日买量倍数区间过滤（过滤多空博弈信号）
                        if intraday_buy_ratio > 0 and self.enable_intraday_buy_ratio_filter:
                            for danger_min, danger_max in self.intraday_buy_ratio_danger_ranges:
                                if danger_min <= intraday_buy_ratio <= danger_max:
                                    logging.warning(f"🚫 {symbol} 当日买量倍数风控过滤信号: {intraday_buy_ratio:.2f}倍在危险区间[{danger_min}, {danger_max}]（卖量暴涨{ surge_ratio:.2f}倍但买量也暴涨，疑似多空博弈信号）")
                                    break  # 跳过这个信号
                            else:
                                # 如果没有在危险区间内，则记录信号
                                signals.append({
                                    'symbol': symbol,
                                    'surge_ratio': surge_ratio,
                                    'price': signal_price,
                                    'signal_time': signal_time_str,
                                    'hour_sell_volume': hour_sell_volume,
                                    'yesterday_avg': yesterday_avg_hour_sell,
                                    'intraday_buy_ratio': intraday_buy_ratio  # 🆕 添加买量倍数信息
                                })
                                logging.info(f"🔥 发现信号: {symbol} 卖量暴涨 {surge_ratio:.2f}倍 @ {signal_price:.6f} (买量倍数:{intraday_buy_ratio:.2f}倍) (时间: {signal_time_utc.strftime('%Y-%m-%d %H:%M UTC')})")
                        else:
                            # 如果不启用买量倍数风控，直接记录信号
                            signals.append({
                                'symbol': symbol,
                                'surge_ratio': surge_ratio,
                                'price': signal_price,
                                'signal_time': signal_time_str,
                                'hour_sell_volume': hour_sell_volume,
                                'yesterday_avg': yesterday_avg_hour_sell,
                                'intraday_buy_ratio': intraday_buy_ratio  # 🆕 添加买量倍数信息
                            })
                            logging.info(f"🔥 发现信号: {symbol} 卖量暴涨 {surge_ratio:.2f}倍 @ {signal_price:.6f} (买量倍数:{intraday_buy_ratio:.2f}倍) (时间: {signal_time_utc.strftime('%Y-%m-%d %H:%M UTC')})")
                
                except Exception as e:
                    # 单个交易对失败不影响整体
                    continue
            
            logging.info(f"✅ API扫描完成，共发现 {len(signals)} 个信号")
            return sorted(signals, key=lambda x: x['surge_ratio'], reverse=True)
        
        except Exception as e:
            logging.error(f"❌ API扫描信号失败: {e}")
            return []
    
    def server_check_position_limits(self) -> bool:
        """检查持仓限制 - 服务器版本"""
        # 🔧 修复：从交易所API获取实际持仓数量，而不是仅检查内存中的记录
        try:
            # 🔧 API调用重试机制
            actual_positions = None
            max_retries = 3
            retry_delay = 2  # 秒
            
            for attempt in range(1, max_retries + 1):
                try:
                    actual_positions = self.client.futures_position_information()
                    break
                except Exception as e:
                    if attempt < max_retries:
                        logging.warning(f"⚠️ 第{attempt}次获取持仓信息失败，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        logging.error(f"❌ 尝试{max_retries}次后仍无法获取持仓信息: {e}")
                        raise
            
            if actual_positions is None:
                raise Exception("无法从交易所获取持仓信息")
            
            # 过滤出真实持仓（持仓数量>0）
            active_positions = [p for p in actual_positions if float(p['positionAmt']) != 0]
            actual_count = len(active_positions)
            
            logging.info(f"📊 持仓检查: 内存记录={len(self.positions)}, 交易所实际={actual_count}, 上限={self.max_positions}")
            
            if actual_count >= self.max_positions:
                logging.warning(f"⚠️ 交易所实际持仓数 {actual_count} 已达到上限 {self.max_positions}")
                return False
        except Exception as e:
            logging.error(f"❌ 获取交易所持仓信息失败: {e}，使用内存记录")
            # 如果API调用失败，降级使用内存中的记录
            if len(self.positions) >= self.max_positions:
                logging.warning(f"⚠️ 已达到最大持仓数 {self.max_positions}")
                return False
        
        # 检查每日建仓数（重置计数器）
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self.last_entry_date != today:
            self.daily_entries = 0
            self.last_entry_date = today
            logging.info(f"📅 新的一天开始，建仓计数器已重置")
        
        if self.daily_entries >= self.max_daily_entries:
            logging.warning(f"⚠️ 今日已达到最大建仓数 {self.daily_entries}/{self.max_daily_entries}")
            return False
        
        # 检查每小时建仓限制
        current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if self.last_entry_hour == current_hour:
            logging.warning(f"⚠️ 本小时已建仓，请等待下一个小时 (当前: {current_hour.strftime('%H:00 UTC')})")
            return False
        
        return True

    def check_sufficient_funds(self, required_margin: float) -> bool:
        """检查是否有足够的可用资金（要求至少15%可用资金余量）"""
        try:
            account_info = self.client.futures_account()
            available_balance = float(account_info['availableBalance'])
            total_balance = float(account_info['totalWalletBalance'])

            # 计算需要的最小可用资金（除了建仓保证金，还要留15%余量）
            min_required = required_margin * 1.15

            # 同时检查绝对金额和比例
            available_ratio = available_balance / total_balance if total_balance > 0 else 0

            logging.info(f"💰 资金检查: 可用余额${available_balance:.2f} ({available_ratio*100:.1f}%), 需要${min_required:.2f}")

            if available_balance >= min_required:
                logging.info(f"✅ 资金充足: 可用${available_balance:.2f} ≥ 需要${min_required:.2f}")
                return True
            else:
                logging.warning(f"❌ 资金不足: 可用${available_balance:.2f} < 需要${min_required:.2f}，跳过建仓")
                return False

        except Exception as e:
            logging.error(f"❌ 检查资金失败: {e}")
            # 资金检查失败时保守处理，不建仓
            return False

    def server_set_leverage(self, symbol: str):
        """设置杠杆倍数 - 服务器版本"""
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=int(self.leverage))
            logging.info(f"✅ {symbol} 设置杠杆 {int(self.leverage)}x")
        except Exception as e:
            logging.error(f"❌ {symbol} 设置杠杆失败: {e}")
    
    def server_open_position(self, signal: Dict) -> bool:
        """开仓 - 服务器版本"""
        symbol = signal['symbol']
        
        # 🔒 获取或创建该symbol的锁
        with self.position_lock_master:
            if symbol not in self.position_locks:
                import threading
                self.position_locks[symbol] = threading.Lock()
            symbol_lock = self.position_locks[symbol]
        
        # 🔒 使用锁防止并发建仓
        acquired = symbol_lock.acquire(blocking=False)
        if not acquired:
            logging.warning(f"🔒 {symbol} 正在建仓中，跳过重复请求")
            return False
        
        try:
            signal_price = signal['price']  # 信号价格（用于记录）
            
            # 获取当前市价作为建仓价格
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker['price'])
            logging.info(f"💰 {symbol} 信号价格: {signal_price:.6f}, 当前市价: {price:.6f}")
            
            # 检查持仓限制
            if not self.server_check_position_limits():
                return False
            
            # 检查是否已持仓（增强版：防止重复建仓）
            existing_positions = [p for p in self.positions if p['symbol'] == symbol]
            if existing_positions:
                logging.warning(f"⚠️ {symbol} 已存在 {len(existing_positions)} 个持仓，跳过建仓")
                for idx, pos in enumerate(existing_positions, 1):
                    pos_id = pos.get('position_id', '未知')[:8]
                    entry_time = pos.get('entry_time', '未知')
                    logging.warning(f"   持仓{idx}: ID={pos_id}, 建仓时间={entry_time}")
                return False
            
            # 计算建仓金额
            position_value = self.account_balance * self.position_size_ratio

            # 🔧 新增：资金充足性检查（要求至少15%余量）
            if not self.check_sufficient_funds(position_value):
                return False

            quantity = (position_value * self.leverage) / price

            logging.info(f"💰 {symbol} 初始计算: 账户{self.account_balance:.2f} × {self.position_size_ratio} × {self.leverage} / {price} = {quantity:.2f}")
            
            # 获取交易对的精度要求
            exchange_info = self.client.futures_exchange_info()
            symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
            
            if not symbol_info:
                logging.error(f"❌ 无法获取 {symbol} 的交易规则")
                return False
            
            # 获取LOT_SIZE过滤器
            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if lot_size_filter:
                step_size = float(lot_size_filter['stepSize'])
                min_qty = float(lot_size_filter['minQty'])
                
                logging.info(f"📏 {symbol} LOT_SIZE规则: stepSize={step_size}, minQty={min_qty}")
                
                # 根据stepSize精度取整
                if step_size >= 1:
                    # 如果stepSize是整数，则向下取整到整数
                    quantity = int(quantity)
                    logging.info(f"🔢 {symbol} 取整为整数: {quantity}")
                else:
                    # 如果stepSize是小数，计算精度
                    precision = len(str(step_size).rstrip('0').split('.')[-1])
                    quantity = round(quantity / step_size) * step_size
                    quantity = round(quantity, precision)
                    logging.info(f"🔢 {symbol} 按精度{precision}取整: {quantity}")
                
                # 检查最小数量
                if quantity < min_qty:
                    logging.warning(f"⚠️ {symbol} 计算数量 {quantity} 小于最小数量 {min_qty}")
                    return False
            else:
                # 如果没有LOT_SIZE过滤器，默认保留3位小数
                quantity = round(quantity, 3)
            
            logging.info(f"📊 {symbol} 最终建仓数量: {quantity}, 价格: {price}, 名义价值: ${quantity * price:.2f}")
            
            # 设置杠杆
            self.server_set_leverage(symbol)
            
            # 设置逐仓模式
            try:
                self.client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
            except:
                pass  # 可能已经是逐仓模式
            
            # 设置为单向持仓模式（如果是双向模式会失败，忽略）
            try:
                self.client.futures_change_position_mode(dualSidePosition=False)
            except:
                pass  # 可能已经是单向模式
            
            # 下单（做空）
            order = self.client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )
            
            # 记录持仓
            current_time = datetime.now(timezone.utc)
            position_id = str(uuid.uuid4())  # ✨ 生成唯一持仓ID
            
            position = {
                'position_id': position_id,  # ✨ 唯一持仓ID
                'symbol': symbol,
                'signal_price': signal_price,  # 记录信号价格
                'signal_datetime': signal.get('signal_time'),  # 🔥 新增：信号发生时间（用于连续确认判断）
                'entry_price': price,  # 实际建仓价格
                'entry_time': current_time.isoformat(),  # 实际建仓时间
                'quantity': quantity,
                'position_value': position_value,
                'surge_ratio': signal['surge_ratio'],
                'leverage': self.leverage,
                'tp_pct': self.strong_coin_tp_pct,  # 初始止盈33%
                'status': 'normal',
                'order_id': order['orderId'],
                'tp_order_id': None,  # ✨ 止盈订单ID（稍后设置）
                'sl_order_id': None   # ✨ 止损订单ID（稍后设置）
            }
            
            self.positions.append(position)
            self.daily_entries += 1
            
            # 记录建仓小时（用于每小时限制）
            current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            self.last_entry_hour = current_hour
            
            # 保存持仓记录到文件
            self.server_save_positions_record()
            
            logging.info(f"🚀 开仓成功: {symbol} 价格:{price:.6f} 数量:{quantity:.3f} 杠杆:{self.leverage}x")
            
            # 🔧 强制刷新日志（确保开仓日志立即写入）
            for handler in logging.getLogger().handlers:
                if hasattr(handler, 'flush'):
                    handler.flush()
            logging.info(f"📊 建仓计数: 本小时第1个, 今日第{self.daily_entries}个 (限额{self.max_daily_entries})")
            logging.info(f"💾 已保存建仓记录到文件")
            
            # 自动设置交易所止盈止损订单
            try:
                # 🔧 动态获取价格精度（修复COMPUSDT等币种的精度错误）
                price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                if price_filter:
                    tick_size = float(price_filter['tickSize'])
                    # 计算价格精度
                    if tick_size >= 1:
                        price_precision = 0
                    else:
                        price_precision = len(str(tick_size).rstrip('0').split('.')[-1])
                    logging.info(f"📏 {symbol} PRICE_FILTER规则: tickSize={tick_size}, precision={price_precision}")
                else:
                    # 如果没有PRICE_FILTER，默认6位小数
                    tick_size = 0.000001
                    price_precision = 6
                    logging.warning(f"⚠️ {symbol} 未找到PRICE_FILTER，使用默认精度6")
                
                # 计算止盈价格（做空：价格下跌33%）
                tp_price_raw = price * (1 - self.strong_coin_tp_pct / 100)
                take_profit_price = round(tp_price_raw / tick_size) * tick_size
                # 🔧 最终格式化：使用Decimal确保精度，避免浮点误差
                from decimal import Decimal, ROUND_HALF_UP
                take_profit_price = float(Decimal(str(take_profit_price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))
                
                # 计算止损价格（做空：价格上涨18%）
                sl_price_raw = price * (1 + abs(self.stop_loss_pct) / 100)
                stop_loss_price = round(sl_price_raw / tick_size) * tick_size
                # 🔧 最终格式化：使用Decimal确保精度，避免浮点误差
                stop_loss_price = float(Decimal(str(stop_loss_price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))
                
                logging.info(f"📋 {symbol} 设置交易所止盈止损: 止盈={take_profit_price}, 止损={stop_loss_price}")
                
                # 🔧 修复9：建仓前检查并清理该交易对的所有旧订单（防止之前的订单残留）
                try:
                    old_algo_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
                    if old_algo_orders:
                        logging.warning(f"⚠️ {symbol} 建仓前发现 {len(old_algo_orders)} 个残留订单，清理中...")
                        for old_order in old_algo_orders:
                            try:
                                self.client.futures_cancel_algo_order(symbol=symbol, algoId=old_order['algoId'])
                                logging.info(f"✅ {symbol} 已清理残留订单: {old_order['orderType']} (algoId: {old_order['algoId']})")
                            except:
                                pass
                except:
                    pass
                
                # 设置止盈订单
                try:
                    tp_order = self.client.futures_create_order(
                        symbol=symbol,
                        side='BUY',
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=take_profit_price,
                        quantity=quantity,
                        reduceOnly=True  # 🔧 修复10：添加reduceOnly防止开反向仓位
                    )
                    tp_order_id = str(tp_order.get('algoId') or tp_order.get('orderId'))
                    position['tp_order_id'] = tp_order_id  # ✨ 记录止盈订单ID
                    logging.info(f"✅ {symbol} 止盈订单已设置: {take_profit_price:.6f} (订单ID: {tp_order_id})")
                except Exception as tp_error:
                    logging.warning(f"⚠️ {symbol} 止盈订单设置失败: {tp_error}")
                
                # 设置止损订单
                tp_order_id = position.get('tp_order_id', 'N/A')
                sl_order_id = 'N/A'
                try:
                    sl_order = self.client.futures_create_order(
                        symbol=symbol,
                        side='BUY',
                        type='STOP_MARKET',
                        stopPrice=stop_loss_price,
                        quantity=quantity,
                        reduceOnly=True  # 🔧 修复11：添加reduceOnly防止开反向仓位
                    )
                    sl_order_id = str(sl_order.get('algoId') or sl_order.get('orderId'))
                    position['sl_order_id'] = sl_order_id  # ✨ 记录止损订单ID
                    logging.info(f"✅ {symbol} 止损订单已设置: {stop_loss_price:.6f} (订单ID: {sl_order_id})")
                except Exception as sl_error:
                    logging.warning(f"⚠️ {symbol} 止损订单设置失败: {sl_error}")
                
                # ✨ 保存更新后的订单ID到文件
                self.server_save_positions_record()
                
                # 🆕 建仓完成摘要日志（详细记录所有关键信息）
                logging.info(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 🎉 {symbol} 建仓完成摘要
╠════════════════════════════════════════════════════════════════════════════╣
║ 📅 建仓时间: {entry_time}
║ 💰 建仓价格: ${price:.6f}
║ 📊 持仓数量: {quantity}
║ 💵 投入金额: ${position_value:.2f} USDT
║ ⚡ 杠杆倍数: {self.leverage}x
║ 📈 止盈设置: 价格 ${take_profit_price:.6f} ({self.strong_coin_tp_pct:.1f}%) [ID: {tp_order_id}]
║ 📉 止损设置: 价格 ${stop_loss_price:.6f} ({abs(self.stop_loss_pct):.1f}%) [ID: {sl_order_id}]
║ 🔢 Position ID: {position.get('position_id', 'N/A')[:8]}
╚════════════════════════════════════════════════════════════════════════════╝
""")
                    
            except Exception as e:
                logging.error(f"⚠️ {symbol} 设置止盈止损订单失败: {e}")
            
            return True
        
        except BinanceAPIException as e:
            logging.error(f"❌ {symbol} 开仓失败(API): {e}")
            return False
        except Exception as e:
            logging.error(f"❌ {symbol} 开仓失败: {e}")
            return False
        finally:
            # 🔓 确保锁被释放
            symbol_lock.release()
    
    def server_get_5min_klines_from_binance(self, symbol: str, start_time: datetime, end_time: datetime) -> List[float]:
        """从币安API获取5分钟K线收盘价 - 服务器版本"""
        try:
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)
            
            klines = self.client.futures_klines(
                symbol=symbol,
                interval='5m',
                startTime=start_ms,
                endTime=end_ms,
                limit=500
            )
            
            # 提取收盘价
            closes = [float(k[4]) for k in klines]
            return closes
        
        except Exception as e:
            logging.error(f"获取5分钟K线失败 {symbol}: {e}")
            return []
    
    def server_get_exchange_tp_order(self, symbol: str) -> Optional[Dict]:
        """获取交易所当前的止盈订单 - 服务器版本"""
        try:
            algo_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
            for order in algo_orders:
                if order['orderType'] == 'TAKE_PROFIT_MARKET':
                    return order
            return None
        except Exception as e:
            logging.error(f"❌ 获取 {symbol} 止盈订单失败: {e}")
            return None
    
    def server_play_alert_sound(self):
        """播放报警声音 - 服务器版本"""
        try:
            import os
            # macOS系统声音
            os.system('afplay /System/Library/Sounds/Basso.aiff')
        except Exception as e:
            logging.warning(f"播放报警声音失败: {e}")

    def server_log_position_change(self, change_type: str, symbol: str, details: Dict,
                                  before_state: Dict = None, after_state: Dict = None,
                                  success: bool = True, error_msg: str = None):
        """统一的仓位变动日志记录系统

        Args:
            change_type: 变动类型 ('dynamic_tp', 'manual_tp_sl', 'manual_close', 'auto_close')
            symbol: 交易对
            details: 变动详情字典
            before_state: 变动前状态 (可选)
            after_state: 变动后状态 (可选)
            success: 是否成功
            error_msg: 错误信息 (如果失败)
        """
        import datetime

        # 构建日志头部
        status_icon = "✅" if success else "❌"
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        # 变动类型映射
        type_names = {
            'dynamic_tp': '🔄 动态止盈调整',
            'manual_tp_sl': '🔧 手动修改止盈止损',
            'manual_close': '💰 手动平仓',
            'auto_close': '🤖 自动平仓'
        }
        type_name = type_names.get(change_type, change_type)

        # 开始构建详细日志
        log_lines = [
            "=" * 80,
            f"{status_icon} {type_name} - {symbol}",
            "=" * 80,
            f"时间: {timestamp}",
        ]

        # 添加详情信息
        if details:
            log_lines.append("📋 操作详情:")
            for key, value in details.items():
                if isinstance(value, float):
                    log_lines.append(f"   {key}: {value:.6f}")
                else:
                    log_lines.append(f"   {key}: {value}")

        # 添加前后状态对比
        if before_state or after_state:
            log_lines.append("")
            log_lines.append("📊 状态对比:")

            if before_state:
                log_lines.append("   变动前:")
                for key, value in before_state.items():
                    if isinstance(value, float):
                        log_lines.append(f"     {key}: {value:.6f}")
                    else:
                        log_lines.append(f"     {key}: {value}")

            if after_state:
                log_lines.append("   变动后:")
                for key, value in after_state.items():
                    if isinstance(value, float):
                        log_lines.append(f"     {key}: {value:.6f}")
                    else:
                        log_lines.append(f"     {key}: {value}")

        # 添加结果信息
        if success:
            log_lines.append("")
            log_lines.append("✅ 执行成功")
        else:
            log_lines.append("")
            log_lines.append("❌ 执行失败")
            if error_msg:
                log_lines.append(f"错误信息: {error_msg}")

        log_lines.append("=" * 80)

        # 输出日志
        full_log = "\n".join(log_lines)
        logging.info(f"\n{full_log}")

        # 同时写入专门的仓位变动日志文件
        try:
            log_dir = "logs"
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            position_log_file = os.path.join(log_dir, "position_changes.log")

            with open(position_log_file, 'a', encoding='utf-8') as f:
                f.write(f"\n{full_log}\n")
        except Exception as e:
            logging.warning(f"写入仓位变动日志失败: {e}")

    def check_order_history(self, symbol: str, order_id: str = None) -> dict:
        """
        检查订单历史，判断订单状态
        用于排查止损单是否被触发/取消/失败
        
        Args:
            symbol: 交易对
            order_id: 订单ID（可选，如果提供则查找特定订单）
        
        Returns:
            dict: 订单历史信息
        """
        try:
            # 查询历史订单（最近100条）
            orders = self.client.futures_get_all_orders(
                symbol=symbol,
                limit=100
            )
            
            result = {
                'symbol': symbol,
                'order_id': order_id,
                'found': False,
                'orders': []
            }
            
            # 如果指定了order_id，查找特定订单
            if order_id:
                for order in orders:
                    if str(order.get('orderId')) == order_id or str(order.get('algoId')) == order_id:
                        status = order['status']
                        order_type = order.get('type', 'UNKNOWN')
                        update_time = datetime.fromtimestamp(order['updateTime']/1000, tz=timezone.utc)
                        
                        result['found'] = True
                        result['order'] = order
                        
                        logging.info(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 📋 {symbol} 订单历史查询结果
╠════════════════════════════════════════════════════════════════════════════╣
║ 订单ID: {order_id}
║ 订单类型: {order_type}
║ 订单状态: {status}
║ 更新时间: {update_time}
║ {f'成交价格: ${order["avgPrice"]}' if status == 'FILLED' and order.get('avgPrice') else ''}
║ {f'触发价格: ${order.get("stopPrice", "N/A")}' if 'stopPrice' in order else ''}
║ 
║ 状态说明:
║   - NEW: 未触发（还在等待）
║   - FILLED: 已成交（订单成功执行）
║   - CANCELED: 已取消（触发后未成交 或 被手动/程序取消）
║   - REJECTED: 被拒绝（保证金不足 或 风控拦截）
║   - EXPIRED: 已过期
╚════════════════════════════════════════════════════════════════════════════╝
""")
                        
                        # 根据状态给出分析
                        if status == 'CANCELED':
                            logging.error(f"❌ {symbol} 订单被取消！可能原因：触发后成交失败 或 被手动/程序取消")
                        elif status == 'REJECTED':
                            logging.error(f"❌ {symbol} 订单被拒绝！可能原因：保证金不足 或 风控拦截")
                        elif status == 'EXPIRED':
                            logging.error(f"❌ {symbol} 订单已过期！")
                        elif status == 'FILLED':
                            logging.info(f"✅ {symbol} 订单已成功执行")
                        
                        break
                
                if not result['found']:
                    logging.warning(f"⚠️ {symbol} 订单ID {order_id} 未在历史记录中找到（可能已被删除）")
            
            else:
                # 未指定order_id，返回所有算法订单
                algo_orders = [o for o in orders if o.get('type') in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']]
                result['orders'] = algo_orders
                
                if algo_orders:
                    logging.info(f"📋 {symbol} 找到 {len(algo_orders)} 个算法订单历史")
                    for order in algo_orders[:5]:  # 只显示最近5个
                        logging.info(f"  - {order['type']} | {order['status']} | {order.get('stopPrice', 'N/A')}")
            
            return result
            
        except Exception as e:
            logging.error(f"❌ 查询 {symbol} 订单历史失败: {e}")
            return {'symbol': symbol, 'error': str(e)}
    
    def server_update_exchange_tp_order(self, position: Dict, new_tp_pct: float) -> bool:
        """更新交易所的止盈订单（方案B：先取消所有旧订单再创建）- 服务器版本"""
        try:
            symbol = position['symbol']
            entry_price = position['entry_price']
            quantity = position['quantity']
            old_tp_pct = position.get('tp_pct', self.strong_coin_tp_pct)
            
            # 🔧 动态获取价格精度
            try:
                exchange_info = self.client.futures_exchange_info()
                symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
                
                if symbol_info:
                    price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                    if price_filter:
                        tick_size = float(price_filter['tickSize'])
                        if tick_size >= 1:
                            price_precision = 0
                        else:
                            price_precision = len(str(tick_size).rstrip('0').split('.')[-1])
                    else:
                        tick_size = 0.000001
                        price_precision = 6
                else:
                    tick_size = 0.000001
                    price_precision = 6
            except:
                tick_size = 0.000001
                price_precision = 6
            
            # 计算新的止盈价格（做空：价格下跌触发止盈）
            tp_price_raw = entry_price * (1 - new_tp_pct / 100)
            new_tp_price = round(tp_price_raw / tick_size) * tick_size
            # 🔧 使用Decimal确保精度，避免浮点误差
            from decimal import Decimal, ROUND_HALF_UP
            new_tp_price = float(Decimal(str(new_tp_price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))
            
            logging.info(f"🔄 {symbol} 准备更新止盈订单: {old_tp_pct}% → {new_tp_pct}% (价格: {new_tp_price})")
            
            # 🔧 修复4：添加重复更新检查
            if hasattr(position, '_tp_updating') and position.get('_tp_updating'):
                logging.warning(f"⚠️ {symbol} 止盈订单正在更新中，跳过本次操作")
                return False
            position['_tp_updating'] = True  # 标记正在更新
            
            try:
                # 步骤1：查询所有止盈订单
                try:
                    algo_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
                    tp_orders = [o for o in algo_orders if o['orderType'] == 'TAKE_PROFIT_MARKET' and o['side'] == 'BUY']
                    
                    if tp_orders:
                        logging.info(f"📋 {symbol} 找到 {len(tp_orders)} 个旧止盈订单，准备全部取消")
                        
                        # 步骤2：取消所有旧止盈订单
                        cancel_success = 0
                        cancel_fail = 0
                        for old_tp in tp_orders:
                            try:
                                self.client.futures_cancel_algo_order(
                                    symbol=symbol,
                                    algoId=old_tp['algoId']
                                )
                                cancel_success += 1
                                logging.info(f"✅ {symbol} 已取消旧订单 {cancel_success}/{len(tp_orders)} (algoId: {old_tp['algoId']})")
                            except Exception as cancel_error:
                                cancel_fail += 1
                                logging.error(f"❌ {symbol} 取消订单失败 (algoId: {old_tp['algoId']}): {cancel_error}")
                        
                        if cancel_fail > 0:
                            logging.warning(f"⚠️ {symbol} 有 {cancel_fail} 个旧订单取消失败")
                            self.server_play_alert_sound()
                        
                        # 🔧 修复5：等待订单取消生效
                        if cancel_success > 0:
                            import time
                            time.sleep(0.5)  # 等待0.5秒确保取消生效
                            logging.info(f"⏰ {symbol} 等待订单取消生效...")
                except Exception as query_error:
                    logging.error(f"❌ {symbol} 查询旧订单失败: {query_error}")
                    # 查询失败，跳过取消步骤，直接创建新订单
                    pass
                
                # 🔧 修复6：创建新订单前再次检查是否还有止盈订单
                try:
                    algo_orders_check = self.client.futures_get_open_algo_orders(symbol=symbol)
                    tp_orders_check = [o for o in algo_orders_check if o['orderType'] == 'TAKE_PROFIT_MARKET' and o['side'] == 'BUY']
                    if tp_orders_check:
                        logging.warning(f"⚠️ {symbol} 取消后仍有 {len(tp_orders_check)} 个止盈订单残留，强制再次取消")
                        for order in tp_orders_check:
                            try:
                                self.client.futures_cancel_algo_order(symbol=symbol, algoId=order['algoId'])
                                logging.info(f"✅ {symbol} 强制取消残留订单: {order['algoId']}")
                            except:
                                pass
                        import time
                        time.sleep(0.3)
                except:
                    pass
                
                # 步骤3：创建新订单
                try:
                    new_order = self.client.futures_create_order(
                        symbol=symbol,
                        side='BUY',
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=new_tp_price,
                        quantity=quantity,
                        reduceOnly=True  # 🔧 修复7：添加reduceOnly防止开反向仓位
                    )
                    logging.info(f"✅ {symbol} 新止盈订单已创建: {new_tp_price:.6f} (algoId: {new_order['algoId']})")
                    
                    # 更新position记录
                    old_tp_pct_before = position.get('tp_pct', self.strong_coin_tp_pct)
                    position['tp_pct'] = new_tp_pct
                    position['last_tp_update'] = datetime.now(timezone.utc).isoformat()
                    
                    # 记录止盈修改历史
                    if 'tp_history' not in position:
                        position['tp_history'] = []
                    position['tp_history'].append({
                        'time': datetime.now(timezone.utc).isoformat(),
                        'from': old_tp_pct_before,
                        'to': new_tp_pct,
                        'reason': position.get('dynamic_tp_trigger', 'manual')
                    })
                    
                    # 🆕 获取止损订单信息（确认止损未被影响）
                    sl_order_info = "未知"
                    try:
                        algo_orders_final = self.client.futures_get_open_algo_orders(symbol=symbol)
                        sl_orders_final = [o for o in algo_orders_final if o['orderType'] == 'STOP_MARKET']
                        if sl_orders_final:
                            sl_order = sl_orders_final[0]
                            sl_order_info = f"价格 ${float(sl_order['triggerPrice']):.6f} (ID: {sl_order['algoId']})"
                        else:
                            sl_order_info = "⚠️ 未找到止损订单"
                    except:
                        pass
                    
                    # 🆕 动态调整完成摘要日志
                    logging.info(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 📊 {symbol} 止盈订单动态调整完成
╠════════════════════════════════════════════════════════════════════════════╣
║ 调整原因: {position.get('dynamic_tp_trigger', '未知')}
║ 止盈变化: {old_tp_pct_before:.1f}% → {new_tp_pct:.1f}%
║ 新止盈订单: 价格 ${new_tp_price:.6f} (ID: {new_order['algoId']})
║ 止损订单: {sl_order_info}
║ ⚠️  重要：止损订单未修改，继续保护中
╚════════════════════════════════════════════════════════════════════════════╝
""")
                    
                    position['_tp_updating'] = False  # 🔧 清除更新标记
                    return True
                
                except Exception as create_error:
                    logging.error(f"❌ {symbol} 创建新止盈订单失败: {create_error}")
                    # 播放报警声音
                    self.play_alert_sound()
                    position['_tp_updating'] = False  # 🔧 清除更新标记
                    return False
            finally:
                # 🔧 修复8：确保无论如何都清除更新标记
                if '_tp_updating' in position:
                    position['_tp_updating'] = False
        
        except Exception as e:
            logging.error(f"❌ {symbol} 更新止盈订单失败: {e}")
            self.play_alert_sound()
            if '_tp_updating' in position:
                position['_tp_updating'] = False
            return False
    
    def server_calculate_dynamic_tp(self, position: Dict) -> float:
        """计算动态止盈阈值（完整实现2h和12h判断）- 服务器版本"""
        try:
            symbol = position['symbol']
            entry_price = position['entry_price']
            entry_time = datetime.fromisoformat(position['entry_time'])
            current_time = datetime.now(timezone.utc)
            elapsed_hours = (current_time - entry_time).total_seconds() / 3600
            
            # 0-2小时：固定强势币止盈33%
            if elapsed_hours < 2.0:
                logging.debug(f"{symbol} 持仓{elapsed_hours:.1f}h，使用强势币止盈{self.strong_coin_tp_pct}%")
                return self.strong_coin_tp_pct
            
            # 2-12小时：2小时判断
            if 2.0 <= elapsed_hours < 12.0:
                if position.get('tp_2h_checked'):
                    cached_tp = position.get('tp_pct', self.strong_coin_tp_pct)
                    logging.debug(f"{symbol} 使用2h判断缓存结果: {cached_tp}%")
                    return cached_tp
                
                # 执行2小时判断
                logging.info(f"🔍 {symbol} 执行2小时动态止盈判断...")
                
                # 获取建仓后2小时的5分钟K线
                window_2h_end = entry_time + timedelta(hours=2)
                closes = self.server_get_5min_klines_from_binance(symbol, entry_time, window_2h_end)
                
                if len(closes) >= 2:
                    # 做空策略：计算每根K线相对建仓价的跌幅
                    returns = [(close - entry_price) / entry_price for close in closes]
                    
                    # 统计跌幅>5.5%的K线数量
                    count_drop = sum(1 for r in returns if r < -self.dynamic_tp_2h_growth_threshold)
                    pct_drop = count_drop / len(closes)
                    
                    if pct_drop >= self.dynamic_tp_2h_ratio:
                        # 强势币：下跌K线≥60%
                        adjusted_tp = self.strong_coin_tp_pct
                        position['tp_pct'] = adjusted_tp
                        position['dynamic_tp_strong'] = True
                        logging.info(f"✅ {symbol} 2h判定为强势币: 下跌占比{pct_drop*100:.1f}% ≥ 60%, 止盈{adjusted_tp}%")
                    else:
                        # 中等币：下跌K线<60%
                        adjusted_tp = self.medium_coin_tp_pct
                        position['tp_pct'] = adjusted_tp
                        position['dynamic_tp_medium'] = True
                        logging.warning(f"⚠️ {symbol} 2h判定为中等币: 下跌占比{pct_drop*100:.1f}% < 60%, 止盈降至{adjusted_tp}%")
                    
                    # 🔧 修复：不在这里设置tp_2h_checked，应该在更新订单后才设置
                    # position['tp_2h_checked'] = True
                    return adjusted_tp
                else:
                    # K线不足，保持强势币
                    logging.warning(f"⚠️ {symbol} 2h K线不足({len(closes)}根)，保持强势币{self.strong_coin_tp_pct}%")
                    # 🔧 修复：不在这里设置tp_2h_checked
                    # position['tp_2h_checked'] = True
                    return self.strong_coin_tp_pct
            
            # 12小时后：12小时判断
            if elapsed_hours >= 12.0:
                if position.get('tp_12h_checked'):
                    cached_tp = position.get('tp_pct', self.weak_coin_tp_pct)
                    logging.debug(f"{symbol} 使用12h判断缓存结果: {cached_tp}%")
                    return cached_tp
                
                # 执行12小时判断
                logging.info(f"🔍 {symbol} 执行12小时动态止盈判断...")
                
                # 获取建仓后12小时的5分钟K线
                window_12h_end = entry_time + timedelta(hours=12)
                closes = self.server_get_5min_klines_from_binance(symbol, entry_time, window_12h_end)
                
                if len(closes) >= 2:
                    # 做空策略：计算每根K线相对建仓价的跌幅
                    returns = [(close - entry_price) / entry_price for close in closes]
                    
                    # 统计跌幅>7.5%的K线数量
                    count_drop = sum(1 for r in returns if r < -self.dynamic_tp_12h_growth_threshold)
                    pct_drop = count_drop / len(closes)
                    
                    if pct_drop >= self.dynamic_tp_12h_ratio:
                        # 强势币：下跌K线≥60%（升级或保持）
                        adjusted_tp = self.strong_coin_tp_pct
                        position['tp_pct'] = adjusted_tp
                        position['dynamic_tp_strong'] = True
                        position['dynamic_tp_weak'] = False
                        logging.info(f"⬆️ {symbol} 12h确认为强势币: 下跌占比{pct_drop*100:.1f}% ≥ 60%, 止盈{adjusted_tp}%")
                    else:
                        # 下跌占比<60%：检查是否为连续暴涨
                        is_consecutive = self._server_check_consecutive_surge(position)
                        
                        if is_consecutive:
                            # 🔥 连续暴涨保护：保持强势或中等币止盈，不降为弱势币
                            if position.get('dynamic_tp_strong'):
                                adjusted_tp = self.strong_coin_tp_pct  # 保持33%
                                logging.info(
                                    f"✅ {symbol} 12h判断：连续2小时暴涨，保持强势币止盈：\n"
                                    f"  • 下跌占比 {pct_drop*100:.1f}% < 60%\n"
                                    f"  • 但为连续暴涨，保持强势币止盈={adjusted_tp}%"
                                )
                            else:
                                adjusted_tp = self.medium_coin_tp_pct  # 保持21%
                                logging.info(
                                    f"✅ {symbol} 12h判断：连续2小时暴涨，保持中等币止盈：\n"
                                    f"  • 下跌占比 {pct_drop*100:.1f}% < 60%\n"
                                    f"  • 但为连续暴涨，保持中等币止盈={adjusted_tp}%"
                                )
                            position['tp_pct'] = adjusted_tp
                        else:
                            # 非连续暴涨：正常降为弱势币
                            adjusted_tp = self.weak_coin_tp_pct
                            position['tp_pct'] = adjusted_tp
                            position['dynamic_tp_weak'] = True
                            position['dynamic_tp_strong'] = False
                            logging.warning(f"⚠️⚠️ {symbol} 12h判定为弱势币: 下跌占比{pct_drop*100:.1f}% < 60%, 止盈降至{adjusted_tp}%")
                    
                    position['tp_12h_checked'] = True
                    return adjusted_tp
                else:
                    # K线不足，保持原判断
                    if position.get('dynamic_tp_strong'):
                        tp = self.strong_coin_tp_pct
                    else:
                        tp = self.medium_coin_tp_pct
                    logging.warning(f"⚠️ {symbol} 12h K线不足({len(closes)}根)，保持{tp}%")
                    return tp
            
            return self.strong_coin_tp_pct
        
        except Exception as e:
            logging.error(f"❌ 计算动态止盈失败 {symbol}: {e}")
            return self.strong_coin_tp_pct
    
    def server_check_exit_conditions(self, position: Dict) -> Optional[str]:
        """检查平仓条件（完整实现）- 服务器版本"""
        try:
            symbol = position['symbol']
            entry_price = position['entry_price']
            entry_time = datetime.fromisoformat(position['entry_time'])
            current_time = datetime.now(timezone.utc)
            
            # 获取当前价格
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])
            
            # 计算涨跌幅（做空策略：价格下跌=正收益）
            price_change_pct = (current_price - entry_price) / entry_price
            
            # 计算持仓时间
            elapsed_hours = (current_time - entry_time).total_seconds() / 3600
            
            # 1. 72小时强制平仓（最高优先级）
            if elapsed_hours >= self.max_hold_hours:
                logging.warning(f"⏰ {symbol} 持仓{elapsed_hours:.1f}h 超过72h限制，强制平仓")
                return 'max_hold_time'
            
            # 2. 止损检查（做空：价格上涨触发止损）
            sl_threshold = self.stop_loss_pct / 100  # 18% -> 0.18
            if price_change_pct >= sl_threshold:
                actual_loss = price_change_pct * self.leverage * 100
                logging.warning(f"🛑 {symbol} 触发止损: 价格涨幅{price_change_pct*100:.2f}% ≥ {self.stop_loss_pct}%, 实际亏损{actual_loss:.1f}%")
                return 'stop_loss'
            
            # 3. 24小时涨幅止损（动态止损）
            if 24.0 <= elapsed_hours < 25.0 and not position.get('checked_24h'):
                if price_change_pct > self.max_gain_24h_threshold:
                    logging.warning(
                        f"🚨 {symbol} 24h涨幅止损: 涨幅{price_change_pct*100:.2f}% > {self.max_gain_24h_threshold*100:.1f}%"
                    )
                    position['checked_24h'] = True
                    return 'max_gain_24h'
                else:
                    position['checked_24h'] = True  # 标记已检查，避免重复
            
            # 🆕 4. 12小时及早平仓检查（精确在12小时整点）
            # 📌 修改逻辑与hm1l.py保持一致：从建仓时间开始获取144根K线，取第144根的收盘价判断
            # ⚠️ 只在12-13小时之间检查一次，判断的是"12小时整点时"的价格，不是之后的任意时刻
            if self.enable_12h_early_stop and 12.0 <= elapsed_hours < 13.0 and not position.get('checked_12h_early_stop'):
                try:
                    # 从币安API获取建仓后的5分钟K线（只用startTime，不用endTime）
                    entry_time_ms = int(entry_time.timestamp() * 1000)
                    
                    # 🔥 关键修改：只使用startTime和limit，不使用endTime
                    # 原因：同时指定startTime、endTime和limit会导致API返回最近的144根，而不是从startTime开始的144根
                    klines = self.client.futures_klines(
                        symbol=symbol,
                        interval='5m',
                        startTime=entry_time_ms,
                        limit=144
                    )
                    
                    if len(klines) >= 144:
                        # 取第144根K线的收盘价（12小时整点）
                        close_12h = float(klines[143][4])  # [4]是close价格
                        price_change_12h = (close_12h - entry_price) / entry_price
                        
                        # 验证K线时间是否正确（第144根应该接近建仓后12小时）
                        kline_144_time = datetime.fromtimestamp(klines[143][0] / 1000, tz=timezone.utc)
                        expected_time = entry_time + timedelta(hours=12)
                        time_diff_minutes = abs((kline_144_time - expected_time).total_seconds() / 60)
                        
                        if time_diff_minutes > 30:  # 如果时间相差超过30分钟，说明数据不对
                            logging.warning(
                                f"⚠️ {symbol} 12h检查时间异常：第144根K线时间{kline_144_time}与预期{expected_time}相差{time_diff_minutes:.0f}分钟，跳过检查"
                            )
                        elif price_change_12h > self.early_stop_12h_threshold:
                            logging.warning(
                                f"🚨 {symbol} 12h及早平仓触发: 持仓{elapsed_hours:.1f}h\n"
                                f"  • 12h整点收盘价：{close_12h:.6f}\n"
                                f"  • 建仓价：{entry_price:.6f}\n"
                                f"  • 涨幅：{price_change_12h*100:.2f}% > 阈值{self.early_stop_12h_threshold*100:.2f}%"
                            )
                            position['checked_12h_early_stop'] = True
                            return 'early_stop_loss_12h'
                        else:
                            logging.info(
                                f"✅ {symbol} 12h及早平仓检查通过: 涨幅{price_change_12h*100:.2f}% ≤ {self.early_stop_12h_threshold*100:.2f}%"
                            )
                    else:
                        logging.warning(f"⚠️ {symbol} 12h K线不足({len(klines)}根)，跳过检查")
                    
                    position['checked_12h_early_stop'] = True  # 标记已检查
                    
                except Exception as e:
                    logging.error(f"❌ {symbol} 12h及早平仓检查失败: {e}")
                    position['checked_12h_early_stop'] = True  # 失败也标记，避免重复
            
            # 5. 止盈检查（做空：价格下跌触发止盈）
            tp_pct = self.server_calculate_dynamic_tp(position)
            tp_threshold = -tp_pct / 100  # 33% -> -0.33
            if price_change_pct <= tp_threshold:
                actual_profit = abs(price_change_pct) * self.leverage * 100
                logging.info(
                    f"✨ {symbol} 触发止盈: 价格跌幅{abs(price_change_pct)*100:.2f}% ≥ {tp_pct}%, "
                    f"实际收益{actual_profit:.1f}%"
                )
                return 'take_profit'
            
            return None
        
        except Exception as e:
            logging.error(f"检查平仓条件失败 {symbol}: {e}")
            return None

    def server_setup_tp_sl_orders(self, position: Dict):
        """重新设置止盈止损订单（用于平仓失败后的恢复）"""
        try:
            symbol = position['symbol']
            entry_price = position['entry_price']
            quantity = position['quantity']
            tp_pct = position.get('tp_pct', self.strong_coin_tp_pct)

            logging.info(f"🔄 {symbol} 重新设置止盈止损订单...")

            # 获取交易对信息（价格精度等）
            symbol_info = self.client.futures_exchange_info()
            symbol_info = next((s for s in symbol_info['symbols'] if s['symbol'] == symbol), None)
            if not symbol_info:
                raise Exception(f"无法获取{symbol}交易对信息")

            # 获取价格精度
            price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
            if price_filter:
                tick_size = float(price_filter['tickSize'])
                if tick_size >= 1:
                    price_precision = 0
                else:
                    price_precision = len(str(tick_size).rstrip('0').split('.')[-1])
            else:
                tick_size = 0.000001
                price_precision = 6

            # 计算止盈价格（做空：价格下跌tp_pct%）
            tp_price_raw = entry_price * (1 - tp_pct / 100)
            take_profit_price = round(tp_price_raw / tick_size) * tick_size
            from decimal import Decimal, ROUND_HALF_UP
            take_profit_price = float(Decimal(str(take_profit_price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))

            # 计算止损价格（做空：价格上涨18%）
            sl_price_raw = entry_price * (1 + abs(self.stop_loss_pct) / 100)
            stop_loss_price = round(sl_price_raw / tick_size) * tick_size
            stop_loss_price = float(Decimal(str(stop_loss_price)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))

            # 清理可能存在的旧订单
            try:
                old_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
                if old_orders:
                    logging.info(f"🧹 {symbol} 清理 {len(old_orders)} 个旧订单...")
                    for old_order in old_orders:
                        try:
                            self.client.futures_cancel_algo_order(symbol=symbol, algoId=old_order['algoId'])
                        except:
                            pass
            except:
                pass

            # 设置止盈订单
            tp_order_id = None
            try:
                tp_order = self.client.futures_create_order(
                    symbol=symbol,
                    side='BUY',
                    type='TAKE_PROFIT_MARKET',
                    stopPrice=take_profit_price,
                    quantity=quantity,
                    reduceOnly=True
                )
                tp_order_id = str(tp_order.get('algoId') or tp_order.get('orderId'))
                position['tp_order_id'] = tp_order_id
                logging.info(f"✅ {symbol} 重新设置止盈订单: {take_profit_price:.6f} (ID: {tp_order_id})")
            except Exception as tp_error:
                logging.warning(f"⚠️ {symbol} 重新设置止盈订单失败: {tp_error}")

            # 设置止损订单
            sl_order_id = None
            try:
                sl_order = self.client.futures_create_order(
                    symbol=symbol,
                    side='BUY',
                    type='STOP_MARKET',
                    stopPrice=stop_loss_price,
                    quantity=quantity,
                    reduceOnly=True
                )
                sl_order_id = str(sl_order.get('algoId') or sl_order.get('orderId'))
                position['sl_order_id'] = sl_order_id
                logging.info(f"✅ {symbol} 重新设置止损订单: {stop_loss_price:.6f} (ID: {sl_order_id})")
            except Exception as sl_error:
                logging.warning(f"⚠️ {symbol} 重新设置止损订单失败: {sl_error}")

            # 保存更新
            self.server_save_positions_record()

            return {
                'tp_order_id': tp_order_id,
                'sl_order_id': sl_order_id,
                'tp_price': take_profit_price,
                'sl_price': stop_loss_price
            }

        except Exception as e:
            logging.error(f"❌ 重新设置止盈止损订单失败 {position['symbol']}: {e}")
            raise

    def server_close_position(self, position: Dict, reason: str):
        """平仓 - 服务器版本"""
        try:
            symbol = position['symbol']

            # 记录变动前状态
            before_state = {
                '持仓数量': position['quantity'],
                '建仓价格': position['entry_price'],
                '当前价格': self.client.futures_symbol_ticker(symbol=symbol)['price'],
                '未实现盈亏': position.get('pnl', 0),
                '持仓时长': (datetime.now(timezone.utc) - datetime.fromisoformat(position['entry_time'])).total_seconds() / 3600
            }
            
            # 🔧 修复1：平仓前先取消所有未成交的止盈止损订单
            logging.info(f"🔄 {symbol} 平仓前取消所有未成交订单...")
            cancelled_orders = []  # 记录被取消的订单
            try:
                algo_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
                if algo_orders:
                    logging.info(f"📋 {symbol} 找到 {len(algo_orders)} 个未成交订单，准备取消")
                    for order in algo_orders:
                        order_type = order['orderType']
                        order_id = order['algoId']
                        trigger_price = order.get('triggerPrice', 'N/A')
                        
                        try:
                            self.client.futures_cancel_algo_order(
                                symbol=symbol,
                                algoId=order_id
                            )
                            cancelled_orders.append({
                                'type': order_type,
                                'id': order_id,
                                'price': trigger_price
                            })
                            logging.info(f"✅ {symbol} 已取消订单: {order_type} (ID: {order_id}, 价格: {trigger_price})")
                        except Exception as cancel_error:
                            logging.error(f"❌ {symbol} 取消订单失败 (ID: {order_id}): {cancel_error}")
                else:
                    logging.info(f"✅ {symbol} 没有未成交订单")
            except Exception as cancel_all_error:
                logging.error(f"❌ {symbol} 查询/取消订单失败: {cancel_all_error}")
            
            # 🔧 修复2：从交易所获取实际持仓数量和方向（避免程序记录不准确）
            try:
                positions_info = self.client.futures_position_information(symbol=symbol)
                actual_position = next((p for p in positions_info if p['symbol'] == symbol), None)

                if actual_position:
                    actual_amt = float(actual_position['positionAmt'])
                    quantity = abs(actual_amt)  # 取绝对值作为平仓数量
                    is_long_position = actual_amt > 0  # 正数=做多，负数=做空

                    logging.info(f"📊 {symbol} 从交易所获取实际持仓: 数量={actual_amt} (方向={'做多' if is_long_position else '做空'}, 记录数量: {position['quantity']})")
                else:
                    quantity = position['quantity']
                    is_long_position = False  # 默认假设是做空（程序只开做空）
                    logging.warning(f"⚠️ {symbol} 无法获取实际持仓，使用程序记录数量: {quantity} (假设做空)")
            except Exception as get_position_error:
                quantity = position['quantity']
                is_long_position = False  # 默认假设是做空
                logging.warning(f"⚠️ {symbol} 获取实际持仓失败: {get_position_error}，使用程序记录数量: {quantity} (假设做空)")

            # 🔧 修复3：动态获取数量精度并调整（使用round而非int，避免丢失）
            try:
                exchange_info = self.client.futures_exchange_info()
                symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)

                if symbol_info:
                    lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                    if lot_size_filter:
                        step_size = float(lot_size_filter['stepSize'])
                        # 根据stepSize精度调整（使用round四舍五入，而非int向下截断）
                        if step_size >= 1:
                            quantity_adjusted = round(quantity / step_size) * step_size
                            quantity_adjusted = int(quantity_adjusted)
                            qty_precision = 0
                        else:
                            qty_precision = len(str(step_size).rstrip('0').split('.')[-1])
                            # 四舍五入到stepSize的整数倍
                            quantity_adjusted = round(quantity / step_size) * step_size
                            quantity_adjusted = round(quantity_adjusted, qty_precision)

                        logging.info(f"📏 {symbol} 数量精度调整: {quantity} → {quantity_adjusted} (stepSize={step_size})")
                        quantity = quantity_adjusted
                    else:
                        quantity = round(quantity, 3)
                else:
                    quantity = round(quantity, 3)
            except Exception as precision_error:
                logging.warning(f"⚠️ {symbol} 获取精度失败: {precision_error}，使用默认精度")
                quantity = round(quantity, 3)

            # 🔧 修复4：根据实际仓位方向决定平仓买卖方向
            if is_long_position:
                close_side = 'SELL'  # 做多平仓 = 卖出
                logging.info(f"🔄 {symbol} 检测到做多仓位，将使用SELL订单平仓")
            else:
                close_side = 'BUY'   # 做空平仓 = 买入
                logging.info(f"🔄 {symbol} 检测到做空仓位，将使用BUY订单平仓")

            # 🔧 先尝试带reduceOnly，如果失败则重试不带reduceOnly
            try:
                order = self.client.futures_create_order(
                    symbol=symbol,
                    side=close_side,
                    type='MARKET',
                    quantity=quantity,
                    reduceOnly=True
                )
            except Exception as reduce_error:
                if 'ReduceOnly Order is rejected' in str(reduce_error):
                    logging.warning(f"⚠️ {symbol} reduceOnly平仓被拒绝，尝试普通市价单")
                    try:
                        # 重试：不带reduceOnly
                        order = self.client.futures_create_order(
                            symbol=symbol,
                            side=close_side,
                            type='MARKET',
                            quantity=quantity
                        )
                    except Exception as margin_error:
                        if 'Margin is insufficient' in str(margin_error):
                            logging.error(f"❌ {symbol} 保证金不足，尝试分批平仓")
                            # 尝试分批平仓：先平一半仓位
                            half_quantity = quantity / 2

                            # 🔧 修复：对分批数量也进行精度调整
                            try:
                                # 使用和之前相同的精度调整逻辑
                                if 'step_size' in locals():
                                    half_quantity_adjusted = round(half_quantity / step_size) * step_size
                                    if step_size >= 1:
                                        half_quantity_adjusted = int(half_quantity_adjusted)
                                    else:
                                        qty_precision = len(str(step_size).rstrip('0').split('.')[-1])
                                        half_quantity_adjusted = round(half_quantity_adjusted, qty_precision)
                                    half_quantity = half_quantity_adjusted
                                    logging.info(f"📏 {symbol} 分批数量精度调整: {half_quantity}")
                            except:
                                half_quantity = round(half_quantity, 3)

                            try:
                                order = self.client.futures_create_order(
                                    symbol=symbol,
                                    side=close_side,
                                    type='MARKET',
                                    quantity=half_quantity
                                )
                                logging.info(f"✅ {symbol} 成功平仓一半仓位 ({half_quantity})，等待再次尝试")

                                # 🔧 修复：重新获取实际剩余持仓数量，而不是假设还有一半
                                import time
                                time.sleep(0.5)  # 等待订单执行

                                try:
                                    # 重新获取实际持仓
                                    positions_info = self.client.futures_position_information(symbol=symbol)
                                    actual_position = next((p for p in positions_info if p['symbol'] == symbol), None)

                                    if actual_position:
                                        remaining_amt = float(actual_position['positionAmt'])
                                        remaining_quantity = abs(remaining_amt)

                                        # 🔧 修复：对剩余数量也进行精度调整
                                        if 'step_size' in locals() and remaining_quantity > 0:
                                            remaining_adjusted = round(remaining_quantity / step_size) * step_size
                                            if step_size >= 1:
                                                remaining_adjusted = int(remaining_adjusted)
                                            else:
                                                remaining_adjusted = round(remaining_adjusted, qty_precision)
                                            remaining_quantity = remaining_adjusted

                                        logging.info(f"📊 {symbol} 重新获取剩余持仓: {remaining_quantity}")

                                        if remaining_quantity > 0:
                                            # 平仓剩余仓位
                                            remaining_order = self.client.futures_create_order(
                                                symbol=symbol,
                                                side=close_side,
                                                type='MARKET',
                                                quantity=remaining_quantity
                                            )
                                            logging.info(f"✅ {symbol} 成功平仓剩余仓位 ({remaining_quantity})")
                                        else:
                                            logging.info(f"✅ {symbol} 所有仓位已平仓完毕")
                                    else:
                                        logging.warning(f"⚠️ {symbol} 无法获取剩余持仓信息，可能已全部平仓")

                                except Exception as remaining_error:
                                    logging.error(f"❌ {symbol} 平仓剩余仓位失败: {remaining_error}")
                                    # 如果仍然失败，发送紧急报警
                                    send_email_alert(
                                        "平仓失败 - 需要人工干预",
                                        f"{symbol} 分批平仓仍失败，请立即检查账户状态并手动平仓\n"
                                        f"已平仓: {half_quantity}\n"
                                        f"剩余仓位: 未知\n"
                                        f"错误信息: {remaining_error}"
                                    )

                            except Exception as half_error:
                                logging.error(f"❌ {symbol} 分批平仓也失败: {half_error}")
                                # 发送紧急报警
                                send_email_alert(
                                    "平仓完全失败 - 紧急",
                                    f"{symbol} 所有平仓尝试都失败，请立即检查账户\n"
                                    f"建仓价格: {position['entry_price']}\n"
                                    f"当前价格: {self.client.futures_symbol_ticker(symbol=symbol)['price']}\n"
                                    f"持仓数量: {quantity}\n"
                                    f"杠杆: {self.leverage}x\n"
                                    f"最后错误: {half_error}"
                                )
                                raise margin_error  # 重新抛出原错误
                        else:
                            raise margin_error  # 其他错误直接抛出
                else:
                    raise
            
            # 获取成交价格
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            exit_price = float(ticker['price'])
            
            # 计算盈亏（根据实际仓位方向）
            entry_price = position['entry_price']
            if is_long_position:
                # 做多：价格上涨=盈利
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                # 做空：价格下跌=盈利
                pnl_pct = (entry_price - exit_price) / entry_price
            pnl_value = pnl_pct * position['position_value'] * self.leverage
            
            # 计算持仓时长
            entry_time = datetime.fromisoformat(position['entry_time'])
            current_time = datetime.now(timezone.utc)
            elapsed_hours = (current_time - entry_time).total_seconds() / 3600
            
            # 从持仓列表移除
            self.positions.remove(position)

            # 记录变动后状态
            after_state = {
                '持仓数量': 0,
                '状态': '已平仓',
                '平仓价格': exit_price,
                '盈亏金额': pnl_value,
                '盈亏比例': pnl_pct
            }

            # 定义平仓原因中文映射
            reason_map = {
                'take_profit': '止盈',
                'stop_loss': '止损',
                'max_hold_time': '72小时强制平仓',
                'max_gain_24h': '24h涨幅止损',
                'early_stop_loss_2h': '2h及早止损',
                'early_stop_loss_12h': '12h及早止损',
                'manual_close': '手动平仓'
            }
            reason_cn = reason_map.get(reason, reason)

            # 统一日志记录
            change_type = 'manual_close' if reason == 'manual_close' else 'auto_close'
            self.server_log_position_change(
                change_type,
                symbol,
                {
                    '平仓原因': reason_cn,
                    '持仓时长': f"{elapsed_hours:.1f}小时",
                    '成交价格': exit_price,
                    '盈亏比例': f"{pnl_pct*100:.2f}%",
                    '盈亏金额': pnl_value
                },
                before_state,
                after_state,
                success=True
            )

            # 从记录文件中删除
            self.server_save_positions_record()
            
            # 🆕 平仓完成摘要日志（包含订单取消详情）
            cancelled_orders_str = ""
            if cancelled_orders:
                for co in cancelled_orders:
                    cancelled_orders_str += f"\n║   - {co['type']}: ID {co['id']}, 价格 {co['price']}"
            else:
                cancelled_orders_str = "\n║   - 无未成交订单"
            
            # reason_cn 已在前面定义
            
            logging.info(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 💰 {symbol} 平仓完成
╠════════════════════════════════════════════════════════════════════════════╣
║ 平仓原因: {reason_cn}
║ 建仓时间: {entry_time}
║ 平仓时间: {current_time}
║ 持仓时长: {elapsed_hours:.1f}小时
║ 
║ 价格信息:
║   - 建仓价格: ${entry_price:.6f}
║   - 平仓价格: ${exit_price:.6f}
║   - 价格变化: {pnl_pct*100:+.2f}%
║ 
║ 盈亏情况:
║   - 持仓数量: {quantity}
║   - 投入金额: ${position['position_value']:.2f}
║   - 杠杆倍数: {self.leverage}x
║   - 盈亏金额: ${pnl_value:+.2f} USDT
║   - 盈亏比例: {pnl_pct*100:+.2f}%
║ 
║ 取消的订单:{cancelled_orders_str}
║ 
║ 剩余持仓: {len(self.positions)}个
╚════════════════════════════════════════════════════════════════════════════╝
""")
            
            # 🔧 强制刷新日志（确保平仓日志立即写入）
            for handler in logging.getLogger().handlers:
                if hasattr(handler, 'flush'):
                    handler.flush()
            
            # 🔧 修复3：平仓后再次检查并清理残留订单
            try:
                import time
                time.sleep(0.5)  # 等待0.5秒确保订单状态同步
                algo_orders_after = self.client.futures_get_open_algo_orders(symbol=symbol)
                if algo_orders_after:
                    logging.warning(f"⚠️ {symbol} 平仓后仍有 {len(algo_orders_after)} 个残留订单，再次清理")
                    for order in algo_orders_after:
                        try:
                            self.client.futures_cancel_algo_order(
                                symbol=symbol,
                                algoId=order['algoId']
                            )
                            logging.info(f"✅ {symbol} 已清理残留订单: {order['orderType']} (algoId: {order['algoId']})")
                        except Exception as cleanup_error:
                            logging.warning(f"⚠️ {symbol} 清理残留订单失败: {cleanup_error}")
            except Exception as cleanup_check_error:
                logging.warning(f"⚠️ {symbol} 检查残留订单失败: {cleanup_check_error}")
        
        except Exception as e:
            logging.error(f"❌ 平仓失败 {position['symbol']}: {e}")

            # 🚨 关键修复：平仓失败时重新设置止盈止损订单
            # 因为前面已经取消了所有订单，如果平仓失败，持仓还在但止盈止损没了
            try:
                logging.warning(f"🔄 {position['symbol']} 平仓失败，尝试重新设置止盈止损订单...")

                # 重新设置止盈止损订单
                self.server_setup_tp_sl_orders(position)

                logging.info(f"✅ {position['symbol']} 已重新设置止盈止损订单")

            except Exception as reset_error:
                logging.error(f"❌ 重新设置止盈止损失败 {position['symbol']}: {reset_error}")

                # 发送紧急告警
                send_email_alert(
                    "止盈止损重设失败 - 紧急",
                    f"{position['symbol']} 平仓失败且重新设置止盈止损也失败\n"
                    f"建仓价格: {position['entry_price']}\n"
                    f"当前价格: {self.client.futures_symbol_ticker(symbol=position['symbol'])['price']}\n"
                    f"请立即手动设置止盈止损！\n"
                    f"平仓错误: {e}\n"
                    f"重设错误: {reset_error}"
                )
    
    def server_monitor_positions(self):
        """监控持仓（集成动态止盈订单更新）- 服务器版本"""
        if not self.positions:
            return  # 没有持仓，直接返回
        
        for position in self.positions[:]:  # 复制列表避免迭代时修改
            # 1. 检查平仓条件
            exit_reason = self.server_check_exit_conditions(position)
            if exit_reason:
                self.server_close_position(position, exit_reason)
                continue
            
            # 2. 检查是否需要动态调整止盈订单
            entry_time = datetime.fromisoformat(position['entry_time'])
            current_time = datetime.now(timezone.utc)
            elapsed_hours = (current_time - entry_time).total_seconds() / 3600
            
            # 2小时检查窗口（2.0-2.5小时）
            if 2.0 <= elapsed_hours < 2.5 and not position.get('tp_2h_checked'):
                logging.info(f"🕐 {position['symbol']} 进入2小时检查窗口 ({elapsed_hours:.2f}h)")

                # 计算新止盈
                new_tp_pct = self.server_calculate_dynamic_tp(position)

                # ✅ 关键修复：从交易所获取实际的止盈价格，而不是从position记录
                symbol = position['symbol']
                entry_price = position['entry_price']
                exchange_tp_order = self.server_get_exchange_tp_order(symbol)
                
                if exchange_tp_order:
                    # 从交易所订单反推止盈比例
                    exchange_tp_price = float(exchange_tp_order['triggerPrice'])
                    old_tp_pct = abs((entry_price - exchange_tp_price) / entry_price * 100)
                    logging.info(f"📊 {symbol} 当前交易所止盈: {old_tp_pct:.1f}%, 新止盈: {new_tp_pct:.1f}%")
                else:
                    old_tp_pct = self.strong_coin_tp_pct
                    logging.warning(f"⚠️ {symbol} 未找到交易所止盈订单，使用默认值{old_tp_pct}%")
                
                # 如果止盈比例改变，更新交易所订单
                if abs(new_tp_pct - old_tp_pct) > 0.5:  # 差异超过0.5%才更新
                    # 记录变动前状态
                    before_state = {
                        '止盈百分比': old_tp_pct,
                        '止盈价格': exchange_tp_price
                    }

                    success = self.server_update_exchange_tp_order(position, new_tp_pct)
                    if success:
                        # 记录变动后状态
                        entry_price = position['entry_price']
                        new_tp_price = entry_price * (1 - new_tp_pct / 100)
                        after_state = {
                            '止盈百分比': new_tp_pct,
                            '止盈价格': new_tp_price
                        }

                        # 统一日志记录
                        self.server_log_position_change(
                            'dynamic_tp',
                            position['symbol'],
                            {
                                '触发类型': '2小时动态止盈',
                                '判断结果': '中等币' if new_tp_pct == self.medium_coin_tp_pct else '强势币',
                                '时长': f"{elapsed_hours:.1f}小时"
                            },
                            before_state,
                            after_state,
                            success=True
                        )

                        # 保存更新后的记录
                        self.server_save_positions_record()
                    else:
                        # 记录失败
                        self.server_log_position_change(
                            'dynamic_tp',
                            position['symbol'],
                            {
                                '触发类型': '2小时动态止盈',
                                '操作': '更新止盈订单'
                            },
                            before_state,
                            None,
                            success=False,
                            error_msg="止盈订单更新失败"
                        )
                else:
                    # 即使没变化，也标记为已检查
                    position['tp_2h_checked'] = True
                    # 保存标记状态
                    self.server_save_positions_record()
                    logging.info(f"ℹ️ {position['symbol']} 2h判断完成，止盈维持{old_tp_pct:.1f}%")
            
            # 12小时检查窗口（12.0-12.5小时）
            if 12.0 <= elapsed_hours < 12.5 and not position.get('tp_12h_checked'):
                logging.info(f"🕐 {position['symbol']} 进入12小时检查窗口 ({elapsed_hours:.2f}h)")

                # 计算新止盈
                new_tp_pct = self.server_calculate_dynamic_tp(position)
                
                # ✅ 关键修复：从交易所获取实际的止盈价格
                symbol = position['symbol']
                entry_price = position['entry_price']
                exchange_tp_order = self.server_get_exchange_tp_order(symbol)
                
                if exchange_tp_order:
                    # 从交易所订单反推止盈比例
                    exchange_tp_price = float(exchange_tp_order['triggerPrice'])
                    old_tp_pct = abs((entry_price - exchange_tp_price) / entry_price * 100)
                    logging.info(f"📊 {symbol} 当前交易所止盈: {old_tp_pct:.1f}%, 新止盈: {new_tp_pct:.1f}%")
                else:
                    old_tp_pct = self.medium_coin_tp_pct
                    logging.warning(f"⚠️ {symbol} 未找到交易所止盈订单，使用默认值{old_tp_pct}%")
                
                # 🔥 关键逻辑：如果判定为弱势币（10%），检查是否为连续确认
                if new_tp_pct == self.weak_coin_tp_pct:
                    is_consecutive = self._server_check_consecutive_surge(position)
                    
                    if is_consecutive:
                        # ✅ 连续确认：保留原止盈（强势或中等币）
                        if position.get('dynamic_tp_strong'):
                            new_tp_pct = self.strong_coin_tp_pct  # 保持33%
                            logging.info(
                                f"🔥 {position['symbol']} 连续2小时卖量暴涨确认！\n"
                                f"  • 原判定：弱势币10%\n"
                                f"  • 修正后：保持强势币{new_tp_pct:.0f}%止盈"
                            )
                        else:
                            new_tp_pct = self.medium_coin_tp_pct  # 保持21%
                            logging.info(
                                f"🔥 {position['symbol']} 连续2小时卖量暴涨确认！\n"
                                f"  • 原判定：弱势币10%\n"
                                f"  • 修正后：保持中等币{new_tp_pct:.0f}%止盈"
                            )
                        
                        # 更新position状态
                        position['is_consecutive_confirmed'] = True
                        position['tp_pct'] = new_tp_pct
                        
                        # 记录止盈修改历史
                        if 'tp_history' not in position:
                            position['tp_history'] = []
                        position['tp_history'].append({
                            'time': datetime.now(timezone.utc).isoformat(),
                            'from': old_tp_pct,
                            'to': new_tp_pct,
                            'reason': position.get('dynamic_tp_trigger', '12h_consecutive_check')
                        })
                
                # 如果止盈比例改变，更新交易所订单
                if abs(new_tp_pct - old_tp_pct) > 0.5:  # 差异超过0.5%才更新
                    # 记录变动前状态
                    before_state = {
                        '止盈百分比': old_tp_pct,
                        '止盈价格': exchange_tp_price
                    }

                    success = self.server_update_exchange_tp_order(position, new_tp_pct)
                    if success:
                        # 记录变动后状态
                        entry_price = position['entry_price']
                        new_tp_price = entry_price * (1 - new_tp_pct / 100)
                        after_state = {
                            '止盈百分比': new_tp_pct,
                            '止盈价格': new_tp_price
                        }

                        # 统一日志记录
                        self.server_log_position_change(
                            'dynamic_tp',
                            position['symbol'],
                            {
                                '触发类型': '12小时动态止盈',
                                '判断结果': '弱势币' if new_tp_pct == self.weak_coin_tp_pct else ('中等币' if new_tp_pct == self.medium_coin_tp_pct else '强势币'),
                                '连续确认': is_consecutive,
                                '时长': f"{elapsed_hours:.1f}小时"
                            },
                            before_state,
                            after_state,
                            success=True
                        )

                        # 保存更新后的记录
                        self.server_save_positions_record()
                    else:
                        # 记录失败
                        self.server_log_position_change(
                            'dynamic_tp',
                            position['symbol'],
                            {
                                '触发类型': '12小时动态止盈',
                                '操作': '更新止盈订单'
                            },
                            before_state,
                            None,
                            success=False,
                            error_msg="止盈订单更新失败"
                        )
                else:
                    # 即使没变化，也标记为已检查
                    position['tp_12h_checked'] = True
                    # 保存标记状态
                    self.server_save_positions_record()
                    logging.info(f"ℹ️ {position['symbol']} 12h判断完成，止盈维持{old_tp_pct:.1f}%")
    
    def server_get_tp_sl_from_binance(self, symbol: str) -> tuple:
        """从币安查询止盈止损价格 - 服务器版本"""
        try:
            algo_orders = self.client.futures_get_open_algo_orders(symbol=symbol)
            
            tp_price = "N/A"
            sl_price = "N/A"
            
            for order in algo_orders:
                if order['orderType'] == 'TAKE_PROFIT_MARKET':
                    tp_price = f"{float(order['triggerPrice']):.6f}"
                elif order['orderType'] == 'STOP_MARKET':
                    sl_price = f"{float(order['triggerPrice']):.6f}"
            
            return tp_price, sl_price
            
        except Exception as e:
            logging.warning(f"查询 {symbol} 止盈止损失败: {e}")
            return "N/A", "N/A"


# ==================== Flask Web服务 ====================
app = Flask(__name__)
CORS(app)  # 允许跨域
auth = HTTPBasicAuth()

# 🔐 用户认证配置
# 用户名和密码（可以从环境变量或配置文件读取）
users = {
    "admin": generate_password_hash(os.getenv('WEB_PASSWORD', 'admin123'))  # 默认密码admin123
}

@auth.verify_password
def verify_password(username, password):
    """验证用户名和密码"""
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None

        # 全局变量
strategy = None
is_running = False
start_time = None  # 系统启动时间
scan_thread = None
monitor_thread = None


# ==================== Web界面路由 ====================
@app.route('/')
@auth.login_required
def index():
    """主页 - Web监控界面"""
    return render_template('monitor.html')


# ==================== API接口 - 查看类 ====================
@app.route('/api/status')
@auth.login_required
def get_status():
    """获取系统状态"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        # 获取详细账户信息
        account_info = strategy.server_get_account_info()
        
        # 今日统计
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        today_entries = strategy.daily_entries if strategy.last_entry_date == today else 0
        
        result = {
            'success': True,
            'running': is_running,
            'positions_count': len(strategy.positions),
            'today_entries': today_entries,
            'max_positions': strategy.max_positions,
            'max_daily_entries': strategy.max_daily_entries,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # 添加详细账户信息
        if account_info:
            result.update({
                'total_balance': account_info['total_balance'],
                'available_balance': account_info['available_balance'],
                'unrealized_pnl': account_info['unrealized_pnl'],
                'daily_pnl': account_info['daily_pnl']
            })
        else:
            # 降级：如果获取详细信息失败，使用简单余额
            balance = strategy.server_get_account_balance()
            strategy.account_balance = balance
            result['balance'] = balance
        
        return jsonify(result)
    except Exception as e:
        logging.error(f"❌ 获取系统状态失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/funding_fee')
@auth.login_required
def get_funding_fee():
    """获取资金费历史"""
    try:
        days = int(request.args.get('days', 3))
        
        # 查询最近N天的资金费
        now = datetime.now(timezone.utc)
        start_time = int((now - timedelta(days=days)).timestamp() * 1000)
        
        income_history = strategy.client.futures_income_history(
            incomeType='FUNDING_FEE',
            startTime=start_time,
            limit=1000
        )
        
        # 按日期分组统计
        daily_fees = {}
        total_fee = 0
        
        for record in income_history:
            income = float(record['income'])
            timestamp = int(record['time']) / 1000
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            date_str = dt.strftime('%Y-%m-%d')
            symbol = record['symbol']
            
            if date_str not in daily_fees:
                daily_fees[date_str] = {
                    'total': 0,
                    'count': 0,
                    'details': []
                }
            
            daily_fees[date_str]['total'] += income
            daily_fees[date_str]['count'] += 1
            daily_fees[date_str]['details'].append({
                'time': dt.strftime('%H:%M UTC'),
                'symbol': symbol,
                'amount': income
            })
            
            total_fee += income
        
        return jsonify({
            'success': True,
            'days': days,
            'daily_fees': daily_fees,
            'total_fee': total_fee,
            'average_daily': total_fee / days if days > 0 else 0
        })
    except Exception as e:
        logging.error(f"❌ 获取资金费失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/positions')
@auth.login_required
def get_positions():
    """获取持仓详情"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        # 获取币安持仓信息
        positions_info = strategy.client.futures_position_information()
        
        # 获取账户余额信息（用于计算仓位占比）
        account_balance = 0
        try:
            account_info = strategy.client.futures_account()
            account_balance = float(account_info.get('totalWalletBalance', 0))
        except Exception as e:
            logging.error(f"❌ 获取账户余额失败: {e}")
            account_balance = 0
        
        result = []
        for pos in strategy.positions:
            symbol = pos['symbol']
            
            # 从交易所获取实时价格和盈亏
            binance_pos = next((p for p in positions_info if p['symbol'] == symbol), None)
            
            if binance_pos:
                mark_price = float(binance_pos['markPrice'])
                unrealized_pnl = float(binance_pos['unRealizedProfit'])
                pnl_pct = ((pos['entry_price'] - mark_price) / pos['entry_price']) * 100  # 做空收益
            else:
                # 如果交易所没有数据，用市价
                ticker = strategy.client.futures_symbol_ticker(symbol=symbol)
                mark_price = float(ticker['price'])
                pnl_pct = ((pos['entry_price'] - mark_price) / pos['entry_price']) * 100
                unrealized_pnl = pnl_pct / 100 * pos['position_value'] * strategy.leverage
            
            # 💰 计算新增字段
            leverage = int(pos.get('leverage', strategy.leverage))
            quantity = pos['quantity']
            entry_price = pos['entry_price']
            
            # 1. 持仓投入金额（保证金）= 持仓价值 / 杠杆
            position_margin = (quantity * entry_price) / leverage
            
            # 2. 当下金额（当前仓位价值）
            current_value = quantity * mark_price
            
            # 3. 仓位占比 = 投入金额 / 账户总余额 * 100%
            position_ratio = (position_margin / account_balance * 100) if account_balance > 0 else 0
            
            # 获取挂单
            try:
                algo_orders = strategy.client.futures_get_open_algo_orders(symbol=symbol)
                orders = []
                for order in algo_orders:
                    orders.append({
                        'id': order.get('algoId', ''),
                        'type': order.get('orderType', ''),
                        'side': order.get('side', ''),
                        'price': float(order.get('triggerPrice', 0)),
                        'status': order.get('status', 'ACTIVE')  # 🔧 修复：status字段可能不存在
                    })
            except Exception as e:
                logging.error(f"❌ 查询 {symbol} 挂单失败: {e}")
                orders = []
            
            # 计算持仓时间
            entry_time = datetime.fromisoformat(pos['entry_time'])
            elapsed_hours = (datetime.now(timezone.utc) - entry_time).total_seconds() / 3600
            
            result.append({
                'symbol': symbol,
                'entry_price': pos['entry_price'],
                'entry_time': pos['entry_time'],
                'quantity': pos['quantity'],
                'mark_price': mark_price,
                'pnl': unrealized_pnl,
                'pnl_pct': pnl_pct,
                'leverage': leverage,
                'tp_pct': pos.get('tp_pct', strategy.strong_coin_tp_pct),
                'orders': orders,
                'elapsed_hours': elapsed_hours,
                'tp_2h_checked': pos.get('tp_2h_checked', False),
                'tp_12h_checked': pos.get('tp_12h_checked', False),
                'is_consecutive': pos.get('is_consecutive_confirmed', False),
                # 🔧 修复：添加动态止盈判断标记
                'dynamic_tp_strong': pos.get('dynamic_tp_strong', False),
                'dynamic_tp_medium': pos.get('dynamic_tp_medium', False),
                'dynamic_tp_weak': pos.get('dynamic_tp_weak', False),
                # 💰 新增字段
                'position_margin': position_margin,      # 持仓投入金额（保证金）
                'current_value': current_value,          # 当下金额（当前仓位价值）
                'position_ratio': position_ratio,        # 仓位占比（%）
                'account_balance': account_balance       # 账户总余额（用于前端显示）
            })
        
        return jsonify({
            'success': True,
            'positions': result,
            'account_balance': account_balance  # 也在顶层返回账户余额
        })
    
    except Exception as e:
        logging.error(f"❌ 获取持仓失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
@auth.login_required
def get_logs():
    """获取最新日志"""
    try:
        all_logs = []

        # 获取主日志文件
        log_files = glob.glob(os.path.join(log_dir, 'ae_server_*.log'))
        if log_files:
            # 按文件修改时间排序，选择最近修改的文件
            latest_log = max(log_files, key=os.path.getmtime)

            with open(latest_log, 'r', encoding='utf-8') as f:
                main_logs = f.readlines()
            all_logs.extend(main_logs)

        # 获取仓位变动日志
        position_log_file = os.path.join(log_dir, 'position_changes.log')
        if os.path.exists(position_log_file):
            with open(position_log_file, 'r', encoding='utf-8') as f:
                position_logs = f.readlines()
            all_logs.extend(position_logs)

        # 获取请求参数
        lines_count = request.args.get('lines', 100, type=int)
        lines_count = min(lines_count, 500)  # 最多500行

        if all_logs:
            # 按时间倒序排列（最新的在前面）
            all_logs.sort(key=lambda x: x, reverse=True)
            last_lines = all_logs[:lines_count]  # 前N行（最新的）

            # 确定日志文件名
            log_file_names = []
            if log_files:
                log_file_names.append(os.path.basename(latest_log))
            if os.path.exists(position_log_file):
                log_file_names.append('position_changes.log')
            log_file_name = ' + '.join(log_file_names) if log_file_names else 'unknown'
        else:
            last_lines = []
            log_file_name = 'no logs found'

        return jsonify({
            'success': True,
            'logs': [line.strip() for line in last_lines],
            'log_file': log_file_name
        })
    
    except Exception as e:
        logging.error(f"❌ 获取日志失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs/search')
@auth.login_required
def search_logs():
    """搜索所有日志文件中的关键字"""
    try:
        keyword = request.args.get('keyword', '')
        date = request.args.get('date', '')  # 可选：只搜索特定日期，格式：YYYYMMDD
        max_results = request.args.get('max', 100, type=int)
        max_results = min(max_results, 500)  # 最多500条
        
        if not keyword:
            return jsonify({'error': 'keyword参数必须提供'}), 400
        
        # 获取日志文件
        if date:
            # 只搜索指定日期的日志
            log_pattern = os.path.join(log_dir, f'ae_server_{date}_*.log')
        else:
            # 搜索所有日志
            log_pattern = os.path.join(log_dir, 'ae_server_*.log')
        
        log_files = sorted(glob.glob(log_pattern), key=os.path.getmtime, reverse=True)
        
        if not log_files:
            return jsonify({'success': True, 'results': [], 'files_searched': 0})
        
        results = []
        files_searched = 0
        
        # 搜索日志文件
        for log_file in log_files:
            files_searched += 1
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line_num, line in enumerate(f, 1):
                        if keyword in line:
                            results.append({
                                'file': os.path.basename(log_file),
                                'line': line_num,
                                'content': line.strip()
                            })
                            
                            if len(results) >= max_results:
                                break
            except Exception as file_error:
                logging.warning(f"⚠️ 读取日志文件失败 {log_file}: {file_error}")
            
            if len(results) >= max_results:
                break
        
        return jsonify({
            'success': True,
            'keyword': keyword,
            'results': results,
            'files_searched': files_searched,
            'total_found': len(results)
        })
    
    except Exception as e:
        logging.error(f"❌ 搜索日志失败: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== API接口 - 操作类 ====================
@app.route('/api/close_position', methods=['POST'])
@auth.login_required
def api_close_position():
    """手动平仓 - API端点"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        data = request.json
        symbol = data['symbol']
        
        # 查找持仓
        position = next((p for p in strategy.positions if p['symbol'] == symbol), None)
        
        if not position:
            return jsonify({'error': f'{symbol} not found'}), 404
        
        # 记录变动前状态
        before_state = {
            '持仓数量': position['quantity'],
            '建仓价格': position['entry_price'],
            '当前价格': strategy.client.futures_symbol_ticker(symbol=symbol)['price'],
            '未实现盈亏': position.get('pnl', 0)
        }

        # 执行平仓
        strategy.server_close_position(position, 'manual_close')

        # 记录变动后状态
        after_state = {
            '持仓数量': 0,
            '状态': '已平仓'
        }

        # 统一日志记录
        strategy.server_log_position_change(
            'manual_close',
            symbol,
            {
                '操作人': 'Web界面用户',
                '请求IP': request.remote_addr,
                '平仓原因': '手动平仓',
                '持仓ID': position.get('position_id', '未知')[:8]
            },
            before_state,
            after_state,
            success=True
        )

        return jsonify({
            'success': True,
            'message': f'{symbol} 平仓成功'
        })
    
    except Exception as e:
        logging.error(f"❌ 手动平仓失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/update_tp_sl', methods=['POST'])
@auth.login_required
def update_tp_sl():
    """修改止盈止损（支持精确定位position_id，解决重复持仓问题）"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        data = request.json
        symbol = data.get('symbol')
        position_id = data.get('position_id')  # ✨ 新增：支持通过position_id精确定位
        tp_price = data.get('tp_price')  # 止盈价格
        sl_price = data.get('sl_price')  # 止损价格
        
        # ✨ 优先通过position_id查找（精确匹配）
        if position_id:
            position = next((p for p in strategy.positions if p.get('position_id') == position_id), None)
            if not position:
                return jsonify({'error': f'Position ID {position_id[:8]} not found'}), 404
            logging.info(f"🎯 通过position_id定位持仓: {position_id[:8]} ({position['symbol']})")
        elif symbol:
            # 兼容旧版本：通过symbol查找（如有多个持仓会有歧义）
            matching_positions = [p for p in strategy.positions if p['symbol'] == symbol]
            if not matching_positions:
                return jsonify({'error': f'{symbol} not found'}), 404
            if len(matching_positions) > 1:
                logging.warning(f"⚠️ {symbol} 发现{len(matching_positions)}个持仓，建议使用position_id参数精确定位")
                # 返回所有持仓的ID供用户选择
                positions_info = [
                    {
                        'position_id': p.get('position_id', '未知')[:8],
                        'entry_price': p['entry_price'],
                        'entry_time': p['entry_time'],
                        'quantity': p['quantity']
                    }
                    for p in matching_positions
                ]
                return jsonify({
                    'error': f'{symbol} 存在多个持仓，请使用position_id参数指定',
                    'positions': positions_info
                }), 400
            position = matching_positions[0]
        else:
            return jsonify({'error': '必须提供symbol或position_id参数'}), 400
        
        entry_price = position['entry_price']
        symbol = position['symbol']

        # 🔧 从交易所获取实际持仓数量（避免数量不一致问题）
        try:
            positions_info = strategy.client.futures_position_information(symbol=symbol)
            actual_position = next((p for p in positions_info if p['symbol'] == symbol), None)

            if actual_position:
                actual_amt = float(actual_position['positionAmt'])
                quantity = abs(actual_amt)  # 取绝对值作为订单数量
                is_long_position = actual_amt > 0
                logging.info(f"📊 {symbol} 从交易所获取实际持仓数量: {quantity} (方向: {'做多' if is_long_position else '做空'}, 记录数量: {position['quantity']})")
            else:
                quantity = position['quantity']
                is_long_position = False
                logging.warning(f"⚠️ {symbol} 无法获取实际持仓，使用程序记录数量: {quantity}")
        except Exception as get_position_error:
            quantity = position['quantity']
            is_long_position = False
            logging.warning(f"⚠️ {symbol} 获取实际持仓失败: {get_position_error}，使用程序记录数量: {quantity}")
        
        # 🆕 记录修改请求的详细信息
        logging.info(f"""
╔════════════════════════════════════════════════════════════════════════════╗
║ 🔧 Web界面修改止盈止损请求
╠════════════════════════════════════════════════════════════════════════════╣
║ 交易对: {symbol}
║ Position ID: {position.get('position_id', 'N/A')[:8]}
║ 建仓价格: ${entry_price:.6f}
║ 请求来源IP: {request.remote_addr}
║ 请求参数:
║   - 止盈价格: {f'${float(tp_price):.6f}' if tp_price else '❌ 不修改'}
║   - 止损价格: {f'${float(sl_price):.6f}' if sl_price else '❌ 不修改'}
║ 当前订单ID:
║   - 止盈订单: {position.get('tp_order_id', 'N/A')}
║   - 止损订单: {position.get('sl_order_id', 'N/A')}
╚════════════════════════════════════════════════════════════════════════════╝
""")
        
        # ✨ 取消现有订单（使用记录的订单ID精确取消）
        old_tp_id = position.get('tp_order_id')
        old_sl_id = position.get('sl_order_id')
        
        try:
            algo_orders = strategy.client.futures_get_open_algo_orders(symbol=symbol)
            tp_order_count = len([o for o in algo_orders if o['orderType'] == 'TAKE_PROFIT_MARKET'])
            sl_order_count = len([o for o in algo_orders if o['orderType'] == 'STOP_MARKET'])
            logging.info(f"📋 {symbol} 当前算法订单: 止盈×{tp_order_count}, 止损×{sl_order_count}")
            for order in algo_orders:
                order_id = str(order.get('algoId'))
                order_type = order.get('orderType', '')
                
                # 🔧 只取消需要更新的订单类型
                should_cancel = False
                cancel_reason = ""
                
                # 如果传了tp_price，取消止盈订单
                if tp_price and order_type == 'TAKE_PROFIT_MARKET':
                    should_cancel = True
                    cancel_reason = "更新止盈"
                
                # 如果传了sl_price，取消止损订单
                if sl_price and order_type == 'STOP_MARKET':
                    should_cancel = True
                    cancel_reason = "更新止损"
                
                if should_cancel:
                    try:
                        strategy.client.futures_cancel_algo_order(symbol=symbol, algoId=order['algoId'])
                        logging.info(f"✅ {symbol} 已取消旧订单: {order_type} (ID: {order_id}, 原因: {cancel_reason})")
                    except Exception as cancel_error:
                        logging.error(f"❌ {symbol} 取消订单失败 (ID: {order_id}): {cancel_error}")
        except Exception as query_error:
            logging.warning(f"⚠️ {symbol} 查询订单失败: {query_error}")
        
        # 🔧 动态获取价格精度（修复COMPUSDT、LPTUSDT等币种的精度错误）
        try:
            exchange_info = strategy.client.futures_exchange_info()
            symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
            
            if symbol_info:
                price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                if price_filter:
                    tick_size = float(price_filter['tickSize'])
                    if tick_size >= 1:
                        price_precision = 0
                    else:
                        price_precision = len(str(tick_size).rstrip('0').split('.')[-1])
                    logging.info(f"📏 {symbol} 价格精度: tickSize={tick_size}, precision={price_precision}")
                else:
                    tick_size = 0.000001
                    price_precision = 6
            else:
                tick_size = 0.000001
                price_precision = 6
        except:
            tick_size = 0.000001
            price_precision = 6
        
        # 设置新的止盈订单
        new_tp_order_id = None
        if tp_price:
            try:
                # 按正确精度取整
                tp_price_adjusted = round(float(tp_price) / tick_size) * tick_size
                # 🔧 使用Decimal确保精度，避免浮点误差
                from decimal import Decimal, ROUND_HALF_UP
                tp_price_adjusted = float(Decimal(str(tp_price_adjusted)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))
                
                tp_order = strategy.client.futures_create_order(
                    symbol=symbol,
                    side='BUY',
                    type='TAKE_PROFIT_MARKET',
                    stopPrice=tp_price_adjusted,
                    quantity=quantity,
                    reduceOnly=True
                )
                new_tp_order_id = str(tp_order.get('algoId') or tp_order.get('orderId'))
                position['tp_order_id'] = new_tp_order_id  # ✨ 更新订单ID
                
                # 更新position记录
                tp_pct = abs((entry_price - tp_price_adjusted) / entry_price * 100)
                position['tp_pct'] = tp_pct
                
                logging.info(f"✅ {symbol} 止盈订单已更新: {tp_price_adjusted} ({tp_pct:.1f}%), 订单ID: {new_tp_order_id}")
            except Exception as e:
                logging.error(f"❌ {symbol} 设置止盈失败: {e}")
        
        # 设置新的止损订单
        new_sl_order_id = None
        if sl_price:
            try:
                # 按正确精度取整
                sl_price_adjusted = round(float(sl_price) / tick_size) * tick_size
                # 🔧 使用Decimal确保精度，避免浮点误差
                sl_price_adjusted = float(Decimal(str(sl_price_adjusted)).quantize(Decimal(str(tick_size)), rounding=ROUND_HALF_UP))
                
                sl_order = strategy.client.futures_create_order(
                    symbol=symbol,
                    side='BUY',
                    type='STOP_MARKET',
                    stopPrice=sl_price_adjusted,
                    quantity=quantity,
                    reduceOnly=True
                )
                new_sl_order_id = str(sl_order.get('algoId') or sl_order.get('orderId'))
                position['sl_order_id'] = new_sl_order_id  # ✨ 更新订单ID
                
                logging.info(f"✅ {symbol} 止损订单已更新: {sl_price_adjusted}, 订单ID: {new_sl_order_id}")
            except Exception as e:
                logging.error(f"❌ {symbol} 设置止损失败: {e}")
        
        # 记录变动前状态
        before_state = {
            '止盈价格': position.get('tp_price', '无'),
            '止损价格': position.get('sl_price', '无')
        }

        # 记录变动后状态
        after_state = {}
        if tp_price:
            after_state['止盈价格'] = tp_price_adjusted if 'tp_price_adjusted' in locals() else tp_price
        if sl_price:
            after_state['止损价格'] = sl_price_adjusted if 'sl_price_adjusted' in locals() else sl_price

        # 统一日志记录
        details = {
            '操作人': 'Web界面用户',
            '请求IP': request.remote_addr,
            '持仓数量': quantity,
            '建仓价格': entry_price
        }

        if tp_price:
            details['新止盈价格'] = tp_price_adjusted if 'tp_price_adjusted' in locals() else tp_price
        if sl_price:
            details['新止损价格'] = sl_price_adjusted if 'sl_price_adjusted' in locals() else sl_price

        strategy.server_log_position_change(
            'manual_tp_sl',
            symbol,
            details,
            before_state,
            after_state,
            success=bool(new_tp_order_id or new_sl_order_id),
            error_msg=None if (new_tp_order_id or new_sl_order_id) else "未修改任何订单"
        )

        # 保存记录
        strategy.server_save_positions_record()

        return jsonify({
            'success': True,
            'message': '止盈止损已更新',
            'position_id': position.get('position_id', '未知')[:8],
            'tp_order_id': new_tp_order_id,
            'sl_order_id': new_sl_order_id
        })
    
    except Exception as e:
        logging.error(f"❌ 修改止盈止损失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cancel_order', methods=['POST'])
@auth.login_required
def cancel_order():
    """取消订单"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        data = request.json
        symbol = data['symbol']
        order_id = data['order_id']
        
        strategy.client.futures_cancel_algo_order(
            symbol=symbol,
            algoId=order_id
        )
        
        logging.info(f"✅ Web界面取消订单: {symbol} - {order_id}")
        
        return jsonify({
            'success': True,
            'message': '订单已取消'
        })
    
    except Exception as e:
        logging.error(f"❌ 取消订单失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/start_trading', methods=['POST'])
@auth.login_required
def start_trading():
    """启动自动交易"""
    global is_running, scan_thread, monitor_thread
    
    try:
        # 🔒 使用原子操作防止并发启动
        if is_running:
            return jsonify({'success': False, 'message': '已经在运行中'})
        
        # ✨ 立即设置标志（在启动线程之前）
        is_running = True
        
        try:
            # 启动扫描线程
            scan_thread = threading.Thread(target=scan_loop, daemon=True)
            scan_thread.start()
            
            # 启动监控线程
            monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
            monitor_thread.start()
            
            logging.info("🚀 Web界面启动自动交易")
            
            return jsonify({'success': True, 'message': '自动交易已启动'})
        except Exception as start_error:
            # 启动失败，恢复标志
            is_running = False
            raise
    
    except Exception as e:
        logging.error(f"❌ 启动交易失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/send_daily_report', methods=['POST'])
@auth.login_required
def send_daily_report_api():
    """手动发送每日报告"""
    try:
        logging.info("📧 手动触发发送每日报告")

        # 发送报告
        send_daily_report()

        return jsonify({
            'success': True,
            'message': '每日报告已发送'
        })

    except Exception as e:
        logging.error(f"❌ 手动发送每日报告失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop_trading', methods=['POST'])
@auth.login_required
def stop_trading():
    """停止自动交易"""
    global is_running
    
    try:
        is_running = False
        
        logging.info("⏹️ Web界面停止自动交易")
        
        return jsonify({'success': True, 'message': '自动交易已停止'})
    
    except Exception as e:
        logging.error(f"❌ 停止交易失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/manual_scan', methods=['POST'])
@auth.login_required
def manual_scan():
    """手动扫描"""
    try:
        if strategy is None:
            return jsonify({'error': 'Strategy not initialized'}), 500
        
        logging.info("🔍 Web界面触发手动扫描...")
        
        # 更新账户余额
        strategy.account_balance = strategy.server_get_account_balance()
        
        # 扫描信号
        signals = strategy.server_scan_sell_surge_signals()
        
        # 尝试建仓
        opened_count = 0
        for signal in signals:
            if strategy.server_open_position(signal):
                opened_count += 1
        
        return jsonify({
            'success': True,
            'message': f'扫描完成，发现 {len(signals)} 个信号，建仓 {opened_count} 个',
            'signals': signals
        })
    
    except Exception as e:
        logging.error(f"❌ 手动扫描失败: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== 后台线程 ====================
def scan_loop():
    """信号扫描循环（每小时3-5分钟扫描一次）
    
    ⚠️ 重要：每小时固定时间扫描，避免价格已经变化
    - 扫描时间窗口：每小时的第3-5分钟（UTC时间）
    - 每小时只扫描一次，避免重复
    - 检查上一个完整小时的卖量暴涨信号
    """
    global is_running
    
    logging.info("📡 信号扫描线程已启动")
    last_scan_hour = None  # 记录上次扫描的小时，避免重复
    consecutive_failures = 0  # 连续失败计数
    
    while True:
        try:
            if not is_running:
                time.sleep(10)
                continue
            
            # 获取当前UTC时间
            now = datetime.now(timezone.utc)
            current_hour = now.replace(minute=0, second=0, microsecond=0)
            
            # 每小时3-5分钟扫描，且本小时未扫描过
            if 3 <= now.minute < 5 and last_scan_hour != current_hour:
                logging.info(f"🔍 [定时扫描] UTC {now.strftime('%Y-%m-%d %H:%M:%S')} 开始扫描...")
                
                try:
                    # 更新账户余额
                    strategy.account_balance = strategy.server_get_account_balance()
                    logging.info(f"💰 账户余额: ${strategy.account_balance:.2f}")
                    
                    # 🔧 强制刷新日志
                    for handler in logging.getLogger().handlers:
                        if hasattr(handler, 'flush'):
                            handler.flush()
                    
                    # 扫描信号
                    signals = strategy.server_scan_sell_surge_signals()
                    
                    if signals:
                        logging.info(f"✅ 发现 {len(signals)} 个信号")
                        # 显示前5个信号
                        for i, signal in enumerate(signals[:5]):
                            logging.info(f"   {signal['symbol']}: {signal['surge_ratio']:.2f}倍 @ {signal['price']:.6f}")
                        
                        # 🔧 强制刷新日志
                        for handler in logging.getLogger().handlers:
                            if hasattr(handler, 'flush'):
                                handler.flush()
                        
                        # 尝试建仓（按优先级从高到低）
                        opened = False
                        for signal in signals:
                            if not is_running:
                                break
                            if strategy.server_open_position(signal):
                                logging.info(f"🚀 开仓成功: {signal['symbol']}")
                                opened = True
                                break  # 只开一个仓位
                        
                        if not opened:
                            logging.warning(f"⚠️ 所有信号均无法建仓（已达到限制或已持有）")
                    else:
                        logging.info("⚠️ 未发现信号")
                    
                    # 🔧 强制刷新日志
                    for handler in logging.getLogger().handlers:
                        if hasattr(handler, 'flush'):
                            handler.flush()
                    
                    # 扫描成功，重置失败计数
                    consecutive_failures = 0
                    
                except Exception as scan_error:
                    consecutive_failures += 1
                    error_msg = str(scan_error)
                    
                    # 判断是否为网络问题
                    is_network_error = any(keyword in error_msg.lower() for keyword in [
                        'network', 'connection', 'timeout', 'proxy', 'ssl', 
                        'max retries', 'unreachable', 'timed out'
                    ])
                    
                    if is_network_error:
                        if consecutive_failures == 1:
                            logging.warning(f"🌐 网络异常 (第{consecutive_failures}次): {error_msg[:100]}")
                        elif consecutive_failures == 3:
                            logging.error(f"🚨 网络连续失败{consecutive_failures}次！")
                            send_email_alert(
                                "网络连续失败警告",
                                f"信号扫描网络连续失败{consecutive_failures}次\n\n错误信息：{error_msg}"
                            )
                        elif consecutive_failures >= 5:
                            logging.critical(f"🚨🚨🚨 网络连续失败{consecutive_failures}次！系统可能无法正常交易！")
                            send_email_alert(
                                "【紧急】网络严重异常",
                                f"信号扫描网络连续失败{consecutive_failures}次！\n\n系统可能无法正常交易，请立即检查！\n\n错误信息：{error_msg}"
                            )
                    else:
                        logging.error(f"❌ 扫描错误 (第{consecutive_failures}次): {error_msg[:100]}")
                        if consecutive_failures >= 3:
                            send_email_alert(
                                "信号扫描异常",
                                f"信号扫描连续失败{consecutive_failures}次\n\n错误信息：{error_msg}"
                            )
                
                # 标记本小时已扫描
                last_scan_hour = current_hour
                
                # 扫描完成后等待到下一分钟
                time.sleep(60)
            else:
                # 不在扫描时间窗口，等待30秒后再检查
                time.sleep(30)
        
        except Exception as e:
            logging.error(f"❌ 扫描循环异常: {e}")
            time.sleep(60)


def monitor_loop():
    """持仓监控循环（每30秒检查一次）"""
    global is_running
    
    logging.info("👁️ 持仓监控线程已启动")
    consecutive_failures = 0  # 连续失败计数
    check_count = 0  # 检查计数器
    
    while True:
        try:
            if not is_running:
                time.sleep(10)
                continue
            
            check_count += 1
            
            # 监控持仓
            strategy.server_monitor_positions()
            
            # 每10次检查（5分钟）输出一次状态
            if check_count % 10 == 0:
                logging.info(f"👁️ [监控] 已检查{check_count}次，持仓{len(strategy.positions)}个")
                # 🔧 强制刷新日志
                for handler in logging.getLogger().handlers:
                    if hasattr(handler, 'flush'):
                        handler.flush()
            
            # 监控成功，重置失败计数
            consecutive_failures = 0
            
            # 每30秒检查一次（与ae.py保持一致）
            time.sleep(30)
        
        except Exception as e:
            consecutive_failures += 1
            error_msg = str(e)
            
            # 判断是否为网络问题
            is_network_error = any(keyword in error_msg.lower() for keyword in [
                'network', 'connection', 'timeout', 'proxy', 'ssl', 
                'max retries', 'unreachable', 'timed out'
            ])
            
            if is_network_error:
                if consecutive_failures == 1:
                    logging.warning(f"🌐 持仓监控网络异常 (第{consecutive_failures}次)")
                elif consecutive_failures >= 5:
                    logging.error(f"🚨 持仓监控网络连续失败{consecutive_failures}次！")
                    send_email_alert(
                        "持仓监控网络异常",
                        f"持仓监控网络连续失败{consecutive_failures}次\n\n持仓显示可能延迟！\n\n错误信息：{error_msg}"
                    )
            else:
                logging.error(f"❌ 监控循环错误 (第{consecutive_failures}次): {error_msg[:100]}")
            
            time.sleep(30)


# ==================== 信号处理 ====================
def signal_handler(sig, frame):
    """处理Ctrl+C信号"""
    global is_running
    
    logging.info("\n⏹️ 收到停止信号，正在退出...")
    is_running = False
    
    # 给线程1秒时间退出
    time.sleep(1)
    
    logging.info("👋 AE Server 已停止")
    sys.exit(0)


# ==================== 主程序 ====================
def main():
    """主函数"""
    global strategy, is_running
    
    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logging.info("="*60)
        logging.info("🚀 AE Server v2.0 启动中...")
        logging.info("="*60)
        
        # 加载配置
        config = load_config()
        logging.info("✅ 配置文件加载成功")
        
        # 初始化策略引擎
        strategy = AutoExchangeStrategy(config)
        global start_time
        start_time = datetime.now(timezone.utc)
        logging.info("✅ 策略引擎初始化完成")
        
        # 更新账户余额
        strategy.account_balance = strategy.server_get_account_balance()
        
        # 启动Flask服务（后台线程）
        flask_thread = threading.Thread(
            target=lambda: app.run(host='0.0.0.0', port=5002, debug=False, use_reloader=False),
            daemon=True
        )
        flask_thread.start()
        
        logging.info("✅ Flask Web服务已启动: http://localhost:5002")
        
        # 🔧 关键修复：启动扫描和监控线程
        logging.info("🚀 启动后台任务线程...")
        
        # 启动扫描线程
        scan_thread = threading.Thread(target=scan_loop, daemon=True)
        scan_thread.start()
        logging.info("✅ 信号扫描线程已启动")
        
        # 启动监控线程
        monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        monitor_thread.start()
        logging.info("✅ 持仓监控线程已启动")

        # 启动每日报告线程
        report_thread = threading.Thread(target=daily_report_loop, daemon=True)
        report_thread.start()
        logging.info("✅ 每日报告线程已启动")
        
        logging.info("="*60)
        logging.info("📋 使用说明:")
        logging.info("  - 浏览器打开: http://localhost:5002")
        logging.info("  - API服务器(旧): http://localhost:5001")
        logging.info("  - 停止程序: Ctrl+C")
        logging.info("="*60)
        
        # 主线程保持运行
        while True:
            time.sleep(60)
            # 每分钟输出一次状态
            if is_running:
                logging.info(f"💓 系统运行中... 持仓: {len(strategy.positions)}, 余额: ${strategy.account_balance:.2f}")
                # 🔧 强制刷新日志
                for handler in logging.getLogger().handlers:
                    if hasattr(handler, 'flush'):
                        handler.flush()
    
    except FileNotFoundError:
        logging.error("❌ 配置文件不存在")
        sys.exit(1)
    
    except Exception as e:
        logging.error(f"❌ 程序启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def daily_report_loop():
    """每日报告循环（每天早上8点发送报告）"""
    global is_running

    logging.info("📧 每日报告线程已启动")
    last_report_date = None

    while True:
        try:
            if not is_running:
                time.sleep(60)
                continue

            # 获取当前UTC时间
            now = datetime.now(timezone.utc)
            current_date = now.date()

            # 检查是否是新的一天且时间在早上8点之后
            # 北京时间8点 = UTC时间0点
            if current_date != last_report_date and now.hour >= 0:
                logging.info("📧 开始生成每日交易报告...")

                # 发送每日报告
                send_daily_report()

                # 标记已发送
                last_report_date = current_date

                logging.info(f"📧 每日报告已发送 ({current_date})")

            # 每小时检查一次
            time.sleep(3600)  # 1小时

        except Exception as e:
            logging.error(f"❌ 每日报告循环异常: {e}")
            time.sleep(300)  # 5分钟后重试

if __name__ == "__main__":
    main()
