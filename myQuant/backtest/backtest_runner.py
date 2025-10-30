# backtest_runner.py - Core backtesting engine

"""
Refactored backtest runner implementing "normalize early, standardize everywhere" principle.

Key Changes:
- All data normalization moved to single entry point
- Uses centralized DataNormalizer for consistent data quality
- Downstream components assume data is pre-validated
- Improved error handling and logging
- Better separation of concerns

Data Flow:
Raw Data â†’ DataNormalizer â†’ Indicators â†’ Strategy â†’ Position Manager
          â†‘
    SINGLE SOURCE OF TRUTH
    (All normalization happens here)

    """

import importlib
import logging
import sys
import traceback
import pandas as pd
import os
import inspect
import json
from datetime import datetime, time
from types import MappingProxyType
from typing import Tuple, Any, Dict
import logging
import importlib
import inspect
import os
import pandas as pd

from ..utils.logger import setup_from_config, HighPerfLogger
import logging
# Module-level logger for utility functions only
logger = logging.getLogger(__name__)

# Time utilities (timezone handling, buffer helpers)
from ..utils.time_utils import ensure_tz_aware, is_within_session, apply_buffer_to_time

# Data loader used by the centralized loader / runner
from ..utils.simple_loader import load_data_simple

# Position manager used by the runner
from ..core.position_manager import PositionManager

# Strategy and results
from ..core.researchStrategy import ModularIntradayStrategy
from .results import BacktestResults

# legacy smart_logger removed
# (module-level stdlib logger removed — use self.perf_logger inside BacktestRunner)

def get_available_indicator_columns(df, max_columns=6):
    """Get available indicator columns for logging in priority order"""
    priority_order = ['close', 'fast_ema', 'slow_ema', 'vwap', 'macd', 'rsi', 'htf_ema', 'atr', 'volume']
    available = [col for col in priority_order if col in df.columns]
    return available[:max_columns]

def safe_column_selection(df, desired_columns):
    """Return only columns that actually exist in the DataFrame"""
    return [col for col in desired_columns if col in df.columns]

try:
    # Verify the function exists and comes from time_utils
    assert callable(ensure_tz_aware)
    assert 'time_utils' in inspect.getmodule(ensure_tz_aware).__name__
    logger.info(f"âœ… ensure_tz_aware verified from {inspect.getmodule(ensure_tz_aware).__name__}")
except (AssertionError, AttributeError, ImportError) as e:
    logger.error(f"âŒ ensure_tz_aware verification failed: {e}")
    # Raise immediately to prevent hard-to-diagnose errors later
    raise ImportError("Critical timezone function not properly available")

# NOTE: YAML config loading removed from the BacktestRunner.
# The BacktestRunner MUST receive a frozen MappingProxyType config produced
# by the GUI workflow (create_config_from_defaults -> validate_config -> freeze_config).
# If a CLI YAML-driven workflow is required later, implement a separate helper
# script (outside of this module) that produces the frozen config and passes it
# into BacktestRunner. Keeping YAML loading out of this module enforces the
# single-source-of-truth and prevents divergent runtime configuration.

def get_strategy(config: dict):
    """
    Load strategy module with full configuration.

    Hard-coded to use researchStrategy for backtesting.
    """
    strat_mod = importlib.import_module("core.researchStrategy")
    
    # FIXED: Maintain consistent nested structure, no more flattening
    logger.info("NESTED CONFIG: Using consistent nested configuration structure")
    logger.info(f"Strategy parameters found: {list(config['strategy'].keys())}")
     
    # FIXED: researchStrategy only takes frozen_config, not indicators module
    return strat_mod.ModularIntradayStrategy(config)

