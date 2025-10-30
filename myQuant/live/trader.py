"""
live/trader.py

Unified forward-test/live simulation runner.
- Loads configuration and strategy logic.
- Connects to SmartAPI for live tick data (or mock data if in simulation).
- Processes incoming bar/tick data with your core strategy and position manager.
- Simulates all trades: never sends real orders.
"""

import time
import logging
import importlib
import pandas as pd
from types import MappingProxyType
from ..core.position_manager import PositionManager
from .broker_adapter import BrokerAdapter
from .forward_test_results import ForwardTestResults
from ..utils.time_utils import now_ist
from ..utils.config_helper import validate_config, freeze_config, create_config_from_defaults

# Module-level logger
logger = logging.getLogger(__name__)

# Phase 1.5: Pre-convergence instrumentation
_pre_convergence_instrumentor = None

def set_pre_convergence_instrumentor(instrumentor):
    """Set the Phase 1.5 instrumentor for pre-convergence measurements."""
    global _pre_convergence_instrumentor
    _pre_convergence_instrumentor = instrumentor
    logger.info(f"🔬 [TRADER] Instrumentor SET: {instrumentor is not None}, Type: {type(instrumentor).__name__ if instrumentor else 'None'}")

def get_strategy(config):
    """Get strategy instance with frozen MappingProxyType config - strict validation"""
    if not isinstance(config, MappingProxyType):
        raise TypeError(f"get_strategy requires frozen MappingProxyType config, got {type(config)}")
    
    strat_module = importlib.import_module("myQuant.core.liveStrategy")
    ind_mod = importlib.import_module("myQuant.core.indicators")
    return strat_module.ModularIntradayStrategy(config, ind_mod)

