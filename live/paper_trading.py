#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DSX Paper Trading Module — migrated from dsxtx.py

Provides paper (simulated) trading based on the DSX amplitude strategy.
Core components:
  - DSXDataCollector: Binance futures 5m kline data via REST + WebSocket
  - DSXStateManager: Four-stage state machine (searching → cooling → consolidating → monitoring)
  - PaperTradingEngine: Virtual capital, pending orders, TP/SL/timeout
  - DSXPaperTrading: Orchestrator (start/stop, signal loop, price check loop)
"""

import os
import time
import json
import threading
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

import pandas as pd
import numpy as np

try:
    import ccxt
    from websocket import create_connection, WebSocketConnectionClosedException
except ImportError as e:
    raise ImportError(
        f"Missing dependency for paper trading: {e}\n"
        "Install with: pip install ccxt websocket-client"
    ) from e


# ═══════════════════════════════════════════════════════════════════
#  AmplitudePredictor — slim version (no database, WebSocket-only)
#  Extracted from dsx.py, stripped of PostgreSQL / backtest logic.
# ═══════════════════════════════════════════════════════════════════

class AmplitudePredictor:
    """Signal-detection core: swing detection + consolidation check.

    Only the config and two detection methods are kept; all database
    and back-test logic is stripped because paper trading feeds data
    via DSXDataCollector (WebSocket + REST).
    """

    def __init__(self, symbol='SOLUSDT', initial_capital=10000, position_ratio=0.2):
        self.symbol = symbol.upper()
        self.base_currency = self.symbol.replace('USDT', '')

        # ── Time controls ───────────────────────────────────
        self.cooling_period_hours = 10
        self.consolidation_hours = 4
        self.consolidation_threshold = 2.4  # max amplitude % for consolidation
        self.cycle_days = 72
        self.max_holding_hours = 26

        # ── Amplitude levels ────────────────────────────────
        self.amplitude_levels = {
            'micro': {
                'name': '微幅', 'amplitude': 8.0,
                'daily_amp_threshold': None, 'hourly_amp_threshold': None,
                'confirm': 4.5, 'target': 3, 'leverage': 15,
                'stop_loss_pct': 4.0, 'invest_ratio': 0.2,
            },
            'small': {
                'name': '小幅', 'amplitude': 12.0,
                'daily_amp_threshold': None, 'hourly_amp_threshold': None,
                'hourly_count_threshold': {'count': 3, 'amp': 3.5},
                'confirm': 3.0, 'target': 3.5, 'leverage': 20,
                'stop_loss_pct': 3.0, 'invest_ratio': 0.2,
            },
            'medium': {
                'name': '中幅', 'amplitude': 25.0,
                'daily_amp_threshold': None, 'hourly_amp_threshold': 7.0,
                'confirm': 2.5, 'target': 3, 'leverage': 20,
                'stop_loss_pct': 3.0, 'invest_ratio': 0.2,
            },
            'large': {
                'name': '大幅', 'amplitude': 35.0,
                'daily_amp_threshold': 20.0, 'hourly_amp_threshold': None,
                'confirm': 2.0, 'target': 3.0, 'leverage': 20,
                'stop_loss_pct': 3.0, 'invest_ratio': 0.2,
            },
            'huge': {
                'name': '巨幅', 'amplitude': 45.0,
                'confirm': 2.0, 'target': 3.0, 'leverage': 15,
                'stop_loss_pct': 4.0, 'invest_ratio': 0.2,
            },
        }

        # ── Data (set externally via sync_predictor_data) ───
        self.df = None

    # ── Signal detection ────────────────────────────────────

    def detect_swing_completion(self, idx):
        """Detect whether a large swing has completed in the last 72h.

        Returns (is_completed, level_key, direction, swing_pct).
        """
        min_window_points = 72 * 12  # 72 hours of 5m bars
        if idx < min_window_points:
            return False, None, None, 0

        window = self.df.iloc[idx - min_window_points:idx + 1]
        current_price = self.df.iloc[idx]['close']

        max_price = window['high'].max()
        min_price = window['low'].min()
        up_swing = (max_price - min_price) / min_price * 100
        down_from_high = (max_price - current_price) / max_price * 100
        up_from_low = (current_price - min_price) / min_price * 100

        # Check each level from highest to lowest amplitude
        for level_key, level_data in sorted(
            self.amplitude_levels.items(),
            key=lambda x: x[1]['amplitude'],
            reverse=True,
        ):
            level_matched = False

            # Condition 1: window amplitude reaches threshold
            if up_swing >= level_data['amplitude']:
                level_matched = True

            # Condition 2: daily_amp_threshold — skipped in WS-only mode
            # (no daily kline data available without database)

            # Condition 3: hourly amplitude threshold
            if level_data.get('hourly_amp_threshold'):
                if window['amplitude'].max() >= level_data['hourly_amp_threshold']:
                    level_matched = True

            # Condition 4: hourly count threshold
            if level_data.get('hourly_count_threshold'):
                ct = level_data['hourly_count_threshold']
                high_amp_count = (window['amplitude'] >= ct['amp']).sum()
                if high_amp_count >= ct['count']:
                    level_matched = True

        # Last matched level (smallest matching amplitude)
        if level_matched:
            if down_from_high <= up_from_low:
                return True, level_key, 'up', up_swing
            else:
                return True, level_key, 'down', up_swing

        return False, None, None, 0

    def check_consolidation(self, idx):
        """Check whether the last N hours show low volatility (consolidation).

        Returns (is_consolidating, base_price, consolidation_time, None).
        """
        min_window_points = self.consolidation_hours * 12  # 4h * 12 = 48 bars
        if idx < min_window_points:
            return False, None, None, None

        window_data = self.df.iloc[idx - min_window_points:idx + 1]
        window_high = window_data['high'].max()
        window_low = window_data['low'].min()

        if window_low <= 0:
            return False, None, None, None

        window_amplitude = (window_high - window_low) / window_low * 100
        if window_amplitude >= self.consolidation_threshold:
            return False, None, None, None

        current_time = self.df.iloc[idx]['trade_date']
        base_price = self.df.iloc[idx]['close']

        if base_price <= 0 or base_price > 100000:
            return False, None, None, None

        return True, base_price, current_time, None


logger = logging.getLogger(__name__)

# Resolve data directory (project root / data)
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(_DATA_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
#  DSXDataCollector — in-memory + API
# ═══════════════════════════════════════════════════════════════════

class DSXDataCollector:
    """Collect real-time 5m klines from Binance futures, stored in memory."""

    MAX_MEMORY_ROWS = 2000  # ~7 days of 5m candles

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.is_running = False

        # In-memory data: symbol -> pd.DataFrame
        self.dataframes: Dict[str, pd.DataFrame] = {}
        self._df_locks: Dict[str, threading.Lock] = {}
        for s in symbols:
            self.dataframes[s] = pd.DataFrame(columns=[
                'trade_date', 'open', 'high', 'low', 'close', 'volume', 'amplitude', 'pct_chg'
            ])
            self._df_locks[s] = threading.Lock()

        # WebSocket status tracking
        self.ws_status: Dict[str, str] = {s: 'idle' for s in symbols}
        self.last_data_time: Dict[str, Optional[datetime]] = {s: None for s in symbols}
        self.reconnect_count: Dict[str, int] = {s: 0 for s in symbols}
        self.ws_error: Dict[str, str] = {s: '' for s in symbols}

        # ccxt exchange instance (futures market)
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
        })

    # ── Helper ────────────────────────────────────────────

    def _to_ccxt_symbol(self, symbol: str) -> str:
        """Convert SOLUSDT to ccxt futures format: SOL/USDT:USDT."""
        base = symbol[:-4] if symbol.endswith('USDT') else symbol
        return f'{base}/USDT:USDT'

    # ── Fetch history on startup ──────────────────────────

    def fetch_history(self):
        """Fetch last 1000 5m klines per symbol from Binance futures REST API."""
        logger.info("📥 Fetching historical data from Binance futures API...")

        try:
            self.exchange.load_markets()
        except Exception as e:
            logger.error("Failed to load market info: %s", e)

        for i, symbol in enumerate(self.symbols):
            try:
                ccxt_sym = self._to_ccxt_symbol(symbol)
                if ccxt_sym not in self.exchange.markets:
                    logger.error("  ❌ %s futures market not found", symbol)
                    self.ws_error[symbol] = 'Not found in futures market'
                    continue

                ohlcv = self.exchange.fetch_ohlcv(ccxt_sym, '5m', limit=1000)

                rows = []
                for ts, o, h, l, c, v in ohlcv:
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    amp = (h - l) / l * 100 if l > 0 else 0
                    pct = (c - o) / o * 100 if o > 0 else 0
                    rows.append({
                        'trade_date': dt,
                        'open': float(o), 'high': float(h),
                        'low': float(l), 'close': float(c),
                        'volume': float(v),
                        'amplitude': amp, 'pct_chg': pct,
                    })

                df = pd.DataFrame(rows)
                with self._df_locks[symbol]:
                    self.dataframes[symbol] = df

                logger.info("  ✅ %s: %d rows", symbol, len(df))

            except Exception as e:
                logger.error("  ❌ %s fetch failed: %s", symbol, e)
                self.ws_error[symbol] = str(e)[:100]

            # Simple rate limiting
            if (i + 1) % 10 == 0:
                time.sleep(1)

    # ── WebSocket real-time data ──────────────────────────

    def start_collection(self):
        """Fetch history then start WebSocket streams."""
        self.is_running = True
        self.fetch_history()
        logger.info("🚀 Starting futures WebSocket data collection...")
        batch_size = 5
        for i, symbol in enumerate(self.symbols):
            t = threading.Thread(target=self._ws_loop, args=(symbol,), daemon=True)
            t.start()
            if (i + 1) % batch_size == 0:
                time.sleep(1.0)
            else:
                time.sleep(0.2)

    def stop_collection(self):
        self.is_running = False
        logger.info("🛑 Data collection stopped")

    def _ws_loop(self, symbol: str):
        stream = f"{symbol.lower()}@kline_5m"
        ws_url = f"wss://fstream.binance.com/ws/{stream}"
        while self.is_running:
            try:
                self.ws_status[symbol] = 'connecting'
                self.ws_error[symbol] = ''
                ws = create_connection(ws_url)
                self.ws_status[symbol] = 'connected'
                self.ws_error[symbol] = ''
                logger.info("✅ WS connected: %s", symbol)

                while self.is_running:
                    try:
                        msg = ws.recv()
                        data = json.loads(msg)
                        if 'k' in data:
                            k = data['k']
                            self._ingest_candle(symbol, k)
                            self.last_data_time[symbol] = datetime.now()
                    except WebSocketConnectionClosedException:
                        self.ws_status[symbol] = 'disconnected'
                        self.ws_error[symbol] = 'Connection closed'
                        break
                    except Exception as e:
                        self.ws_status[symbol] = 'disconnected'
                        self.ws_error[symbol] = str(e)[:100]
                        break
                ws.close()
            except Exception as e:
                self.ws_status[symbol] = 'reconnecting'
                self.ws_error[symbol] = str(e)[:100]
                self.reconnect_count[symbol] += 1
                time.sleep(5)

    def _ingest_candle(self, symbol: str, k: dict):
        """Append/update a WebSocket kline to the in-memory DataFrame."""
        ts = k['t']
        o, h, l, c, v = float(k['o']), float(k['h']), float(k['l']), float(k['c']), float(k['v'])
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        amp = (h - l) / l * 100 if l > 0 else 0
        pct = (c - o) / o * 100 if o > 0 else 0

        with self._df_locks[symbol]:
            df = self.dataframes[symbol]
            # Same timestamp → update (incomplete candle)
            if len(df) > 0 and df.iloc[-1]['trade_date'] == dt:
                df.iloc[-1, df.columns.get_loc('high')] = max(df.iloc[-1]['high'], h)
                df.iloc[-1, df.columns.get_loc('low')] = min(df.iloc[-1]['low'], l)
                df.iloc[-1, df.columns.get_loc('close')] = c
                df.iloc[-1, df.columns.get_loc('volume')] = v
                df.iloc[-1, df.columns.get_loc('amplitude')] = amp
                df.iloc[-1, df.columns.get_loc('pct_chg')] = pct
            else:
                # New candle
                new = pd.DataFrame([{
                    'trade_date': dt, 'open': o, 'high': h, 'low': l,
                    'close': c, 'volume': v, 'amplitude': amp, 'pct_chg': pct,
                }])
                df = pd.concat([df, new], ignore_index=True)
                # Limit memory
                if len(df) > self.MAX_MEMORY_ROWS:
                    df = df.iloc[-self.MAX_MEMORY_ROWS:]
                self.dataframes[symbol] = df

    # ── Query methods ─────────────────────────────────────

    def get_df(self, symbol: str) -> pd.DataFrame:
        """Get a copy of the symbol's full DataFrame."""
        with self._df_locks[symbol]:
            return self.dataframes[symbol].copy()

    def get_latest(self, symbol: str) -> Optional[Dict]:
        """Get the latest candle as a dict."""
        with self._df_locks[symbol]:
            df = self.dataframes[symbol]
            if len(df) == 0:
                return None
            row = df.iloc[-1]
            return {
                'trade_date': row['trade_date'],
                'open': row['open'], 'high': row['high'],
                'low': row['low'], 'close': row['close'],
                'volume': row['volume'],
            }

    def get_ws_summary(self) -> Dict:
        """WebSocket connection status summary."""
        now = datetime.now()
        summary = {}
        for s in self.symbols:
            last = self.last_data_time[s]
            stale = (now - last).total_seconds() if last else None
            summary[s] = {
                'status': self.ws_status[s],
                'last_data': last.isoformat() if last else None,
                'stale_seconds': round(stale, 1) if stale is not None else None,
                'reconnects': self.reconnect_count[s],
                'error': self.ws_error[s] or None,
                'rows': len(self.dataframes.get(s, [])),
            }
        return summary


