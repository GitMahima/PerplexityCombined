"""
researchStrategy.py - Core trading strategy implementation

MIGRATED: This module has been migrated from batch to incremental processing 
to mirror liveStrategy.py behavior and eliminate look-ahead bias.

Key Changes:
- calculate_indicators() now processes data incrementally row-by-row
- process_tick_or_bar() fully implemented with derived signals
- Incremental trackers (EMA, MACD, VWAP, ATR) actively used
- Session-based indicator resets for proper backtesting
- Maintains compatibility with existing backtest infrastructure

This module contains the main trading strategy logic used by both backtest
and live trading systems.
"""

import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import pytz
from ..utils.time_utils import is_within_session, ensure_tz_aware, apply_buffer_to_time
from ..utils.config_helper import ConfigAccessor
from types import MappingProxyType
from .indicators import IncrementalEMA, IncrementalMACD, IncrementalVWAP, IncrementalATR
# Use new core logger primitives (no legacy adapters). STRICT: fail-fast if requested.
from ..utils.logger import HighPerfLogger, increment_tick_counter, get_tick_counter, format_tick_message

# Module uses HighPerfLogger via self.perf_logger (no module-level stdlib logger)

def extract_scalar_value(row, key, default=0, perf_logger=None):
    """Safely extract scalar value from row, handling Series objects.
    If perf_logger provided, emit a concise lifecycle event when extraction fails.
    """
    try:
        if key not in row:
            if perf_logger:
                perf_logger.session_start(f"Missing key '{key}', using default {default}")
            return default
        value = row[key]
        # If value is a Series, take the first element
        if isinstance(value, pd.Series):
            if len(value) > 0:
                value = value.iloc[0]
            else:
                value = default
        return value
    except Exception as e:
        # Use perf_logger for lightweight diagnostics if available; otherwise silent fallback
        if perf_logger:
            perf_logger.session_start(f"Failed to extract {key}: {e}, using default {default}")
        return default

@dataclass
class TradingSignal:
    """Represents a trading signal with all necessary information."""
    action: str  # 'BUY', 'CLOSE', 'HOLD'
    timestamp: datetime
    price: float
    confidence: float = 1.0
    reason: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

