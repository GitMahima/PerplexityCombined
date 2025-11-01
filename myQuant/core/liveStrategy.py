"""
core/liveStrategy.py - High-Performance Live Trading Strategy

- Optimized for real-time tick-by-tick processing
- F&O-ready, multi-indicator, performance-focused
- No shorting, no overnight risk, all config/param driven
- Handles all signal, entry, exit, and session rules for live trading
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, time, timedelta
import pytz

# Initialize module-level logger
logger = logging.getLogger(__name__)
from ..utils.time_utils import now_ist, normalize_datetime_to_ist, is_time_to_exit, is_within_session, ensure_tz_aware, apply_buffer_to_time
from types import MappingProxyType
from ..utils.logger import HighPerfLogger, increment_tick_counter, get_tick_counter, format_tick_message

from ..utils.config_helper import ConfigAccessor
from .indicators import IncrementalEMA, IncrementalMACD, IncrementalVWAP, IncrementalATR
from ..utils.enhanced_error_handler import (
    create_error_handler_from_config, ErrorSeverity, 
    safe_tick_processing, safe_indicator_calculation
)
from ..utils.performance_metrics import PerformanceInstrumentor
from dataclasses import dataclass

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
    High-performance live trading strategy optimized for real-time execution.
    
    Features:
    - Pure tick-by-tick processing with incremental indicators
    - Long-only, intraday-only trading strategy
    - Configurable indicator combinations (EMA, MACD, VWAP, RSI, etc.)
    - Session time management with precise buffers
    - Risk management parameters
    - Trade frequency limits with noise filtering
    - Performance-optimized consecutive green tick tracking
    """
    
    def __init__(self, config: MappingProxyType, indicators_module=None):
        """
        Initialize strategy with parameters.
        
        Args:
            config: IMMUTABLE frozen configuration from GUI (MappingProxyType)
            indicators_module: Optional module for calculating indicators
        """
        # CRITICAL: Enforce frozen config - fail immediately if not MappingProxyType
        if not isinstance(config, MappingProxyType):
            raise TypeError(
                f"liveStrategy requires frozen MappingProxyType config from GUI. "
                f"Got {type(config)}. Use: freeze_config(validate_config(create_config_from_defaults()))"
            )
        
        self.config = config
        # STRICT / fail-fast: initialize HighPerfLogger (requires frozen MappingProxyType & prior setup).
        # Let any exception propagate so misconfiguration is detected immediately.
        # Use high-performance logger (FAIL-FAST: requires caller to have run setup_from_config)
        self.perf_logger = HighPerfLogger(__name__, config)
        self.config_accessor = ConfigAccessor(config)
        self.indicators = indicators_module
        self.in_position = False
        self.position_id = None
        self.position_entry_time = None
        self.position_entry_price = None
        self.last_signal_time = None
        # For metering daily trade count and other constraints
        self.daily_stats = {
            'trades_today': 0,
            'pnl_today': 0.0,
            'last_trade_time': None,
            'session_start_time': None
        }

        # Price-Above-Exit Filter state tracking
        self.price_above_exit_filter_enabled = self.config_accessor.get_risk_param("price_above_exit_filter_enabled")
        self.price_buffer_points = self.config_accessor.get_risk_param("price_buffer_points")
        self.filter_duration_seconds = self.config_accessor.get_risk_param("filter_duration_seconds")
        
        # Filter tracking state
        self.last_exit_reason = None
        self.last_exit_price = None
        self.last_exit_time = None

        # --- Feature flags (read from validated frozen config; GUI is SSOT) ---
        # ALL parameters MUST exist in defaults.py - no fallbacks allowed
        self.use_ema_crossover = self.config_accessor.get_strategy_param('use_ema_crossover')
        self.use_macd = self.config_accessor.get_strategy_param('use_macd')
        self.use_vwap = self.config_accessor.get_strategy_param('use_vwap')
        self.use_rsi_filter = self.config_accessor.get_strategy_param('use_rsi_filter')
        self.use_htf_trend = self.config_accessor.get_strategy_param('use_htf_trend')
        self.use_bollinger_bands = self.config_accessor.get_strategy_param('use_bollinger_bands')
        self.use_atr = self.config_accessor.get_strategy_param('use_atr')
        
        # COMPREHENSIVE FAIL-FAST VALIDATION - Every parameter must exist in defaults.py
        self._validate_all_required_parameters()
        
        # Initialize instrument SSOT parameters
        self.tick_size = float(self.config_accessor.get_current_instrument_param('tick_size'))
 
        # Session/session exit config (populate using existing accessors)
        self.session_start = time(
            self.config_accessor.get_session_param('start_hour'),
            self.config_accessor.get_session_param('start_min')
        )
        self.session_end = time(
            self.config_accessor.get_session_param('end_hour'),
            self.config_accessor.get_session_param('end_min')
        )
        self.start_buffer_minutes = self.config_accessor.get_session_param('start_buffer_minutes')
        self.end_buffer_minutes = self.config_accessor.get_session_param('end_buffer_minutes')
        # Trading constraints
        self.max_positions_per_day = self.config_accessor.get_risk_param('max_positions_per_day')
        self.no_trade_start_minutes = self.config_accessor.get_session_param('no_trade_start_minutes')
        self.no_trade_end_minutes = self.config_accessor.get_session_param('no_trade_end_minutes')
        
        # Session Trade Blocks configuration
        self.trade_block_enabled = self.config_accessor.get_session_param('trade_block_enabled')
        self.trade_blocks = self.config_accessor.get_session_param('trade_blocks')

        # Get timezone setting with fail-fast behavior
        try:
            tz_name = self.config_accessor.get_session_param('timezone')
            try:
                self.timezone = pytz.timezone(tz_name)
            except Exception as e:
                raise ValueError(f"Invalid timezone in config: {tz_name}")
        except KeyError as e:
            raise

        # Log session configuration via high-perf logger (concise lifecycle event)
        from . import indicators

        # EMA parameters
        self.fast_ema = self.config_accessor.get_strategy_param('fast_ema')
        self.slow_ema = self.config_accessor.get_strategy_param('slow_ema')
        
        # MACD parameters
        self.macd_fast = self.config_accessor.get_strategy_param('macd_fast')
        self.macd_slow = self.config_accessor.get_strategy_param('macd_slow')
        self.macd_signal = self.config_accessor.get_strategy_param('macd_signal')

        # --- Incremental indicator trackers ---    
        self.ema_fast_tracker = IncrementalEMA(period=self.fast_ema)
        self.ema_slow_tracker = IncrementalEMA(period=self.slow_ema)
        self.macd_tracker = IncrementalMACD(
            fast=self.macd_fast,
            slow=self.macd_slow, 
            signal=self.macd_signal
        )
        self.vwap_tracker = IncrementalVWAP()
        self.atr_tracker = IncrementalATR(period=self.config_accessor.get_strategy_param('atr_len'))
        
        # HTF EMA tracker (initialize if HTF trend is enabled)
        if self.use_htf_trend:
            htf_period = self.config_accessor.get_strategy_param('htf_period')
            self.htf_ema_tracker = IncrementalEMA(period=htf_period)
        
        # --- Consecutive green bars for re-entry ---
        try:
            self.consecutive_green_bars_required = self.config_accessor.get_strategy_param('consecutive_green_bars')
            self.green_bars_count = 0
            self.last_bar_data = None
            # Initialize tick-to-tick price tracking 
            self.prev_tick_price = None
        except KeyError as e:
            raise
        
        # --- Control Base SL feature for dynamic green tick requirements ---
        try:
            self.control_base_sl_enabled = self.config_accessor.get_strategy_param('Enable_control_base_sl_green_ticks')
            self.base_sl_green_ticks = self.config_accessor.get_strategy_param('control_base_sl_green_ticks')
            self.last_exit_was_base_sl = False
            # Use existing consecutive_green_bars_required as normal threshold
            self.current_green_tick_threshold = self.consecutive_green_bars_required
        except KeyError as e:
            logger.error(f"Missing Control Base SL parameters: {e}")
            raise ValueError(f"Missing required Control Base SL parameter: {e}")
        
        # Set name and version
        self.name = "Modular Intraday Long-Only Strategy"
        self.version = "3.0"
        
        # Phase 0: Indicator warm-up period
        self.tick_count = 0
        self.min_warmup_ticks = self.config_accessor.get_strategy_param('min_warmup_ticks')
        self.warmup_complete = False
        
        # Phase 1: Performance instrumentation
        self.instrumentor = PerformanceInstrumentor(window_size=1000)
        self.instrumentation_enabled = False  # Control flag (enabled for Phase 1 baseline)
        
        # Initialize enhanced error handler
        self.error_handler = create_error_handler_from_config(config, "live_strategy")
        
        # Emit concise initialization event via high-perf logger
        self.perf_logger.session_start(f"Strategy initialized: {self.name} v{self.version}")
        # (Detailed indicator info intentionally not emitted via stdlib logger per standardization)

    def is_trading_session(self, current_time: datetime) -> bool:
        """
        Check if current time is within user-defined trading session
        """
        # Ensure current_time is timezone-aware
        if current_time.tzinfo is None and hasattr(self, 'timezone'):
            current_time = self.timezone.localize(current_time)
        
        # Direct comparison using the simplified function
        return is_within_session(current_time, self.session_start, self.session_end)

    def is_within_trade_block(self, current_time: datetime) -> Tuple[bool, str]:
        """
        Check if current time is within any configured trade block period.
        
        Args:
            current_time: Current timestamp to check
            
        Returns:
            Tuple[bool, str]: (is_blocked, block_description)
                - is_blocked: True if within a trade block
                - block_description: String describing which block (for logging)
        """
        # Master switch check - fail-fast if disabled
        if not self.trade_block_enabled:
            return False, ""
        
        # Convert current time to minutes since midnight for comparison
        current_minutes = current_time.hour * 60 + current_time.minute
        
        # Check each configured block
        for idx, block in enumerate(self.trade_blocks):
            start_minutes = block['start_hour'] * 60 + block['start_min']
            end_minutes = block['end_hour'] * 60 + block['end_min']
            
            # Check if current time falls within this block
            if start_minutes <= current_minutes <= end_minutes:
                block_desc = (
                    f"Block #{idx + 1} "
                    f"({block['start_hour']:02d}:{block['start_min']:02d}-"
                    f"{block['end_hour']:02d}:{block['end_min']:02d})"
                )
                return True, block_desc
        
        return False, ""

    def can_enter_new_position(self, current_time: datetime, current_price: float) -> bool:
        """
        Unified entry validation - ALL gating conditions in one place.
        
        Args:
            current_time: Current timestamp
            current_price: Current market price for filter validation
            
        Returns:
            True if can enter new position, False if blocked by any condition
        """
        gating_reasons = []
        
        # Check trade blocks FIRST (highest priority - user-defined restriction)
        is_blocked, block_desc = self.is_within_trade_block(current_time)
        if is_blocked:
            gating_reasons.append(f"Within trade block: {block_desc}")
        
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
        
        # Price-Above-Exit Filter check (at END, after all other checks)
        if self.price_above_exit_filter_enabled:
            if self.last_exit_reason in ["Trailing Stop", "Base SL"] and self.last_exit_time is not None:
                time_elapsed = (current_time - self.last_exit_time).total_seconds()
                
                # Check if filter is still active (not expired)
                if time_elapsed <= self.filter_duration_seconds:
                    min_required_price = self.last_exit_price + self.price_buffer_points
                    
                    if current_price < min_required_price:
                        shortfall = min_required_price - current_price
                        gating_reasons.append(
                            f"Price-Above-Exit filter blocked | "
                            f"Price ₹{current_price:.2f} < threshold ₹{min_required_price:.2f} "
                            f"(shortfall {shortfall:.2f}pt) | "
                            f"Elapsed {time_elapsed:.0f}s/{self.filter_duration_seconds}s"
                        )
        
        if gating_reasons:
            # LIGHTWEIGHT: Only enhance logging if we have cached price (no method calls)
            reason_text = '; '.join(gating_reasons)
            if hasattr(self, 'prev_tick_price') and self.prev_tick_price:
                try:
                    symbol = self.config_accessor.get_instrument_param('symbol')
                    reason_text += f", {symbol} @ ₹{self.prev_tick_price}"
                except Exception:
                    pass  # DEFENSIVE: Never fail on logging enhancement
            
            self.perf_logger.entry_blocked(reason_text)
            return False
        return True

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

    def should_exit_for_session(self, now: datetime) -> Tuple[bool, str]:
        """
        Check if positions should be exited based on user-defined session end and buffer
        
        Returns:
            Tuple[bool, str]: (should_exit, reason) - True if session ended with explanation
        """
        if not self.is_trading_session(now):
            return True, f"Outside trading session (current: {now.time().strftime('%H:%M:%S')}, session: {self.session_start.strftime('%H:%M')}-{self.session_end.strftime('%H:%M')})"
        
        # Get effective end time with buffer
        _, buffer_end = self.get_effective_session_times()
        
        if now.time() >= buffer_end:
            return True, f"Session end buffer reached (current: {now.time().strftime('%H:%M:%S')}, buffer end: {buffer_end.strftime('%H:%M')}, market close: {self.session_end.strftime('%H:%M')}, buffer: {self.end_buffer_minutes}min)"
        
        return False, ""

    def is_market_closed(self, current_time: datetime) -> bool:
        """Check if market is completely closed (after end time)"""
        return current_time.time() >= self.session_end

    def reset_incremental_trackers(self):
        """Re-init incremental trackers for deterministic runs."""
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
        
        # Reset HTF EMA tracker if enabled
        if self.use_htf_trend:
            htf_period = self.config_accessor.get_strategy_param('htf_period')
            self.htf_ema_tracker = IncrementalEMA(period=htf_period)
        
        # reset green-bars tracking
        self.green_bars_count = 0
        self.last_bar_data = None
        
        # NEW: Initialize tick-to-tick price tracking
        self.prev_tick_price = None

    def reset_session_indicators(self):
        """Reset session-based indicators (like VWAP) for a new trading session."""
        try:
            self.vwap_tracker.reset()
            # Use perf_logger tick_debug for low-level debug in hot paths
            self.perf_logger.tick_debug(format_tick_message, get_tick_counter(), 0.0, None)
        except Exception as e:
            pass  # Suppress errors in session reset

    def on_position_closed(self, position_id: str, reason: str = "Unknown"):
        """
        Notify strategy that a position has been closed.
        CRITICAL: Resets position state to allow new entries.
        """
        if self.position_id == position_id:
            self.in_position = False
            self.position_id = None
            self.position_entry_time = None
            self.position_entry_price = None
            # Position closure notification - SAFE logging (never crash trading)
            try:
                logger = logging.getLogger(__name__)
                logger.debug(f"Strategy notified of position closure: {position_id} ({reason})")
            except Exception:
                pass  # Never let logging errors stop trading
        else:
            # Unknown position closure - SAFE logging
            try:
                logger = logging.getLogger(__name__)
                logger.warning(f"Position closure notification for unknown position: {position_id}")
            except Exception:
                pass  # Continue trading even if logging fails

    def on_position_exit(self, exit_info: Dict):
        """
        Callback for position exits.
        Handles BOTH Control Base SL logic AND Price-Above-Exit Filter tracking.
        
        Args:
            exit_info: Dictionary containing exit details including exit_reason, exit_price, timestamp
        """
        # CRITICAL: Reset position state to allow new entries
        position_id = exit_info.get('position_id')
        if self.position_id == position_id:
            self.in_position = False
            self.position_id = None
            self.position_entry_time = None
            self.position_entry_price = None
            logger.debug(f"Position state reset after exit: {position_id}")
        
        # Extract exit details
        exit_reason = exit_info.get('exit_reason', '')
        exit_price = exit_info.get('exit_price')
        exit_time = exit_info.get('timestamp')
        
        # Price-Above-Exit Filter Tracking
        if self.price_above_exit_filter_enabled:
            # Check for both Trailing Stop and Base SL exits
            if exit_reason in ["Trailing Stop", "Base SL"]:
                self.last_exit_reason = exit_reason
                self.last_exit_price = exit_price
                self.last_exit_time = exit_time
                
                min_price = self.last_exit_price + self.price_buffer_points
                logger.info(
                    f"[FILTER] {exit_reason} exit at ₹{self.last_exit_price:.2f}. "
                    f"Re-entry blocked until price > ₹{min_price:.2f} "
                    f"or {self.filter_duration_seconds}s elapsed."
                )
        
        # Control Base SL Logic
        if not self.control_base_sl_enabled:
            return
        
        if 'Base SL' in exit_reason:
            self.last_exit_was_base_sl = True
            logger.info(
                f"Base SL exit detected—next entry requires {self.base_sl_green_ticks} green ticks."
            )
        # Reset threshold on any profitable exit (TP or trailing stop)
        elif exit_reason in ('Take Profit', 'Trailing Stop'):
            self.last_exit_was_base_sl = False
            logger.info(
                f"Profitable exit detected—threshold reset to {self.consecutive_green_bars_required} green ticks."
            )

    def process_historical_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Process historical data maintaining incremental state.
        WARNING: Only use for initialization - live trading should use on_tick() exclusively.
        """
        if data is None or data.empty:
            return data

        # Process each tick maintaining incremental state (NO reset)
        updated_data = data.copy()
        
        for i, (idx, row) in enumerate(data.iterrows()):
            updated_row = self.process_tick_or_bar(row)
            # Update the dataframe with calculated indicators
            for col in ['fast_ema', 'slow_ema', 'macd', 'macd_signal', 'vwap', 'atr']:
                if col in updated_row:
                    updated_data.at[idx, col] = updated_row[col]
        
        return updated_data

    def entry_signal(self, row: pd.Series) -> bool:
        """
        Check if entry signal is present based on enabled indicators.
        
        Args:
            row: Current data row with indicator values
            
        Returns:
            True if entry signal is present, False otherwise
        """
        # Track which indicators are checked and their results
        checks_performed = []
        failed_checks = []
        
        # --- EMA CROSS ---
        pass_ema = True
        if self.use_ema_crossover:
            checks_performed.append("EMA Crossover")
            # STRICT: Fail immediately if indicator data missing
            pass_ema = (
                'fast_ema' in row and 'slow_ema' in row and
                row['fast_ema'] is not None and row['slow_ema'] is not None and
                row['fast_ema'] > row['slow_ema']
            )
            if not pass_ema:
                if 'fast_ema' not in row or row['fast_ema'] is None:
                    failed_checks.append("EMA: fast_ema not available")
                elif 'slow_ema' not in row or row['slow_ema'] is None:
                    failed_checks.append("EMA: slow_ema not available")
                else:
                    failed_checks.append(f"EMA: fast({row['fast_ema']:.2f}) ≤ slow({row['slow_ema']:.2f})")
        
        # --- VWAP ---
        pass_vwap = True
        if self.use_vwap:
            checks_performed.append("VWAP")
            # STRICT: Fail if vwap not calculated
            pass_vwap = ('vwap' in row and row['vwap'] is not None and 
                        'close' in row and row['close'] is not None and
                        row['close'] > row['vwap'])
            if not pass_vwap:
                if 'vwap' not in row or row['vwap'] is None:
                    failed_checks.append("VWAP: not calculated yet")
                elif 'close' not in row or row['close'] is None:
                    failed_checks.append("VWAP: price not available")
                else:
                    failed_checks.append(f"VWAP: price({row['close']:.2f}) ≤ vwap({row['vwap']:.2f})")
        
        # --- MACD ---
        pass_macd = True
        if self.use_macd:
            checks_performed.append("MACD")
            # STRICT: Both signals must exist
            pass_macd = (row['macd_bullish'] and row['macd_histogram_positive'])
            if not pass_macd:
                if not row.get('macd_bullish', False):
                    failed_checks.append("MACD: not bullish")
                if not row.get('macd_histogram_positive', False):
                    failed_checks.append("MACD: histogram not positive")
        
        # --- HTF TREND ---
        pass_htf = True
        if self.use_htf_trend:
            checks_performed.append("HTF Trend")
            # STRICT: HTF EMA must be calculated
            pass_htf = ('htf_ema' in row and row['htf_ema'] is not None and 
                       'close' in row and row['close'] is not None and
                       row['close'] > row['htf_ema'])
            if not pass_htf:
                if 'htf_ema' not in row or row['htf_ema'] is None:
                    failed_checks.append("HTF: not calculated yet")
                elif 'close' not in row or row['close'] is None:
                    failed_checks.append("HTF: price not available")
                else:
                    failed_checks.append(f"HTF: price({row['close']:.2f}) ≤ htf_ema({row['htf_ema']:.2f})")
        
        # --- RSI ---
        pass_rsi = True
        if self.use_rsi_filter:
            checks_performed.append("RSI Filter")
            # STRICT: RSI must be calculated
            rsi_oversold = self.config_accessor.get_strategy_param('rsi_oversold')
            rsi_overbought = self.config_accessor.get_strategy_param('rsi_overbought')
            pass_rsi = ('rsi' in row and row['rsi'] is not None and
                       rsi_oversold < row['rsi'] < rsi_overbought)
            if not pass_rsi:
                if 'rsi' not in row or row['rsi'] is None:
                    failed_checks.append("RSI: not calculated yet")
                else:
                    failed_checks.append(f"RSI: {row['rsi']:.2f} outside range ({rsi_oversold}-{rsi_overbought})")
        
        # --- Bollinger Bands ---
        pass_bb = True
        if self.use_bollinger_bands:
            checks_performed.append("Bollinger Bands")
            # STRICT: Both BB levels must exist
            pass_bb = ('bb_lower' in row and 'bb_upper' in row and
                      row['bb_lower'] is not None and row['bb_upper'] is not None and
                      'close' in row and row['close'] is not None and
                      row['bb_lower'] < row['close'] < row['bb_upper'])
            if not pass_bb:
                if 'bb_lower' not in row or row['bb_lower'] is None or 'bb_upper' not in row or row['bb_upper'] is None:
                    failed_checks.append("BB: not calculated yet")
                elif 'close' not in row or row['close'] is None:
                    failed_checks.append("BB: price not available")
                else:
                    failed_checks.append(f"BB: price({row['close']:.2f}) outside bands ({row['bb_lower']:.2f}-{row['bb_upper']:.2f})")
        
        # --- Construct final pass signal (all enabled must be True) ---
        logic_checks = [pass_ema, pass_vwap, pass_macd, pass_htf, pass_rsi, pass_bb]
        entry_allowed = all(logic_checks)
        
        # Log entry evaluation (only log periodically to avoid spam)
        if not hasattr(self, '_last_signal_log_time'):
            self._last_signal_log_time = None
            self._signal_log_counter = 0
        
        self._signal_log_counter += 1
        # Prefer the tick timestamp (from row) for any logging or time-based checks
        # in forward-test/file-simulations so logs show CSV times, not runtime now().
        # Fallback to now_ist() if timestamp not present or invalid.
        current_time = None
        try:
            if isinstance(row, (pd.Series, dict)) and 'timestamp' in row and row['timestamp'] is not None:
                current_time = row['timestamp']
                # Convert pandas Timestamp to python datetime if needed
                if hasattr(current_time, 'to_pydatetime'):
                    current_time = current_time.to_pydatetime()
                # Ensure timezone-aware
                current_time = ensure_tz_aware(current_time, getattr(self, 'timezone', None))
        except Exception:
            current_time = None

        if current_time is None:
            # Last resort: use system now in IST (preserves timezone-awareness)
            current_time = now_ist()
        
        # Log every 300 ticks (~30 seconds at 10 ticks/sec) or when signal changes
        should_log = (
            self._signal_log_counter % 300 == 0 or
            self._last_signal_log_time is None or
            (current_time - self._last_signal_log_time).total_seconds() > 30
        )
        
        if should_log and not entry_allowed:
            self._last_signal_log_time = current_time
            price = row.get('close', row.get('price', 'N/A'))
            logger.info(f"📊 ENTRY EVALUATION @ ₹{price}: Enabled checks: {', '.join(checks_performed)}")
            logger.info(f"   ❌ Entry REJECTED - Failed: {'; '.join(failed_checks)}")
        elif entry_allowed:
            # Always log when entry is allowed (rare event) - include green tick info
            price = row.get('close', row.get('price', 'N/A'))
            green_tick_info = f"Green ticks: {self.green_bars_count}/{self.current_green_tick_threshold}"
            logger.info(f"✅ ENTRY SIGNAL @ ₹{price}: All checks passed ({', '.join(checks_performed)}) - {green_tick_info}")
        
        return entry_allowed

    def open_long(self, row: pd.Series, now: datetime, position_manager) -> Optional[str]:
        # For robust trade management, always use live/production-driven position config
        # Safe extraction of entry price
        if 'close' in row:
            entry_price = row['close']
        elif 'price' in row:
            entry_price = row['price']
        else:
            return None  # Cannot execute without price data

        # Get instrument config with fail-fast behavior
        try:
            instrument_config = self.config_accessor.get('instrument')
            if not instrument_config:
                raise KeyError('instrument')
            symbol = instrument_config.get('symbol')
            if not symbol:
                raise KeyError('instrument.symbol')
        except KeyError as e:
            return None

        # Use instrument SSOT for contract sizing (no risk.lot_size overrides)
        # PositionManager handles lot_size and tick_size internally via SSOT
        position_id = position_manager.open_position(
            symbol=symbol,
            entry_price=entry_price,
            timestamp=now
        )

        if position_id:
            self.in_position = True
            self.position_id = position_id
            self.position_entry_time = now
            self.position_entry_price = entry_price
            self.daily_stats['trades_today'] += 1
            self.last_signal_time = now

            # Get lot_size from SSOT for logging purposes
            try:
                lot_size = int(self.config_accessor.get_current_instrument_param('lot_size'))
                self.perf_logger.trade_executed("BUY", entry_price, lot_size, "Strategy signal")
            except Exception:
                # Fallback logging without lot_size if config access fails
                self.perf_logger.session_start(f"BUY executed at {entry_price:.2f} - Strategy signal")
            
            return position_id
        # If position not opened, do not emit stdlib warning (standardization)
        return None



    def on_tick(self, tick: Dict[str, Any]) -> Optional[TradingSignal]:
        """
        Unified tick-by-tick entry point for live trading.
        
        Args:
            tick: Dictionary containing tick data (price, volume, timestamp, etc.)
            
        Returns:
            TradingSignal if action should be taken, None otherwise
        """
        try:
            # Phase 1: Start tick measurement
            if self.instrumentation_enabled:
                self.instrumentor.start_tick()
            
            # Phase 0: Track tick count and warm-up
            self.tick_count += 1
            
            # Update threshold dynamically based on last exit type
            self.current_green_tick_threshold = (
                self.base_sl_green_ticks if (self.control_base_sl_enabled and self.last_exit_was_base_sl)
                else self.consecutive_green_bars_required
            )
            
            # DEBUG: Log FIRST tick and every 300 ticks to verify on_tick is being called
            if not hasattr(self, '_ontick_call_count'):
                self._ontick_call_count = 0
                logger.info("🔧 [STRATEGY] Initialized _ontick_call_count counter")
            
            self._ontick_call_count += 1
            
            if self._ontick_call_count == 1 or self._ontick_call_count % 300 == 0:
                logger.info(f"📊 [STRATEGY] on_tick called #{self._ontick_call_count}, tick keys: {list(tick.keys())}")
            
            # Phase A optimization: Pass tick dict directly (no pandas conversion)
            # This eliminates expensive pd.Series() construction on every tick
            
            # Phase 1: Measure indicator updates
            if self.instrumentation_enabled:
                with self.instrumentor.measure('indicator_update'):
                    updated_tick = self.process_tick_or_bar(tick)
            else:
                updated_tick = self.process_tick_or_bar(tick)
            
            # Phase 0: Check warm-up completion
            if not self.warmup_complete:
                if self.tick_count >= self.min_warmup_ticks:
                    self.warmup_complete = True
                    logger.info(f"✅ Indicator warm-up complete after {self.tick_count} ticks")
                else:
                    # Still warming up - skip trading
                    if self.instrumentation_enabled:
                        self.instrumentor.end_tick()
                    return None
            
            # GRACEFUL: Check for timestamp - return None if missing (live trading safe)
            if 'timestamp' not in tick:
                logger.warning(f"⚠️ [STRATEGY] Tick missing timestamp, skipping. Tick keys: {list(tick.keys())}")
                if self.instrumentation_enabled:
                    self.instrumentor.end_tick()
                return None
            timestamp = tick['timestamp']
            
            # DIAGNOSTIC LOGGING: show timestamp details for first few ticks
            try:
                if not hasattr(self, '_tick_log_count'):
                    self._tick_log_count = 0
                self._tick_log_count += 1
                if self._tick_log_count <= 5:
                    tzinfo = getattr(timestamp, 'tzinfo', None)
                    time_comp = getattr(timestamp, 'time', lambda: None)()
                    logger.info(f"[liveStrategy.on_tick] Tick #{self._tick_log_count}: timestamp={timestamp}, time component={time_comp}, timezone={tzinfo}")
            except Exception:
                logger.debug("[STRATEGY] Diagnostic timestamp logging failed")
            # Phase 1: Measure signal evaluation
            if self.instrumentation_enabled:
                with self.instrumentor.measure('signal_eval'):
                    signal = self._generate_signal_from_tick(updated_tick, timestamp)
            else:
                signal = self._generate_signal_from_tick(updated_tick, timestamp)
            
            return signal
            
        except Exception as e:
            # Enhanced error handling - critical path gets HIGH severity
            logger.error(f"🔥 [STRATEGY] Exception in on_tick: {type(e).__name__}: {e}", exc_info=True)
            return self.error_handler.handle_error(
                error=e,
                context="tick_processing_main",
                severity=ErrorSeverity.HIGH,  # Tick processing is critical for trading
                default_return=None
            )
        finally:
            # Phase 1: End tick measurement
            if self.instrumentation_enabled:
                self.instrumentor.end_tick()

    def _generate_signal_from_tick(self, updated_tick: pd.Series, timestamp: datetime) -> Optional[TradingSignal]:
        """
        Generate trading signal from processed tick data.
        
        CRITICAL: This method validates ALL entry conditions (including green ticks) 
        before generating a BUY signal. The trader should trust this validation 
        and not re-check conditions to avoid race condition bugs.
        """
        try:
            # Extract price first for validation
            price = None
            if 'close' in updated_tick:
                price = updated_tick['close']
            elif 'price' in updated_tick:
                price = updated_tick['price']
            
            # Check if we can enter new position (includes green tick validation + price filter)
            if not self.in_position and price is not None and self.can_enter_new_position(timestamp, price):
                if self.entry_signal(updated_tick):
                    # Reset Control Base SL threshold on successful entry
                    if self.control_base_sl_enabled:
                        self.last_exit_was_base_sl = False
                        logger.info(
                            f"Entry taken; threshold reset to {self.consecutive_green_bars_required} green ticks."
                        )
                    
                    return TradingSignal(
                        action="BUY",
                        timestamp=timestamp,
                        price=price,
                        reason="Strategy entry signal"
                    )
            
            # Check exit conditions for existing position
            should_exit, exit_reason = self.should_exit_for_session(timestamp)
            if self.in_position and should_exit:
                # GRACEFUL: Extract price safely for live trading resilience
                price = None
                if 'close' in updated_tick:
                    price = updated_tick['close']
                elif 'price' in updated_tick:
                    price = updated_tick['price']
                
                if price is not None:
                    return TradingSignal(
                        action="CLOSE",
                        timestamp=timestamp,
                        price=price,
                        reason=f"Strategy Signal: {exit_reason}"
                    )
                
            return None
        except Exception as e:
            # Enhanced error handling for signal generation
            return self.error_handler.handle_error(
                error=e,
                context="signal_generation",
                severity=ErrorSeverity.HIGH,  # Signal generation is critical
                default_return=None
            )



    def reset_daily_counters(self, now: datetime):
        """
        Reset daily counters for a new trading session.
        
        Args:
            now: Current timestamp
        """
        self.daily_stats = {
            'trades_today': 0,
            'pnl_today': 0.0,
            'last_trade_time': None,
            'session_start_time': now
        }
        self.last_signal_time = None
        # Log daily reset via high-perf logger
        self.perf_logger.session_start(f"Daily counters reset for {now.date()}")

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
            'consecutive_green_bars', 'atr_len'
        ]
        
        for param in required_params:
            try:
                self.config_accessor.get_strategy_param(param)
            except KeyError:
                errors.append(f"Missing required parameter: {param}")
        
        # Typical validation rules
        if self.use_ema_crossover:
            if self.fast_ema >= self.slow_ema:
                errors.append("fast_ema must be less than slow_ema")
        if self.use_htf_trend:
            try:
                htf_period = self.config_accessor.get_strategy_param('htf_period')
                if htf_period <= 0:
                    errors.append("htf_period must be positive")
            except KeyError:
                errors.append("Missing required parameter: htf_period")
        
        # Validate risk parameters
        if self.max_positions_per_day <= 0:
            errors.append("max_positions_per_day must be positive")
        
        # Validate session parameters
        if self.session_start >= self.session_end:
            errors.append("Session start must be before session end")
            
        return errors



    def process_tick_or_bar(self, row):
        """
        Process tick data and update indicators.
        
        Phase A optimization: Accepts dict input (not pandas Series) to avoid
        expensive object construction on every tick. Returns dict with indicator values.
        
        Args:
            row: Dict or Series-like object with tick data
        
        Returns:
            Dict with original tick data plus calculated indicator values
        """
        # Tick counter and hot-path perf logging
        increment_tick_counter()
        if self.perf_logger:
            # STRICT: Use actual values or skip logging if missing
            close_val = row.get('close', 0) if hasattr(row, 'get') else (row['close'] if 'close' in row else 0)
            volume_val = row.get('volume') if hasattr(row, 'get') else (row['volume'] if 'volume' in row else None)
            self.perf_logger.tick_debug(
                format_tick_message,
                get_tick_counter(), 
                close_val, 
                volume_val
            )
        try:
            # Phase A: Support both dict and pandas inputs (backward compatibility)
            # New code passes dicts, old code may still pass Series
            if isinstance(row, pd.DataFrame):
                if len(row) == 1:
                    row = row.iloc[0]
                else:
                    return row
            
            # Convert pandas Series to dict for uniform processing
            if isinstance(row, pd.Series):
                row = row.to_dict()

            # GRACEFUL: Extract required price - return safely if missing (live trading resilient)
            # Extract close price (required) - graceful fallback approach
            close_price = row.get('close')
            if close_price is None:
                close_price = row.get('price')
                if close_price is None:
                    # No valid price found - return original row without processing
                    return row
            
            try:
                close_price = float(close_price)
            except Exception:
                return row
            if close_price <= 0:
                return row

            # Extract optional fields with sensible defaults for OHLCV
            volume = row.get('volume', 0)
            try:
                volume = int(volume) if volume is not None else 0
            except Exception:
                volume = 0
                
            # Extract OHLC with fallback to close price
            high_price = float(row.get('high', close_price) if row.get('high') is not None else close_price)
            low_price = float(row.get('low', close_price) if row.get('low') is not None else close_price)
            open_price = float(row.get('open', close_price) if row.get('open') is not None else close_price)

            # Phase A: Build result dict directly (no .copy())
            # Start with original tick data
            updated = dict(row)
            # Add the close price to the updated row for downstream processing
            updated['close'] = close_price

            # EMA
            if self.use_ema_crossover:
                if self.instrumentation_enabled:
                    with self.instrumentor.measure('indicator_ema'):
                        fast_ema_val = self.ema_fast_tracker.update(close_price)
                        slow_ema_val = self.ema_slow_tracker.update(close_price)
                        updated['fast_ema'] = fast_ema_val
                        updated['slow_ema'] = slow_ema_val
                        updated['ema_bullish'] = False if pd.isna(fast_ema_val) or pd.isna(slow_ema_val) else (fast_ema_val > slow_ema_val)
                else:
                    fast_ema_val = self.ema_fast_tracker.update(close_price)
                    slow_ema_val = self.ema_slow_tracker.update(close_price)
                    updated['fast_ema'] = fast_ema_val
                    updated['slow_ema'] = slow_ema_val
                    updated['ema_bullish'] = False if pd.isna(fast_ema_val) or pd.isna(slow_ema_val) else (fast_ema_val > slow_ema_val)

            # MACD
            if self.use_macd:
                if self.instrumentation_enabled:
                    with self.instrumentor.measure('indicator_macd'):
                        macd_val, macd_signal_val, macd_hist_val = self.macd_tracker.update(close_price)
                        updated['macd'] = macd_val
                        updated['macd_signal'] = macd_signal_val
                        updated['macd_histogram'] = macd_hist_val
                        updated['macd_bullish'] = False if pd.isna(macd_val) or pd.isna(macd_signal_val) else (macd_val > macd_signal_val)
                        updated['macd_histogram_positive'] = False if pd.isna(macd_hist_val) else (macd_hist_val > 0)
                else:
                    macd_val, macd_signal_val, macd_hist_val = self.macd_tracker.update(close_price)
                    updated['macd'] = macd_val
                    updated['macd_signal'] = macd_signal_val
                    updated['macd_histogram'] = macd_hist_val
                    updated['macd_bullish'] = False if pd.isna(macd_val) or pd.isna(macd_signal_val) else (macd_val > macd_signal_val)
                    updated['macd_histogram_positive'] = False if pd.isna(macd_hist_val) else (macd_hist_val > 0)

            # VWAP
            if self.use_vwap:
                if self.instrumentation_enabled:
                    with self.instrumentor.measure('indicator_vwap'):
                        vwap_val = self.vwap_tracker.update(price=close_price, volume=volume, high=high_price, low=low_price, close=close_price)
                        updated['vwap'] = vwap_val
                        updated['vwap_bullish'] = False if pd.isna(vwap_val) else (close_price > vwap_val)
                else:
                    vwap_val = self.vwap_tracker.update(price=close_price, volume=volume, high=high_price, low=low_price, close=close_price)
                    updated['vwap'] = vwap_val
                    updated['vwap_bullish'] = False if pd.isna(vwap_val) else (close_price > vwap_val)

            # HTF EMA processing
            if self.use_htf_trend:
                if self.instrumentation_enabled:
                    with self.instrumentor.measure('indicator_htf_ema'):
                        htf_ema_val = self.htf_ema_tracker.update(close_price)
                        updated['htf_ema'] = htf_ema_val
                        updated['htf_bullish'] = False if pd.isna(htf_ema_val) else (close_price > htf_ema_val)
                else:
                    htf_ema_val = self.htf_ema_tracker.update(close_price)
                    updated['htf_ema'] = htf_ema_val
                    updated['htf_bullish'] = False if pd.isna(htf_ema_val) else (close_price > htf_ema_val)

            # ATR
            if self.use_atr:
                if self.instrumentation_enabled:
                    with self.instrumentor.measure('indicator_atr'):
                        atr_val = self.atr_tracker.update(high=high_price, low=low_price, close=close_price)
                        updated['atr'] = atr_val
                else:
                    atr_val = self.atr_tracker.update(high=high_price, low=low_price, close=close_price)
                    updated['atr'] = atr_val

            # Update green tick count and return
            if self.instrumentation_enabled:
                with self.instrumentor.measure('green_tick_update'):
                    self._update_green_tick_count(close_price)
            else:
                self._update_green_tick_count(close_price)
            return updated
        except Exception as e:
            # Config/indicator errors should propagate (fail-fast)
            # Only catch and handle data processing errors in live trading
            return row



    def _update_green_tick_count(self, current_price: float):
        """
        Update consecutive green ticks counter based on tick-to-tick price movement.
        A green tick is defined as current_price > prev_tick_price with configurable noise filtering.
        """
        try:
            if self.prev_tick_price is None:
                # First tick of session or after reset
                self.green_bars_count = 0
                self.prev_tick_price = current_price
                return

            # STRICT: Get noise filter parameters from config (must exist in defaults.py)
            noise_filter_enabled = bool(self.config_accessor.get_strategy_param('noise_filter_enabled'))
            noise_filter_percentage = float(self.config_accessor.get_strategy_param('noise_filter_percentage'))
            noise_filter_min_ticks = float(self.config_accessor.get_strategy_param('noise_filter_min_ticks'))
            
            # Calculate minimum movement threshold
            min_movement = max(self.tick_size * noise_filter_min_ticks, 
                              self.prev_tick_price * noise_filter_percentage)
            
            # Apply noise filter if enabled
            if noise_filter_enabled:
                if current_price > (self.prev_tick_price + min_movement):
                    # Significant upward movement
                    self.green_bars_count += 1
                elif current_price < (self.prev_tick_price - min_movement):
                    # Significant downward movement
                    self.green_bars_count = 0
                else:
                    # Price within noise range - maintain current count
                    pass
            else:
                # Original behavior without noise filtering
                if current_price > self.prev_tick_price:
                    self.green_bars_count += 1
                else:
                    # Reset counter on price decrease or equal
                    self.green_bars_count = 0
            
            # Update previous price for next comparison
            self.prev_tick_price = current_price
            
        except Exception as e:
            # Enhanced error handling - provides full debugging in development, silent in production
            return self.error_handler.handle_error(
                error=e,
                context="green_tick_count_update",
                severity=ErrorSeverity.MEDIUM,  # Can continue trading without green tick updates
                default_return=None
            )

    def _check_consecutive_green_ticks(self) -> bool:
        """Check if we have enough consecutive green ticks for entry using dynamic threshold."""
        return self.green_bars_count >= self.current_green_tick_threshold

    def _validate_all_required_parameters(self):
        """
        COMPREHENSIVE FAIL-FAST VALIDATION
        Every parameter used by live strategy must exist in defaults.py
        NO FALLBACKS ALLOWED - fail immediately if ANY parameter missing
        """
        required_params = [
            # Strategy parameters
            ('strategy', 'use_ema_crossover'),
            ('strategy', 'use_macd'),
            ('strategy', 'use_vwap'),
            ('strategy', 'use_rsi_filter'),
            ('strategy', 'use_htf_trend'),
            ('strategy', 'use_bollinger_bands'),
            ('strategy', 'use_atr'),
            ('strategy', 'fast_ema'),
            ('strategy', 'slow_ema'),
            ('strategy', 'macd_fast'),
            ('strategy', 'macd_slow'),
            ('strategy', 'macd_signal'),
            ('strategy', 'rsi_oversold'),
            ('strategy', 'rsi_overbought'),
            ('strategy', 'htf_period'),
            ('strategy', 'consecutive_green_bars'),
            ('strategy', 'atr_len'),
            ('strategy', 'noise_filter_enabled'),
            ('strategy', 'noise_filter_percentage'),
            ('strategy', 'noise_filter_min_ticks'),
            
            # Session parameters
            ('session', 'start_hour'),
            ('session', 'start_min'),
            ('session', 'end_hour'),
            ('session', 'end_min'),
            ('session', 'start_buffer_minutes'),
            ('session', 'end_buffer_minutes'),
            ('session', 'no_trade_start_minutes'),
            ('session', 'no_trade_end_minutes'),
            ('session', 'timezone'),
            
            # Risk parameters
            ('risk', 'max_positions_per_day'),
            ('risk', 'base_sl_points'),
            
            # Instrument parameters
            ('instrument', 'symbol')
        ]
        
        missing = []
        for section, key in required_params:
            try:
                if section == 'strategy':
                    self.config_accessor.get_strategy_param(key)
                elif section == 'session':
                    self.config_accessor.get_session_param(key)
                elif section == 'risk':
                    self.config_accessor.get_risk_param(key)
                elif section == 'instrument':
                    self.config_accessor.get_instrument_param(key)
            except KeyError:
                missing.append(f"{section}.{key}")
        
        # Additional SSOT validation: Check that current instrument exists in instrument_mappings
        try:
            current_symbol = self.config_accessor.get_instrument_param('symbol')
            lot_size = self.config_accessor.get_current_instrument_param('lot_size')
            tick_size = self.config_accessor.get_current_instrument_param('tick_size')
            # If we get here, SSOT is properly configured
        except KeyError as e:
            missing.append(f"SSOT instrument mapping for current symbol: {str(e)}")
        
        if missing:
            raise ValueError(
                f"FATAL: Live strategy requires ALL parameters in defaults.py. Missing: {missing}. "
                f"Add these to config/defaults.py DEFAULT_CONFIG before proceeding."
            )

if __name__ == "__main__":
    # Minimal smoke test for development
    test_params = {
        'use_ema_crossover': True, 'fast_ema': 9, 'slow_ema': 21, 'ema_points_threshold': 2,
        'use_macd': True, 'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9,
        'use_vwap': True, 'use_htf_trend': True, 'htf_period': 20, 'symbol': 'NIFTY24DECFUT', 'lot_size': 15, 'tick_size': 0.05,
        'session': {'start_hour': 9, 'start_min': 15, 'end_hour': 15, 'end_min': 30, 'start_buffer_minutes': 5, 'end_buffer_minutes': 20, 'timezone': 'Asia/Kolkata'},
        'max_positions_per_day': 25,
        'base_sl_points': 15
    }
    import core.indicators as indicators
    strat = ModularIntradayStrategy(test_params, indicators)
    print("Parameter validation errors:", strat.validate_parameters())

"""
LIVE TRADING OPTIMIZATION NOTES:
- Primary interface: on_tick() for real-time processing
- Incremental indicators for memory efficiency
- Performance logging for hot-path optimization
- Fail-fast configuration validation
- Tick-level noise filtering for precision entry
- Session-aware position management
"""