# ═══════════════════════════════════════════════════════════════════
#  DSXStateManager — four-stage state machine
# ═══════════════════════════════════════════════════════════════════

class DSXStateManager:
    """Manage per-symbol state machines driven by in-memory DataFrames."""

    def __init__(self, symbols: List[str], data_collector: DSXDataCollector):
        self.symbols = symbols
        self.data_collector = data_collector
        self.predictors: Dict[str, AmplitudePredictor] = {}
        self.state_data: Dict[str, Dict] = {}

        for symbol in symbols:
            self.predictors[symbol] = AmplitudePredictor(symbol=symbol)
            self.state_data[symbol] = {
                'state': 'searching',
                'last_swing_idx': None,
                'last_swing_level': None,
                'cooling_start_time': None,
                'base_price': None,
                'base_idx': None,
                'base_time': None,
                'pending_cooling': False,
                'current_market_level': 'micro',
            }

    def sync_predictor_data(self, symbol: str):
        """Sync DataCollector memory data into predictor.df."""
        df = self.data_collector.get_df(symbol)
        if len(df) > 0:
            self.predictors[symbol].df = df

    def smart_init_all(self):
        """Smart state initialization for all symbols on startup."""
        logger.info("🧠 Smart-initializing state machines...")
        for symbol in self.symbols:
            self.sync_predictor_data(symbol)
            self._smart_init(symbol)

    def _smart_init(self, symbol: str):
        """Determine initial state from historical data."""
        predictor = self.predictors[symbol]
        if predictor.df is None or len(predictor.df) < 48:
            self.state_data[symbol]['state'] = 'searching'
            return

        idx = len(predictor.df) - 1
        try:
            is_swing, level, direction, pct = predictor.detect_swing_completion(idx)
            if is_swing:
                is_cons, base, cons_time, _ = predictor.check_consolidation(idx)
                if is_cons:
                    self.state_data[symbol].update({
                        'state': 'monitoring',
                        'base_price': base,
                        'base_idx': idx,
                        'base_time': cons_time,
                        'last_swing_level': level,
                        'current_market_level': level or 'micro',
                    })
                    logger.info("  🎯 %s: monitoring (base $%.4f)", symbol, base)
                else:
                    self.state_data[symbol].update({
                        'state': 'consolidating',
                        'last_swing_level': level,
                        'current_market_level': level or 'micro',
                    })
                    logger.info("  ⏳ %s: consolidating", symbol)
            else:
                logger.info("  🔍 %s: searching", symbol)
        except Exception as e:
            logger.error("  ❌ %s init failed: %s", symbol, e)

    def process_tick(self, symbol: str) -> List[Dict]:
        """Process latest tick for a symbol, run state machine."""
        self.sync_predictor_data(symbol)
        predictor = self.predictors[symbol]
        state = self.state_data[symbol]

        if predictor.df is None or len(predictor.df) < 12:
            return []

        idx = len(predictor.df) - 1
        return self._run_state_machine(symbol, predictor, state, idx)

    def _run_state_machine(self, symbol, predictor, state, idx) -> List[Dict]:
        """Four-stage state machine core logic."""
        signals = []
        try:
            current_time = predictor.df.iloc[idx]['trade_date']
            current_price = predictor.df.iloc[idx]['close']

            # Update market volatility level
            if idx >= 12:
                window = predictor.df.iloc[max(0, idx - 864):idx + 1]
                mh, ml = window['high'].max(), window['low'].min()
                mamp = (mh - ml) / ml * 100 if ml > 0 else 0
                for lk, ld in sorted(predictor.amplitude_levels.items(),
                                      key=lambda x: x[1]['amplitude'], reverse=True):
                    if mamp >= ld['amplitude']:
                        state['current_market_level'] = lk
                        break
                else:
                    state['current_market_level'] = 'micro'

            # ── State 1: Searching ──
            if state['state'] == 'searching':
                is_completed, level, direction, pct = predictor.detect_swing_completion(idx)
                if is_completed:
                    logger.info("🔔 %s swing detected (%s): %.2f%%",
                                symbol, predictor.amplitude_levels[level]['name'], pct)
                    state['last_swing_level'] = level
                    state['cooling_start_time'] = current_time
                    state['state'] = 'cooling'

            # ── State 2: Cooling ──
            elif state['state'] == 'cooling':
                if state['cooling_start_time']:
                    elapsed = (current_time - pd.to_datetime(state['cooling_start_time'])).total_seconds() / 3600
                    if elapsed >= predictor.cooling_period_hours:
                        logger.info("✅ %s cooling period ended", symbol)
                        state['state'] = 'consolidating'

            # ── State 3: Consolidating ──
            elif state['state'] == 'consolidating':
                is_cons, base_price, cons_time, _ = predictor.check_consolidation(idx)
                if is_cons:
                    logger.info("📍 %s consolidation complete, base: $%.4f", symbol, base_price)
                    state.update({
                        'base_price': base_price,
                        'base_idx': idx,
                        'base_time': current_time,
                        'state': 'monitoring',
                    })
                    # Generate long/short price alerts
                    lev = predictor.amplitude_levels.get(state['current_market_level'], {})
                    confirm = lev.get('confirm', 2.0)
                    target = lev.get('target', 3.0)
                    sl_pct = lev.get('stop_loss_pct', 4.0)
                    leverage = lev.get('leverage', 1)
                    level_name = lev.get('name', state['current_market_level'])

                    logger.info(
                        "📊 %s 振幅级别: %s | confirm=%.1f%% target=%.1f%% sl=%.1f%% leverage=%dx",
                        symbol, level_name, confirm, target, sl_pct, leverage,
                    )

                    for direction in ['long', 'short']:
                        if direction == 'long':
                            entry = base_price * (1 + confirm / 100)
                            tp = entry * (1 + target / 100)
                            sl = entry * (1 - sl_pct / 100)
                        else:
                            entry = base_price * (1 - confirm / 100)
                            tp = entry * (1 - target / 100)
                            sl = entry * (1 + sl_pct / 100)

                        signals.append({
                            'type': 'price_alert', 'symbol': symbol,
                            'direction': direction,
                            'entry_price': entry, 'tp_price': tp, 'sl_price': sl,
                            'force_close_hours': predictor.max_holding_hours,
                            'leverage': leverage,
                        })

            # ── State 4: Monitoring ──
            elif state['state'] == 'monitoring':
                if state['base_price']:
                    chg = (current_price - state['base_price']) / state['base_price'] * 100
                    lev = predictor.amplitude_levels.get(state['last_swing_level'] or 'small', {})
                    confirm = lev.get('confirm', 2.0)
                    if abs(chg) >= confirm:
                        logger.info("🎯 %s breakout confirmed: %+.2f%%", symbol, chg)
                        state.update({
                            'state': 'searching',
                            'base_price': None, 'base_idx': None, 'base_time': None,
                        })

        except Exception as e:
            logger.error("State machine error %s: %s", symbol, e)

        return signals