class BacktestRunner:
    """
    Backtesting engine for testing strategies against historical data.
    """
    
    def __init__(self, config: MappingProxyType, data_path: str = ""):
        # Enforce frozen MappingProxyType per workflow
        if not isinstance(config, MappingProxyType):
            raise ValueError("BacktestRunner requires a frozen MappingProxyType config produced by the GUI workflow.")

        self.config = config
        self.data_path = data_path

        # Require explicit logging configuration from the frozen config.
        if "logging" not in self.config:
            raise RuntimeError("Missing 'logging' section in config. Provide logging settings in the frozen config.")

        # Initialize logging from frozen config (idempotent)
        setup_from_config(self.config)
        
        # STRICT: initialize HighPerfLogger (requires frozen MappingProxyType & prior setup).
        # Let exceptions propagate so misconfiguration is detected immediately.
        self.perf_logger = HighPerfLogger(__name__, self.config)
        
        # High-performance logger for backtest events
        self.perf_logger = HighPerfLogger(__name__, self.config)
        # All event logging uses self.perf_logger for consistency

        # Create strict config accessor (will raise KeyError on missing keys)
        from ..utils.config_helper import ConfigAccessor
        self.config_accessor = ConfigAccessor(self.config)
        
        # Use performance logger for initialization messages
        self.perf_logger.session_start(f"BacktestRunner initialized")

    # Example usage points: in the hot loops, call self.smart_logger.log_progress_smart(...)
    # or self.smart_logger.log_signal_event(...) only if self.smart_logger is not None.

    def _prepare_data(self):
        """Load and prepare data for backtesting"""
        # Load data (let exceptions propagate - do not log exceptions here)
        # (Use perf_logger for important lifecycle events in the hot/class context)
        self.data = load_data_simple(self.data_path, process_as_ticks=True)

        if self.data is None or self.data.empty:
            raise ValueError(f"No data loaded from {self.data_path}")

        # Use performance logger for data loading status
        self.perf_logger.session_start(
            f"Data loaded: {len(self.data)} rows from {self.data.index[0]} to {self.data.index[-1]}"
        )
        # Strategy will handle all indicator calculation incrementally
        self.perf_logger.session_start("Data prepared successfully - strategy will calculate indicators incrementally")

    def run(self):
        """
        Run backtest with current configuration.
        
        Returns:
            BacktestResults object with performance metrics
        """
        try:
            self.perf_logger.session_start("Starting backtest run")
            
            # Prepare data
            self._prepare_data()
            
            # Create strategy and position manager with callback
            self.strategy = ModularIntradayStrategy(self.config)
            self.position_manager = PositionManager(self.config, strategy_callback=self.strategy.on_position_exit)
            
            # Run backtest logic and get trades/performance
            trades_df, performance = self._run_backtest_logic()
            
            # --- FIX: Populate Results with trades from trades_df ---
            self.results = BacktestResults(self.position_manager.initial_capital)
            self.results.set_config(self.config)  # Pass config for additional info
            if not trades_df.empty:
                for _, trade in trades_df.iterrows():
                    self.results.add_trade({
                        'entry_time': trade['entry_time'],
                        'exit_time': trade['exit_time'],
                        'entry_price': trade['entry_price'],
                        'exit_price': trade['exit_price'],
                        'quantity': trade['quantity'],
                        'pnl': trade['net_pnl'],
                        'commission': trade['commission'],
                        'exit_reason': trade['exit_reason'],
                    })
            # --- END FIX ---
            
            # Now export results as before
            results_dir = self.config['backtest']['results_dir']
            # self.results.export_to_csv(output_dir=results_dir)
            self.results.export_to_excel(output_dir=results_dir)
            self.perf_logger.session_end("Backtest completed successfully")
            return self.results
        except Exception:
            # Let exceptions propagate (no logger.exception per standardization)
            raise
    
    def _run_backtest_logic(self):
        """
        Core backtest loop logic
        """
        self.perf_logger.session_start("STARTING BACKTEST WITH NORMALIZED DATA PIPELINE")
        
        # Load configuration
        config = self.config
        
        # Extract parameters
        strategy_params = config['strategy']
        session_params = config['session']
        risk_params = config['risk']
        instrument_params = config['instrument']
        capital = config['capital']['initial_capital']
        
        # Add a default for the symbol if it's missing
        if 'symbol' not in instrument_params:
            instrument_params['symbol'] = 'DEFAULT_SYMBOL'
            logger.warning(f"Instrument symbol not found in config. Using default: '{instrument_params['symbol']}'")
     
        # FIXED: Maintain consistent nested structure throughout
        logger.info("=== NESTED CONFIG STRUCTURE MAINTAINED ===")
        for section, params in config.items():
            if isinstance(params, dict):
                logger.info(f"Section '{section}': {len(params)} parameters")
     
        # Ensure session parameters are consistent
        session_params = config["session"]
        if "intraday_end_min" not in session_params:
            session_params["intraday_end_min"] = 30  # Consistent with NSE close time
        if "exit_before_close" not in session_params:
            session_params["exit_before_close"] = 20  # Default value
        if "timezone" not in session_params:
            session_params["timezone"] = "Asia/Kolkata"

        # Initialize components with nested config
        strategy = get_strategy(config)
        
        # CRITICAL: Validate strategy interface before proceeding
        required_methods = ['can_open_long', 'open_long', 'calculate_indicators', 'should_exit']
        missing_methods = [method for method in required_methods if not hasattr(strategy, method)]
        if missing_methods:
            logger.error(f"CRITICAL: Strategy missing required methods: {missing_methods}")
            return pd.DataFrame(), {"error": f"Strategy validation failed: missing {missing_methods}"}
        
        logger.info("Strategy interface validation passed")
        
        # FIXED: Pass nested config directly to PositionManager
        logger.info("=== NESTED CONFIG PASSED TO POSITION MANAGER ===")
        
        # Validate configuration completeness
        config_validation = _validate_complete_config(config)
        if not config_validation["valid"]:
            logger.error(f"Configuration validation failed: {config_validation['errors']}")
            return pd.DataFrame(), {"error": f"Config validation failed: {config_validation['errors']}"}
        
        # Validate critical sections exist
        required_sections = ['strategy', 'risk', 'capital', 'instrument', 'session']
        missing_sections = [s for s in required_sections if s not in config]
        if missing_sections:
            logger.warning(f"âŒ MISSING config sections: {missing_sections}")
     
        # Initialize PositionManager with nested config (strategy callback not needed for this use case)
        position_manager = PositionManager(config)
        
        # Skip data loading if df_normalized is provided
        df_normalized = self.data
        if df_normalized is None:
            logger.info("Loading data with centralized loader...")
            df_normalized, quality_report = load_and_normalize_data(self.data_path, process_as_ticks=True)
            logger.info(f"Loaded and normalized data. Shape: {df_normalized.shape}. Time range: {df_normalized.index.min()} to {df_normalized.index.max()}")
            if df_normalized.empty:
                logger.error("CRITICAL: DataFrame is empty after normalization. Cannot proceed.")
                return pd.DataFrame(), position_manager.get_performance_summary()
        else:
            # Create simple quality report for pre-loaded data
            total_rows = len(df_normalized)
            sample_indices = []
            for chunk_start in range(0, total_rows, 1000):
                chunk_end = min(chunk_start + 1000, total_rows)
                if chunk_end - chunk_start >= 5:
                    step = (chunk_end - chunk_start) // 5
                    chunk_sample = [chunk_start + i * step for i in range(5)]
                else:
                    chunk_sample = list(range(chunk_start, chunk_end))
                sample_indices.extend(chunk_sample)
    
        # Create quality report WITH sample_indices
        quality_report = type('SimpleQualityReport', (), {
            'total_rows': len(df_normalized),
            'rows_processed': len(df_normalized),
            'rows_dropped': 0,
            'issues_found': {},
            'sample_indices': sample_indices  # Add this critical field
        })

        # Get session configuration
        session_config = config['session']
        
        # Apply user-defined session filtering before processing
        if df_normalized is not None and not df_normalized.empty:
            logger.info("Applying session filtering to data based on user configuration")
            df_normalized = filter_data_by_session(df_normalized, session_config)
            if df_normalized.empty:
                logger.error("No data remains after session filtering. Check session settings.")
                return pd.DataFrame(), position_manager.get_performance_summary()
        
        # TRUE INCREMENTAL PROCESSING - No chunking, no batch processing
        logger.info("=== PROCESSING INDICATORS INCREMENTALLY (ROW-BY-ROW) ===")
        logger.info(f"Processing {len(df_normalized)} rows incrementally without chunking")
        
        df_with_indicators = strategy.calculate_indicators(df_normalized)
        logger.info(f"Indicators calculated successfully. DataFrame shape: {df_with_indicators.shape}")
        logger.info("=== INCREMENTAL PROCESSING COMPLETE ===")
        
        if hasattr(quality_report, 'sample_indices'):
            logger.info("=" * 80)
            logger.info("STAGE 3: AFTER INDICATOR CALCULATION (Same Rows)")
            logger.info("=" * 80)
            
            for i, idx in enumerate(quality_report.sample_indices[:25]):
                if idx < len(df_with_indicators):
                    row_data = df_with_indicators.iloc[idx]
                    
                    # Build indicator summary string
                    indicators = []
                    if 'fast_ema' in row_data and not pd.isna(row_data['fast_ema']):
                        indicators.append(f"FastEMA={row_data['fast_ema']:.3f}")
                    if 'slow_ema' in row_data and not pd.isna(row_data['slow_ema']):
                        indicators.append(f"SlowEMA={row_data['slow_ema']:.3f}")
                    if 'vwap' in row_data and not pd.isna(row_data['vwap']):
                        indicators.append(f"VWAP={row_data['vwap']:.3f}")
                    if 'macd' in row_data and not pd.isna(row_data['macd']):
                        indicators.append(f"MACD={row_data['macd']:.4f}")
                    if 'rsi' in row_data and not pd.isna(row_data['rsi']):
                        indicators.append(f"RSI={row_data['rsi']:.1f}")
                    
                    indicator_str = ", ".join(indicators[:4]) if indicators else "No indicators"
                    
                    # Signal status
                    signals = []
                    if 'ema_bullish' in row_data:
                        signals.append(f"EMA_Bull={row_data['ema_bullish']}")
                    if 'vwap_bullish' in row_data:
                        signals.append(f"VWAP_Bull={row_data['vwap_bullish']}")
                    
                    signal_str = ", ".join(signals) if signals else "No signals"
                    
                    logger.info(f"Ind  Row {idx:6d} (Sample {i+1:2d}): "
                               f"Time={row_data.name}, "
                               f"Close={row_data.get('close', 'N/A'):8.2f}, "
                               f"[{indicator_str}], Signals=[{signal_str}]")
        
        # Log final indicator status for verification
        logger.info("Indicators calculated. Final 5 rows:")
        # Dynamically build column list based on what's available
        log_columns = get_available_indicator_columns(df_with_indicators)
        if len(log_columns) > 1:  # More than just 'close'
            logger.info(f"\n{df_with_indicators[log_columns].tail(5).to_string()}")
        else:
            # Use safe column selection for fallback logging
            fallback_columns = safe_column_selection(df_with_indicators, ['close', 'volume'])
            logger.info(f"\n{df_with_indicators[fallback_columns].tail(5).to_string()}")

        # Quick EMA diagnostic
        if 'fast_ema' in df_with_indicators.columns and 'slow_ema' in df_with_indicators.columns:
            fast_above_slow = (df_with_indicators['fast_ema'] > df_with_indicators['slow_ema']).sum()
            total_rows = len(df_with_indicators)
            logger.info(f"EMA DIAGNOSTIC: {fast_above_slow}/{total_rows} rows have fast > slow ({fast_above_slow/total_rows*100:.1f}%)")
        
        # VWAP diagnostics (only if VWAP is enabled)
        if 'vwap' in df_with_indicators.columns:
            above_vwap = (df_with_indicators['close'] > df_with_indicators['vwap']).sum()
            logger.info(f"VWAP DIAGNOSTIC: {above_vwap}/{total_rows} rows have price > VWAP ({above_vwap/total_rows*100:.1f}%)")
        
        # MACD diagnostics (only if MACD is enabled)
        if 'macd' in df_with_indicators.columns and 'macd_signal' in df_with_indicators.columns:
            macd_bullish = (df_with_indicators['macd'] > df_with_indicators['macd_signal']).sum()
            logger.info(f"MACD DIAGNOSTIC: {macd_bullish}/{total_rows} rows have MACD > Signal ({macd_bullish/total_rows*100:.1f}%)")
        
        # Show sample of ALL available indicators
        available_for_sample = safe_column_selection(df_with_indicators, ['fast_ema', 'slow_ema', 'vwap', 'macd', 'macd_signal', 'rsi', 'htf_ema'])
        if available_for_sample:
            sample = df_with_indicators[available_for_sample].dropna().head(10)
            logger.info(f"Sample indicator values:\n{sample.to_string()}")

        # Backtest execution loop
        logger.info("Starting backtest execution...")
        position_id = None
        in_position = False
         
        processed_bars = 0
        signals_detected = 0
        entries_attempted = 0
        trades_executed = 0
         
        for timestamp, row in df_with_indicators.iterrows():
            processed_bars += 1
            
            # ENSURE timezone awareness for timestamp
            now = ensure_tz_aware(timestamp)

            # Check if session end reached using position manager
            if position_manager.should_exit_for_session_end(now):
                # Close all positions and terminate
                for pos_id in list(position_manager.positions.keys()):
                    position_manager.close_position_full(pos_id, row['close'], now, "Exit Buffer")
                logger.info(f"Session end reached at {now.time()}, closing all positions")
                break  # Stop processing completely
            
            # OPTIMIZATION: Skip processing if no more trading opportunities
            # If not in position and can't open new positions, skip entry checks
            if not in_position and hasattr(strategy, 'daily_stats') and hasattr(strategy, 'max_positions_per_day'):
                if strategy.daily_stats.get('trades_today', 0) >= strategy.max_positions_per_day:
                    # Only process position management, skip entry logic
                    position_manager.process_positions(row, now)
                    continue

            # For debugging the first few iterations
            if processed_bars <= 1:
                logger.info(f"Processing timestamp: {now} (tzinfo: {now.tzinfo})")
            
            # Process positions with timezone-aware timestamp
            position_manager.process_positions(row, now)
            
            # Entry Logic: only if not already in position and conditions meet
            if not in_position and strategy.can_open_long(row, now):
                signals_detected += 1
                entries_attempted += 1

                # Unified signal detection logging
                self.perf_logger.session_start(f"SIGNAL DETECTED at {now}: Price={row['close']:.2f}")
                
                position_id = strategy.open_long(row, now, position_manager)
                in_position = position_id is not None
                
                if in_position:
                    # FIXED: Log detailed trade execution info
                    position = position_manager.positions.get(position_id)
                    if position:
                        lots = position.current_quantity // position.lot_size if position.lot_size > 0 else position.current_quantity
                        logger.info(f"TRADE EXECUTED: {lots} lots ({position.current_quantity} units) @ {row['close']:.2f}")
                    trades_executed += 1
                    # Unified trade execution logging
                    pos = position_manager.positions.get(position_id, {})
                    qty = getattr(pos, 'current_quantity', getattr(pos, 'quantity', getattr(pos, 'initial_quantity', 0))) if pos else 0
                    self.perf_logger.session_start(f"TRADE EXECUTED: {position_id} @ {row['close']:.2f} Qty={qty}")
                else:
                    logger.warning(f"TRADE FAILED: Signal detected but position not opened")
            
            # Exit Logic: PositionManager handles trailing stops, TPs, SLs and session-end exits
            if in_position:
                position_manager.process_positions(row, now)
                
                # FIXED: Use correct method name
                if strategy.should_exit(row, now, position_manager):
                    last_price = row['close']
                    strategy.handle_exit(position_id, last_price, now, position_manager, reason="Strategy Exit")
                    in_position = False
                    position_id = None
                    logger.debug(f"Strategy exit at {now} @ {last_price:.2f}")
            else:
                # Still allow PositionManager to process positions in edge cases
                position_manager.process_positions(row, now)
            
            # Reset position state if position closed by PositionManager
            if position_id and position_id not in position_manager.positions:
                in_position = False
                position_id = None
            
            # FIXED: Add periodic progress logging
            if processed_bars % 1000 == 0:
                # Unified progress logging
                self.perf_logger.session_start(f"Progress: {processed_bars:,} bars processed, Signals: {signals_detected}, Entries: {entries_attempted}, Trades: {trades_executed}")
        
        logger.info(f"Backtest completed: {signals_detected} signals, {trades_executed} trades executed")
        
        # Defensive: flatten any still-open positions at backtest end
        if position_id and position_id in position_manager.positions:
            last_price = df_with_indicators.iloc[-1]['close']
            now = df_with_indicators.index[-1]
            strategy.handle_exit(position_id, last_price, now, position_manager, reason="End of Backtest")
            logger.info(f"Closed final position at backtest end @ {last_price:.2f}")
        
        # Gather and print summary
        trades = position_manager.get_trade_history()
        performance = position_manager.get_performance_summary()
        
        logger.info("=" * 60)
        logger.info("BACKTEST SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Data Quality:")
        logger.info(f"  Total input rows: {quality_report.total_rows}")
        logger.info(f"  Processed rows: {quality_report.rows_processed}")
        logger.info(f"  Data quality: {quality_report.rows_processed/quality_report.total_rows*100:.1f}%")
        logger.info("")
        logger.info(f"Trading Performance:")
        logger.info(f"  Total Trades: {performance['total_trades']}")
        logger.info(f"  Win Rate: {performance['win_rate']:.2f}%")
        logger.info(f"  Total P&L: {performance['total_pnl']:.2f}")
        logger.info(f"  Avg Win: {performance['avg_win']:.2f}")
        logger.info(f"  Avg Loss: {performance['avg_loss']:.2f}")
        logger.info(f"  Profit Factor: {performance['profit_factor']:.2f}")
        logger.info(f"  Max Win: {performance['max_win']:.2f}")
        logger.info(f"  Max Loss: {performance['max_loss']:.2f}")
        logger.info(f"  Total Commission: {performance['total_commission']:.2f}")
        logger.info("=" * 60)
        
        # Save trade log CSV file
        if trades:
            trades_df = pd.DataFrame(trades)
            trades_df.to_csv("backtest_trades.csv", index=False)
            logger.info("Trade log written to backtest_trades.csv")
        else:
            logger.warning("No trades executed during backtest")
            trades_df = pd.DataFrame()
        
        return trades_df, performance

    def _save_results(self):
        """Save trades and equity curve to CSV using BacktestResults."""
        if self.results is not None:
            output_dir = "results"
            self.results.export_to_csv(output_dir)
            logger.info(f"Results exported to {output_dir}/")
        else:
            logger.warning("No results to save.")

    def _maybe_log_stage_samples(self, tag: str, df_stage: pd.DataFrame, sample_count: int = 10):
        """
        Helper: if smart logger present, emit concise data-loading event;
        otherwise print a small set of sample rows for debugging (legacy behavior).
        Non-destructive: does not change pipeline.
        """
        # Simplified stage logging via performance logger
        rows_loaded = len(df_stage)
        first_ts = df_stage.index.min() if hasattr(df_stage, 'index') else None  
        last_ts = df_stage.index.max() if hasattr(df_stage, 'index') else None
        self.perf_logger.session_start(f"Stage {tag}: {rows_loaded} rows loaded, range: {first_ts} to {last_ts}")

    def _process_row_signal_and_trade(self, row: pd.Series, now: datetime,
                                      strategy, position_manager, instrument_params: dict,
                                      in_position: bool, processed_counters: dict):
        """
        Small helper to encapsulate per-row signal detection and trade execution logging.
        Returns updated in_position and position_id (or None).
        processed_counters: dict with keys 'signals_detected','entries_attempted','trades_executed'
        """
        position_id = None
        try:
            if not in_position and strategy.can_open_long(row, now):
                processed_counters['signals_detected'] += 1
                processed_counters['entries_attempted'] += 1

                # Unified signal logging
                self.perf_logger.session_start(f"SIGNAL DETECTED at {now}: Price={row.get('close', 'N/A')}")

                position_id = strategy.open_long(row, now, position_manager)
                in_position = position_id is not None

                if in_position:
                    processed_counters['trades_executed'] += 1
                    # Unified trade logging
                    pos = position_manager.positions.get(position_id, {})
                    qty = getattr(pos, 'current_quantity', getattr(pos, 'quantity', getattr(pos, 'initial_quantity', 0))) if pos else 0
                    self.perf_logger.session_start(f"TRADE EXECUTED: {position_id} @ {row.get('close', 'N/A')} Qty={qty}")
            # Exit handling (if in_position)
            elif in_position:
                # Strategy decides when to exit; runner logs closes via smart_logger when present
                exit_decision = strategy.should_exit(row, now) if hasattr(strategy, 'should_exit') else False
                if exit_decision:
                    try:
                        position_to_close = position_manager.get_open_position_id_for_instrument(instrument_params.get('symbol'))
                    except Exception:
                        position_to_close = None

                    if position_to_close:
                        exit_price = float(row.get('close', float('nan')))
                        closed = position_manager.close_position_full(position_to_close, exit_price, now, reason="Runner Exit")
                        if closed:
                            # Unified position close logging
                            pos = position_manager.positions.get(position_to_close, {})
                            pnl = getattr(pos, 'last_realized_pnl', getattr(pos, 'pnl', 0))
                            qty = int(getattr(pos, 'current_quantity', getattr(pos, 'quantity', getattr(pos, 'initial_quantity', 0))) if pos else 0)
                            self.perf_logger.session_start(f"POSITION CLOSED by runner: {position_to_close} @ {exit_price} PnL={pnl:.2f} Qty={qty}")
                            in_position = False
        except Exception as e:
            logger.exception(f"Error in row processing: {e}")
        return in_position, position_id