class LiveTrader:
    def __init__(self, config_path: str = None, config_dict: dict = None, frozen_config: MappingProxyType = None, dialog_text: str = None):
        """Initialize LiveTrader with frozen config validation
        
        Args:
            config_path: Path to YAML config file (legacy)
            config_dict: Raw dict config (legacy) 
            frozen_config: MappingProxyType from GUI workflow (preferred)
            dialog_text: Configuration dialog text from GUI (REQUIRED for results export)
        """
        # Accept frozen config directly from GUI (preferred path)
        if frozen_config is not None:
            if not isinstance(frozen_config, MappingProxyType):
                raise TypeError(f"frozen_config must be MappingProxyType, got {type(frozen_config)}")
            config = frozen_config
            
            # Verify credentials are loaded for live trading
            live_params = config.get("live", {})
            logger.info(f"LiveTrader initialized with credentials: "
                       f"api_key={'LOADED' if live_params.get('api_key') else 'EMPTY'}, "
                       f"client_code={'LOADED' if live_params.get('client_code') else 'EMPTY'}")
        elif config_dict is not None:
            # Legacy path - validate and freeze raw dict
            validation = validate_config(config_dict)
            if not validation.get('valid', False):
                errors = validation.get('errors', ['Unknown validation error'])
                raise ValueError(f"Invalid config: {errors}")
            config = freeze_config(config_dict)
        else:
            # File path - use defaults.py as SSOT (config_path parameter kept for legacy compatibility)
            raw_config = create_config_from_defaults()
            validation = validate_config(raw_config)
            if not validation.get('valid', False):
                errors = validation.get('errors', ['Unknown validation error'])
                raise ValueError(f"Invalid config from defaults: {errors}")
            config = freeze_config(raw_config)
        
        self.config = config
        
        # Pass complete frozen config to strategy (not partial params)
        self.strategy = get_strategy(config)
        
        # Pass frozen config directly to PositionManager with strategy callback
        self.position_manager = PositionManager(config, strategy_callback=self.strategy.on_position_exit)
        self.broker = BrokerAdapter(config)  # Pass frozen config downstream
        
        # 🔍 DEBUG: Log dialog_text before passing to ForwardTestResults
        logger.info(f"🔍 LiveTrader creating ForwardTestResults - dialog_text type: {type(dialog_text)}, length: {len(dialog_text) if dialog_text else 0}")
        if dialog_text:
            logger.info(f"✅ Passing dialog_text to ForwardTestResults - first 100 chars: {dialog_text[:100]}")
        else:
            logger.warning(f"⚠️ dialog_text is empty or None before passing: {repr(dialog_text)}")
        
        self.results_exporter = ForwardTestResults(config, self.position_manager, now_ist(), dialog_text=dialog_text)
        self.is_running = False
        self.active_position_id = None
        
        # Hybrid mode: Support both polling and direct callbacks (Wind-style)
        self.use_direct_callbacks = False  # Toggle for Wind-style performance
        self.tick_count = 0
        self.last_price = None  # Track last seen price for heartbeat logging
        self._last_no_tick_log = None

    def stop(self):
        """Stop the forward test session gracefully"""
        logger = logging.getLogger(__name__)
        logger.info("🛑 Stop requested - ending forward test session")
        self.is_running = False
        
        # Close any open positions
        if self.active_position_id:
            self.close_position("Stop Requested")
        
        # Disconnect broker
        try:
            self.broker.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting broker: {e}")
        
        # Finalize and export results automatically
        self.results_exporter.finalize()
        try:
            filename = self.results_exporter.export_to_excel()
            logger.info(f"Forward test results automatically exported to: {filename}")
        except Exception as e:
            logger.error(f"Failed to export results: {e}")
        
        logger.info("✅ Forward test session stopped successfully")

    def start(self, run_once=False, result_box=None, performance_callback=None):
        """Start trading session with hybrid mode support
        
        Supports two modes:
        1. Polling Mode (default): Queue-based tick polling (~70ms latency)
        2. Callback Mode (Wind-style): Direct callbacks (~50ms latency)
        
        Toggle with self.use_direct_callbacks = True
        """
        self.is_running = True
        self.performance_callback = performance_callback
        logger = logging.getLogger(__name__)
        
        # Initialize NaN tracking - shared by both modes
        self.nan_streak = 0
        self.nan_threshold = self.config['strategy']['nan_streak_threshold']
        self.nan_recovery_threshold = self.config['strategy']['nan_recovery_threshold']
        self.consecutive_valid_ticks = 0
        self.result_box = result_box
        self.run_once = run_once
        
        # Register callback if Wind-style mode enabled
        if self.use_direct_callbacks:
            self.broker.on_tick_callback = self._on_tick_direct
            logger.info("⚡ Direct callback mode enabled (Wind-style, ~50ms latency)")
        else:
            logger.info("📊 Polling mode enabled (Queue-based, ~70ms latency)")
        
        # Connect broker (WebSocket initialization happens here)
        self.broker.connect()
        logger.info("🟢 Forward testing session started - TRUE TICK-BY-TICK PROCESSING")
        
        # Choose execution path based on mode
        if self.use_direct_callbacks:
            self._run_callback_loop()
        else:
            self._run_polling_loop(run_once, result_box, performance_callback)
    
    def _run_polling_loop(self, run_once, result_box, performance_callback):
        """Original polling-based trading loop (backwards compatible)"""
        logger = logging.getLogger(__name__)
        nan_streak = 0
        nan_threshold = self.nan_threshold
        nan_recovery_threshold = self.nan_recovery_threshold
        consecutive_valid_ticks = 0
        tick_count = 0
        
        try:
            while self.is_running:
                # STEP 1: Get individual tick (no bar aggregation)
                tick = self.broker.get_next_tick()
                if not tick:
                    # Check if file simulation is complete
                    if hasattr(self.broker, 'file_simulator') and self.broker.file_simulator:
                        if hasattr(self.broker.file_simulator, 'completed') and self.broker.file_simulator.completed:
                            logger.info("File simulation completed - ending trading session")
                            break
                    
                    # Intelligent sleep based on data source
                    if hasattr(self.broker, 'streaming_mode') and self.broker.streaming_mode:
                        # WebSocket mode: shorter sleep as ticks arrive asynchronously
                        time.sleep(0.05)  # 50ms for WebSocket
                        # Debug: Log every 200 empty tick cycles but throttle to max once per 30 seconds
                        if tick_count % 200 == 0:
                            current_time = time.time()
                            # Initialize _last_no_tick_log if not present
                            if not hasattr(self, '_last_no_tick_log'):
                                self._last_no_tick_log = current_time
                            # Log if enough time has passed since last log
                            if (current_time - self._last_no_tick_log) >= 30:
                                logger.info(f"[DEBUG] WebSocket active but no ticks received (cycle {tick_count})")
                                self._last_no_tick_log = current_time
                    else:
                        # Polling mode: longer sleep to respect rate limits
                        time.sleep(1.0)   # 1 second for polling
                    continue
                
                # Check stop condition more frequently during processing
                if not self.is_running:
                    logger.info("Stop requested during tick processing")
                    break
                
                # GUI responsiveness: Brief yield every 100 ticks to keep GUI responsive
                tick_count += 1
                if tick_count % 100 == 0:
                    time.sleep(0.001)  # Minimal yield to allow GUI updates
                    # Heartbeat logging every 100 ticks
                    logger.info(f"[HEARTBEAT] Trading loop active - tick count: {tick_count}, position: {self.active_position_id is not None}")
                    # Check stop condition during GUI yield
                    if not self.is_running:
                        logger.info("Stop requested during GUI yield - exiting immediately")
                        break

                
                now = tick['timestamp'] if 'timestamp' in tick else now_ist()
                
                # STEP 2: Session end enforcement (before processing)
                if hasattr(self.strategy, "should_exit_for_session"):
                    should_exit, exit_reason = self.strategy.should_exit_for_session(now)
                    if should_exit:
                        self.close_position("Session End")
                        logger.info(f"🛑 Session ended - stopping trading: {exit_reason}")
                        logger.info("All positions flattened (if any).")
                        break
                
                # STEP 3: TRUE TICK-BY-TICK PROCESSING - Use on_tick() directly
                try:
                    signal = self.strategy.on_tick(tick)
                    
                    # Reset NaN streak on successful processing
                    nan_streak = 0
                    consecutive_valid_ticks += 1
                    
                except Exception as e:
                    # NaN threshold implementation
                    nan_streak += 1
                    consecutive_valid_ticks = 0
                    logger.warning(f"Tick processing failed (streak: {nan_streak}/{nan_threshold}): {e}")
                    
                    if nan_streak >= nan_threshold:
                        logger.error(f"NaN streak threshold ({nan_threshold}) exceeded. Stopping trading.")
                        self.close_position("NaN Threshold Exceeded")
                        break
                    continue
                
                # STEP 4: Process signal immediately if generated
                if signal:
                    current_price = tick.get('price', tick.get('ltp', 0))
                    
                    if signal.action == 'BUY' and not self.active_position_id:
                        # Trust the strategy's entry validation - signal was already generated with proper checks
                        # Create optimized tick row for position manager
                        tick_row = self._create_tick_row(tick, signal.price, now)
                        
                        self.active_position_id = self.strategy.open_long(tick_row, now, self.position_manager)
                        if self.active_position_id:
                            qty = self.position_manager.positions[self.active_position_id].current_quantity
                            logger.info(f"[TICK] ENTERED LONG at ₹{signal.price:.2f} ({qty} contracts) - {signal.reason}")
                            self._update_result_box(result_box, f"Tick BUY: {qty} @ {signal.price:.2f} ({signal.reason})")
                    
                    elif signal.action == 'CLOSE' and self.active_position_id:
                        self.close_position(f"Strategy Signal: {signal.reason}")
                        self._update_result_box(result_box, f"Tick CLOSE: @ {signal.price:.2f} ({signal.reason})")
                
                # Check stop condition before position processing
                if not self.is_running:
                    logger.info("Stop requested before position processing - exiting")
                    break
                
                # STEP 5: Position manager processes TP/SL/trail exits (if position exists)
                if self.active_position_id:
                    # Debug logging every 100 ticks when in position
                    if tick_count % 100 == 0:
                        logger.info(f"[DEBUG] Position active: {self.active_position_id} | Tick count: {tick_count} | Price: ₹{tick.get('price', 0):.2f}")
                    
                    current_price = tick.get('price', tick.get('ltp', 0))
                    current_tick_row = self._create_tick_row(tick, current_price, now)
                    
                    try:
                        self.position_manager.process_positions(current_tick_row, now)
                    except Exception as e:
                        logger.error(f"Error in position_manager.process_positions: {e}")
                        logger.exception("Position processing exception details:")
                    
                    # Check if position was closed by risk management
                    if self.active_position_id not in self.position_manager.positions:
                        logger.info("Position closed by risk management (TP/SL/trailing).")
                        self._update_result_box(result_box, f"Risk CLOSE: @ {current_price:.2f}")
                        # CRITICAL FIX: Notify strategy of position closure to reset state
                        try:
                            self.strategy.on_position_closed(self.active_position_id, "Risk Management")
                        except Exception as e:
                            # Log notification failed, but continue trading
                            logger.warning(f"Strategy notification failed: {e}")
                        self.active_position_id = None
                
                # STEP 6: Check for single-run mode
                if run_once:
                    self.is_running = False
        except KeyboardInterrupt:
            logger.info("Forward test interrupted by user.")
            self.close_position("Keyboard Interrupt")
        except Exception as e:
            logger.exception(f"Error in trading loop: {e}")
            self.close_position("Error Occurred")
        finally:
            self.broker.disconnect()
            logger.info("Session ended, data connection closed.")
            
            # Finalize and export results automatically
            self.results_exporter.finalize()
            try:
                filename = self.results_exporter.export_to_excel()
                logger.info(f"Forward test results automatically exported to: {filename}")
            except Exception as e:
                logger.error(f"Failed to export results: {e}")
    
    def _run_callback_loop(self):
        """Wind-style callback-driven trading loop (high performance)
        
        LIVE WEBSTREAM (PRIMARY): Ticks processed via _on_tick_direct callback
        FILE SIMULATION (TESTING): Delegates to separate simulation loop
        
        Expected performance: ~50ms latency (vs ~70ms with polling)
        """
        logger = logging.getLogger(__name__)
        
        # CRITICAL: Check if file simulation (completely separate workflow)
        if hasattr(self.broker, 'file_simulator') and self.broker.file_simulator:
            logger.info("📁 File simulation detected - using dedicated simulation loop")
            self._run_file_simulation_callback_mode()
            return
        
        # LIVE WEBSTREAM CALLBACK LOOP (SACROSANCT - NO SIMULATION INTERFERENCE)
        logger.info("🔥 Callback mode active - ticks processed directly as they arrive")
        
        # Check if WebSocket is available
        has_websocket = hasattr(self.broker, 'streaming_mode') and self.broker.streaming_mode
        
        try:
            while self.is_running:
                # If no WebSocket, manually poll and trigger callback
                if not has_websocket:
                    tick = self.broker.get_next_tick()
                    if tick:
                        symbol = self.config['instrument']['symbol']
                        self._on_tick_direct(tick, symbol)
                    else:
                        time.sleep(0.1)  # Brief sleep when no tick available
                
                # Heartbeat logging
                self.tick_count += 1
                if self.tick_count % 100 == 0:
                    last_price = self.broker.get_last_price()
                    price_str = f"₹{last_price:.2f}" if last_price > 0 else "N/A"
                    logger.info(f"[HEARTBEAT] Callback mode - {self.tick_count} cycles, position: {self.active_position_id is not None}, price: {price_str}")
                
                # Update GUI performance display
                if self.performance_callback and self.tick_count % 50 == 0:
                    try:
                        self.performance_callback(self)
                    except Exception as e:
                        logger.warning(f"Performance callback error: {e}")
                
                # Check for session end
                if hasattr(self.strategy, "should_exit_for_session"):
                    should_exit, exit_reason = self.strategy.should_exit_for_session(now_ist())
                    if should_exit:
                        self.close_position("Session End")
                        logger.info(f"🛑 Session ended - stopping trading: {exit_reason}")
                        logger.info("All positions flattened (if any).")
                        break
                
                # Check for single-run mode
                if self.run_once and self.tick_count > 100:
                    logger.info("Single-run mode - exiting after initial processing")
                    self.is_running = False
                
                # Minimal sleep - just for heartbeat (ticks arrive via callback)
                time.sleep(0.1)  # 100ms heartbeat interval
                
        except KeyboardInterrupt:
            logger.info("Callback mode interrupted by user")
            self.close_position("Keyboard Interrupt")
        except Exception as e:
            logger.exception(f"Error in callback loop: {e}")
            self.close_position("Error Occurred")
        finally:
            self.broker.disconnect()
            logger.info("Callback mode session ended, data connection closed.")
            
            # Finalize and export results
            self.results_exporter.finalize()
            try:
                filename = self.results_exporter.export_to_excel()
                logger.info(f"Forward test results automatically exported to: {filename}")
            except Exception as e:
                logger.error(f"Failed to export results: {e}")
    
    def _run_file_simulation_callback_mode(self):
        """Dedicated file simulation loop for callback mode testing
        
        ISOLATED from live WebStream workflow - zero interference.
        Polls data simulator and processes ticks to test callback logic.
        """
        logger = logging.getLogger(__name__)
        logger.info("📁 File simulation callback mode - testing callback logic with file data")
        
        try:
            while self.is_running:
                # Poll data simulator for next tick
                tick = self.broker.get_next_tick()
                
                if tick:
                    # Process tick through callback handler (testing callback logic)
                    self._on_tick_direct(tick, "FILE_SIM")
                    self.tick_count += 1
                    
                    # Periodic progress logging
                    if self.tick_count % 1000 == 0:
                        logger.info(f"[FILE SIM] Processed {self.tick_count} ticks, position: {self.active_position_id is not None}")
                    
                    # Update GUI performance display
                    if self.performance_callback and self.tick_count % 50 == 0:
                        try:
                            self.performance_callback(self)
                        except Exception as e:
                            logger.warning(f"Performance callback error: {e}")
                    
                    # CRITICAL: Yield to GUI thread to prevent freezing
                    # Small sleep simulates realistic tick timing and allows GUI updates
                    time.sleep(0.001)  # 1ms delay = ~1000 ticks/sec max
                else:
                    # Simulation complete
                    logger.info("📋 File simulation completed - all data processed")
                    break
                
                # Check for single-run mode
                if self.run_once and self.tick_count > 100:
                    logger.info("Single-run mode - exiting after initial processing")
                    self.is_running = False
                    break
                
        except KeyboardInterrupt:
            logger.info("File simulation interrupted by user")
            self.close_position("Keyboard Interrupt")
        except Exception as e:
            logger.exception(f"Error in file simulation callback loop: {e}")
            self.close_position("Error Occurred")
        finally:
            self.broker.disconnect()
            logger.info("File simulation session ended")
            
            # Finalize and export results
            self.results_exporter.finalize()
            try:
                filename = self.results_exporter.export_to_excel()
                logger.info(f"File simulation results exported to: {filename}")
            except Exception as e:
                logger.error(f"Failed to export results: {e}")
    
    def _on_tick_direct(self, tick, symbol):
        """Direct callback handler for Wind-style tick processing
        
        Called directly from WebSocket thread when tick arrives.
        Processes tick immediately without queuing for minimum latency.
        
        Args:
            tick: Tick data dict with price, timestamp, volume
            symbol: Symbol identifier
        """
        logger = logging.getLogger(__name__)
        
        try:
            # Phase 1.5: Start trader measurement
            global _pre_convergence_instrumentor
            if _pre_convergence_instrumentor:
                _pre_convergence_instrumentor.start_trader_tick()
            
            # Track actual tick count (separate from heartbeat)
            if not hasattr(self, '_callback_tick_count'):
                self._callback_tick_count = 0
                logger.info("🔧 [CALLBACK] Initialized _callback_tick_count counter")
            
            self._callback_tick_count += 1
            
            # Log FIRST tick and every 100 ticks to verify callback is receiving ticks
            if self._callback_tick_count == 1 or self._callback_tick_count % 100 == 0:
                logger.info(f"🔍 [CALLBACK] Processing tick #{self._callback_tick_count}, price: ₹{tick.get('price', 'N/A')}, keys: {list(tick.keys())}")
            # Add timestamp if not present
            if 'timestamp' not in tick:
                tick['timestamp'] = now_ist()
            
            now = tick['timestamp']
            
            # Update last price for heartbeat logging
            self.last_price = tick.get('price', tick.get('ltp', None))
            
            # Phase 1.5: Measure session checks
            if _pre_convergence_instrumentor:
                with _pre_convergence_instrumentor.measure_trader('session_check'):
                    # Session end check
                    if hasattr(self.strategy, "should_exit_for_session"):
                        should_exit, exit_reason = self.strategy.should_exit_for_session(now)
                        if should_exit:
                            self.close_position("Session End")
                            logger.info(f"🛑 Session ended - stopping trading: {exit_reason}")
                            logger.info("All positions flattened (if any).")
                            self.is_running = False  # Stop processing further ticks
                            _pre_convergence_instrumentor.end_trader_tick()
                            return
            else:
                # Session end check
                if hasattr(self.strategy, "should_exit_for_session"):
                    should_exit, exit_reason = self.strategy.should_exit_for_session(now)
                    if should_exit:
                        self.close_position("Session End")
                        logger.info(f"🛑 Session ended - stopping trading: {exit_reason}")
                        logger.info("All positions flattened (if any).")
                        self.is_running = False  # Stop processing further ticks
                        return
            
            # Phase 1.5: Measure signal handling preparation
            if _pre_convergence_instrumentor:
                with _pre_convergence_instrumentor.measure_trader('signal_prep'):
                    # Log strategy call
                    if self._callback_tick_count == 1 or self._callback_tick_count % 300 == 0:
                        logger.info(f"📊 [CALLBACK] Calling strategy.on_tick() for tick #{self._callback_tick_count}")
            else:
                # Log strategy call
                if self._callback_tick_count == 1 or self._callback_tick_count % 300 == 0:
                    logger.info(f"📊 [CALLBACK] Calling strategy.on_tick() for tick #{self._callback_tick_count}")
            
            # Process tick through strategy (KEEP MEASURING - this is part of pre-convergence)
            try:
                # Phase 1.5: Measure strategy call overhead (NOT strategy internals)
                if _pre_convergence_instrumentor:
                    with _pre_convergence_instrumentor.measure_trader('strategy_call'):
                        signal = self.strategy.on_tick(tick)
                else:
                    signal = self.strategy.on_tick(tick)
                
                # Log signal result for FIRST tick and occasionally
                if self._callback_tick_count == 1 or self._callback_tick_count % 300 == 0:
                    if signal:
                        logger.info(f"✅ [CALLBACK] Strategy returned signal: {signal.action} @ ₹{signal.price}")
                    else:
                        logger.info(f"⏸️ [CALLBACK] Strategy returned None (no entry signal)")
                
                # Reset NaN streak on success
                self.nan_streak = 0
                self.consecutive_valid_ticks += 1
                
            except Exception as e:
                # NaN threshold implementation
                self.nan_streak += 1
                self.consecutive_valid_ticks = 0
                logger.warning(f"Tick processing failed (streak: {self.nan_streak}/{self.nan_threshold}): {e}")
                
                if self.nan_streak >= self.nan_threshold:
                    logger.error(f"NaN streak threshold ({self.nan_threshold}) exceeded. Stopping trading.")
                    self.close_position("NaN Threshold Exceeded")
                    self.is_running = False
                
                # Phase 1.5: End trader measurement on error
                if _pre_convergence_instrumentor:
                    _pre_convergence_instrumentor.end_trader_tick()
                return
            
            # Phase 1.5: Measure signal processing
            if _pre_convergence_instrumentor:
                with _pre_convergence_instrumentor.measure_trader('signal_handling'):
                    # Handle signal immediately
                    if signal:
                        current_price = tick.get('price', tick.get('ltp', 0))
                        self.last_price = current_price
                        
                        if signal.action == 'BUY' and not self.active_position_id:
                            tick_row = self._create_tick_row(tick, signal.price, now)
                            self.active_position_id = self.strategy.open_long(tick_row, now, self.position_manager)
                            
                            if self.active_position_id:
                                qty = self.position_manager.positions[self.active_position_id].current_quantity
                                logger.info(f"[DIRECT] ENTERED LONG at ₹{signal.price:.2f} ({qty} contracts) - {signal.reason}")
                                self._update_result_box(self.result_box, f"Direct BUY: {qty} @ {signal.price:.2f} ({signal.reason})")
                        
                        elif signal.action == 'CLOSE' and self.active_position_id:
                            self.close_position(f"Strategy Signal: {signal.reason}")
                            self._update_result_box(self.result_box, f"Direct CLOSE: @ {signal.price:.2f} ({signal.reason})")
            else:
                # Handle signal immediately (no instrumentation)
                if signal:
                    current_price = tick.get('price', tick.get('ltp', 0))
                    self.last_price = current_price
                    
                    if signal.action == 'BUY' and not self.active_position_id:
                        tick_row = self._create_tick_row(tick, signal.price, now)
                        self.active_position_id = self.strategy.open_long(tick_row, now, self.position_manager)
                        
                        if self.active_position_id:
                            qty = self.position_manager.positions[self.active_position_id].current_quantity
                            logger.info(f"[DIRECT] ENTERED LONG at ₹{signal.price:.2f} ({qty} contracts) - {signal.reason}")
                            self._update_result_box(self.result_box, f"Direct BUY: {qty} @ {signal.price:.2f} ({signal.reason})")
                    
                    elif signal.action == 'CLOSE' and self.active_position_id:
                        self.close_position(f"Strategy Signal: {signal.reason}")
                        self._update_result_box(self.result_box, f"Direct CLOSE: @ {signal.price:.2f} ({signal.reason})")
            
            # Phase 1.5: Measure position management
            if _pre_convergence_instrumentor:
                with _pre_convergence_instrumentor.measure_trader('position_mgmt'):
                    # Position management (TP/SL/trailing)
                    if self.active_position_id:
                        current_price = tick.get('price', tick.get('ltp', 0))
                        self.last_price = current_price
                        current_tick_row = self._create_tick_row(tick, current_price, now)
                        
                        try:
                            self.position_manager.process_positions(current_tick_row, now)
                        except Exception as e:
                            logger.error(f"Error in position_manager.process_positions: {e}")
                        
                        # Check if position was closed by risk management
                        if self.active_position_id not in self.position_manager.positions:
                            logger.info("Position closed by risk management (TP/SL/trailing) [Direct Callback]")
                            self._update_result_box(self.result_box, f"Risk CLOSE: @ {current_price:.2f}")
                            
                            # Notify strategy
                            try:
                                self.strategy.on_position_closed(self.active_position_id, "Risk Management")
                            except Exception as e:
                                logger.warning(f"Strategy notification failed: {e}")
                            
                            self.active_position_id = None
            else:
                # Position management (TP/SL/trailing) - no instrumentation
                if self.active_position_id:
                    current_price = tick.get('price', tick.get('ltp', 0))
                    self.last_price = current_price
                    current_tick_row = self._create_tick_row(tick, current_price, now)
                    
                    # Debug log every 100 ticks when in position
                    if self._callback_tick_count % 100 == 0:
                        logger.info(f"[DEBUG] Position active: {self.active_position_id} | Tick count: {self._callback_tick_count} | Price: ₹{current_price:.2f}")
                    
                    try:
                        self.position_manager.process_positions(current_tick_row, now)
                    except Exception as e:
                        logger.error(f"Error in position_manager.process_positions: {e}")
                        logger.exception("Position processing exception details:")
                    
                    # Check if position was closed by risk management
                    if self.active_position_id not in self.position_manager.positions:
                        logger.info("Position closed by risk management (TP/SL/trailing) [Direct Callback]")
                        self._update_result_box(self.result_box, f"Risk CLOSE: @ {current_price:.2f}")
                        
                        # Notify strategy
                        try:
                            self.strategy.on_position_closed(self.active_position_id, "Risk Management")
                        except Exception as e:
                            logger.warning(f"Strategy notification failed: {e}")
                        
                        self.active_position_id = None
            
            # Phase 1.5: End trader measurement (normal completion)
            if _pre_convergence_instrumentor:
                _pre_convergence_instrumentor.end_trader_tick()
                    
        except Exception as e:
            logger.error(f"Error in direct callback handler: {e}")
            logger.exception("Direct callback exception details:")
            
            # End trader measurement on exception too
            if _pre_convergence_instrumentor:
                _pre_convergence_instrumentor.end_trader_tick()

    def close_position(self, reason: str = "Manual"):
        if self.active_position_id and self.active_position_id in self.position_manager.positions:
            last_price = self.broker.get_last_price()
            now = now_ist()
            self.position_manager.close_position_full(self.active_position_id, last_price, now, reason)
            logger = logging.getLogger(__name__)
            logger.info(f"[SIM] Position closed at {last_price} for reason: {reason}")
            # CRITICAL FIX: Notify strategy of position closure to reset state
            self.strategy.on_position_closed(self.active_position_id, reason)
            self.active_position_id = None
            
            # Update performance summary in GUI when trade completes
            if hasattr(self, 'performance_callback') and self.performance_callback:
                try:
                    self.performance_callback(self)
                except Exception as e:
                    logger.warning(f"Failed to update performance callback: {e}")

    def _create_tick_row(self, tick: dict, price: float, timestamp) -> pd.Series:
        """Create standardized tick row for position manager compatibility."""
        return pd.Series({
            'close': price,
            'high': price,
            'low': price,
            'open': price,
            'volume': tick.get('volume', 1000),
            'timestamp': timestamp
        })
    
    def _update_result_box(self, result_box, message: str):
        """Update result box with thread-safe GUI operations."""
        if result_box:
            try:
                result_box.config(state="normal")
                result_box.insert("end", f"{message}\n")
                result_box.see("end")
                result_box.config(state="disabled")
            except Exception as e:
                # GUI updates can fail in threading context
                logging.getLogger(__name__).warning(f"Result box update failed: {e}")

if __name__ == "__main__":
    import argparse
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Unified Forward Test Runner (Paper Trading)")
    parser.add_argument("--config", default="config/strategy_config.yaml", help="Config YAML path")
    args = parser.parse_args()

    bot = LiveTrader(args.config)
    bot.start()