# ═══════════════════════════════════════════════════════════════════
#  PaperTradingEngine — simulated trading engine
# ═══════════════════════════════════════════════════════════════════

class PaperTradingEngine:
    """Simulated trading: virtual capital + pending orders + TP/SL/timeout."""

    INITIAL_CAPITAL = 10000.0
    POSITION_SIZE_PCT = 0.10
    MAX_POSITIONS = 5
    STATE_FILE = os.path.join(_DATA_DIR, 'dsxtx_state.json')

    def __init__(self):
        self.capital = self.INITIAL_CAPITAL
        self.pending_orders: List[Dict] = []
        self.open_positions: List[Dict] = []
        self.trade_history: List[Dict] = []
        self._lock = threading.Lock()
        self._dirty = False

    def add_order(self, signal: Dict):
        """Convert signal to a pending order."""
        with self._lock:
            # Skip duplicate symbol+direction
            for o in self.pending_orders:
                if o['symbol'] == signal['symbol'] and o['direction'] == signal['direction']:
                    return

            order = {
                'symbol': signal['symbol'],
                'direction': signal['direction'],
                'entry_price': signal['entry_price'],
                'tp_price': signal['tp_price'],
                'sl_price': signal['sl_price'],
                'force_close_hours': signal['force_close_hours'],
                'leverage': signal.get('leverage', 1),
                'created_at': datetime.now(timezone.utc),
                'size_usdt': self.capital * self.POSITION_SIZE_PCT,
            }
            self.pending_orders.append(order)
            logger.info(
                "📋 Pending order: %s %s entry:$%.4f size:$%.0f",
                signal['symbol'], signal['direction'].upper(),
                signal['entry_price'], order['size_usdt'],
            )
            self._dirty = True

    def check_prices(self, symbol: str, high: float, low: float, close: float, current_time: datetime):
        """Check pending orders and open positions against latest candle."""
        with self._lock:
            self._clean_expired_orders(current_time)
            self._check_pending_orders(symbol, high, low, close, current_time)
            self._check_open_positions(symbol, high, low, close, current_time)

    def _clean_expired_orders(self, now):
        """Remove orders older than 48h."""
        before = len(self.pending_orders)
        self.pending_orders = [
            o for o in self.pending_orders
            if (now - o['created_at']).total_seconds() / 3600 <= 48
        ]
        removed = before - len(self.pending_orders)
        if removed > 0:
            logger.info("🗑️ Cleaned %d expired orders", removed)
            self._dirty = True

    def _check_pending_orders(self, symbol, high, low, close, now):
        """Check if any pending orders are triggered."""
        remaining = []
        for order in self.pending_orders:
            if order['symbol'] != symbol:
                remaining.append(order)
                continue

            triggered = False
            if order['direction'] == 'long' and low <= order['entry_price'] <= high:
                triggered = True
            elif order['direction'] == 'short' and low <= order['entry_price'] <= high:
                triggered = True

            if triggered and len(self.open_positions) < self.MAX_POSITIONS:
                if self.capital < order['size_usdt']:
                    logger.warning("⚠️ Insufficient capital: need $%.0f, have $%.0f",
                                   order['size_usdt'], self.capital)
                    remaining.append(order)
                    continue

                leverage = order.get('leverage', 1)
                qty = (order['size_usdt'] * leverage) / order['entry_price']
                pos = {
                    'symbol': symbol,
                    'direction': order['direction'],
                    'entry_price': order['entry_price'],
                    'entry_time': now,
                    'tp_price': order['tp_price'],
                    'sl_price': order['sl_price'],
                    'force_close_hours': order['force_close_hours'],
                    'leverage': leverage,
                    'size_usdt': order['size_usdt'],
                    'qty': qty,
                    '_new_this_tick': True,
                }
                self.open_positions.append(pos)
                self.capital -= order['size_usdt']
                self._dirty = True
                logger.info(
                    "✅ Paper open: %s %s @$%.4f qty:%.4f lev:%dx size:$%.0f",
                    symbol, order['direction'].upper(),
                    order['entry_price'], qty, leverage, order['size_usdt'],
                )
            else:
                remaining.append(order)

        self.pending_orders = remaining

    def _check_open_positions(self, symbol, high, low, close, now):
        """Check TP/SL/timeout for open positions."""
        remaining = []
        for pos in self.open_positions:
            if pos['symbol'] != symbol:
                remaining.append(pos)
                continue

            # Skip positions just opened this tick
            if pos.pop('_new_this_tick', False):
                pos['unrealized_pnl'] = self._calc_pnl(pos, close)
                remaining.append(pos)
                continue

            exit_price = None
            exit_reason = None

            if pos['direction'] == 'long':
                if high >= pos['tp_price']:
                    exit_price = pos['tp_price']
                    exit_reason = 'tp'
                elif low <= pos['sl_price']:
                    exit_price = pos['sl_price']
                    exit_reason = 'sl'
            else:  # short
                if low <= pos['tp_price']:
                    exit_price = pos['tp_price']
                    exit_reason = 'tp'
                elif high >= pos['sl_price']:
                    exit_price = pos['sl_price']
                    exit_reason = 'sl'

            # Timeout check
            hold_h = (now - pos['entry_time']).total_seconds() / 3600
            if exit_price is None and hold_h >= pos['force_close_hours']:
                exit_price = close
                exit_reason = 'timeout'

            if exit_price:
                self._close_position(pos, exit_price, exit_reason, now)
                self._dirty = True
            else:
                pos['unrealized_pnl'] = self._calc_pnl(pos, close)
                remaining.append(pos)

        self.open_positions = remaining

    def _close_position(self, pos, exit_price, reason, now):
        """Close position and record trade."""
        pnl = self._calc_pnl(pos, exit_price)
        self.capital += pos['size_usdt'] + pnl

        trade = {
            'symbol': pos['symbol'],
            'direction': pos['direction'],
            'entry_price': pos['entry_price'],
            'exit_price': exit_price,
            'entry_time': pos['entry_time'].isoformat(),
            'exit_time': now.isoformat(),
            'hold_hours': round((now - pos['entry_time']).total_seconds() / 3600, 1),
            'size_usdt': pos['size_usdt'],
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl / pos['size_usdt'] * 100, 2),
            'exit_reason': reason,
        }
        self.trade_history.append(trade)

        emoji = '🟢' if pnl > 0 else '🔴'
        logger.info(
            "%s Paper close: %s %s @$%.4f pnl:$%+.2f (%s)",
            emoji, pos['symbol'], pos['direction'].upper(),
            exit_price, pnl, reason,
        )

    @staticmethod
    def _calc_pnl(pos, current_price):
        if pos['direction'] == 'long':
            return pos['qty'] * (current_price - pos['entry_price'])
        else:
            return pos['qty'] * (pos['entry_price'] - current_price)

    def get_stats(self) -> Dict:
        """Summary statistics."""
        total = len(self.trade_history)
        wins = [t for t in self.trade_history if t['pnl'] > 0]
        losses = [t for t in self.trade_history if t['pnl'] <= 0]
        total_pnl = sum(t['pnl'] for t in self.trade_history)
        return {
            'initial_capital': self.INITIAL_CAPITAL,
            'current_capital': round(self.capital, 2),
            'total_pnl': round(total_pnl, 2),
            'total_pnl_pct': round(total_pnl / self.INITIAL_CAPITAL * 100, 2),
            'total_trades': total,
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / total * 100, 1) if total > 0 else 0,
            'avg_pnl': round(total_pnl / total, 2) if total > 0 else 0,
            'avg_hold_hours': round(sum(t['hold_hours'] for t in self.trade_history) / total, 1) if total > 0 else 0,
            'open_positions': len(self.open_positions),
            'pending_orders': len(self.pending_orders),
            'max_positions': self.MAX_POSITIONS,
        }

    def get_positions_data(self) -> List[Dict]:
        """Current positions with unrealized P&L."""
        result = []
        for p in self.open_positions:
            result.append({
                'symbol': p['symbol'],
                'direction': p['direction'],
                'entry_price': p['entry_price'],
                'tp_price': p['tp_price'],
                'sl_price': p['sl_price'],
                'entry_time': p['entry_time'].isoformat(),
                'size_usdt': round(p['size_usdt'], 2),
                'unrealized_pnl': round(p.get('unrealized_pnl', 0), 2),
                'hold_hours': round((datetime.now(timezone.utc) - p['entry_time']).total_seconds() / 3600, 1),
            })
        return result

    # ── Persistence ────────────────────────────────────────

    def save_to_file(self, signal_history: List[Dict] = None, force: bool = False):
        """Save state to JSON file (only when dirty or forced)."""
        if not force and not self._dirty:
            return
        try:
            positions_data = []
            for p in self.open_positions:
                pd_copy = dict(p)
                pd_copy['entry_time'] = pd_copy['entry_time'].isoformat()
                positions_data.append(pd_copy)

            pending_data = []
            for o in self.pending_orders:
                od_copy = dict(o)
                od_copy['created_at'] = od_copy['created_at'].isoformat()
                pending_data.append(od_copy)

            state = {
                'capital': self.capital,
                'pending_orders': pending_data,
                'open_positions': positions_data,
                'trade_history': self.trade_history,
                'signal_history': signal_history or [],
                'saved_at': datetime.now(timezone.utc).isoformat(),
            }
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            self._dirty = False
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    def load_from_file(self) -> List[Dict]:
        """Restore state from JSON file. Returns signal_history."""
        if not os.path.exists(self.STATE_FILE):
            return []
        try:
            with open(self.STATE_FILE) as f:
                state = json.load(f)

            self.capital = state.get('capital', self.INITIAL_CAPITAL)
            self.trade_history = state.get('trade_history', [])

            for o in state.get('pending_orders', []):
                o['created_at'] = datetime.fromisoformat(o['created_at'])
                self.pending_orders.append(o)

            for p in state.get('open_positions', []):
                p['entry_time'] = datetime.fromisoformat(p['entry_time'])
                self.open_positions.append(p)

            signals = state.get('signal_history', [])
            logger.info(
                "📂 State restored: capital=$%.0f positions:%d pending:%d trades:%d signals:%d",
                self.capital, len(self.open_positions), len(self.pending_orders),
                len(self.trade_history), len(signals),
            )
            return signals

        except Exception as e:
            logger.error("Failed to load state: %s", e)
            return []