# ...existing code...

def run_backtest_debug(strategy, data, position_manager, risk_manager, start_date, end_date):
    """Enhanced backtest with production-level debugging."""
    
    # Add at the beginning of the method
    logger.info(f"Starting backtest with {len(data)} rows")
    logger.info(f"Strategy type: {type(strategy)}")
    
    # Verify strategy has required methods
    required_methods = ['can_open_long', 'open_long', 'calculate_indicators']
    missing_methods = [m for m in required_methods if not hasattr(strategy, m)]
    if missing_methods:
        logger.error(f"CRITICAL: Strategy missing methods: {missing_methods}")
        return {}
    
    signals_checked = 0
    entries_attempted = 0
    
    for i, (timestamp, row) in enumerate(data.iterrows()):
        try:
            # Debug every 100 rows
            if i % 100 == 0:
                logger.info(f"Processing row {i}/{len(data)}: {timestamp}")
            
            # Check if strategy can open long
            can_open = strategy.can_open_long(row, timestamp)
            signals_checked += 1
            
            if can_open:
                logger.info(f"SIGNAL DETECTED at {timestamp}: Price={row['close']}")
                entries_attempted += 1
                
                # Attempt to open position
                position_id = strategy.open_long(row, timestamp, position_manager)
                if position_id:
                    logger.info(f"TRADE EXECUTED: Position {position_id} opened")
                else:
                    logger.warning(f"TRADE FAILED: Could not open position")
            
            # Add this debug every 100 rows
            if i % 100 == 0:
                logger.info(f"Processed {i} rows, Signals checked: {signals_checked}, Entries attempted: {entries_attempted}")
        
        except Exception as e:
            logger.error(f"Error processing row {i}: {e}")
            continue
    
    logger.info("Backtest debug completed")
    return {}

