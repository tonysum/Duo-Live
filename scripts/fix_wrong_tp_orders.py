#!/usr/bin/env python3
"""
修复错误的止盈挂单

这个脚本会：
1. 检查所有持仓的止盈单
2. 验证止盈单的方向是否正确
3. 取消错误的止盈单
4. 重新创建正确的止盈单
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from live.binance_client import BinanceFuturesClient
from live.live_config import LiveTradingConfig
from live.store import PositionStore
from decimal import Decimal, ROUND_DOWN
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Main function to fix wrong TP orders."""
    
    # Initialize client
    config = LiveTradingConfig()
    client = BinanceFuturesClient(
        api_key=config.api_key,
        api_secret=config.api_secret,
    )
    
    store = PositionStore(config.db_path)
    
    try:
        logger.info("🔍 开始检查所有持仓的止盈单...")
        
        # Get all positions from exchange
        all_positions = await client.get_position_risk()
        active_positions = [p for p in all_positions if float(p.position_amt) != 0]
        
        if not active_positions:
            logger.info("✅ 没有活跃持仓")
            return
        
        logger.info(f"📊 发现 {len(active_positions)} 个活跃持仓")
        
        # Get all algo orders
        fixed_count = 0
        
        for pos in active_positions:
            symbol = pos.symbol
            position_amt = float(pos.position_amt)
            is_long = position_amt > 0
            actual_side = "LONG" if is_long else "SHORT"
            
            logger.info(f"\n{'='*60}")
            logger.info(f"📍 检查持仓: {symbol}")
            logger.info(f"   方向: {actual_side}")
            logger.info(f"   数量: {abs(position_amt)}")
            
            # Get algo orders for this symbol
            try:
                algo_orders = await client.get_open_algo_orders(symbol)
                tp_orders = [o for o in algo_orders if o.order_type == "TAKE_PROFIT_MARKET"]
                
                if not tp_orders:
                    logger.warning(f"⚠️  {symbol} 没有止盈单！")
                    continue
                
                logger.info(f"   找到 {len(tp_orders)} 个止盈单")
                
                # Check each TP order
                for tp_order in tp_orders:
                    order_side = tp_order.side
                    correct_side = "SELL" if is_long else "BUY"
                    
                    logger.info(f"   止盈单 {tp_order.algo_id}:")
                    logger.info(f"     - 当前方向: {order_side}")
                    logger.info(f"     - 应该方向: {correct_side}")
                    logger.info(f"     - 触发价格: {tp_order.trigger_price}")
                    logger.info(f"     - 数量: {tp_order.quantity}")
                    
                    if order_side != correct_side:
                        logger.error(f"❌ 止盈单方向错误！")
                        
                        # Ask for confirmation
                        response = input(f"\n是否取消并重新创建正确的止盈单？(y/n): ")
                        if response.lower() != 'y':
                            logger.info("⏭️  跳过")
                            continue
                        
                        # Cancel wrong order
                        try:
                            await client.cancel_algo_order(symbol, algo_id=tp_order.algo_id)
                            logger.info(f"✅ 已取消错误的止盈单: {tp_order.algo_id}")
                        except Exception as e:
                            logger.error(f"❌ 取消失败: {e}")
                            continue
                        
                        # Get position mode
                        is_hedge = await client.get_position_mode()
                        position_side = actual_side if is_hedge else "BOTH"
                        
                        # Get entry price and calculate TP price
                        entry_price = float(pos.entry_price)
                        
                        # Try to get TP percentage from database
                        db_state = store.get_position_state(symbol)
                        if db_state and db_state.get("current_tp_pct"):
                            tp_pct = db_state["current_tp_pct"]
                        else:
                            # Default to 33% (strong TP)
                            tp_pct = 33.0
                        
                        logger.info(f"   使用止盈百分比: {tp_pct}%")
                        
                        # Calculate TP price
                        tp_mult = (1 + tp_pct / 100) if is_long else (1 - tp_pct / 100)
                        tp_price = Decimal(str(entry_price)) * Decimal(str(tp_mult))
                        
                        # Round trigger price
                        exchange_info = await client.get_exchange_info()
                        tick_size = None
                        for s in exchange_info.get("symbols", []):
                            if s["symbol"] == symbol:
                                for f in s.get("filters", []):
                                    if f["filterType"] == "PRICE_FILTER":
                                        tick_size = Decimal(f["tickSize"])
                                        break
                                break
                        
                        if tick_size:
                            tp_price = (tp_price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
                            tp_price = tp_price.quantize(tick_size, rounding=ROUND_DOWN)
                        
                        # Round quantity
                        quantity = abs(position_amt)
                        step_size = None
                        for s in exchange_info.get("symbols", []):
                            if s["symbol"] == symbol:
                                for f in s.get("filters", []):
                                    if f["filterType"] == "LOT_SIZE":
                                        step_size = Decimal(f["stepSize"])
                                        break
                                break
                        
                        if step_size:
                            qty_decimal = Decimal(str(quantity))
                            quantity = (qty_decimal / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size
                            quantity = float(quantity)
                        
                        # Create new TP order
                        try:
                            new_tp = await client.place_algo_order(
                                symbol=symbol,
                                side=correct_side,
                                positionSide=position_side,
                                type="TAKE_PROFIT_MARKET",
                                triggerPrice=str(tp_price),
                                quantity=str(quantity),
                                reduceOnly="true",
                                priceProtect="true",
                                workingType="CONTRACT_PRICE",
                            )
                            logger.info(f"✅ 已创建新的止盈单:")
                            logger.info(f"   - algoId: {new_tp.algo_id}")
                            logger.info(f"   - 方向: {correct_side}")
                            logger.info(f"   - 触发价: {tp_price}")
                            logger.info(f"   - 数量: {quantity}")
                            
                            fixed_count += 1
                        except Exception as e:
                            logger.error(f"❌ 创建新止盈单失败: {e}")
                    else:
                        logger.info(f"✅ 止盈单方向正确")
                        
            except Exception as e:
                logger.error(f"❌ 处理 {symbol} 时出错: {e}")
                continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🎉 完成！共修复 {fixed_count} 个错误的止盈单")
        
    except Exception as e:
        logger.error(f"❌ 脚本执行失败: {e}", exc_info=True)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