# ═══════════════════════════════════════════════════════════════════
#  DSXPaperTrading — orchestrator
# ═══════════════════════════════════════════════════════════════════

class DSXPaperTrading:
    """DSX paper trading system (no Flask, no web routes)."""

    DEFAULT_SYMBOLS = [
        'SOLUSDT', 'DOGEUSDT', 'XRPUSDT',
        'COAIUSDT', 'RIVERUSDT', 'ESPUSDT',
        'LIGHTUSDT', 'POWERUSDT', 'MYXUSDT',
        'BLESSUSDT', 'BEATUSDT', 'COLLECTUSDT',
        'PIEVERSEUSDT', 'HUSDT', 'BASUSDT',
        'RAVEUSDT', '4USDT', 'CLOUSDT',
        'FOLKSUSDT', 'ARCUSDT', 'RVVUSDT',
        'PIPPINUSDT', 'TRUTHUSDT', 'CYSUSDT',
        'EVAAUSDT', 'GIGGLEUSDT', 'PTBUSDT',
        'NAORISUSDT', 'USELESSUSDT', 'JELLYJELLYUSDT',
        'FHEUSDT', 'LABUSDT', 'QUSDT',
    ]

    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols or self.DEFAULT_SYMBOLS
        self.data_collector = DSXDataCollector(self.symbols)
        self.state_manager = DSXStateManager(self.symbols, self.data_collector)
        self.paper_engine = PaperTradingEngine()
        self.is_running = False
        self.start_time: Optional[datetime] = None
        self.price_alerts: Dict[str, List[Dict]] = {s: [] for s in self.symbols}
        self.signal_history: List[Dict] = []

        # Restore persisted data
        self.signal_history = self.paper_engine.load_from_file()

    def start(self):
        self.is_running = True
        self.start_time = datetime.now(timezone.utc)
        logger.info("🚀 Starting DSX paper trading system")

        self.data_collector.start_collection()
        self.state_manager.smart_init_all()

        threading.Thread(target=self._signal_loop, daemon=True).start()
        threading.Thread(target=self._price_check_loop, daemon=True).start()

    def stop(self):
        self.is_running = False
        self.data_collector.stop_collection()
        self._save(force=True)
        logger.info("🛑 Paper trading system stopped")

    def _save(self, force: bool = False):
        self.paper_engine.save_to_file(self.signal_history, force=force)

    def _signal_loop(self):
        """Scan signals every 60 seconds."""
        while self.is_running:
            try:
                for symbol in self.symbols:
                    signals = self.state_manager.process_tick(symbol)
                    for sig in signals:
                        if sig['type'] == 'price_alert':
                            self._add_alert(sig)
                            self.paper_engine.add_order(sig)
                            self._save()
                time.sleep(60)
            except Exception as e:
                logger.error("Signal loop error: %s", e)
                time.sleep(10)

    def _price_check_loop(self):
        """Check latest prices every 10 seconds to drive paper trading engine."""
        while self.is_running:
            try:
                now = datetime.now(timezone.utc)
                for symbol in self.symbols:
                    candle = self.data_collector.get_latest(symbol)
                    if candle:
                        self.paper_engine.check_prices(
                            symbol,
                            candle['high'], candle['low'], candle['close'],
                            now,
                        )
                self._save()
                time.sleep(10)
            except Exception as e:
                logger.error("Price check error: %s", e)
                time.sleep(5)

    def _add_alert(self, sig: Dict):
        symbol = sig['symbol']
        alert = {
            'symbol': symbol,
            'direction': sig['direction'],
            'entry_price': sig['entry_price'],
            'tp_price': sig['tp_price'],
            'sl_price': sig['sl_price'],
            'force_close_hours': sig['force_close_hours'],
            'created_at': datetime.now(timezone.utc),
            'active': True,
        }
        self.price_alerts[symbol].append(alert)
        logger.info(
            "💰 %s %s entry:$%.4f tp:$%.4f sl:$%.4f",
            symbol, sig['direction'].upper(),
            sig['entry_price'], sig['tp_price'], sig['sl_price'],
        )

        # Record signal history
        candle = self.data_collector.get_latest(symbol)
        self.signal_history.append({
            'symbol': symbol,
            'direction': sig['direction'],
            'entry_price': sig['entry_price'],
            'tp_price': sig['tp_price'],
            'sl_price': sig['sl_price'],
            'signal_time': datetime.now(timezone.utc).isoformat(),
            'current_price': candle['close'] if candle else None,
            'state': self.state_manager.state_data[symbol].get('current_market_level', 'micro'),
        })

    # ── API data methods ──────────────────────────────────

    def get_status_data(self) -> Dict:
        """System status for API response."""
        state_info = {}
        for sym in self.symbols:
            sd = self.state_manager.state_data.get(sym, {})
            state_info[sym] = {
                'state': sd.get('state', 'unknown'),
                'base_price': sd.get('base_price'),
                'alerts_count': len([a for a in self.price_alerts.get(sym, []) if a['active']]),
            }

        # Calculate uptime
        uptime_seconds = 0
        uptime_display = "—"
        if self.start_time:
            delta = datetime.now(timezone.utc) - self.start_time
            uptime_seconds = int(delta.total_seconds())
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, secs = divmod(remainder, 60)
            if hours > 0:
                uptime_display = f"{hours}h {minutes}m"
            else:
                uptime_display = f"{minutes}m {secs}s"

        return {
            'running': self.is_running,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'uptime_seconds': uptime_seconds,
            'uptime_display': uptime_display,
            'symbols': self.symbols,
            'state_info': state_info,
            'total_alerts': sum(len([a for a in al if a['active']]) for al in self.price_alerts.values()),
        }

    def get_positions_data(self) -> Dict:
        """Positions + pending orders for API response."""
        return {
            'positions': self.paper_engine.get_positions_data(),
            'pending': [{
                'symbol': o['symbol'], 'direction': o['direction'],
                'entry_price': o['entry_price'],
                'created_at': o['created_at'].isoformat(),
                'size_usdt': round(o['size_usdt'], 2),
            } for o in self.paper_engine.pending_orders],
        }