def load_and_normalize_data(data_path: str, process_as_ticks: bool = False) -> Tuple[pd.DataFrame, Any]:
    """
    Centralized data loading function with comprehensive row tracking.
    """
    logger.info(f"Loading data from: {data_path}")
    
    if not os.path.isfile(data_path):
        raise FileNotFoundError(f"Data file not found: {data_path}")

    # === STAGE 1: RAW DATA LOADING ===
    df_raw = load_data_simple(data_path, process_as_ticks)
    
    # Calculate sample rows (5 rows every 1000 rows)
    total_rows = len(df_raw)
    sample_indices = []
    
    for chunk_start in range(0, total_rows, 1000):
        chunk_end = min(chunk_start + 1000, total_rows)
        # Select 5 evenly distributed rows within each 1000-row chunk
        if chunk_end - chunk_start >= 5:
            step = (chunk_end - chunk_start) // 5
            chunk_sample = [chunk_start + i * step for i in range(5)]
        else:
            # If chunk has less than 5 rows, take all
            chunk_sample = list(range(chunk_start, chunk_end))
        sample_indices.extend(chunk_sample)
    
    # Remove duplicates and ensure indices are within bounds
    sample_indices = sorted(list(set(idx for idx in sample_indices if idx < total_rows)))
    
    logger.info("=" * 80)
    logger.info("STAGE 1: RAW DATA SAMPLE (5 rows per 1000)")
    logger.info("=" * 80)
    logger.info(f"Sampling {len(sample_indices)} rows from {total_rows} total rows")
    
    for i, idx in enumerate(sample_indices[:25]):
        row_data = df_raw.iloc[idx]
        logger.info(f"Raw Row {idx:6d} (Sample {i+1:2d}): "
                   f"Time={row_data.name}, "
                   f"Close={row_data.get('close', 'N/A'):8.2f}, "
                   f"Volume={row_data.get('volume', 'N/A'):6.0f}")
    
    # === STAGE 2: AFTER NORMALIZATION ===
    df_normalized = df_raw  # Your normalization happens in simple_loader
    
    logger.info("=" * 80)
    logger.info("STAGE 2: AFTER NORMALIZATION (Same Rows)")
    logger.info("=" * 80)
    
    for i, idx in enumerate(sample_indices[:25]):
        if idx < len(df_normalized):
            row_data = df_normalized.iloc[idx]
            logger.info(f"Norm Row {idx:6d} (Sample {i+1:2d}): "
                       f"Time={row_data.name}, "
                       f"Close={row_data.get('close', 'N/A'):8.2f}, "
                       f"Volume={row_data.get('volume', 'N/A'):6.0f}")
    
    # Store sample indices for later use in indicator stage
    quality_report = type('DetailedQualityReport', (), {
        'total_rows': len(df_normalized),
        'rows_processed': len(df_normalized),
        'rows_dropped': 0,
        'issues_found': {},
        'sample_indices': sample_indices
    })
    
    # Add basic validation (previously handled by DataNormalizer)
    if df_normalized.isnull().any().any():
        logger.warning(f"Dataset contains {df_normalized.isnull().sum().sum()} missing values")
    
    # Check for negative prices (previously handled by DataNormalizer)
    neg_prices = (df_normalized['close'] <= 0).sum() if 'close' in df_normalized.columns else 0
    if neg_prices > 0:
        logger.warning(f"Dataset contains {neg_prices} negative or zero prices")
    
    logger.info("=== COMPLETE DATASET ANALYSIS ===")
    logger.info(f"Dataset shape: {df_normalized.shape}")
    logger.info(f"Time range: {df_normalized.index.min()} to {df_normalized.index.max()}")
    logger.info(f"Total duration: {df_normalized.index.max() - df_normalized.index.min()}")

    # Show time distribution
    time_groups = df_normalized.groupby(df_normalized.index.hour).size()
    logger.info("Hourly tick distribution:")
    for hour, count in time_groups.items():
        logger.info(f"  Hour {hour:02d}: {count:,} ticks")

    # Show first and last 10 rows with timestamps
    logger.info("First 10 rows:")
    logger.info(f"\n{df_normalized.head(10)[['close', 'volume']].to_string()}")
    logger.info("Last 10 rows:")
    logger.info(f"\n{df_normalized.tail(10)[['close', 'volume']].to_string()}")
    
    return df_normalized, quality_report