class ModularIntradayStrategy:
    """
    Unified long-only intraday strategy supporting multiple indicators.
    """
    
    def __init__(self, frozen_config: MappingProxyType):
        # frozen_config is expected to be MappingProxyType produced by GUI (SSOT)
        self.config = frozen_config
        # ConfigAccessor must be available before reading any strategy params
        self.config_accessor = ConfigAccessor(self.config)
        # STRICT / fail-fast: initialize HighPerfLogger (requires frozen MappingProxyType & prior setup).
        # Let any exception propagate so misconfiguration is detected immediately.
        self.perf_logger = HighPerfLogger(__name__, frozen_config)

        # --- Strategy section (use values from defaults.py only) ---
        # Use strict API: no 'cast' keyword on accessor; do explicit conversion where needed.
        self.use_ema_crossover = bool(self.config_accessor.get_strategy_param('use_ema_crossover'))
        self.use_macd = bool(self.config_accessor.get_strategy_param('use_macd'))
        self.use_vwap = bool(self.config_accessor.get_strategy_param('use_vwap'))
        self.use_rsi_filter = bool(self.config_accessor.get_strategy_param('use_rsi_filter'))
        self.use_htf_trend = bool(self.config_accessor.get_strategy_param('use_htf_trend'))
        self.use_bollinger_bands = bool(self.config_accessor.get_strategy_param('use_bollinger_bands'))
        self.use_stochastic = bool(self.config_accessor.get_strategy_param('use_stochastic'))
        self.use_atr = bool(self.config_accessor.get_strategy_param('use_atr'))

        self.fast_ema = int(self.config_accessor.get_strategy_param('fast_ema'))
        self.slow_ema = int(self.config_accessor.get_strategy_param('slow_ema'))
        self.macd_fast = int(self.config_accessor.get_strategy_param('macd_fast'))
        self.macd_slow = int(self.config_accessor.get_strategy_param('macd_slow'))
        self.macd_signal = int(self.config_accessor.get_strategy_param('macd_signal'))
        self.rsi_length = int(self.config_accessor.get_strategy_param('rsi_length'))
        self.rsi_overbought = float(self.config_accessor.get_strategy_param('rsi_overbought'))
        self.rsi_oversold = float(self.config_accessor.get_strategy_param('rsi_oversold'))
        self.htf_period = int(self.config_accessor.get_strategy_param('htf_period'))
        self.indicator_update_mode = str(self.config_accessor.get_strategy_param('indicator_update_mode'))
        self.consecutive_green_bars_required = int(self.config_accessor.get_strategy_param('consecutive_green_bars'))
        self.atr_len = int(self.config_accessor.get_strategy_param('atr_len'))

        # --- Risk section ---
        self.base_sl_points = float(self.config_accessor.get_risk_param('base_sl_points'))
        self.tp_points = list(self.config_accessor.get_risk_param('tp_points'))
        self.tp_percents = list(self.config_accessor.get_risk_param('tp_percents'))
        self.use_trail_stop = bool(self.config_accessor.get_risk_param('use_trail_stop'))
        self.trail_activation_points = float(self.config_accessor.get_risk_param('trail_activation_points'))
        self.trail_distance_points = float(self.config_accessor.get_risk_param('trail_distance_points'))
        self.risk_per_trade_percent = float(self.config_accessor.get_risk_param('risk_per_trade_percent'))
        self.commission_percent = float(self.config_accessor.get_risk_param('commission_percent'))
        self.commission_per_trade = float(self.config_accessor.get_risk_param('commission_per_trade'))
        self.max_position_value_percent = float(self.config_accessor.get_risk_param('max_position_value_percent'))
        self.stt_percent = float(self.config_accessor.get_risk_param('stt_percent'))
        self.exchange_charges_percent = float(self.config_accessor.get_risk_param('exchange_charges_percent'))
        self.gst_percent = float(self.config_accessor.get_risk_param('gst_percent'))
        self.slippage_points = float(self.config_accessor.get_risk_param('slippage_points'))

        # --- Capital section ---
        self.initial_capital = float(self.config_accessor.get_capital_param('initial_capital'))

        # --- Instrument section ---
        self.symbol = str(self.config_accessor.get_instrument_param('symbol'))
        self.exchange = str(self.config_accessor.get_instrument_param('exchange'))
        self.lot_size = int(self.config_accessor.get_instrument_param('lot_size'))
        self.tick_size = float(self.config_accessor.get_instrument_param('tick_size'))
        self.product_type = str(self.config_accessor.get_instrument_param('product_type'))

        # --- Session section ---
        self.is_intraday = bool(self.config_accessor.get_session_param('is_intraday'))
        sh = int(self.config_accessor.get_session_param('start_hour'))
        sm = int(self.config_accessor.get_session_param('start_min'))
        eh = int(self.config_accessor.get_session_param('end_hour'))
        em = int(self.config_accessor.get_session_param('end_min'))
        self.session_start = time(sh, sm)
        self.session_end = time(eh, em)
        self.start_buffer_minutes = int(self.config_accessor.get_session_param('start_buffer_minutes'))
        self.end_buffer_minutes = int(self.config_accessor.get_session_param('end_buffer_minutes'))
        self.timezone = str(self.config_accessor.get_session_param('timezone'))
        self.max_positions_per_day = int(self.config_accessor.get_risk_param('max_positions_per_day'))

        # No-trade guard periods (minutes before session start / before session end).
        no_trade_start = self.config_accessor.get_session_param('no_trade_start_minutes')
        no_trade_end = self.config_accessor.get_session_param('no_trade_end_minutes')
        self.no_trade_start_minutes = int(no_trade_start if no_trade_start is not None else self.start_buffer_minutes)
        self.no_trade_end_minutes = int(no_trade_end if no_trade_end is not None else self.end_buffer_minutes)

        # Add logging throttling to prevent spam during backtests
        self.last_blocked_reason = None
        self.blocked_reason_count = 0
        self.blocked_reason_log_interval = 1000  # Log every 1000 occurrences

        # Logging section - consolidated logging API. Use HighPerfLogger (self.perf_logger).
        # Legacy "smart logger" flag removed from runtime reads.
        self.verbosity = self.config_accessor.get_logging_param('verbosity')
        self.log_progress = bool(self.config_accessor.get_logging_param('log_progress'))
        self.max_signal_reasons = int(self.config_accessor.get_logging_param('max_signal_reasons'))
        self.log_to_file = bool(self.config_accessor.get_logging_param('log_to_file'))
        self.log_file = str(self.config_accessor.get_logging_param('log_file'))
        self.log_level_overrides = self.config_accessor.get_logging_param('log_level_overrides')

        # Backtest section -> use strict 'backtest' section via get('backtest.key')
        # Use ConfigAccessor convenience helpers for backtest section (consistent SSOT access)
        self.allow_short = bool(self.config_accessor.get_backtest_param('allow_short'))
        self.close_at_session_end = bool(self.config_accessor.get_backtest_param('close_at_session_end'))
        self.save_results = bool(self.config_accessor.get_backtest_param('save_results'))
        self.results_dir = str(self.config_accessor.get_backtest_param('results_dir'))
        self.backtest_log_level = str(self.config_accessor.get_backtest_param('log_level'))

        # NOTE: researchStrategy is backtest-only. All forward-test / live settings are handled
        # by liveStrategy.py (forward-test harness). Do not read 'live' keys here.
        self.paper_trading = False
        self.exchange_type = "backtest"
        self.feed_type = "historical"
        self.log_ticks = False
        self.visual_indicator = False

        # CRITICAL FIX: Initialize daily_stats immediately
        self.daily_stats = {
            'trades_today': 0,
            'pnl_today': 0.0,
            'last_trade_time': None,
            'session_start_time': None
        }

        # Initialize other state variables that might be accessed
        self.is_initialized = False
        self.current_position = None
        self.last_signal_time = None
        self.green_bars_count = 0
        self.last_bar_data = None

        # --- Indicator / parameter initialization (ensure trackers exist before any processing) ---
        # Consecutive green bars for re-entry
        self.consecutive_green_bars_required = self.config_accessor.get_strategy_param('consecutive_green_bars')
        self.green_bars_count = 0
        self.last_bar_data = None

        # EMA parameters
        self.fast_ema = self.config_accessor.get_strategy_param('fast_ema')
        self.slow_ema = self.config_accessor.get_strategy_param('slow_ema')

        # MACD parameters
        self.macd_fast = self.config_accessor.get_strategy_param('macd_fast')
        self.macd_slow = self.config_accessor.get_strategy_param('macd_slow')
        self.macd_signal = self.config_accessor.get_strategy_param('macd_signal')

        # RSI / HTF / ATR parameters
        self.rsi_length = self.config_accessor.get_strategy_param('rsi_length')
        self.rsi_overbought = self.config_accessor.get_strategy_param('rsi_overbought')
        self.rsi_oversold = self.config_accessor.get_strategy_param('rsi_oversold')
        self.htf_period = self.config_accessor.get_strategy_param('htf_period')

        # Risk management and defaults
        self.risk_per_trade_percent = self.config_accessor.get_risk_param('risk_per_trade_percent')

        # Initialize incremental indicator trackers (MIGRATED to incremental processing)
        self.ema_fast_tracker = IncrementalEMA(period=self.fast_ema)
        self.ema_slow_tracker = IncrementalEMA(period=self.slow_ema)
        self.macd_tracker = IncrementalMACD(
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal
        )
        self.vwap_tracker = IncrementalVWAP()
        atr_len = self.config_accessor.get_strategy_param('atr_len')
        self.atr_tracker = IncrementalATR(period=atr_len)
        # --- end inserted initialization ---
        
    def reset(self):
        """Reset strategy state and incremental indicators."""
        self.is_initialized = False
        self.current_position = None
        self.last_signal_time = None
        # reset daily stats too
        self.daily_stats = {
            'trades_today': 0,
            'pnl_today': 0.0,
            'last_trade_time': None,
            'session_start_time': None
        }
        
        # IMPORTANT: Reset incremental trackers for clean backtest runs
        self.reset_incremental_trackers()
        # Emit concise lifecycle event via performance logger
        self.perf_logger.session_start("Strategy reset to initial state")

    def reset_incremental_trackers(self):
        """Reset all incremental indicator trackers for clean state."""
        self.ema_fast_tracker = IncrementalEMA(period=self.fast_ema)
        self.ema_slow_tracker = IncrementalEMA(period=self.slow_ema)
        self.macd_tracker = IncrementalMACD(
            fast=self.macd_fast,
            slow=self.macd_slow, 
            signal=self.macd_signal
        )
        self.vwap_tracker = IncrementalVWAP()
        # ConfigAccessor.get_strategy_param currently accepts only the key argument.
        # Use a safe lookup with fallback to avoid TypeError when param missing.
        try:
            atr_len = self.config_accessor.get_strategy_param('atr_len')
        except Exception:
            atr_len = 14
        self.atr_tracker = IncrementalATR(period=atr_len)
    
        # Reset green bars tracking
        self.green_bars_count = 0
        self.last_bar_data = None
        
        # NEW: Initialize tick-to-tick price tracking
        self.prev_tick_price = None

    def reset_session_indicators(self):
        """Reset session-based indicators (like VWAP) for new trading session."""
        self.vwap_tracker.reset()
        # Use perf_logger.tick_debug for low-level/hot-path debug messages
        self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, "Session indicators reset for new trading day")
        # Emit concise initialization event via performance logger (preserve lifecycle visibility)
        self.perf_logger.session_start(f"Strategy initialized: {getattr(self, 'name', 'N/A')} v{getattr(self, 'version', 'N/A')}")
        self.perf_logger.session_start(
            f"[INIT] Indicator switches: EMA={self.use_ema_crossover}, MACD={self.use_macd}, VWAP={self.use_vwap}, "
            f"RSI={self.use_rsi_filter}, HTF={self.use_htf_trend}, BB={self.use_bollinger_bands}, "
            f"STOCH={self.use_stochastic}, ATR={self.use_atr}"
        )
        self.perf_logger.session_start(
            f"[INIT] Indicator params: fast_ema={self.fast_ema}, slow_ema={self.slow_ema}, "
            f"MACD=({self.macd_fast}, {self.macd_slow}, {self.macd_signal}), "
            f"RSI=({self.rsi_length}, OB={self.rsi_overbought}, OS={self.rsi_oversold}), "
            f"HTF period={self.htf_period}, Green Bars Req={self.consecutive_green_bars_required}"
        )
        self.perf_logger.session_start(
            f"[INIT] Session: {self.session_start.strftime('%H:%M')}–{self.session_end.strftime('%H:%M')}, "
            f"Buffers: +{self.start_buffer_minutes}/-{self.end_buffer_minutes}, "
            f"no_trade_start={self.no_trade_start_minutes} no_trade_end={self.no_trade_end_minutes}, "
            f"Max/day={self.max_positions_per_day}"
        )
    
    def calculate_indicators(self, df):
        """
        TRUE INCREMENTAL PROCESSING: Process data row-by-row to mirror real-time trading.
        This completely eliminates batch processing and ensures no look-ahead bias.
        """
        self.perf_logger.session_start(f"Incremental processing: {len(df)} rows")
        
        # Reset all incremental trackers for clean processing
        self.reset_incremental_trackers()
        
        # Create result dataframe with same index as input
        df = df.copy()
        
        # Initialize indicator columns with appropriate dtypes
        numeric_columns = ['fast_ema', 'slow_ema', 'macd', 'macd_signal', 'macd_histogram', 
                           'vwap', 'htf_ema', 'rsi', 'atr']
        boolean_columns = ['ema_bullish', 'macd_bullish', 'macd_histogram_positive', 
                           'vwap_bullish', 'htf_bullish']
        
        # Initialize numeric columns with NaN (float64)
        for col in numeric_columns:
            if col not in df.columns:
                df[col] = np.nan
        
        # Initialize boolean columns with False (bool)
        for col in boolean_columns:
            if col not in df.columns:
                df[col] = False
        
        # Combined indicator columns list for processing
        indicator_columns = numeric_columns + boolean_columns
        
        # Process each row incrementally - TRUE ROW-BY-ROW PROCESSING
        rows_processed = 0
        for i, (idx, row) in enumerate(df.iterrows()):
            # Process this single row through incremental indicators
            updated_row = self.process_tick_or_bar(row.copy())
            
            # Update the dataframe with incremental results using iloc for safety
            for col in indicator_columns:
                if col in updated_row and col in df.columns:
                    df.iloc[i, df.columns.get_loc(col)] = updated_row[col]
            
            rows_processed += 1
            
            # Log progress every 1000 rows
            if rows_processed % 1000 == 0:
                # rate-limited debug via perf_logger
                self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, None)
        
        self.perf_logger.session_end(f"Incremental processing complete: {rows_processed} rows")
 
        return df
    
    def is_trading_session(self, current_time: datetime) -> bool:
        """
        Check if current time is within user-defined trading session
        """
        # Ensure current_time is timezone-aware
        if current_time.tzinfo is None and hasattr(self, 'timezone'):
            current_time = self.timezone.localize(current_time)
        
        # Direct comparison using the simplified function
        return is_within_session(current_time, self.session_start, self.session_end)

    def get_effective_session_times(self):
        """
        Get effective session start and end times after applying buffers
        Returns tuple of (effective_start, effective_end) as time objects
        """
        effective_start = apply_buffer_to_time(
            self.session_start, self.start_buffer_minutes, is_start=True)
        effective_end = apply_buffer_to_time(
            self.session_end, self.end_buffer_minutes, is_start=False)
        return effective_start, effective_end

    def should_exit_for_session(self, now: datetime) -> bool:
        """
        Check if positions should be exited based on user-defined session end and buffer
        """
        if not self.is_trading_session(now):
            self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, f"Should exit: Not in trading session {now}")
            return True
        
        # Get effective end time with buffer
        _, buffer_end = self.get_effective_session_times()
        
        if now.time() >= buffer_end:
            self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, f"Should exit: After buffer end {buffer_end}")
            return True
        
        return False

    def is_market_closed(self, current_time: datetime) -> bool:
        """Check if market is completely closed (after end time)"""
        return current_time.time() >= self.session_end

    def can_enter_new_position(self, current_time: datetime) -> bool:
        """
        Check if new positions can be entered.
        
        Args:
            current_time: Current timestamp
            
        Returns:
            True if can enter new position
        """
        gating_reasons = []
        if not self.is_trading_session(current_time):
            gating_reasons.append(f"Not in trading session (now={current_time.time()}, allowed={self.session_start}-{self.session_end})")
        buffer_start, buffer_end = self.get_effective_session_times()
        if current_time.time() < buffer_start:
            gating_reasons.append(f"Before buffer start ({current_time.time()} < {buffer_start})")
        if current_time.time() > buffer_end:
            gating_reasons.append(f"After buffer end ({current_time.time()} > {buffer_end})")
        if self.daily_stats['trades_today'] >= self.max_positions_per_day:
            gating_reasons.append(f"Exceeded max trades: {self.daily_stats['trades_today']} >= {self.max_positions_per_day}")
        session_start = ensure_tz_aware(datetime.combine(current_time.date(), self.session_start), current_time.tzinfo)
        session_end = ensure_tz_aware(datetime.combine(current_time.date(), self.session_end), current_time.tzinfo)
        if current_time < session_start + timedelta(minutes=self.no_trade_start_minutes):
            gating_reasons.append(f"In no-trade start period ({current_time.time()} < {session_start.time()} + {self.no_trade_start_minutes}m)")
        if current_time > session_end - timedelta(minutes=self.no_trade_end_minutes):
            gating_reasons.append(f"In no-trade end period ({current_time.time()} > {session_end.time()} - {self.no_trade_end_minutes}m)")
        if not self._check_consecutive_green_ticks():
            gating_reasons.append(f"Need {self.consecutive_green_bars_required} green ticks, have {self.green_bars_count}")
        if gating_reasons:
            # Throttle repeated logging of the same blocking reason to prevent spam
            current_reason = ' | '.join(gating_reasons)
            if current_reason == self.last_blocked_reason:
                self.blocked_reason_count += 1
                # Only log at intervals to prevent excessive logging
                if self.blocked_reason_count % self.blocked_reason_log_interval == 0:
                    self.perf_logger.session_start(f"[ENTRY BLOCKED] at {current_time}: {current_reason} (repeated {self.blocked_reason_count} times)")
            else:
                # New blocking reason - reset counter and log immediately
                self.last_blocked_reason = current_reason
                self.blocked_reason_count = 1
                self.perf_logger.session_start(f"[ENTRY BLOCKED] at {current_time}: {current_reason}")
            return False
        return True
    
    def generate_entry_signal(self, row: pd.Series, current_time: datetime) -> TradingSignal:
        """
        Generate entry signal based on all enabled indicators.
        
        Args:
            row: Current data row with indicators
            current_time: Current timestamp
            
        Returns:
            TradingSignal object
        """
        try:
            close_px = None
            if isinstance(row, pd.Series):
                close_px = row.get('close', None)
            elif isinstance(row, dict):
                close_px = row.get('close', None)
            # Only update when we can extract a numeric close price
            if close_px is not None:
                try:
                    self._update_green_tick_count(float(close_px))
                except Exception:
                    # Let exception propagate per standardization (do not log.exception)
                    raise
            else:
                # Low-level debug via perf_logger
                self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, "generate_entry_signal: missing close price")
        except Exception:
            # Let callers handle unexpected errors (no logger.exception)
            raise
        
        # Check if we can enter
        if not self.can_enter_new_position(current_time):
            return TradingSignal('HOLD', current_time, row['close'], reason="Cannot enter new position")
        
               
        # Collect all signal conditions
        signal_conditions = []
        signal_reasons = []
        confidence = 1.0
        
        # === EMA CROSSOVER SIGNAL ===
        if self.use_ema_crossover:
            if 'ema_bullish' in row:
                # Use pre-calculated continuous ema_bullish state
                if row['ema_bullish']:
                    signal_conditions.append(True)
                    signal_reasons.append(f"EMA: Fast ({row.get('fast_ema', 0):.2f}) above Slow ({row.get('slow_ema', 0):.2f})")
                else:
                    signal_conditions.append(False)
                    signal_reasons.append(f"EMA: Fast not above Slow EMA")
            else:
                signal_conditions.append(False)
                signal_reasons.append("EMA Cross: Data not available")
        
        # === MACD SIGNAL ===
        if self.use_macd:
            if ('macd_bullish' in row and 'macd_histogram_positive' in row):
                macd_bullish = row.get('macd_bullish', False)
                histogram_positive = row.get('macd_histogram_positive', False)
                
                # MACD bullish: MACD line > signal line AND histogram > 0
                macd_signal = macd_bullish and histogram_positive
                signal_conditions.append(macd_signal)
                
                if macd_signal:
                    signal_reasons.append("MACD: Bullish (line > signal & histogram > 0)")
                else:
                    signal_reasons.append(f"MACD: Not bullish (line>{row.get('macd_signal', 'NA')}: {macd_bullish}, hist>0: {histogram_positive})")
            else:
                signal_conditions.append(False)
                signal_reasons.append("MACD: Data not available")
        
        # === VWAP SIGNAL ===
        if self.use_vwap:
            if 'vwap' in row and not pd.isna(row['vwap']):
                vwap_bullish = row['close'] > row['vwap']
                signal_conditions.append(vwap_bullish)
                
                if vwap_bullish:
                    signal_reasons.append(f"VWAP: Bullish ({row['close']:.2f} > {row['vwap']:.2f})")
                else:
                    signal_reasons.append(f"VWAP: Bearish ({row['close']:.2f} <= {row['vwap']:.2f})")
            else:
                signal_conditions.append(False)
                signal_reasons.append("VWAP: Data not available")
        
        # === HTF TREND SIGNAL (Now Optional!) ===
        if self.use_htf_trend:
            if 'htf_ema' in row and not pd.isna(row['htf_ema']):
                htf_bullish = row['close'] > row['htf_ema']
                signal_conditions.append(htf_bullish)
                
                if htf_bullish:
                    signal_reasons.append(f"HTF Trend: Bullish ({row['close']:.2f} > {row['htf_ema']:.2f})")
                else:
                    signal_reasons.append(f"HTF Trend: Bearish ({row['close']:.2f} <= {row['htf_ema']:.2f})")
            else:
                signal_conditions.append(False)
                signal_reasons.append("HTF Trend: Data not available")
        
        # === RSI FILTER ===
        if self.use_rsi_filter:
            if 'rsi' in row and not pd.isna(row['rsi']):
                rsi = row['rsi']
                # RSI should be between oversold and overbought for entry
                rsi_ok = self.rsi_oversold < rsi < self.rsi_overbought
                signal_conditions.append(rsi_ok)
                
                if rsi_ok:
                    signal_reasons.append(f"RSI: Neutral ({rsi:.1f})")
                else:
                    signal_reasons.append(f"RSI: Extreme ({rsi:.1f})")
            else:
                signal_conditions.append(False)
                signal_reasons.append("RSI: Data not available")
        
        # === BOLLINGER BANDS FILTER ===
        if self.use_bollinger_bands:
            if all(col in row for col in ['bb_upper', 'bb_lower', 'bb_middle']):
                bb_ok = row['bb_lower'] < row['close'] < row['bb_upper']
                signal_conditions.append(bb_ok)
                
                if bb_ok:
                    signal_reasons.append("BB: Price within bands")
                else:
                    signal_reasons.append("BB: Price outside bands")
            else:
                signal_conditions.append(False)
                signal_reasons.append("Bollinger Bands: Data not available")
        
        # === FINAL SIGNAL DECISION ===
        # ALL enabled conditions must be True for BUY signal
        # Logging per signal check
        # Emit concise structured signal-check event via perf logger
        self.perf_logger.session_start(
            f"[SIGNAL] @ {current_time.strftime('%Y-%m-%d %H:%M:%S')} | Price={row.get('close', None)} | "
            f"indicators: EMA={self.use_ema_crossover}, MACD={self.use_macd}, VWAP={self.use_vwap}, "
            f"HTF={self.use_htf_trend}, RSI={self.use_rsi_filter}, BB={self.use_bollinger_bands} | "
            f"condition_values={signal_conditions} | reasons={signal_reasons} | "
            f"can_enter={self.can_enter_new_position(current_time)} | GreenBars={self.green_bars_count}/{self.consecutive_green_bars_required}"
        )
        if not (signal_conditions and all(signal_conditions) and self.can_enter_new_position(current_time)):
            fail_reasons = [reason for i, reason in enumerate(signal_reasons) if i < len(signal_conditions) and not signal_conditions[i]]
            self.perf_logger.session_start(f"[ENTRY REJECTED] @ {current_time}: {'; '.join(fail_reasons)}")
        
        if signal_conditions and all(signal_conditions):
            # Calculate stop loss
            stop_loss_price = row['close'] - self.base_sl_points
            
            # Update tracking
            self.last_signal_time = current_time

            # Event-driven logging (only when enabled)
            max_reasons = int(self.config_accessor.get_logging_param('max_signal_reasons'))
            # If max_reasons is None, do not limit reasons (defaults are in defaults.py only)
            reasons = signal_reasons if max_reasons is None else signal_reasons[:max_reasons]
            self.perf_logger.session_start(f"[SIGNAL] BUY @ {current_time} Price={row['close']:.2f} Reasons={' ; '.join(reasons)}")
            
            return TradingSignal(
                action='BUY',
                timestamp=current_time,
                price=row['close'],
                confidence=confidence,
                reason="; ".join(signal_reasons),
                stop_loss=stop_loss_price
            )
        else:
            # Log why signal failed
            failed_reasons = [reason for i, reason in enumerate(signal_reasons) 
                            if i < len(signal_conditions) and not signal_conditions[i]]
            
            # Event-driven hold logging
            max_reasons = int(self.config_accessor.get_logging_param('max_signal_reasons'))
            hold_reasons = failed_reasons if max_reasons is None else failed_reasons[:max_reasons]

            self.perf_logger.session_start(f"[HOLD] @ {current_time} | Reasons: {'; '.join(hold_reasons)}")
            
            return TradingSignal(
                action='HOLD',
                timestamp=current_time,
                price=row['close'],
                confidence=0.0,
                reason=f"Entry blocked: {'; '.join(failed_reasons[:3])}"  # Limit message length
            )
    
    def should_enter_long(self, row: pd.Series, current_time: Optional[datetime] = None) -> bool:
        """
        Check if should enter long position (for backtest compatibility).
        
        Args:
            row: Current data row
            current_time: Current timestamp
            
        Returns:
            True if should enter long
        """
        if current_time is None:
            current_time = row.name if hasattr(row, 'name') else datetime.now()
        
        signal = self.generate_entry_signal(row, current_time)
        return signal.action == 'BUY'
    
    def should_close(self, row: pd.Series, timestamp: datetime, position_manager) -> bool:
        """
        FIXED: Method name compatibility for backtest runner.
        Redirects to should_exit() method.
        """
        return self.should_exit(row, timestamp, position_manager)
    
    def should_enter_short(self, row: pd.Series, current_time: Optional[datetime] = None) -> bool:
        """
        Check if should enter short position.
        
        This strategy is LONG-ONLY, so this always responds with False.
        
        Returns:
            False (no short positions allowed)
        """
        return False  # Long-only strategy
    
    def should_exit_position(self, row: pd.Series, position_type: str, 
                           current_time: Optional[datetime] = None) -> bool:
        """
        Check if should exit current position (for backtest compatibility).
        
        Args:
            row: Current data row
            position_type: Position type ('long' or 'short')
            current_time: Current timestamp
            
        Returns:
            True if should exit position
        """
        if current_time is None:
            current_time = row.name if hasattr(row, 'name') else datetime.now()
        
        # Always exit at session end - renamed from should_exit_session
        if self.should_exit_for_session(current_time):
            return True
        
        # Let position manager handle stop loss, take profit, and trailing stops
        return False
    
    def handle_exit(self, position_id: str, exit_price: float, timestamp: datetime, 
                   position_manager, reason: str = "Strategy Exit") -> bool:
        """
        FIXED: Added missing handle_exit method for backtest compatibility.
        """
        try:
            success = position_manager.close_position_full(position_id, exit_price, timestamp, reason)
            if success:
                # Unified exit logging with comprehensive details
                pos = position_manager.positions.get(position_id, {})
                pnl = getattr(pos, 'last_realized_pnl', 0) or pos.get('pnl', 0)
                symbol = pos.get('symbol', 'N/A')
                self.perf_logger.session_start(f"Strategy exit executed: {position_id} @ {exit_price:.2f} - {reason} PnL={pnl:.2f} Symbol={symbol}")
            return success
        except Exception as e:
            self.perf_logger.session_start(f"Strategy exit failed: {e}")
            return False
    
    def get_signal_description(self, row: pd.Series) -> str:
        """
        Get human-readable signal description (for backtest compatibility).
        
        Args:
            row: Current data row
            
        Returns:
            Signal description string
        """
        descriptions = []
        
        if self.use_ema_crossover and 'fast_ema' in row and 'slow_ema' in row:
            fast_ema = row['fast_ema']
            slow_ema = row['slow_ema']
            if not pd.isna(fast_ema) and not pd.isna(slow_ema):
                descriptions.append(f"EMA {self.fast_ema}/{self.slow_ema}: {fast_ema:.2f}/{slow_ema:.2f}")
        
        if self.use_macd and 'macd' in row and 'macd_signal' in row:
            macd = row['macd']
            signal = row['macd_signal']
            if not pd.isna(macd) and not pd.isna(signal):
                descriptions.append(f"MACD: {macd:.3f}/{signal:.3f}")
        
        if self.use_vwap and 'vwap' in row:
            vwap = row['vwap']
            if not pd.isna(vwap):
                descriptions.append(f"VWAP: {row['close']:.2f} vs {vwap:.2f}")
        
        if self.use_htf_trend and 'htf_ema' in row:
            htf_ema = row['htf_ema']
            if not pd.isna(htf_ema):
                descriptions.append(f"HTF: {row['close']:.2f} vs {htf_ema:.2f}")
        
        return "; ".join(descriptions) if descriptions else "No indicators"
    
    def verify_backtest_interface(self):
        """Production verification of backtest interface."""
        required_methods = ['can_open_long', 'open_long', 'calculate_indicators', 'should_close']
        
        for method in required_methods:
            if not hasattr(self, method):
                self.perf_logger.session_start(f"MISSING METHOD: {method}")
                return False
            else:
                self.perf_logger.session_start(f"✓ Method exists: {method}")
        
        return True

    def can_open_long(self, row: pd.Series, timestamp: datetime) -> bool:
        """PRODUCTION INTERFACE: Entry signal detection."""
        try:
            # Ensure timezone awareness
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize('Asia/Kolkata')
            elif timestamp.tzinfo != pytz.timezone('Asia/Kolkata'):
                timestamp = timestamp.tz_convert('Asia/Kolkata')
            
            # Check session timing
            can_enter = self.can_enter_new_position(timestamp)
            
            # Check signal conditions
            should_enter = self.should_enter_long(row, timestamp)
            
            result = can_enter and should_enter
            
            # Debug logging for first few calls
            if hasattr(self, '_debug_call_count'):
                self._debug_call_count += 1
            else:
                self._debug_call_count = 1
                
            if self._debug_call_count <= 10:
                # Short-lived diagnostic via perf logger
                self.perf_logger.session_start(f"can_open_long called #{self._debug_call_count}: can_enter={can_enter}, should_enter={should_enter}, result={result}")
             
            return result
            
        except Exception as e:
            self.perf_logger.session_start(f"Error in can_open_long: {e}")
            return False

    def open_long(self, row: pd.Series, current_time: datetime, position_manager) -> Optional[str]:
        # Use instrument SSOT for sizing and symbol
        try:
            entry_price = float(row['close'])
            symbol = str(self.config_accessor.get_instrument_param('symbol'))
            lot_size = int(self.config_accessor.get_instrument_param('lot_size'))
            tick_size = float(self.config_accessor.get_instrument_param('tick_size'))
        except KeyError as e:
            self.perf_logger.session_start(f"Missing required instrument parameter for open_long: {e}")
            raise
        except Exception as e:
            self.perf_logger.session_start(f"Invalid instrument/price data for open_long: {e}")
            raise

        # Call position manager to open position
        # Note: position_manager gets lot_size/tick_size from SSOT config, not parameters
        position_id = position_manager.open_position(
            symbol=symbol,
            entry_price=entry_price,
            timestamp=current_time
        )

        if position_id:
            # Update strategy state
            if hasattr(self, 'daily_stats'):
                self.daily_stats['trades_today'] += 1
            self.last_signal_time = current_time
            
            # Unified position opening logging with quantity details
            qty = 0
            try:
                qty = position_manager.positions[position_id].current_quantity if position_id in position_manager.positions else 0
            except Exception:
                qty = 0
            self.perf_logger.session_start(f"Position opened: {position_id} @ {entry_price:.2f} Qty={qty} Symbol={symbol}")
            return position_id
        # If position not opened, emit concise lifecycle event and return None
        self.perf_logger.session_start("Position manager returned None")
        return None
            
       
    
    def should_exit(self, row, timestamp, position_manager):
        """Check if we should close position"""
        # Ensure timezone-aware timestamp
        timestamp = ensure_tz_aware(timestamp)
        
        # Always exit at session end
        if self.should_exit_for_session(timestamp):
            return True
        
        # Let position manager handle stop loss, take profit, and trailing stops
        return False
    
            
        # Reset daily stats if new day
        if (self.daily_stats['session_start_time'] is None or 
            current_time.date() != self.daily_stats['session_start_time'].date()):
            
            self.daily_stats = {
                'trades_today': 0,
                'pnl_today': 0.0,
                'last_trade_time': None,
                'session_start_time': current_time
            }
    
    def get_strategy_info(self) -> Dict[str, Any]:
        """
        Get strategy information (for backtest compatibility).
        
        Returns:
            Strategy information dictionary
        """
        return {
            'name': self.name,
            'version': self.version,
            'type': 'Long-Only Intraday',
            'indicators_enabled': {
                'ema_crossover': self.use_ema_crossover,
                'macd': self.use_macd,
                'vwap': self.use_vwap,
                'rsi_filter': self.use_rsi_filter,
                'htf_trend': self.use_htf_trend,
                'bollinger_bands': self.use_bollinger_bands,
                'stochastic': self.use_stochastic,
                'atr': self.use_atr
            },
            'parameters': {
                'fast_ema': self.fast_ema,
                'slow_ema': self.slow_ema,
                'htf_period': self.htf_period,
                'base_sl_points': self.base_sl_points,
                'risk_per_trade_percent': self.risk_per_trade_percent
            },
            'constraints': {
                'long_only': True,
                'intraday_only': True,
                'max_trades_per_day': self.max_positions_per_day
            },
            'session': {
                'start': self.session_start.strftime('%H:%M'),
                'end': self.session_end.strftime('%H:%M'),
                'start_buffer_minutes': self.start_buffer_minutes,
                'end_buffer_minutes': self.end_buffer_minutes
            },
            'daily_stats': self.daily_stats.copy()
        }
    
    def validate_parameters(self) -> List[str]:
        """
        Validate strategy parameters.
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Check for required parameters
        required_params = [
            'fast_ema', 'slow_ema', 'macd_fast', 'macd_slow', 'macd_signal',
            'consecutive_green_bars', 'atr_len', 'rsi_length', 'rsi_overbought', 'rsi_oversold'
        ]
        
        for param in required_params:
            try:
                self.config_accessor.get_strategy_param(param)
            except KeyError:
                errors.append(f"Missing required parameter: {param}")
        
        # Validate EMA parameters
        if self.use_ema_crossover:
            if self.fast_ema >= self.slow_ema:
                errors.append("Fast EMA must be less than slow EMA")
            if self.fast_ema <= 0 or self.slow_ema <= 0:
                errors.append("EMA periods must be positive")
        
        # Validate HTF parameters
        if self.use_htf_trend:
            try:
                htf_period = self.config_accessor.get_strategy_param('htf_period')
                if htf_period <= 0:
                    errors.append("HTF period must be positive")
            except KeyError:
                errors.append("Missing required parameter: htf_period")
        
        return errors
    
        
    def process_tick_or_bar(self, row: pd.Series):
        # Called per tick
        increment_tick_counter()
        # use perf_logger.tick_debug for rate-limited debug
        if self.perf_logger:
            self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), row.get('close', 0), row.get('volume', None))
        """
        TRUE INCREMENTAL PROCESSING: Update all indicators for a single tick/bar.
        This is the core of incremental processing - called for each data point in sequence.
        
        Args:
            row: Latest price data with OHLCV values
        
        Returns:
            Updated row with indicator values
        """
        try:
            # Accept DataFrame with single row or Series
            if isinstance(row, pd.DataFrame):
                self.perf_logger.session_start(f"Expected Series but got DataFrame with shape {row.shape}")
                if len(row) == 1:
                    row = row.iloc[0]
                else:
                    self.perf_logger.session_start(f"Cannot process DataFrame with {len(row)} rows")
                    return pd.Series()
    
            # Use shared helper to safely extract scalars (handles Series / missing keys)
            def safe_extract(key, default=None):
                # Pass perf_logger for optional diagnostics on extraction failures
                return extract_scalar_value(row, key, default, perf_logger=self.perf_logger)
    
            # Extract OHLCV safely and defensively (mirror liveStrategy)
            close_price = safe_extract('close', safe_extract('price', None))
            if close_price is None:
                self.perf_logger.session_start("Missing close/price in row; skipping indicator update")
                return row
            try:
                close_price = float(close_price)
            except Exception:
                self.perf_logger.session_start("Invalid close price; skipping row")
                return row
            if close_price <= 0:
                self.perf_logger.session_start("Non-positive close price; skipping row")
                return row
    
            volume = safe_extract('volume', 0) or 0
            try:
                volume = int(volume)
            except Exception:
                volume = 0
            high_price = float(safe_extract('high', close_price))
            low_price = float(safe_extract('low', close_price))
            open_price = float(safe_extract('open', close_price))
    
            updated_row = row.copy()
    
            # === INCREMENTAL EMA CALCULATION ===
            if self.use_ema_crossover:
                fast_ema_val = self.ema_fast_tracker.update(close_price)
                slow_ema_val = self.ema_slow_tracker.update(close_price)
                updated_row['fast_ema'] = fast_ema_val
                updated_row['slow_ema'] = slow_ema_val
                updated_row['ema_bullish'] = False if pd.isna(fast_ema_val) or pd.isna(slow_ema_val) else (fast_ema_val > slow_ema_val)
    
            # === INCREMENTAL MACD CALCULATION ===
            if self.use_macd:
                macd_val, macd_signal_val, macd_hist_val = self.macd_tracker.update(close_price)
                updated_row['macd'] = macd_val
                updated_row['macd_signal'] = macd_signal_val
                updated_row['macd_histogram'] = macd_hist_val
                updated_row['macd_bullish'] = False if pd.isna(macd_val) or pd.isna(macd_signal_val) else (macd_val > macd_signal_val)
                updated_row['macd_histogram_positive'] = False if pd.isna(macd_hist_val) else (macd_hist_val > 0)
    
            # === INCREMENTAL VWAP CALCULATION ===
            if self.use_vwap:
                vwap_val = self.vwap_tracker.update(price=close_price, volume=volume, high=high_price, low=low_price, close=close_price)
                updated_row['vwap'] = vwap_val
                updated_row['vwap_bullish'] = False if pd.isna(vwap_val) else (close_price > vwap_val)
    
            # === INCREMENTAL HTF EMA (if enabled) ===
            if self.use_htf_trend:
                if not hasattr(self, 'htf_ema_tracker'):
                    self.htf_ema_tracker = IncrementalEMA(period=self.htf_period)
                htf_ema_val = self.htf_ema_tracker.update(close_price)
                updated_row['htf_ema'] = htf_ema_val
                updated_row['htf_bullish'] = False if pd.isna(htf_ema_val) else (close_price > htf_ema_val)
    
            # === INCREMENTAL ATR CALCULATION ===
            if self.use_atr:
                atr_val = self.atr_tracker.update(high=high_price, low=low_price, close=close_price)
                updated_row['atr'] = atr_val

            # Update green-tick count and return updated row
            self._update_green_tick_count(close_price)
            return updated_row
    
        except Exception as e:
            # Emit concise lifecycle event then re-raise so upstream can handle (no logger.exception)
            self.perf_logger.session_start(f"Error in incremental processing: {e}")
            raise
    
    def _update_green_tick_count(self, current_price: float):
        """
        Update consecutive green ticks counter based on tick-to-tick price movement
        with configurable noise filtering.
        """
        try:
            if self.prev_tick_price is None:
                # First tick of session or after reset
                self.green_bars_count = 0
                self.prev_tick_price = current_price
                self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"First tick: price={current_price:.2f}, green_count=0")
                return
                
            # Get noise filter parameters from config
            noise_filter_enabled = bool(self.config_accessor.get_strategy_param('noise_filter_enabled', True))
            noise_filter_percentage = float(self.config_accessor.get_strategy_param('noise_filter_percentage', 0.0001))
            noise_filter_min_ticks = float(self.config_accessor.get_strategy_param('noise_filter_min_ticks', 1.0))
            
            # Calculate minimum movement threshold
            min_movement = max(self.tick_size * noise_filter_min_ticks, 
                              self.prev_tick_price * noise_filter_percentage)
            
            # Apply noise filter if enabled
            if noise_filter_enabled:
                if current_price > (self.prev_tick_price + min_movement):
                    # Significant upward movement
                    self.green_bars_count += 1
                    self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Green tick: {self.prev_tick_price:.2f} -> {current_price:.2f} (delta: {current_price - self.prev_tick_price:.2f} > {min_movement:.2f}), count={self.green_bars_count}")
                elif current_price < (self.prev_tick_price - min_movement):
                    # Significant downward movement
                    self.green_bars_count = 0
                    self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Red tick: {self.prev_tick_price:.2f} -> {current_price:.2f} (delta: {self.prev_tick_price - current_price:.2f} > {min_movement:.2f}), count reset to 0")
                else:
                    # Price within noise range - maintain current count
                    self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Noise range tick: {self.prev_tick_price:.2f} -> {current_price:.2f} (delta: {abs(current_price - self.prev_tick_price):.2f} <= {min_movement:.2f}), count remains {self.green_bars_count}")
            else:
                # Original behavior without noise filtering
                if current_price > self.prev_tick_price:
                    self.green_bars_count += 1
                    self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Green tick: {self.prev_tick_price:.2f} -> {current_price:.2f}, count={self.green_bars_count}")
                else:
                    # Reset counter on price decrease or equal
                    self.green_bars_count = 0
                    self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Red tick: {self.prev_tick_price:.2f} -> {current_price:.2f}, count reset to 0")
            
            # Update previous price for next comparison
            self.prev_tick_price = current_price
            
            self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), current_price, f"Green tick count: {self.green_bars_count}/{self.consecutive_green_bars_required}")
        except Exception as e:
            self.perf_logger.session_start(f"Error updating green tick count: {e}")
 
    def _check_consecutive_green_ticks(self) -> bool:
        """Check if we have enough consecutive green ticks for entry."""
        return self.green_bars_count >= self.consecutive_green_bars_required
        
    def process_tick_or_bar_legacy(self, row: pd.Series):
        # For EMA
        fast_ema_val = self.ema_fast_tracker.update(row['close'])
        slow_ema_val = self.ema_slow_tracker.update(row['close'])
        # Calculate derived EMA values
        row['ema_bullish'] = fast_ema_val > slow_ema_val

        # For MACD
        macd_val, macd_signal_val, macd_hist_val = self.macd_tracker.update(row['close'])
        # Calculate derived MACD values
        row['macd_bullish'] = macd_val > macd_signal_val
        row['macd_histogram_positive'] = macd_hist_val > 0

        # For VWAP
        vwap_val = self.vwap_tracker.update(
            price=row['close'], volume=row['volume'],
            high=row.get('high'), low=row.get('low'), close=row.get('close')
        )

        # For ATR
        atr_val = self.atr_tracker.update(
            high=row['high'], low=row['low'], close=row['close']
        )

        # Update row/signal state as required
        row['fast_ema'] = fast_ema_val
        row['slow_ema'] = slow_ema_val
        row['macd'] = macd_val
        row['macd_signal'] = macd_signal_val
        row['macd_histogram'] = macd_hist_val
        row['vwap'] = vwap_val
        row['atr'] = atr_val

        return row
    
def entry_signal(self, row: pd.Series) -> bool:
    # Collect signal conditions from enabled indicators only
    signal_conditions = []
    signal_reasons = []

    # EMA Crossover
    if self.config_accessor.get_strategy_param('use_ema_crossover', False):
        if 'ema_bullish' in row:
            # Use pre-calculated continuous ema_bullish state
            if row['ema_bullish']:
                signal_conditions.append(True)
                signal_reasons.append(f"EMA: Fast ({row.get('fast_ema', 0):.2f}) above Slow ({row.get('slow_ema', 0):.2f})")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"EMA: Fast not above Slow EMA")
        else:
            signal_conditions.append(False)
            signal_reasons.append("EMA Cross: Data not available")

    # VWAP
    if self.config_accessor.get_strategy_param('use_vwap', False):
        if 'vwap' in row and not pd.isna(row['vwap']):
            if row['close'] > row['vwap']:
                signal_conditions.append(True)
                signal_reasons.append(f"VWAP: Price {row['close']:.2f} > VWAP {row['vwap']:.2f}")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"VWAP: Price {row['close']:.2f} not above VWAP {row['vwap']:.2f}")
        else:
            signal_conditions.append(False)
            signal_reasons.append("VWAP: Data not available")

    # MACD
    if self.config_accessor.get_strategy_param('use_macd', False):
        if all(x in row and not pd.isna(row[x]) for x in ['macd', 'macd_signal']):
            macd_val = row['macd']
            macd_signal = row['macd_signal']
            if macd_val > macd_signal:
                signal_conditions.append(True)
                signal_reasons.append(f"MACD: {macd_val:.2f} > Signal {macd_signal:.2f}")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"MACD: Not above signal line")
        else:
            signal_conditions.append(False)
            signal_reasons.append("MACD: Data not available")
        
    # Higher Timeframe Trend
    if self.config_accessor.get_strategy_param('use_htf_trend', False):
        if 'htf_trend' in row and not pd.isna(row['htf_trend']):
            if row['htf_trend'] > 0:  # Positive trend
                signal_conditions.append(True)
                signal_reasons.append(f"HTF Trend: Bullish ({row['htf_trend']:.2f})")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"HTF Trend: Not bullish")
        else:
            signal_conditions.append(False)
            signal_reasons.append("HTF Trend: Data not available")
            
    # RSI
    if self.config_accessor.get_strategy_param('use_rsi_filter', False):
        if 'rsi' in row and not pd.isna(row['rsi']):
            rsi_val = row['rsi']
            try:
                rsi_lower = self.config_accessor.get_strategy_param('rsi_lower')
                if rsi_lower is None:
                    raise KeyError('rsi_lower')
                    
                rsi_upper = self.config_accessor.get_strategy_param('rsi_upper')
                if rsi_upper is None:
                    raise KeyError('rsi_upper')
            except KeyError as e:
                # Propagate but emit concise lifecycle event
                self.perf_logger.session_start(f"Missing required RSI parameter: {e}")
                raise
            
            if rsi_lower < rsi_val < rsi_upper:
                signal_conditions.append(True)
                signal_reasons.append(f"RSI: {rsi_val:.2f} in range ({rsi_lower}-{rsi_upper})")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"RSI: {rsi_val:.2f} out of range")
        else:
            signal_conditions.append(False)
            signal_reasons.append("RSI: Data not available")
            
    # Bollinger Bands
    if self.config_accessor.get_strategy_param('use_bb', False):
        if all(x in row and not pd.isna(row[x]) for x in ['bb_upper', 'bb_lower']):
            price = row['close']
            if row['bb_lower'] < price < row['bb_upper']:
                signal_conditions.append(True)
                signal_reasons.append(f"BB: Price {price:.2f} within bands")
            else:
                signal_conditions.append(False)
                signal_reasons.append(f"BB: Price outside bands")
        else:
            signal_conditions.append(False)
            signal_reasons.append("BB: Data not available")

    # Store signal reasons for logging/debugging
    self.perf_logger.session_start(f"Signal reasons: {signal_reasons}")
    
    # Must have at least one enabled indicator with valid signal
    if not signal_conditions:
        return False
        
    # All enabled indicators must agree (pass their conditions)
    return all(signal_conditions)