def add_indicator_signals_to_chunk(chunk_df: pd.DataFrame, config: Dict[str, Any]):
    """
    Add indicator signals to a processed chunk.
    
    Args:
        chunk_df: DataFrame chunk with computed indicators
        config: Strategy configuration
    """
    from ..core.indicators import (
        calculate_ema_crossover_signals,
        calculate_macd_signals,
        calculate_vwap_signals,
        calculate_htf_signals,
        calculate_rsi_signals,
    )

    if config.get('use_ema_crossover', False) and 'fast_ema' in chunk_df.columns:
        ema_signals = calculate_ema_crossover_signals(
            chunk_df['fast_ema'],
            chunk_df['slow_ema'],
        )
        chunk_df = chunk_df.join(ema_signals)
    
    # MACD Signals
    if config.get('use_macd', False) and 'macd' in chunk_df.columns:
        macd_df = pd.DataFrame({
            'macd': chunk_df['macd'],
            'signal': chunk_df['macd_signal'],
            'histogram': chunk_df['histogram']
        })
        macd_signals = calculate_macd_signals(macd_df)
        chunk_df = chunk_df.join(macd_signals)
    
    # VWAP Signals
    if config['use_vwap'] and 'vwap' in chunk_df.columns:
        vwap_signals = calculate_vwap_signals(chunk_df['close'], chunk_df['vwap'])
        chunk_df = chunk_df.join(vwap_signals)
    
    # HTF Trend Signals
    if config['use_htf_trend'] and 'htf_ema' in chunk_df.columns:
        htf_signals = calculate_htf_signals(chunk_df['close'], chunk_df['htf_ema'])
        chunk_df = chunk_df.join(htf_signals)
    
    # RSI Signals
    if config['use_rsi_filter'] and 'rsi' in chunk_df.columns:
        rsi_signals = calculate_rsi_signals(
            chunk_df['rsi'],
            config['rsi_overbought'],
            config['rsi_oversold']
        )
        chunk_df = chunk_df.join(rsi_signals)
    
    return chunk_df

def _validate_complete_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Comprehensive configuration validation.
    """
    validation = {"valid": True, "errors": [], "warnings": []}
    
    # Required sections
    required_sections = ['strategy', 'risk', 'capital', 'instrument', 'session']
    for section in required_sections:
        if section not in config:
            validation["errors"].append(f"Missing required section: {section}")
            validation["valid"] = False
    
    # Strategy-specific validation
    if 'strategy' in config:
        strategy_config = config['strategy']
        
        # Validate EMA parameters if EMA is enabled
        if strategy_config['use_ema_crossover']:
            fast_ema = strategy_config['fast_ema']
            slow_ema = strategy_config['slow_ema']
            if fast_ema >= slow_ema:
                validation["errors"].append("Fast EMA must be less than Slow EMA")
                validation["valid"] = False
    
    # Session validation
    if 'session' in config:
        session_config = config['session']
        start_hour = session_config['start_hour']
        end_hour = session_config['end_hour']
        if start_hour >= end_hour:
            validation["errors"].append("Session start hour must be before end hour")
            validation["valid"] = False
    
    return validation

def validate_system_integrity():
    """
    Validate that all required components are properly configured.
    """
    validation_results = {
        "valid": True,
        "errors": [],
        "warnings": []
    }
    
    # Check if all required modules can be imported
    required_modules = [
        'core.position_manager',
        'utils.simple_loader', 
        'utils.time_utils',
        'utils.config_helper',
        'core.indicators'
    ]
    
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            validation_results["errors"].append(f"Cannot import {module_name}: {e}")
            validation_results["valid"] = False
    
    return validation_results

def filter_data_by_session(df, session_config):
    """
    Filter dataframe to only include rows within the user-defined session
    """
    if df.empty:
        return df
    
    # Create session start and end times
    start_time = time(session_config["start_hour"], session_config["start_min"])
    end_time = time(session_config["end_hour"], session_config["end_min"])
    
    # Filter dataframe
    mask = df.index.map(lambda x: start_time <= x.time() <= end_time)
    filtered_df = df.loc[mask]
    
    logger.info(f"Filtered data from {len(df)} to {len(filtered_df)} rows based on user session timing")
    return filtered_df

# Remove the __main__ CLI convenience builder: the BacktestRunner is now strict and requires
# a frozen MappingProxyType (produced by create_config_from_defaults() -> validate_config() -> freeze_config()).
# If you need a CLI test helper, create a separate script that performs the create->validate->freeze flow,
# then constructs BacktestRunner with the frozen config.

"""
CONFIGURATION PARAMETER NAMING CONVENTION:
- This module uses 'config' for all configuration objects
- Function parameter: run_backtest(config, ...)
- Internal usage: strategy_params = config.get('strategy', {})
- Session params: session_params = config.get('session', {})

INTERFACE COMPATIBILITY:
- get_strategy() function maintains 'params' parameter name for interface consistency
- Strategy classes internally use 'config' but receive 'params' from this factory function

CRITICAL: Do not change 'config' variable naming without updating:
- All config.get() calls throughout this file
- Position manager parameter passing
- Session parameter extraction logic
"""
