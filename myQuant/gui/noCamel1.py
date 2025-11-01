"""
unified_gui.py - Unified Trading Interface for both Backtesting and Live Trading

Provides a comprehensive GUI for:
- Configuring strategy parameters
- Running backtests
- Starting/stopping live trading
- Visualizing results
- Managing configurations

IMPORTANT: CollapsibleFrame Behavior Convention
==============================================
All indicators and risk components follow consistent checkbox behavior:
- CHECKED (✓) = Functionality ENABLED + Parameters VISIBLE (expanded)
- UNCHECKED (✗) = Functionality DISABLED + Parameters HIDDEN (collapsed)

This ensures uniform user experience across all sections where the checkbox
state directly controls both the logical enable/disable state AND the visual
expand/collapse state of parameter sections.
"""
import subprocess
import os
import json
import threading
import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import numpy as np
import pytz
import pandas as pd
import numpy as np
from datetime import datetime
from copy import deepcopy

from types import MappingProxyType
from typing import Dict, Any

from ..utils.config_helper import create_config_from_defaults, validate_config, freeze_config, ConfigAccessor
from ..backtest.backtest_runner import BacktestRunner
from ..utils.cache_manager import refresh_symbol_cache, load_symbol_cache
from ..live.trader import LiveTrader
from ..live.forward_test_results import ForwardTestResults

from ..config.defaults import DEFAULT_CONFIG
from ..utils.logger import setup_from_config

# Build, validate and freeze the canonical config (FAIL-FAST if defaults are invalid).
# This ensures setup_from_config receives a proper MappingProxyType.
base_cfg = create_config_from_defaults()
validation = validate_config(base_cfg)
if not validation.get('valid', False):
    errs = validation.get('errors', []) or ["Unknown validation failure"]
    raise RuntimeError("DEFAULT_CONFIG validation failed:\n" + "\n".join(errs))
frozen_cfg = freeze_config(base_cfg)
setup_from_config(frozen_cfg)
logger = logging.getLogger(__name__)


class GuiLogHandler(logging.Handler):
    """Custom log handler to display logs in GUI text widget"""
    
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        
    def emit(self, record):
        """Emit a log record to the GUI text widget"""
        try:
            msg = self.format(record)
            # Use tkinter's after method to safely update GUI from any thread
            if self.text_widget and self.text_widget.winfo_exists():
                self.text_widget.after(0, self._append_log, msg)
        except Exception:
            # Ignore errors in logging to prevent infinite loops
            pass
    
    def _append_log(self, msg):
        """Safely append log message to text widget"""
        try:
            if self.text_widget and self.text_widget.winfo_exists():
                self.text_widget.configure(state="normal")
                self.text_widget.insert(tk.END, msg + "\n")
                self.text_widget.configure(state="disabled")
                # Auto-scroll to bottom
                self.text_widget.see(tk.END)
        except Exception:
            # Ignore errors in GUI updates
            pass


class CollapsibleFrame(ttk.Frame):
    """Collapsible frame that can hide/show content"""
    
    def __init__(self, parent, title, collapsed=False, **kwargs):
        super().__init__(parent, **kwargs)
        self.collapsed = collapsed
        self.title = title
        
        # Create header frame with toggle button
        self.header_frame = ttk.Frame(self)
        self.header_frame.pack(fill='x', pady=(0,5))
        
        # Toggle button (arrow + title)  
        self.toggle_var = tk.BooleanVar(value=not collapsed)
        self.toggle_btn = ttk.Checkbutton(
            self.header_frame,
            text=f"{'▼' if not collapsed else '▶'} {self.title}",
            variable=self.toggle_var,
            command=self.toggle_content,
            style='CollapsibleHeader.TCheckbutton'
        )
        self.toggle_btn.pack(side='left')
        
        # Enable keyboard activation with Enter/Space
        self.toggle_btn.bind('<Return>', lambda e: self.toggle_content())
        self.toggle_btn.bind('<space>', lambda e: self.toggle_content())
        
        # Content frame
        self.content_frame = ttk.Frame(self)
        if not collapsed:
            self.content_frame.pack(fill='both', expand=True, padx=(20,0))
            # Set initial green color for enabled (expanded) sections
            self.toggle_btn.config(style='Enabled.TCheckbutton')
        else:
            # Set initial standard color for disabled (collapsed) sections
            self.toggle_btn.config(style='CollapsibleHeader.TCheckbutton')
    
    def toggle_content(self):
        """Toggle visibility of content frame and update colors"""
        if self.toggle_var.get():
            self.content_frame.pack(fill='both', expand=True, padx=(20,0))
            self.toggle_btn.config(text=f"▼ {self.title}", style='Enabled.TCheckbutton')
            # Update all child labels to dark green when enabled
            self._update_child_colors('Enabled.TLabel')
        else:
            self.content_frame.pack_forget()
            self.toggle_btn.config(text=f"▶ {self.title}", style='CollapsibleHeader.TCheckbutton')
            # Revert child labels to standard color when disabled
            self._update_child_colors('Parameter.TLabel')
    
    def _update_child_colors(self, style_name):
        """Update colors of all child labels in the content frame"""
        try:
            for widget in self.content_frame.winfo_children():
                if isinstance(widget, ttk.Label):
                    widget.configure(style=style_name)
                # Also check nested frames
                elif isinstance(widget, ttk.Frame):
                    for nested_widget in widget.winfo_children():
                        if isinstance(nested_widget, ttk.Label):
                            nested_widget.configure(style=style_name)
        except Exception:
            # Fail silently if color update fails - visual feedback is nice-to-have
            pass
    
    def get_content_frame(self):
        """Return the content frame for adding widgets"""
        return self.content_frame

# Add: canonical log filename from SSOT defaults (NO FALLBACKS)
# FAIL FAST if logging.logfile is missing from DEFAULT_CONFIG
try:
    LOG_FILENAME = DEFAULT_CONFIG['logging']['logfile']
except KeyError as e:
    raise RuntimeError(f"DEFAULT_CONFIG missing required logging.logfile: {e}")

def now_ist():
    """Return current time in India Standard Time using SSOT timezone"""
    return datetime.now(pytz.timezone(DEFAULT_CONFIG['session']['timezone']))

class UnifiedTradingGUI(tk.Tk):
    
    def _initialize_all_variables(self):
        """Create minimal tkinter Variable placeholders from runtime_config (SSOT) — fail fast."""
        # runtime_config is created in __init__ before this is called
        try:
            strategy_config = self.runtime_config['strategy']
            risk_config = self.runtime_config['risk']
            capital_config = self.runtime_config['capital']
            instrument_config = self.runtime_config['instrument']
            session_config = self.runtime_config['session']
            logging_config = self.runtime_config['logging']
            self.instrument_mappings = self.runtime_config.get('instrument_mappings', {})
        except KeyError as e:
            missing = str(e).strip("'")
            logger.error("DEFAULT_CONFIG missing required section/key: %s", missing)
            messagebox.showerror("Configuration Error", f"DEFAULT_CONFIG missing required section/key: {missing}")
            raise

        # Strategy toggles / params (placeholders)
        self.bt_use_ema_crossover = tk.BooleanVar(value=strategy_config['use_ema_crossover'])
        self.bt_use_macd = tk.BooleanVar(value=strategy_config['use_macd'])
        self.bt_use_vwap = tk.BooleanVar(value=strategy_config['use_vwap'])
        self.bt_use_rsi_filter = tk.BooleanVar(value=strategy_config['use_rsi_filter'])
        self.bt_use_htf_trend = tk.BooleanVar(value=strategy_config['use_htf_trend'])
        self.bt_use_bollinger_bands = tk.BooleanVar(value=strategy_config['use_bollinger_bands'])
        self.bt_use_stochastic = tk.BooleanVar(value=strategy_config['use_stochastic'])
        self.bt_use_atr = tk.BooleanVar(value=strategy_config['use_atr'])
        self.bt_use_consecutive_green = tk.BooleanVar(value=strategy_config['use_consecutive_green'])
        
        # Optional indicators visibility toggle (unchecked by default)
        self.bt_show_optional_indicators = tk.BooleanVar(value=False)

        self.bt_fast_ema = tk.StringVar(value=str(strategy_config['fast_ema']))
        self.bt_slow_ema = tk.StringVar(value=str(strategy_config['slow_ema']))
        self.bt_macd_fast = tk.StringVar(value=str(strategy_config['macd_fast']))
        self.bt_macd_slow = tk.StringVar(value=str(strategy_config['macd_slow']))
        self.bt_macd_signal = tk.StringVar(value=str(strategy_config['macd_signal']))
        self.bt_rsi_length = tk.StringVar(value=str(strategy_config['rsi_length']))
        self.bt_rsi_oversold = tk.StringVar(value=str(strategy_config['rsi_oversold']))
        self.bt_rsi_overbought = tk.StringVar(value=str(strategy_config['rsi_overbought']))
        self.bt_htf_period = tk.StringVar(value=str(strategy_config['htf_period']))
        self.bt_consecutive_green_bars = tk.StringVar(value=str(strategy_config['consecutive_green_bars']))
        
        # Control Base SL parameters
        self.bt_control_base_sl_enabled = tk.BooleanVar(value=strategy_config['Enable_control_base_sl_green_ticks'])
        self.bt_control_base_sl_green_ticks = tk.StringVar(value=str(strategy_config['control_base_sl_green_ticks']))

        # Risk placeholders (required — no fallbacks)
        self.bt_base_sl_points = tk.StringVar(value=str(risk_config['base_sl_points']))
        self.bt_use_trail_stop = tk.BooleanVar(value=risk_config['use_trail_stop'])
        self.bt_trail_activation = tk.StringVar(value=str(risk_config['trail_activation_points']))
        self.bt_trail_distance = tk.StringVar(value=str(risk_config['trail_distance_points']))
        self.bt_risk_per_trade_percent = tk.StringVar(value=str(risk_config['risk_per_trade_percent']))
        
        # Risk component control variables
        self.bt_use_stop_loss = tk.BooleanVar(value=True)  # Stop loss always enabled
        self.bt_use_take_profit = tk.BooleanVar(value=True)  # Take profit always enabled

        tp_points = risk_config['tp_points']
        self.bt_tp_points = [tk.StringVar(value=str(pt)) for pt in tp_points]
        tp_percents = risk_config['tp_percents']
        self.bt_tp_percents = [tk.StringVar(value=str(int(p * 100))) for p in tp_percents]

        # Instrument placeholders (required)
        self.bt_symbol = tk.StringVar(value=str(instrument_config['symbol']))
        self.bt_exchange = tk.StringVar(value=str(instrument_config['exchange']))
        # Get lot_size from instrument_mappings SSOT
        current_symbol = instrument_config['symbol']
        lot_size_from_ssot = self.instrument_mappings.get(current_symbol, {}).get('lot_size', 1)
        self.bt_lot_size = tk.StringVar(value=str(lot_size_from_ssot))

        # Capital placeholders (required)
        self.bt_initial_capital = tk.StringVar(value=str(capital_config['initial_capital']))

        # Session placeholders (required)
        self.bt_is_intraday = tk.BooleanVar(value=session_config['is_intraday'])
        self.bt_session_start_hour = tk.StringVar(value=str(session_config['start_hour']))
        self.bt_session_start_min = tk.StringVar(value=str(session_config['start_min']))
        self.bt_session_end_hour = tk.StringVar(value=str(session_config['end_hour']))
        self.bt_session_end_min = tk.StringVar(value=str(session_config['end_min']))

        # Data / misc placeholders
        self.bt_data_file = tk.StringVar(value="")

        # Logger UI placeholders
        self.logger_levels = {}
        for logger_name in ["core.indicators", "core.researchStrategy", "backtest.backtest_runner", "utils.simple_loader"]:
            self.logger_levels[logger_name] = tk.StringVar(value="DEFAULT")

        # Noise filter placeholders (strategy section)
        self.bt_noise_filter_enabled = tk.BooleanVar(value=strategy_config['noise_filter_enabled'])
        self.bt_noise_filter_percentage = tk.StringVar(value=str(strategy_config['noise_filter_percentage'] * 100))

        # === FORWARD TEST VARIABLES FROM RUNTIME_CONFIG (SSOT) ===
        
        # Forward Test Strategy toggles (from defaults.py)
        self.ft_use_ema_crossover = tk.BooleanVar(value=strategy_config['use_ema_crossover'])
        self.ft_use_macd = tk.BooleanVar(value=strategy_config['use_macd'])
        self.ft_use_vwap = tk.BooleanVar(value=strategy_config['use_vwap'])
        self.ft_use_rsi_filter = tk.BooleanVar(value=strategy_config['use_rsi_filter'])
        self.ft_use_htf_trend = tk.BooleanVar(value=strategy_config['use_htf_trend'])
        self.ft_use_bollinger_bands = tk.BooleanVar(value=strategy_config['use_bollinger_bands'])
        self.ft_use_stochastic = tk.BooleanVar(value=strategy_config['use_stochastic'])
        self.ft_use_atr = tk.BooleanVar(value=strategy_config['use_atr'])

        # Forward Test Strategy parameters (from defaults.py)
        self.ft_fast_ema = tk.StringVar(value=str(strategy_config['fast_ema']))
        self.ft_slow_ema = tk.StringVar(value=str(strategy_config['slow_ema']))
        self.ft_macd_fast = tk.StringVar(value=str(strategy_config['macd_fast']))
        self.ft_macd_slow = tk.StringVar(value=str(strategy_config['macd_slow']))
        self.ft_macd_signal = tk.StringVar(value=str(strategy_config['macd_signal']))
        self.ft_rsi_length = tk.StringVar(value=str(strategy_config['rsi_length']))
        self.ft_rsi_oversold = tk.StringVar(value=str(strategy_config['rsi_oversold']))
        self.ft_rsi_overbought = tk.StringVar(value=str(strategy_config['rsi_overbought']))
        self.ft_htf_period = tk.StringVar(value=str(strategy_config['htf_period']))
        self.ft_consecutive_green_bars = tk.StringVar(value=str(strategy_config['consecutive_green_bars']))
        
        # Forward Test Control Base SL parameters
        self.ft_control_base_sl_enabled = tk.BooleanVar(value=strategy_config['Enable_control_base_sl_green_ticks'])
        self.ft_control_base_sl_green_ticks = tk.StringVar(value=str(strategy_config['control_base_sl_green_ticks']))

        # Forward Test Risk management (from defaults.py)
        self.ft_use_stop_loss = tk.BooleanVar(value=True)  # Always enabled for live trading
        self.ft_base_sl_points = tk.StringVar(value=str(risk_config['base_sl_points']))
        self.ft_use_take_profit = tk.BooleanVar(value=True)  # Always enabled for live trading
        ft_tp_points = risk_config['tp_points']
        self.ft_tp_points = [tk.StringVar(value=str(pt)) for pt in ft_tp_points]
        ft_tp_percents = risk_config['tp_percents'] 
        self.ft_tp_percents = [tk.StringVar(value=str(int(p * 100))) for p in ft_tp_percents]
        self.ft_use_trail_stop = tk.BooleanVar(value=risk_config['use_trail_stop'])
        self.ft_trail_activation = tk.StringVar(value=str(risk_config['trail_activation_points']))
        self.ft_trail_distance = tk.StringVar(value=str(risk_config['trail_distance_points']))
        self.ft_risk_per_trade = tk.StringVar(value=str(risk_config['risk_per_trade_percent']))
        
        # Forward Test Price-Above-Exit Filter (from defaults.py)
        self.ft_price_above_exit_filter_enabled = tk.BooleanVar(value=risk_config['price_above_exit_filter_enabled'])
        self.ft_price_buffer_points = tk.StringVar(value=str(risk_config['price_buffer_points']))
        self.ft_filter_duration_seconds = tk.StringVar(value=str(risk_config['filter_duration_seconds']))

        # Forward Test Session management (from defaults.py)
        self.ft_is_intraday = tk.BooleanVar(value=session_config['is_intraday'])
        self.ft_session_start_hour = tk.StringVar(value=str(session_config['start_hour']))
        self.ft_session_start_min = tk.StringVar(value=str(session_config['start_min']))
        self.ft_session_end_hour = tk.StringVar(value=str(session_config['end_hour']))
        self.ft_session_end_min = tk.StringVar(value=str(session_config['end_min']))
        self.ft_auto_stop_enabled = tk.BooleanVar(value=True)  # Default safety feature
        self.ft_max_trades_per_day = tk.StringVar(value=str(risk_config['max_positions_per_day']))
        self.ft_max_loss_per_day = tk.StringVar(value="500")  # UI-specific default
        
        # Session Trade Blocks (from defaults.py)
        self.ft_trade_block_enabled = tk.BooleanVar(value=session_config.get('trade_block_enabled', False))
        self.ft_trade_blocks = []  # List of dicts with StringVars for each block
        # Initialize from config if blocks exist
        for block in session_config.get('trade_blocks', []):
            self.ft_trade_blocks.append({
                'start_hour': tk.StringVar(value=str(block['start_hour'])),
                'start_min': tk.StringVar(value=str(block['start_min'])),
                'end_hour': tk.StringVar(value=str(block['end_hour'])),
                'end_min': tk.StringVar(value=str(block['end_min']))
            })

        # Forward Test Data Simulation (Optional)
        self.ft_use_file_simulation = tk.BooleanVar(value=False)  # Disabled by default - live trading is primary
        self.ft_data_file_path = tk.StringVar(value="")  # No file selected by default

        # Forward Test Performance Settings (Consumption Mode)
        self.ft_use_direct_callbacks = tk.BooleanVar(value=True)  # Default to callback mode (Wind-style, faster)

        # Forward Test Capital management (from defaults.py)
        self.ft_initial_capital = tk.StringVar(value=str(capital_config['initial_capital']))
        self.ft_position_size_method = tk.StringVar(value="fixed_amount")  # UI default
        self.ft_fixed_amount = tk.StringVar(value="10000")  # UI default (10% of default capital)
        self.ft_fixed_quantity = tk.StringVar(value="25")  # UI default 
        # Get lot_size from instrument_mappings SSOT
        self.ft_lot_size = tk.StringVar(value=str(lot_size_from_ssot))
        self.ft_max_positions = tk.StringVar(value="1")  # Conservative default for live trading

        # Forward Test Exchange/Live settings (from defaults.py)
        live_config = self.runtime_config.get('live', {})
        self.ft_exchange = tk.StringVar(value=str(instrument_config['exchange']))
        self.ft_feed_type = tk.StringVar(value=live_config.get('feed_type', 'LTP'))

        # Forward Test UI-only variables (status displays - can be hardcoded)
        self.ft_symbol = tk.StringVar()  # Selected dynamically
        self.ft_token = tk.StringVar()   # Selected dynamically
        self.ft_cache_status = tk.StringVar(value="Cache not loaded")
        self.ft_connection_status = tk.StringVar(value="🔴 Disconnected")
        self.ft_trading_status = tk.StringVar(value="⏸️ Stopped")
        self.ft_current_price = tk.StringVar(value="--")
        self.ft_position_status = tk.StringVar(value="📭 No Position")
        self.ft_pnl_status = tk.StringVar(value="₹0.00")
        self.ft_capital_display = tk.StringVar(value=f"₹{capital_config['initial_capital']:,.0f}")
        self.ft_trades_today = tk.StringVar(value=f"0/{risk_config['max_positions_per_day']}")
        self.ft_tick_count = tk.StringVar(value="0")
        self.bt_noise_filter_min_ticks = tk.StringVar(value=str(strategy_config['noise_filter_min_ticks']))
        
        # Instrument selection for Forward Test (dropdown-based)
        self.ft_instrument_type = tk.StringVar(value="NIFTY")  # Default to Nifty
        self.ft_available_instruments = list(self.instrument_mappings.keys())
        self.ft_selected_instrument_info = self.instrument_mappings.get("NIFTY", {})

        # mark placeholders ready
        self._widgets_initialized = True

    def __init__(self, master=None):
        super().__init__(master)

        self.title("Unified Trading System")
        self.geometry("1200x800")
        self.minsize(1000, 700)

        # === SINGLE POINT OF CONFIGURATION (FIXED FLOW) ===
        # 1. Build runtime config from defaults (factory may apply normalization & user prefs)
        self.runtime_config = create_config_from_defaults()

        # 2. Initialize all GUI variables (this sets tk.Variable instances)
        #    Must happen before applying user prefs which write into tk.Variables.
        self._initialize_all_variables()

        # 3. Merge persisted user preferences into runtime_config and apply to widgets
        self._merge_user_preferences_into_runtime_config()

        # 4. Initialize GUI variables from runtime_config (single source for widgets)
        self._initialize_variables_from_runtime_config()

        # 5. Logging is configured at module import from DEFAULT_CONFIG (strict, single source).
        # If runtime reconfiguration is desired, call setup_logging(...) explicitly with
        # runtime_config['logging'] (no fallbacks). We intentionally avoid best-effort
        # reconfiguration here to keep logging deterministic.

        # 6. Create GUI components and tabs
        self._create_gui_framework()
        self._build_backtest_tab()
        self._build_forward_test_tab()
        self._build_monitor_tab()
        self._build_log_tab()

        # 7. Initialize instrument selection (after GUI widgets are created)
        self._initialize_instrument_selection()

        # 8. Register window close handler to prevent accidental closures
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        logger.info("GUI initialized successfully with runtime config")

    def _load_user_preferences(self):
        """Load user preferences from saved file"""
        prefs_file = "user_preferences.json"
        if os.path.exists(prefs_file):
            try:
                with open(prefs_file, 'r') as f:
                    prefs = json.load(f)
                self._apply_preferences_to_gui(prefs)
                logger.info("User preferences applied to GUI (legacy path)")
            except Exception:
                logger.exception("Failed to load user preferences (legacy path)")


    def _apply_preferences_to_gui(self, preferences):
        """Apply saved preferences to GUI controls - Complete implementation"""
        # Strategy parameters
        if 'strategy' in preferences:
            strategy_prefs = preferences['strategy']
            self._set_if_exists(self.bt_use_ema_crossover, 'use_ema_crossover', strategy_prefs)
            self._set_if_exists(self.bt_use_macd, 'use_macd', strategy_prefs)
            self._set_if_exists(self.bt_use_vwap, 'use_vwap', strategy_prefs)
            self._set_if_exists(self.bt_use_rsi_filter, 'use_rsi_filter', strategy_prefs)
            self._set_if_exists(self.bt_use_htf_trend, 'use_htf_trend', strategy_prefs)
            self._set_if_exists(self.bt_fast_ema, 'fast_ema', strategy_prefs)
            self._set_if_exists(self.bt_slow_ema, 'slow_ema', strategy_prefs)
            self._set_if_exists(self.bt_macd_fast, 'macd_fast', strategy_prefs)
            self._set_if_exists(self.bt_macd_slow, 'macd_slow', strategy_prefs)
            self._set_if_exists(self.bt_macd_signal, 'macd_signal', strategy_prefs)
    
        # Risk parameters - Complete implementation
        if 'risk' in preferences:
            risk_prefs = preferences['risk']
            self._set_if_exists(self.bt_base_sl_points, 'base_sl_points', risk_prefs)
            self._set_if_exists(self.bt_use_trail_stop, 'use_trail_stop', risk_prefs)
            self._set_if_exists(self.bt_trail_activation, 'trail_activation_points', risk_prefs)
            self._set_if_exists(self.bt_trail_distance, 'trail_distance_points', risk_prefs)
            # Handle TP points array
            if 'tp_points' in risk_prefs and len(risk_prefs['tp_points']) >= 4:
                for i, tp_var in enumerate(self.bt_tp_points[:4]):
                    tp_var.set(str(risk_prefs['tp_points'][i]))

        # Capital settings
        if 'capital' in preferences:
            try:
                if 'initial_capital' in preferences['capital']:
                    self.bt_initial_capital.set(str(preferences['capital']['initial_capital']))
            except Exception:
                pass


        # Instrument settings
        if 'instrument' in preferences:
            try:
                if 'symbol' in preferences['instrument']:
                    self.bt_symbol.set(preferences['instrument']['symbol'])
            except Exception:
                pass


        # Session settings
        if 'session' in preferences:
            try:
                s = preferences['session']
                if 'start_hour' in s:
                    self.bt_session_start_hour.set(str(s['start_hour']))
                if 'start_min' in s:
                    self.bt_session_start_min.set(str(s['start_min']))
                if 'end_hour' in s:
                    self.bt_session_end_hour.set(str(s['end_hour']))
                if 'end_min' in s:
                    self.bt_session_end_min.set(str(s['end_min']))
            except Exception:
                pass

    def _set_if_exists(self, var, key, prefs_dict):
        """Set tkinter variable if key exists in preferences"""
        if key in prefs_dict:
            try:
                if isinstance(var, tk.StringVar):
                    var.set(str(prefs_dict[key]))
                elif isinstance(var, tk.BooleanVar):
                    var.set(bool(prefs_dict[key]))
                elif isinstance(var, tk.IntVar):
                    var.set(int(prefs_dict[key]))
                elif isinstance(var, tk.DoubleVar):
                    var.set(float(prefs_dict[key]))
            except Exception:
                pass

    def save_user_preferences(self):
        """Save user preferences to JSON file"""
        try:
            prefs = self.build_config_from_gui()
            # compute diff against defaults and save only diffs (existing save path handles this)
            diff = self._get_config_diff(prefs, DEFAULT_CONFIG)
            with open("user_preferences.json", "w") as f:
                json.dump(diff, f, indent=2)
            logger.info("User preferences saved")
        except Exception as e:
            logger.exception("Failed to save user preferences")

    def _get_config_diff(self, current, defaults):
        """Extract only values that differ from defaults"""
        diff = {}

        for section, params in current.items():
            if not isinstance(params, dict):
                continue
            for k, v in params.items():
                # STRICT CONFIG ACCESS - fail fast if section/param missing from defaults
                default_v = defaults[section][k] if section in defaults and k in defaults[section] else None
                if v != default_v:
                    diff.setdefault(section, {})[k] = v

        return diff

    def build_config_from_gui(self):
        """Build complete configuration from current GUI state"""
        config = create_config_from_defaults()

        # Update with current GUI values

        # Strategy settings
        config['strategy']['use_ema_crossover'] = self.bt_use_ema_crossover.get()
        config['strategy']['use_macd'] = self.bt_use_macd.get()
        config['strategy']['use_vwap'] = self.bt_use_vwap.get()
        config['strategy']['use_rsi_filter'] = self.bt_use_rsi_filter.get()
        config['strategy']['use_htf_trend'] = self.bt_use_htf_trend.get()
        config['strategy']['use_bollinger_bands'] = self.bt_use_bollinger_bands.get()
        config['strategy']['use_stochastic'] = self.bt_use_stochastic.get()
        config['strategy']['use_atr'] = self.bt_use_atr.get()
        config['strategy']['use_consecutive_green'] = self.bt_use_consecutive_green.get()

        # Convert string inputs to appropriate types
        config['strategy']['fast_ema'] = int(self.bt_fast_ema.get())
        config['strategy']['slow_ema'] = int(self.bt_slow_ema.get())
        config['strategy']['macd_fast'] = int(self.bt_macd_fast.get())
        config['strategy']['macd_slow'] = int(self.bt_macd_slow.get())
        config['strategy']['macd_signal'] = int(self.bt_macd_signal.get())
        # Consecutive Green Bars
        config['strategy']['consecutive_green_bars'] = int(self.bt_consecutive_green_bars.get())
        
        # Control Base SL settings
        config['strategy']['Enable_control_base_sl_green_ticks'] = self.bt_control_base_sl_enabled.get()
        config['strategy']['control_base_sl_green_ticks'] = int(self.bt_control_base_sl_green_ticks.get())

        # Add noise filter settings (no hardcoded defaults)
        config['strategy']['noise_filter_enabled'] = self.bt_noise_filter_enabled.get()
        config['strategy']['noise_filter_percentage'] = float(self.bt_noise_filter_percentage.get()) / 100.0  # Convert from percentage display
        config['strategy']['noise_filter_min_ticks'] = float(self.bt_noise_filter_min_ticks.get())

        # --- ADD THIS LINE ---
        config['strategy']['strategy_version'] = DEFAULT_CONFIG['strategy']['strategy_version']
        # ---------------------

        # Risk settings
        config['risk']['base_sl_points'] = float(self.bt_base_sl_points.get()) if self.bt_use_stop_loss.get() else 0.0
        config['risk']['use_trail_stop'] = self.bt_use_trail_stop.get()
        config['risk']['trail_activation_points'] = float(self.bt_trail_activation.get()) if self.bt_use_trail_stop.get() else 0.0
        config['risk']['trail_distance_points'] = float(self.bt_trail_distance.get()) if self.bt_use_trail_stop.get() else 0.0
        config['risk']['risk_per_trade_percent'] = float(self.bt_risk_per_trade_percent.get())

        # Update take profit points and percentages (only if take profit is enabled)
        if self.bt_use_take_profit.get():
            tp_points = [float(var.get()) for var in self.bt_tp_points]
            tp_percents = [float(var.get())/100.0 for var in self.bt_tp_percents]  # Convert from percentage display
            config['risk']['tp_points'] = tp_points
            config['risk']['tp_percents'] = tp_percents
        else:
            config['risk']['tp_points'] = []
            config['risk']['tp_percents'] = []

        # Capital settings
        config['capital']['initial_capital'] = float(self.bt_initial_capital.get())

        # Instrument settings (lot_size comes from instrument_mappings SSOT)
        config['instrument']['symbol'] = self.bt_symbol.get()
        config['instrument']['exchange'] = self.bt_exchange.get()
        
        # Get all instrument parameters from instrument_mappings SSOT - STRICT ACCESS (fail-fast if missing)
        current_symbol = config['instrument']['symbol']
        if current_symbol not in self.instrument_mappings:
            raise KeyError(f"Symbol '{current_symbol}' not found in instrument_mappings SSOT")
        
        instrument_info = self.instrument_mappings[current_symbol]
        
        # Required instrument parameters - fail-fast if any are missing
        required_params = ['lot_size', 'tick_size', 'exchange', 'type']
        for param in required_params:
            if param not in instrument_info:
                raise KeyError(f"{param} not found for symbol '{current_symbol}' in instrument_mappings SSOT")
        
        config['instrument']['lot_size'] = instrument_info['lot_size']
        config['instrument']['tick_size'] = instrument_info['tick_size']
        config['instrument']['instrument_type'] = instrument_info['type']
        # Note: exchange is already set from GUI, but verify it matches SSOT
        if config['instrument']['exchange'] != instrument_info['exchange']:
            logger.warning(f"Exchange mismatch: GUI={config['instrument']['exchange']}, SSOT={instrument_info['exchange']}")
            config['instrument']['exchange'] = instrument_info['exchange']  # SSOT wins

        # Session settings
        config['session']['is_intraday'] = self.bt_is_intraday.get()
        config['session']['start_hour'] = int(self.bt_session_start_hour.get())
        config['session']['start_min'] = int(self.bt_session_start_min.get())
        config['session']['end_hour'] = int(self.bt_session_end_hour.get())
        config['session']['end_min'] = int(self.bt_session_end_min.get())
        
        # Trade blocks configuration
        config['session']['trade_block_enabled'] = self.ft_trade_block_enabled.get()
        config['session']['trade_blocks'] = []
        for block in self.ft_trade_blocks:
            config['session']['trade_blocks'].append({
                'start_hour': int(block['start_hour'].get()),
                'start_min': int(block['start_min'].get()),
                'end_hour': int(block['end_hour'].get()),
                'end_min': int(block['end_min'].get())
            })

        # Set the data file path for the backtest runner
        config['backtest']['data_path'] = self.bt_data_file.get()

        # --- Ensure logging config is propagated to backtest config ---
        # Include logging defaults from DEFAULT_CONFIG
        config['logging'] = DEFAULT_CONFIG['logging'].copy()
 
        # Add logger level overrides from GUI
        log_level_overrides = {}
        if hasattr(self, 'logger_levels'):
            for logger_name, level_var in self.logger_levels.items():
                level = level_var.get()
                if level != "DEFAULT":  # Only add non-default values
                    log_level_overrides[logger_name] = level
 
        config['logging']['log_level_overrides'] = log_level_overrides
        # Tick logging disabled - using default value only
        config['logging']['tick_log_interval'] = DEFAULT_CONFIG['logging']['tick_log_interval']
 
        # FINAL: authoritative validation + freeze (GUI SSOT)
        try:
            validation = validate_config(config)
        except Exception as e:
            logger.exception("validate_config raised unexpected exception: %s", e)
            messagebox.showerror("Validation Error", f"Unexpected error during validation: {e}")
            return None

        if not validation.get('valid', False):
            errs = validation.get('errors', []) or ["Unknown validation failure"]
            messagebox.showerror("Configuration Validation Failed",
                                 "Please fix configuration issues:\n\n" + "\n".join(errs))
            return None

        try:
            frozen = freeze_config(config)
        except Exception as e:
            logger.exception("freeze_config failed: %s", e)
            messagebox.showerror("Configuration Error", "Failed to freeze configuration. Aborting run.")
            return None

        # Ensure we actually received a MappingProxyType
        if not isinstance(frozen, MappingProxyType):
            logger.error("freeze_config did not return MappingProxyType; aborting run")
            messagebox.showerror("Configuration Error", "Configuration could not be frozen. Aborting run.")
            return None

        return frozen

    def run_backtest(self):
        """Run backtest with GUI configuration"""
        try:
            # Get validated and frozen config from GUI (returns None on validation failure)
            config = self.build_config_from_gui()
            if config is None:
                return

            # Log the actual configuration used
            logger.info("====== BACKTEST CONFIGURATION ======")
            logger.info("Using TRUE INCREMENTAL PROCESSING (row-by-row)")
            logger.info("Batch processing completely eliminated")
            for section, params in config.items():
                if isinstance(params, dict):
                    logger.info(f"{section}: {params}")

            # Create and run backtest with GUI config
            backtest = BacktestRunner(config=config)
            results = backtest.run()

            # Display results
            self.display_backtest_results(results)

            # Optional: Save preferences after successful run
            self.save_user_preferences()

        except Exception as e:
            logger.exception(f"Backtest failed: {e}")
            messagebox.showerror("Backtest Error", f"Failed to run backtest: {e}")

    # --- Forward Test Tab ---
    def _build_forward_test_tab(self):
        frame = self.ft_tab
        
        # Add scrollable frame for the main content
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        def _update_scroll_region(event=None):
            """Update scroll region with proper sizing to ensure all content is scrollable"""
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        scrollable_frame.bind("<Configure>", _update_scroll_region)
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Pack canvas and scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Configure two-column layout in scrollable frame
        scrollable_frame.columnconfigure(0, weight=1)  # Left column
        scrollable_frame.columnconfigure(1, weight=1)  # Right column
        
        # Create main container frames for two columns
        left_column = ttk.Frame(scrollable_frame)
        left_column.grid(row=0, column=0, sticky="nsew", padx=(5,2.5), pady=5)
        
        right_column = ttk.Frame(scrollable_frame)
        right_column.grid(row=0, column=1, sticky="nsew", padx=(2.5,5), pady=5)
        
        # Build left and right column content
        self._build_ft_left_column(left_column)
        self._build_ft_right_column(right_column)
        
        # Ensure scroll region is properly updated after all content is added
        canvas.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        
        # Bind mousewheel to canvas for scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

    def _build_ft_left_column(self, parent):
        """Build the left column of forward test tab - critical controls and symbol management"""
        row = 0

        # === SYMBOL MANAGEMENT SECTION ===
        ttk.Label(parent, text="🔗 Symbol Management", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(5,5))
        row += 1

        # Authentication Status
        ttk.Label(parent, text="SmartAPI: Auto (saved session) | Paper mode if no session", style='Info.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", padx=5, pady=2)
        row += 1

        # Cache and Exchange in one row
        cache_frame = ttk.Frame(parent)
        cache_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=2)
        cache_frame.columnconfigure(1, weight=1)
        
        ttk.Button(cache_frame, text="Refresh Cache", command=self._ft_refresh_cache).grid(row=0, column=0, sticky="w")
        ttk.Label(cache_frame, textvariable=self.ft_cache_status, style='Note.TLabel').grid(row=0, column=1, sticky="w", padx=10)
        row += 1

        # Instrument Type Selection (new primary selector)
        instrument_frame = ttk.LabelFrame(parent, text="Instrument Selection")
        instrument_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        instrument_frame.columnconfigure(1, weight=1)

        ttk.Label(instrument_frame, text="Instrument:").grid(row=0, column=0, sticky="e", padx=5, pady=2)
        # Organize instruments by type for better UI
        organized_instruments = {
            "Index Options": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"],
            "Stock Options": ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "LT", "WIPRO", "MARUTI", "BHARTIARTL"],
            "Index Futures": ["NIFTYFUT", "BANKNIFTYFUT"],
            "Stock Futures": ["RELIANCEFUT"],
            "Cash Market": ["NIFTY_CASH", "BANKNIFTY_CASH", "RELIANCE_CASH", "HDFCBANK_CASH"]
        }
        
        # Flatten for dropdown but keep order
        instrument_values = []
        for category, instruments in organized_instruments.items():
            instrument_values.extend(instruments)
        
        instrument_combo = ttk.Combobox(instrument_frame, textvariable=self.ft_instrument_type, 
                                       values=instrument_values, width=20, state='readonly')
        instrument_combo.grid(row=0, column=1, sticky="w", padx=5, pady=2)
        instrument_combo.bind("<<ComboboxSelected>>", self._ft_on_instrument_change)

        # Display instrument info (lot size, exchange, type)
        info_frame = ttk.Frame(instrument_frame)
        info_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=2)
        
        ttk.Label(info_frame, text="Lot Size:").grid(row=0, column=0, sticky="e", padx=5)
        ttk.Entry(info_frame, textvariable=self.ft_lot_size, width=8, state="readonly").grid(row=0, column=1, padx=2)
        
        ttk.Label(info_frame, text="Exchange:").grid(row=0, column=2, sticky="e", padx=(15,5))
        exchanges = ["NSE_FO", "NSE_CM", "BSE_CM", "BSE_FO"]
        ttk.Combobox(info_frame, textvariable=self.ft_exchange, values=exchanges, width=10, state='readonly').grid(row=0, column=3, padx=2)
        
        ttk.Label(info_frame, text="Feed:").grid(row=0, column=4, sticky="e", padx=(15,5))
        feed_types = ["LTP", "Quote", "SnapQuote"]
        ttk.Combobox(info_frame, textvariable=self.ft_feed_type, values=feed_types, width=10, state='readonly').grid(row=0, column=5, padx=2)
        row += 1

        # Symbol selection (compact version - for specific symbol within instrument type)
        symbol_frame = ttk.LabelFrame(parent, text="Specific Symbol Selection")
        symbol_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        symbol_frame.columnconfigure(1, weight=1)

        ttk.Label(symbol_frame, text="Symbol:").grid(row=0, column=0, sticky="e", padx=5, pady=2)
        self.ft_symbol_entry = ttk.Entry(symbol_frame, textvariable=self.ft_symbol, width=25)
        self.ft_symbol_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(symbol_frame, text="Load", command=self._ft_load_symbols).grid(row=0, column=2, padx=5, pady=2)

        # Compact symbols listbox
        self.ft_symbols_listbox = tk.Listbox(symbol_frame, width=40, height=4)
        self.ft_symbols_listbox.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=2)
        self.ft_symbols_listbox.bind("<<ListboxSelect>>", self._ft_update_symbol_details)

        # Token field (compact, read-only)
        ttk.Label(symbol_frame, text="Token:").grid(row=2, column=0, sticky="e", padx=5, pady=2)
        ttk.Entry(symbol_frame, textvariable=self.ft_token, width=15, state="readonly").grid(row=2, column=1, sticky="w", padx=5, pady=2)
        row += 1

        # Add separator between Symbol Management and Trading Controls
        self._add_grid_separator(parent, row)
        row += 1

        # === CONTROL BUTTONS SECTION (CRITICAL) ===
        ttk.Label(parent, text="🚀 Trading Controls", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(15,5))
        row += 1

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)
        button_frame.columnconfigure((0,1,2,3), weight=1)
        
        ttk.Button(button_frame, text="🚀 Run Forward Test", command=self._ft_run_forward_test, style='RunBacktest.TButton').grid(row=0, column=0, padx=5, sticky="ew")
        ttk.Button(button_frame, text="⏹️ Stop Forward Test", command=self._ft_stop_forward_test).grid(row=0, column=1, padx=5, sticky="ew")
        ttk.Button(button_frame, text="📊 Status Monitor", command=self._ft_open_status_monitor).grid(row=0, column=2, padx=5, sticky="ew")
        ttk.Button(button_frame, text="📄 Export Results", command=self._ft_export_results).grid(row=0, column=3, padx=5, sticky="ew")
        row += 1

        # Add separator between Trading Controls and Live Status
        self._add_grid_separator(parent, row)
        row += 1

        # === LIVE STATUS SECTION (CRITICAL) ===
        ttk.Label(parent, text="📊 Live Trading Status", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(15,5))
        row += 1

        # Compact status display
        status_frame = ttk.LabelFrame(parent, text="Current Status")
        status_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        status_frame.columnconfigure(1, weight=1)

        # Connection & Trading Status
        ttk.Label(status_frame, text="Connection:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_connection_status).grid(row=0, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(status_frame, text="Trading:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_trading_status).grid(row=1, column=1, sticky="w", padx=5, pady=2)

        # Current Price & Position
        ttk.Label(status_frame, text="Price:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_current_price).grid(row=2, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(status_frame, text="Position:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_position_status).grid(row=3, column=1, sticky="w", padx=5, pady=2)

        # P&L & Capital 
        ttk.Label(status_frame, text="P&L:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.ft_pnl_label = ttk.Label(status_frame, textvariable=self.ft_pnl_status)
        self.ft_pnl_label.grid(row=4, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(status_frame, text="Capital:").grid(row=5, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_capital_display).grid(row=5, column=1, sticky="w", padx=5, pady=2)

        # Trades & Ticks
        ttk.Label(status_frame, text="Trades Today:").grid(row=6, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_trades_today).grid(row=6, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(status_frame, text="Ticks:").grid(row=7, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(status_frame, textvariable=self.ft_tick_count).grid(row=7, column=1, sticky="w", padx=5, pady=2)

    def _build_ft_right_column(self, parent):
        """Build the right column of forward test tab - strategy configuration and management sections"""
        row = 0

        # === STRATEGY CONFIGURATION SECTION ===
        ttk.Label(parent, text="⚙️ Strategy Configuration", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(5,5))
        row += 1

        # Indicator Toggles
        indicators_frame = ttk.LabelFrame(parent, text="Indicators")
        indicators_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)



        ttk.Checkbutton(indicators_frame, text="EMA Crossover", variable=self.ft_use_ema_crossover).grid(row=0, column=0, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="MACD", variable=self.ft_use_macd).grid(row=0, column=1, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="VWAP", variable=self.ft_use_vwap).grid(row=1, column=0, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="RSI Filter", variable=self.ft_use_rsi_filter).grid(row=1, column=1, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="HTF Trend", variable=self.ft_use_htf_trend).grid(row=2, column=0, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="Bollinger Bands", variable=self.ft_use_bollinger_bands).grid(row=2, column=1, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="Stochastic", variable=self.ft_use_stochastic).grid(row=3, column=0, sticky="w", padx=2, pady=1)
        ttk.Checkbutton(indicators_frame, text="ATR", variable=self.ft_use_atr).grid(row=3, column=1, sticky="w", padx=2, pady=1)
        row += 1

        # Parameters
        params_frame = ttk.LabelFrame(parent, text="Strategy Parameters")
        params_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        # EMA Parameters
        ttk.Label(params_frame, text="Fast EMA:").grid(row=0, column=0, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_fast_ema, width=8).grid(row=0, column=1, padx=2)

        ttk.Label(params_frame, text="Slow EMA:").grid(row=0, column=2, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_slow_ema, width=8).grid(row=0, column=3, padx=2)

        # MACD Parameters
        ttk.Label(params_frame, text="MACD Fast:").grid(row=1, column=0, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_macd_fast, width=8).grid(row=1, column=1, padx=2)

        ttk.Label(params_frame, text="MACD Slow:").grid(row=1, column=2, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_macd_slow, width=8).grid(row=1, column=3, padx=2)

        ttk.Label(params_frame, text="MACD Signal:").grid(row=1, column=4, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_macd_signal, width=8).grid(row=1, column=5, padx=2)

        # RSI Parameters
        ttk.Label(params_frame, text="RSI Length:").grid(row=2, column=0, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_rsi_length, width=8).grid(row=2, column=1, padx=2)

        ttk.Label(params_frame, text="RSI Oversold:").grid(row=2, column=2, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_rsi_oversold, width=8).grid(row=2, column=3, padx=2)

        ttk.Label(params_frame, text="RSI Overbought:").grid(row=2, column=4, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_rsi_overbought, width=8).grid(row=2, column=5, padx=2)

        # HTF Parameters
        ttk.Label(params_frame, text="HTF Period:").grid(row=3, column=0, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_htf_period, width=8).grid(row=3, column=1, padx=2)
        
        # Consecutive Green Bars (moved to next row for better spacing)
        ttk.Label(params_frame, text="Green Bars Req:").grid(row=4, column=0, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_consecutive_green_bars, width=8).grid(row=4, column=1, padx=2)
        
        # Control Base SL Parameters
        ttk.Label(params_frame, text="Base SL Green Ticks:").grid(row=4, column=2, sticky="e", padx=2)
        ttk.Entry(params_frame, textvariable=self.ft_control_base_sl_green_ticks, width=8).grid(row=4, column=3, padx=2)
        
        # Control Base SL Enable checkbox
        ttk.Checkbutton(params_frame, text="Enable Control Base SL", 
                       variable=self.ft_control_base_sl_enabled).grid(row=4, column=4, columnspan=2, sticky="w", padx=2)
        
        # Info label explaining the feature
        ttk.Label(params_frame, text="(Normal uses 'Green Bars Req' above, Base SL uses higher value)", 
                 foreground='grey').grid(row=5, column=0, columnspan=6, sticky="w", padx=2, pady=(2,0))
        row += 1

        # Add separator between Strategy Configuration and Risk Management
        self._add_grid_separator(parent, row)
        row += 1

        # === RISK MANAGEMENT SECTION ===
        ttk.Label(parent, text="⚠️ Risk Management", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(25,5))
        row += 1

        risk_frame = ttk.LabelFrame(parent, text="Risk Controls")
        risk_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        risk_frame.columnconfigure((1,3), weight=1)



        # Stop Loss Controls
        ttk.Checkbutton(risk_frame, text="Stop Loss", variable=self.ft_use_stop_loss).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(risk_frame, text="Points:").grid(row=0, column=1, sticky="e", padx=2)
        ttk.Entry(risk_frame, textvariable=self.ft_base_sl_points, width=8).grid(row=0, column=2, padx=2)

        # Risk Per Trade
        ttk.Label(risk_frame, text="Risk % per Trade:").grid(row=0, column=3, sticky="e", padx=5)
        ttk.Entry(risk_frame, textvariable=self.ft_risk_per_trade, width=8).grid(row=0, column=4, padx=2)

        # Take Profit Controls
        ttk.Checkbutton(risk_frame, text="Take Profit", variable=self.ft_use_take_profit).grid(row=1, column=0, sticky="w", padx=5, pady=2)
        
        # TP Levels
        tp_labels = ["TP1:", "TP2:", "TP3:", "TP4:"]
        for i, (label, tp_var) in enumerate(zip(tp_labels, self.ft_tp_points[:4])):
            col_offset = i * 2
            if i < 2:  # First row
                ttk.Label(risk_frame, text=label).grid(row=1, column=1+col_offset, sticky="e", padx=2)
                ttk.Entry(risk_frame, textvariable=tp_var, width=6).grid(row=1, column=2+col_offset, padx=2)
            else:  # Second row
                ttk.Label(risk_frame, text=label).grid(row=2, column=1+(col_offset-4), sticky="e", padx=2)
                ttk.Entry(risk_frame, textvariable=tp_var, width=6).grid(row=2, column=2+(col_offset-4), padx=2)

        # Trailing Stop Controls
        ttk.Checkbutton(risk_frame, text="Trailing Stop", variable=self.ft_use_trail_stop).grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(risk_frame, text="Activation:").grid(row=3, column=1, sticky="e", padx=2)
        ttk.Entry(risk_frame, textvariable=self.ft_trail_activation, width=8).grid(row=3, column=2, padx=2)
        ttk.Label(risk_frame, text="Distance:").grid(row=3, column=3, sticky="e", padx=5)
        ttk.Entry(risk_frame, textvariable=self.ft_trail_distance, width=8).grid(row=3, column=4, padx=2)
        row += 1

        # === PRICE-ABOVE-EXIT FILTER SECTION ===
        ttk.Label(parent, text="🛡️ Price-Above-Exit Filter", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(15,5))
        row += 1
        
        filter_frame = ttk.Frame(parent)
        filter_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        
        # Filter Enable checkbox
        ttk.Checkbutton(filter_frame, text="Enable Filter", variable=self.ft_price_above_exit_filter_enabled).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        
        # Price Buffer
        ttk.Label(filter_frame, text="Price Buffer:").grid(row=0, column=1, sticky="e", padx=2)
        ttk.Entry(filter_frame, textvariable=self.ft_price_buffer_points, width=8).grid(row=0, column=2, padx=2)
        ttk.Label(filter_frame, text="pts").grid(row=0, column=3, sticky="w", padx=2)
        
        # Filter Duration
        ttk.Label(filter_frame, text="Duration:").grid(row=0, column=4, sticky="e", padx=(15,2))
        ttk.Entry(filter_frame, textvariable=self.ft_filter_duration_seconds, width=8).grid(row=0, column=5, padx=2)
        ttk.Label(filter_frame, text="sec").grid(row=0, column=6, sticky="w", padx=2)
        row += 1

        # Add separator between Filter and Session Management
        self._add_grid_separator(parent, row)
        row += 1

        # === SESSION MANAGEMENT SECTION ===
        ttk.Label(parent, text="⏰ Session Management", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(25,5))
        row += 1

        session_frame = ttk.LabelFrame(parent, text="Trading Session Controls")
        session_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        session_frame.columnconfigure((1,3), weight=1)



        # Intraday Toggle
        ttk.Checkbutton(session_frame, text="Intraday Only", variable=self.ft_is_intraday).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        
        # Session Times
        ttk.Label(session_frame, text="Start Time:").grid(row=0, column=1, sticky="e", padx=5)
        time_frame1 = ttk.Frame(session_frame)
        time_frame1.grid(row=0, column=2, sticky="w", padx=2)
        ttk.Entry(time_frame1, textvariable=self.ft_session_start_hour, width=3).pack(side='left')
        ttk.Label(time_frame1, text=":").pack(side='left')
        ttk.Entry(time_frame1, textvariable=self.ft_session_start_min, width=3).pack(side='left')

        ttk.Label(session_frame, text="End Time:").grid(row=0, column=3, sticky="e", padx=5)
        time_frame2 = ttk.Frame(session_frame)
        time_frame2.grid(row=0, column=4, sticky="w", padx=2)
        ttk.Entry(time_frame2, textvariable=self.ft_session_end_hour, width=3).pack(side='left')
        ttk.Label(time_frame2, text=":").pack(side='left')
        ttk.Entry(time_frame2, textvariable=self.ft_session_end_min, width=3).pack(side='left')

        # Auto Stop Controls
        ttk.Checkbutton(session_frame, text="Auto Stop", variable=self.ft_auto_stop_enabled).grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(session_frame, text="Max Trades/Day:").grid(row=1, column=1, sticky="e", padx=5)
        ttk.Entry(session_frame, textvariable=self.ft_max_trades_per_day, width=6).grid(row=1, column=2, padx=2)
        ttk.Label(session_frame, text="Max Loss/Day:").grid(row=1, column=3, sticky="e", padx=5)
        ttk.Entry(session_frame, textvariable=self.ft_max_loss_per_day, width=8).grid(row=1, column=4, padx=2)
        
        # Trade Block Controls
        ttk.Checkbutton(session_frame, text="Enable Trade Blocks", variable=self.ft_trade_block_enabled).grid(row=2, column=0, columnspan=2, sticky="w", padx=5, pady=(10,2))
        
        # Container for dynamic trade block entries
        self.trade_blocks_container = ttk.Frame(session_frame)
        self.trade_blocks_container.grid(row=3, column=0, columnspan=5, sticky="ew", padx=5, pady=5)
        
        # Add button for new trade blocks
        add_block_btn = ttk.Button(session_frame, text="➕ Add Trade Block", command=self._add_trade_block_field)
        add_block_btn.grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=2)
        
        # Initialize existing blocks in UI
        self._refresh_trade_blocks_ui()
        
        row += 1

        # === DATA SIMULATION SECTION (OPTIONAL) ===
        ttk.Label(parent, text="📊 Data Simulation (Optional)", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(25,5))
        row += 1

        data_sim_frame = ttk.LabelFrame(parent, text="File-Based Data Simulation")
        data_sim_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        data_sim_frame.columnconfigure((1,3), weight=1)

        ttk.Checkbutton(data_sim_frame, text="Enable File Simulation", variable=self.ft_use_file_simulation).grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Label(data_sim_frame, text="Data File:").grid(row=0, column=1, sticky="e", padx=5)
        ttk.Entry(data_sim_frame, textvariable=self.ft_data_file_path, width=30).grid(row=0, column=2, padx=2, sticky="ew")
        ttk.Button(data_sim_frame, text="Browse", command=self._ft_browse_data_file).grid(row=0, column=3, padx=5)
        
        # Help text
        help_label = ttk.Label(data_sim_frame, text="💡 User-controlled only: When enabled, uses ONLY selected CSV file data. No fallback data if WebSocket fails. Live trading completely preserved.", 
                              font=('TkDefaultFont', 8), foreground='gray')
        help_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=5, pady=(0,5))
        row += 1

        # Add separator
        self._add_grid_separator(parent, row)
        row += 1

        # === PERFORMANCE SETTINGS SECTION ===
        ttk.Label(parent, text="⚡ Performance Settings", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(25,5))
        row += 1

        perf_frame = ttk.LabelFrame(parent, text="Tick Consumption Mode")
        perf_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        perf_frame.columnconfigure(1, weight=1)

        ttk.Label(perf_frame, text="Consumption Mode:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        mode_combo = ttk.Combobox(perf_frame, 
                                 values=["⚡ Callback Mode (Fast - Default)", "📊 Polling Mode (Safe)"], 
                                 state="readonly", width=30)
        mode_combo.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Set initial value based on ft_use_direct_callbacks
        mode_combo.set("⚡ Callback Mode (Fast - Default)" if self.ft_use_direct_callbacks.get() else "📊 Polling Mode (Safe)")
        
        # Bind combo change to update the boolean variable
        def on_mode_change(event):
            selected = mode_combo.get()
            self.ft_use_direct_callbacks.set("Callback" in selected)
        mode_combo.bind("<<ComboboxSelected>>", on_mode_change)

        # Help text explaining the modes
        perf_help = ttk.Label(perf_frame, 
                             text="⚡ Callback Mode: Wind-style direct processing (~50ms latency, 29% faster)\n"
                                  "📊 Polling Mode: Queue-based processing (~70ms latency, proven stable)", 
                             font=('TkDefaultFont', 8), foreground='gray', justify='left')
        perf_help.grid(row=1, column=0, columnspan=2, sticky="w", padx=5, pady=(0,5))
        row += 1

        # Add separator between Performance and Capital Management
        self._add_grid_separator(parent, row)
        row += 1

        # === CAPITAL MANAGEMENT SECTION ===
        ttk.Label(parent, text="💰 Capital Management", style='SectionHeader.TLabel').grid(row=row, column=0, columnspan=2, sticky="w", pady=(25,5))
        row += 1

        capital_frame = ttk.LabelFrame(parent, text="Position Sizing & Capital Controls")
        capital_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        capital_frame.columnconfigure((1,3), weight=1)



        # Capital Settings
        ttk.Label(capital_frame, text="Initial Capital:").grid(row=0, column=0, sticky="e", padx=5, pady=2)
        ttk.Entry(capital_frame, textvariable=self.ft_initial_capital, width=12).grid(row=0, column=1, padx=2)

        ttk.Label(capital_frame, text="Max Positions:").grid(row=0, column=2, sticky="e", padx=5)
        ttk.Entry(capital_frame, textvariable=self.ft_max_positions, width=6).grid(row=0, column=3, padx=2)

        # Position Sizing Method
        ttk.Label(capital_frame, text="Position Sizing:").grid(row=1, column=0, sticky="e", padx=5, pady=2)
        sizing_combo = ttk.Combobox(capital_frame, textvariable=self.ft_position_size_method, 
                                   values=["fixed_amount", "fixed_quantity", "risk_based"], 
                                   state="readonly", width=12)
        sizing_combo.grid(row=1, column=1, padx=2)

        # Position Size Values
        ttk.Label(capital_frame, text="Fixed Amount:").grid(row=1, column=2, sticky="e", padx=5)
        ttk.Entry(capital_frame, textvariable=self.ft_fixed_amount, width=10).grid(row=1, column=3, padx=2)

        ttk.Label(capital_frame, text="Fixed Qty:").grid(row=1, column=4, sticky="e", padx=5)
        ttk.Entry(capital_frame, textvariable=self.ft_fixed_quantity, width=6).grid(row=1, column=5, padx=2)

        # Lot Size (read-only - driven by instrument selection SSOT)
        ttk.Label(capital_frame, text="Lot Size:").grid(row=2, column=0, sticky="e", padx=5, pady=2)
        ttk.Entry(capital_frame, textvariable=self.ft_lot_size, width=6, state="readonly").grid(row=2, column=1, padx=2)
        row += 1

        # Add bottom spacer to ensure content is fully scrollable
        bottom_spacer = ttk.Frame(parent, height=30)
        bottom_spacer.grid(row=row, column=0, columnspan=2, sticky="ew", pady=20)
        row += 1



    def _bt_browse_csv(self):
        """Browse for CSV file"""
        file = filedialog.askopenfilename(
            title="Select Data File",
            filetypes=[
                ("CSV and LOG files", "*.csv;*.log"),
                ("CSV files", "*.csv"),
                ("LOG files", "*.log"),
                ("All files", "*.*")
            ]
        )
        if file:
            self.bt_data_file.set(file)

    def _ft_browse_data_file(self):
        """Browse for Forward Test simulation data file"""
        file = filedialog.askopenfilename(
            title="Select Data File for Simulation",
            filetypes=[
                ("CSV files", "*.csv"),
                ("LOG files", "*.log"), 
                ("All files", "*.*")
            ]
        )
        if file:
            self.ft_data_file_path.set(file)
            logger.info(f"Selected simulation data file: {file}")

    def _add_trade_block_field(self):
        """Add a new trade block time entry field"""
        # Create new block with default values
        new_block = {
            'start_hour': tk.StringVar(value="14"),
            'start_min': tk.StringVar(value="29"),
            'end_hour': tk.StringVar(value="14"),
            'end_min': tk.StringVar(value="55")
        }
        self.ft_trade_blocks.append(new_block)
        
        # Refresh UI to show new block
        self._refresh_trade_blocks_ui()
        logger.info(f"Added trade block #{len(self.ft_trade_blocks)}")

    def _remove_trade_block_field(self, block_index):
        """Remove a trade block field by index"""
        if 0 <= block_index < len(self.ft_trade_blocks):
            self.ft_trade_blocks.pop(block_index)
            self._refresh_trade_blocks_ui()
            logger.info(f"Removed trade block #{block_index + 1}")

    def _refresh_trade_blocks_ui(self):
        """Refresh the trade blocks UI display"""
        # Clear existing widgets
        for widget in self.trade_blocks_container.winfo_children():
            widget.destroy()
        
        # If no blocks, show placeholder
        if not self.ft_trade_blocks:
            placeholder = ttk.Label(self.trade_blocks_container, 
                                   text="No trade blocks defined. Click '➕ Add Trade Block' to create one.",
                                   foreground="gray", font=('TkDefaultFont', 9, 'italic'))
            placeholder.grid(row=0, column=0, sticky="w", padx=5, pady=5)
            return
        
        # Create UI for each block
        for idx, block in enumerate(self.ft_trade_blocks):
            block_frame = ttk.Frame(self.trade_blocks_container)
            block_frame.grid(row=idx, column=0, sticky="ew", padx=5, pady=2)
            block_frame.columnconfigure(1, weight=1)
            
            # Block number label
            ttk.Label(block_frame, text=f"Block {idx + 1}:", font=('TkDefaultFont', 9, 'bold')).grid(row=0, column=0, sticky="w", padx=(0, 10))
            
            # Start time
            ttk.Label(block_frame, text="Start:").grid(row=0, column=1, sticky="e", padx=2)
            start_frame = ttk.Frame(block_frame)
            start_frame.grid(row=0, column=2, sticky="w", padx=2)
            ttk.Entry(start_frame, textvariable=block['start_hour'], width=3).pack(side='left', padx=1)
            ttk.Label(start_frame, text=":").pack(side='left')
            ttk.Entry(start_frame, textvariable=block['start_min'], width=3).pack(side='left', padx=1)
            
            # End time
            ttk.Label(block_frame, text="End:").grid(row=0, column=3, sticky="e", padx=(10, 2))
            end_frame = ttk.Frame(block_frame)
            end_frame.grid(row=0, column=4, sticky="w", padx=2)
            ttk.Entry(end_frame, textvariable=block['end_hour'], width=3).pack(side='left', padx=1)
            ttk.Label(end_frame, text=":").pack(side='left')
            ttk.Entry(end_frame, textvariable=block['end_min'], width=3).pack(side='left', padx=1)
            
            # Remove button
            remove_btn = ttk.Button(block_frame, text="❌", width=3, 
                                   command=lambda i=idx: self._remove_trade_block_field(i))
            remove_btn.grid(row=0, column=5, sticky="w", padx=(10, 0))

    def _bt_run_backtest(self):
        """Run backtest with GUI configuration (enforces frozen config)"""
        try:
            frozen_config = self.build_config_from_gui()
            if frozen_config is None:
                logger.warning("Backtest aborted: invalid or unfrozen configuration")
                return
 
            # Data path: STRICT CONFIG ACCESS - check frozen config first, then GUI input
            data_path = None
            try:
                # STRICT: Access frozen config directly - fail fast if malformed
                data_path = frozen_config["backtest"]["data_path"] if frozen_config["backtest"]["data_path"] else self.bt_data_file.get()
            except (KeyError, TypeError):
                # If config missing backtest section, fall back to GUI input only
                data_path = self.bt_data_file.get()

            if not data_path:
                messagebox.showerror("Missing Data", "Please select a data file for backtest.")
                return
 
            # Construct runner with frozen config (strict)
            runner = BacktestRunner(config=frozen_config, data_path=data_path)
            results = runner.run()
            self.display_backtest_results(results)
        except Exception as e:
            logger.exception("Backtest run failed: %s", e)
            messagebox.showerror("Backtest Error", f"Backtest failed: {e}")

    def _validate_nested_config(self, config):
        """Validate the nested configuration structure"""
        required_sections = ['strategy', 'risk', 'capital', 'instrument', 'session']
        for section in required_sections:
            if section not in config:
                raise ValueError(f"Missing required configuration section: {section}")
        logger.info("Configuration validation passed")

    def display_backtest_results(self, results):
        """Display backtest results in the results box"""
        if hasattr(self, 'bt_result_box'):
            self.bt_result_box.config(state="normal")
            self.bt_result_box.delete(1.0, tk.END)
            self.bt_result_box.insert(tk.END, f"Backtest Results:\n{results}\n")
            self.bt_result_box.config(state="disabled")

    def _merge_user_preferences_into_runtime_config(self):
        """Load and merge user preferences into runtime_config BEFORE widget initialization"""
        prefs_file = "user_preferences.json"
        if os.path.exists(prefs_file):
            try:
                with open(prefs_file, 'r') as f:
                    content = f.read().strip()
                    if not content:
                        logger.warning(f"Empty preferences file: {prefs_file}")
                        return
                    user_prefs = json.loads(content)

                # Shallow merge per-section (preserve keys not represented in prefs)
                for section, params in user_prefs.items():
                    if section in self.runtime_config and isinstance(params, dict):
                        self.runtime_config[section].update(params)

                logger.info(f"User preferences merged into runtime config from {prefs_file}")
            except Exception:
                logger.exception("Failed to merge user preferences into runtime config")

    def _initialize_variables_from_runtime_config(self):
        """Initialize GUI variables systematically from runtime_config (replaces _initialize_variables_from_defaults)"""
        # Variables are initialized in _initialize_all_variables() to avoid duplication
        pass

    def _create_gui_framework(self):
        """Create the core GUI framework - notebook and tabs"""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self)

        # Create tab frames
        self.bt_tab = ttk.Frame(self.notebook)
        self.ft_tab = ttk.Frame(self.notebook)
        self.monitor_tab = ttk.Frame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook)

        # Add tabs to notebook with horizontal spacing
        self.notebook.add(self.bt_tab, text="   Backtest   ")
        self.notebook.add(self.ft_tab, text="   Forward Test   ") 
        self.notebook.add(self.monitor_tab, text="   Monitor   ")
        self.notebook.add(self.log_tab, text="   Logs   ")

        # Pack notebook with vertical spacing
        self.notebook.pack(expand=1, fill="both", pady=(0, 15))

    def _build_backtest_tab(self):
        """Improved backtest tab with collapsible sections and better organization"""
        frame = self.bt_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        
        # Create scrollable main area
        canvas = tk.Canvas(frame)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Bind mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        # Enable keyboard navigation
        canvas.focus_set()
        
        # Pack scrollable area
        canvas.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        scrollbar.grid(row=1, column=1, sticky='ns')
        
        # Configure two-column layout
        scrollable_frame.columnconfigure(0, weight=1)
        scrollable_frame.columnconfigure(1, weight=1)
        
        # Create left and right column frames
        left_column = ttk.Frame(scrollable_frame)
        left_column.grid(row=0, column=0, sticky='nsew', padx=(0,5))
        
        right_column = ttk.Frame(scrollable_frame)
        right_column.grid(row=0, column=1, sticky='nsew', padx=(5,0))
        
        # Build sections in two columns with separators
        # Left column: Data, Strategy, Results
        self._build_data_section(left_column)
        self._add_section_separator(left_column)
        self._build_strategy_section(left_column)
        self._add_section_separator(left_column)
        self._build_results_section(left_column)
        
        # Right column: Risk, Instrument, Session
        self._build_risk_section(right_column)
        self._add_section_separator(right_column)
        self._build_instrument_section(right_column)
        self._add_section_separator(right_column)
        self._build_session_section(right_column)
        
        # Action buttons at top
        self._create_action_buttons(frame)

    def _build_data_section(self, parent):
        """Data file selection - always visible"""
        section = CollapsibleFrame(parent, "Data & File Selection", collapsed=False)
        content = section.get_content_frame()
        content.columnconfigure(1, weight=1)
        
        # Data file row
        ttk.Label(content, text="Data File:").grid(row=0, column=0, sticky='e', padx=(5,10), pady=5)
        
        file_frame = ttk.Frame(content)
        file_frame.grid(row=0, column=1, sticky='ew', padx=(0,5), pady=5)
        file_frame.columnconfigure(0, weight=1)
        
        ttk.Entry(file_frame, textvariable=self.bt_data_file).grid(row=0, column=0, sticky='ew', padx=(0,5))
        ttk.Button(file_frame, text="Browse", command=self._bt_browse_csv).grid(row=0, column=1)
        
        section.pack(fill='x', pady=(0,10))

    def _build_strategy_section(self, parent):
        """Enhanced strategy configuration with grouped indicators and parameters"""
        section = CollapsibleFrame(parent, "Strategy Configuration", collapsed=False)
        content = section.get_content_frame()
        
        # Configure main grid layout
        content.columnconfigure(1, weight=1)
        
        # Create standardized styles for all UI elements
        self._create_ui_styles()
        
        # Build indicator groups with their parameters
        self._build_core_indicators_group(content)
        self._build_optional_indicators_group(content)
        
        section.pack(fill='x', pady=(0,10))

    def _build_core_indicators_group(self, parent):
        """Core indicators: EMA, MACD, VWAP, and Consecutive Green Pattern"""
        row_start = 0
        
        # Group header
        ttk.Label(parent, text="� INDICATORS", 
                 style='GroupHeader.TLabel').grid(row=row_start, column=0, columnspan=6, 
                                                 sticky='w', pady=(10,5))
        
        # EMA Crossover with parameters
        row = row_start + 1
        ema_section = CollapsibleFrame(parent, "EMA Crossover", collapsed=not self.bt_use_ema_crossover.get())
        ema_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=5, pady=5)
        ema_frame = ema_section.get_content_frame()
        ema_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        ema_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('ema_crossover', ema_section))
        
        # EMA Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_ema_crossover.get() else 'Parameter.TLabel'
        ttk.Label(ema_frame, text="Fast Period:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.ema_fast_entry = ttk.Entry(ema_frame, textvariable=self.bt_fast_ema, width=8, style='Standard.TEntry')
        self.ema_fast_entry.grid(row=0, column=1, sticky='w', padx=(0,15))
        
        ttk.Label(ema_frame, text="Slow Period:", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.ema_slow_entry = ttk.Entry(ema_frame, textvariable=self.bt_slow_ema, width=8, style='Standard.TEntry')
        self.ema_slow_entry.grid(row=0, column=3, sticky='w')
        
        # Store section reference for collapsing
        self.ema_crossover_section = ema_section
        
        # MACD with parameters
        row += 1
        macd_section = CollapsibleFrame(parent, "MACD", collapsed=not self.bt_use_macd.get())
        macd_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=5, pady=5)
        macd_frame = macd_section.get_content_frame()
        macd_frame.columnconfigure((1,3,5), weight=1)
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        macd_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('macd', macd_section))
        
        # MACD Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_macd.get() else 'Parameter.TLabel'
        ttk.Label(macd_frame, text="Fast:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.macd_fast_entry = ttk.Entry(macd_frame, textvariable=self.bt_macd_fast, width=6, style='Standard.TEntry')
        self.macd_fast_entry.grid(row=0, column=1, sticky='w', padx=(0,10))
        
        ttk.Label(macd_frame, text="Slow:", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.macd_slow_entry = ttk.Entry(macd_frame, textvariable=self.bt_macd_slow, width=6, style='Standard.TEntry')
        self.macd_slow_entry.grid(row=0, column=3, sticky='w', padx=(0,10))
        
        ttk.Label(macd_frame, text="Signal:", style=initial_style).grid(row=0, column=4, sticky='e', padx=(0,5))
        self.macd_signal_entry = ttk.Entry(macd_frame, textvariable=self.bt_macd_signal, width=6, style='Standard.TEntry')
        self.macd_signal_entry.grid(row=0, column=5, sticky='w')
        
        # Store section reference for collapsing
        self.macd_section = macd_section
        
        # VWAP (no parameters needed)
        row += 1
        vwap_section = CollapsibleFrame(parent, "VWAP", collapsed=not self.bt_use_vwap.get())
        vwap_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=5, pady=5)
        vwap_frame = vwap_section.get_content_frame()
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        vwap_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('vwap', vwap_section))
        
        # VWAP message - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_vwap.get() else 'Parameter.TLabel'
        ttk.Label(vwap_frame, text="No parameters required for VWAP", 
                 style=initial_style, foreground='grey').grid(row=0, column=0, sticky='w', pady=5)
        
        # Store section reference for collapsing
        self.vwap_section = vwap_section
        
        # Consecutive Green Tick Pattern
        row += 1
        green_section = CollapsibleFrame(parent, "Consecutive Green Tick Pattern", collapsed=not self.bt_use_consecutive_green.get())
        green_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=5, pady=5)
        green_frame = green_section.get_content_frame()
        green_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        green_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('consecutive_green', green_section))
        
        # Parameters for consecutive green - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_consecutive_green.get() else 'Parameter.TLabel'
        ttk.Label(green_frame, text="Required Green Bars:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.green_bars_entry = ttk.Entry(green_frame, textvariable=self.bt_consecutive_green_bars, width=6, style='Standard.TEntry')
        self.green_bars_entry.grid(row=0, column=1, sticky='w', padx=(0,15))
        
        # Noise Filter Parameters
        ttk.Label(green_frame, text="Noise Filter (%):", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.noise_filter_entry = ttk.Entry(green_frame, textvariable=self.bt_noise_filter_percentage, width=8, style='Standard.TEntry')
        self.noise_filter_entry.grid(row=0, column=3, sticky='w')
        
        # Second row for additional noise filter parameters
        ttk.Label(green_frame, text="Min Ticks:", style=initial_style).grid(row=1, column=0, sticky='e', padx=(0,5))
        self.noise_ticks_entry = ttk.Entry(green_frame, textvariable=self.bt_noise_filter_min_ticks, width=6, style='Standard.TEntry')
        self.noise_ticks_entry.grid(row=1, column=1, sticky='w', padx=(0,15))
        
        ttk.Checkbutton(green_frame, text="Enable Noise Filter", 
                       variable=self.bt_noise_filter_enabled).grid(row=1, column=2, columnspan=2, sticky='w', padx=(0,5))
        
        # Store section reference for collapsing
        self.consecutive_green_section = green_section

        # Control Base SL Settings
        row += 1
        control_sl_section = CollapsibleFrame(parent, "Control Base SL", collapsed=not self.bt_control_base_sl_enabled.get())
        control_sl_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=5, pady=5)
        control_sl_frame = control_sl_section.get_content_frame()
        control_sl_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to feature enable/disable
        control_sl_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('control_base_sl', control_sl_section))
        
        # Control Base SL Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_control_base_sl_enabled.get() else 'Parameter.TLabel'
        ttk.Label(control_sl_frame, text="Green Ticks After Base SL:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.control_sl_green_ticks_entry = ttk.Entry(control_sl_frame, textvariable=self.bt_control_base_sl_green_ticks, width=6, style='Standard.TEntry')
        self.control_sl_green_ticks_entry.grid(row=0, column=1, sticky='w', padx=(0,15))
        
        # Info label explaining the feature
        ttk.Label(control_sl_frame, text="(Normal uses 'Green Bars Req' above)", 
                 style='Parameter.TLabel', foreground='grey').grid(row=0, column=2, columnspan=2, sticky='w', padx=(10,0))
        
        # Enable checkbox in second row
        ttk.Checkbutton(control_sl_frame, text="Enable Control Base SL", 
                       variable=self.bt_control_base_sl_enabled).grid(row=1, column=0, columnspan=4, sticky='w', pady=(5,0))
        
        # Store section reference for collapsing
        self.control_base_sl_section = control_sl_section

    def _build_optional_indicators_group(self, parent):
        """Optional indicators with toggle functionality: HTF, RSI, Bollinger Bands, Stochastic, ATR"""
        # Find next available row
        used_rows = [int(child.grid_info()['row']) for child in parent.grid_slaves() if child.grid_info()]
        row_start = max(used_rows) + 1 if used_rows else 0
        
        # Optional indicators group header with checkbox
        header_frame = ttk.Frame(parent)
        header_frame.grid(row=row_start, column=0, columnspan=6, sticky='ew', pady=(10,5))
        header_frame.columnconfigure(1, weight=1)
        
        # Checkbox to toggle optional indicators visibility
        self.optional_indicators_checkbox = ttk.Checkbutton(
            header_frame, 
            text="🔧 OPTIONAL INDICATORS", 
            variable=self.bt_show_optional_indicators,
            style='GroupHeader.TCheckbutton',
            command=self._toggle_optional_indicators_visibility
        )
        self.optional_indicators_checkbox.grid(row=0, column=0, sticky='w')
        
        # Container for optional indicators (initially visible if checkbox is checked)
        self.optional_indicators_frame = ttk.Frame(parent)
        self.optional_indicators_frame.grid(row=row_start + 1, column=0, columnspan=6, sticky='ew', padx=5)
        self.optional_indicators_frame.columnconfigure(0, weight=1)
        
        # RSI Filter with parameters
        row = 0
        rsi_section = CollapsibleFrame(self.optional_indicators_frame, "RSI Filter", collapsed=not self.bt_use_rsi_filter.get())
        rsi_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=0, pady=5)
        rsi_frame = rsi_section.get_content_frame()
        rsi_frame.columnconfigure((1,3,5), weight=1)
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        rsi_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('rsi_filter', rsi_section))
        
        # RSI Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_rsi_filter.get() else 'Parameter.TLabel'
        ttk.Label(rsi_frame, text="Period:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.rsi_length_entry = ttk.Entry(rsi_frame, textvariable=self.bt_rsi_length, width=6, style='Standard.TEntry')
        self.rsi_length_entry.grid(row=0, column=1, sticky='w', padx=(0,10))
        
        ttk.Label(rsi_frame, text="Oversold:", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.rsi_oversold_entry = ttk.Entry(rsi_frame, textvariable=self.bt_rsi_oversold, width=6, style='Standard.TEntry')
        self.rsi_oversold_entry.grid(row=0, column=3, sticky='w', padx=(0,10))
        
        ttk.Label(rsi_frame, text="Overbought:", style=initial_style).grid(row=0, column=4, sticky='e', padx=(0,5))
        self.rsi_overbought_entry = ttk.Entry(rsi_frame, textvariable=self.bt_rsi_overbought, width=6, style='Standard.TEntry')
        self.rsi_overbought_entry.grid(row=0, column=5, sticky='w')
        
        # Store section reference for collapsing
        self.rsi_filter_section = rsi_section
        
        # HTF Trend Filter with parameters
        row += 1
        htf_section = CollapsibleFrame(self.optional_indicators_frame, "Higher Timeframe Trend", collapsed=not self.bt_use_htf_trend.get())
        htf_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=0, pady=5)
        htf_frame = htf_section.get_content_frame()
        htf_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        htf_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('htf_trend', htf_section))
        
        # HTF Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_htf_trend.get() else 'Parameter.TLabel'
        ttk.Label(htf_frame, text="HTF Period:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.htf_period_entry = ttk.Entry(htf_frame, textvariable=self.bt_htf_period, width=8, style='Standard.TEntry')
        self.htf_period_entry.grid(row=0, column=1, sticky='w')
        
        # Store section reference for collapsing
        self.htf_trend_section = htf_section
        
        # Bollinger Bands (placeholder - parameters would be added here)
        row += 1
        bb_section = CollapsibleFrame(self.optional_indicators_frame, "Bollinger Bands", collapsed=not self.bt_use_bollinger_bands.get())
        bb_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=0, pady=5)
        bb_frame = bb_section.get_content_frame()
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        bb_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('bollinger_bands', bb_section))
        
        # BB message - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_bollinger_bands.get() else 'Parameter.TLabel'
        ttk.Label(bb_frame, text="Bollinger Bands parameters (to be implemented)", 
                 style=initial_style, foreground='grey').grid(row=0, column=0, sticky='w', pady=5)
        
        # Store section reference for collapsing
        self.bollinger_bands_section = bb_section
        
        # Stochastic (placeholder)
        row += 1
        stoch_section = CollapsibleFrame(self.optional_indicators_frame, "Stochastic", collapsed=not self.bt_use_stochastic.get())
        stoch_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=0, pady=5)
        stoch_frame = stoch_section.get_content_frame()
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        stoch_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('stochastic', stoch_section))
        
        # Stochastic message - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_stochastic.get() else 'Parameter.TLabel'
        ttk.Label(stoch_frame, text="Stochastic parameters (to be implemented)", 
                 style=initial_style, foreground='grey').grid(row=0, column=0, sticky='w', pady=5)
        
        # Store section reference for collapsing
        self.stochastic_section = stoch_section
        
        # ATR (placeholder)
        row += 1
        atr_section = CollapsibleFrame(self.optional_indicators_frame, "Average True Range", collapsed=not self.bt_use_atr.get())
        atr_section.grid(row=row, column=0, columnspan=6, sticky='ew', padx=0, pady=5)
        atr_frame = atr_section.get_content_frame()
        
        # Connect CollapsibleFrame toggle to indicator enable/disable
        atr_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('atr', atr_section))
        
        # ATR message - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_atr.get() else 'Parameter.TLabel'
        ttk.Label(atr_frame, text="ATR parameters (to be implemented)", 
                 style=initial_style, foreground='grey').grid(row=0, column=0, sticky='w', pady=5)
        
        # Store section reference for collapsing
        self.atr_section = atr_section
        
        # Set initial visibility based on checkbox state
        self._toggle_optional_indicators_visibility()

    def _toggle_optional_indicators_visibility(self):
        """Toggle the visibility of optional indicators based on checkbox state"""
        if self.bt_show_optional_indicators.get():
            self.optional_indicators_frame.grid()
        else:
            self.optional_indicators_frame.grid_remove()

    def _build_risk_section(self, parent):
        """Risk management with collapsible components"""
        main_section = CollapsibleFrame(parent, "Risk Management", collapsed=False)
        content = main_section.get_content_frame()
        
        # Build individual risk components
        self._build_stop_loss_section(content)
        self._build_take_profit_section(content)
        self._build_trailing_stop_section(content)
        
        main_section.pack(fill='x', pady=(0,10))

    def _build_stop_loss_section(self, parent):
        """Stop Loss component"""
        # Create checkbox variable for stop loss (always enabled in this system)
        if not hasattr(self, 'bt_use_stop_loss'):
            self.bt_use_stop_loss = tk.BooleanVar(value=True)
        
        sl_section = CollapsibleFrame(parent, "Stop Loss", collapsed=not self.bt_use_stop_loss.get())
        sl_section.pack(fill='x', pady=(5,5))
        sl_frame = sl_section.get_content_frame()
        sl_frame.columnconfigure(1, weight=1)
        
        # Connect CollapsibleFrame toggle to component enable/disable
        sl_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('stop_loss', sl_section))
        
        # Stop Loss Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_stop_loss.get() else 'Parameter.TLabel'
        ttk.Label(sl_frame, text="Stop Loss Points:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.sl_points_entry = ttk.Entry(sl_frame, textvariable=self.bt_base_sl_points, width=12, style='Standard.TEntry')
        self.sl_points_entry.grid(row=0, column=1, sticky='w', padx=(0,5))
        
        ttk.Label(sl_frame, text="Risk % per Trade:", style=initial_style).grid(row=1, column=0, sticky='e', padx=(0,5))
        self.risk_percent_entry = ttk.Entry(sl_frame, textvariable=self.bt_risk_per_trade_percent, width=12, style='Standard.TEntry')
        self.risk_percent_entry.grid(row=1, column=1, sticky='w', padx=(0,5))
        
        # Store section reference
        self.stop_loss_section = sl_section

    def _build_take_profit_section(self, parent):
        """Take Profit component"""
        # Create checkbox variable for take profit 
        if not hasattr(self, 'bt_use_take_profit'):
            self.bt_use_take_profit = tk.BooleanVar(value=True)
        
        tp_section = CollapsibleFrame(parent, "Take Profit", collapsed=not self.bt_use_take_profit.get())
        tp_section.pack(fill='x', pady=(5,5))
        tp_frame = tp_section.get_content_frame()
        tp_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to component enable/disable
        tp_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('take_profit', tp_section))
        
        # Take Profit Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_take_profit.get() else 'Parameter.TLabel'
        ttk.Label(tp_frame, text="TP1 Points:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.tp1_entry = ttk.Entry(tp_frame, textvariable=self.bt_tp_points[0], width=8, style='Standard.TEntry')
        self.tp1_entry.grid(row=0, column=1, sticky='w', padx=(0,10))
        
        ttk.Label(tp_frame, text="TP2 Points:", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.tp2_entry = ttk.Entry(tp_frame, textvariable=self.bt_tp_points[1], width=8, style='Standard.TEntry')
        self.tp2_entry.grid(row=0, column=3, sticky='w')
        
        ttk.Label(tp_frame, text="TP3 Points:", style=initial_style).grid(row=1, column=0, sticky='e', padx=(0,5))
        self.tp3_entry = ttk.Entry(tp_frame, textvariable=self.bt_tp_points[2], width=8, style='Standard.TEntry')
        self.tp3_entry.grid(row=1, column=1, sticky='w', padx=(0,10))
        
        ttk.Label(tp_frame, text="TP4 Points:", style=initial_style).grid(row=1, column=2, sticky='e', padx=(0,5))
        self.tp4_entry = ttk.Entry(tp_frame, textvariable=self.bt_tp_points[3], width=8, style='Standard.TEntry')
        self.tp4_entry.grid(row=1, column=3, sticky='w')
        
        # Store section reference
        self.take_profit_section = tp_section

    def _build_trailing_stop_section(self, parent):
        """Trailing Stop Loss component"""
        trail_section = CollapsibleFrame(parent, "Trailing Stop Loss", collapsed=not self.bt_use_trail_stop.get())
        trail_section.pack(fill='x', pady=(5,5))
        trail_frame = trail_section.get_content_frame()
        trail_frame.columnconfigure((1,3), weight=1)
        
        # Connect CollapsibleFrame toggle to component enable/disable
        trail_section.toggle_btn.configure(command=lambda: self._sync_collapsible_with_indicator('trail_stop', trail_section))
        
        # Trailing Stop Parameters - use enabled style if initially checked
        initial_style = 'Enabled.TLabel' if self.bt_use_trail_stop.get() else 'Parameter.TLabel'
        ttk.Label(trail_frame, text="Trail Activation:", style=initial_style).grid(row=0, column=0, sticky='e', padx=(0,5))
        self.trail_activation_entry = ttk.Entry(trail_frame, textvariable=self.bt_trail_activation, width=12, style='Standard.TEntry')
        self.trail_activation_entry.grid(row=0, column=1, sticky='w', padx=(0,10))
        
        ttk.Label(trail_frame, text="Trail Distance:", style=initial_style).grid(row=0, column=2, sticky='e', padx=(0,5))
        self.trail_distance_entry = ttk.Entry(trail_frame, textvariable=self.bt_trail_distance, width=12, style='Standard.TEntry')
        self.trail_distance_entry.grid(row=0, column=3, sticky='w')
        
        # Store section reference
        self.trailing_stop_section = trail_section

    def _build_instrument_section(self, parent):
        """Instrument and capital settings - collapsible"""  
        section = CollapsibleFrame(parent, "Instrument & Capital", collapsed=False)
        content = section.get_content_frame()
        content.columnconfigure(1, weight=1)
        content.columnconfigure(3, weight=1)
        
        # Instrument and capital settings
        settings = [
            ("Symbol:", self.bt_symbol, 15, False),
            ("Exchange:", self.bt_exchange, 10, False),  
            ("Lot Size:", self.bt_lot_size, 8, True),  # Read-only (from SSOT)
            ("Initial Capital:", self.bt_initial_capital, 15, False)
        ]
        
        for i, (label, var, width, readonly) in enumerate(settings):
            row, col_pair = divmod(i, 2)
            base_col = col_pair * 2
            ttk.Label(content, text=label).grid(row=row, column=base_col, sticky='e', padx=5, pady=2)
            state = "readonly" if readonly else "normal"
            ttk.Entry(content, textvariable=var, width=width, state=state).grid(row=row, column=base_col+1, sticky='w', padx=(0,15), pady=2)
        
        section.pack(fill='x', pady=(0,10))

    def _build_session_section(self, parent):
        """Session timing - collapsible"""
        section = CollapsibleFrame(parent, "Session Settings", collapsed=True)
        content = section.get_content_frame()
        
        # Time settings
        time_frame = ttk.Frame(content)
        time_frame.pack(fill='x', pady=5)
        
        ttk.Label(time_frame, text="Start:").grid(row=0, column=0, sticky='e', padx=5)
        ttk.Entry(time_frame, textvariable=self.bt_session_start_hour, width=4).grid(row=0, column=1, padx=2)
        ttk.Label(time_frame, text=":").grid(row=0, column=2)
        ttk.Entry(time_frame, textvariable=self.bt_session_start_min, width=4).grid(row=0, column=3, padx=2)
        
        ttk.Label(time_frame, text="End:").grid(row=0, column=4, sticky='e', padx=(15,5))
        ttk.Entry(time_frame, textvariable=self.bt_session_end_hour, width=4).grid(row=0, column=5, padx=2)
        ttk.Label(time_frame, text=":").grid(row=0, column=6)
        ttk.Entry(time_frame, textvariable=self.bt_session_end_min, width=4).grid(row=0, column=7, padx=2)
        
        ttk.Checkbutton(content, text="Intraday Trading", variable=self.bt_is_intraday).pack(anchor='w', pady=(10,0))
        
        section.pack(fill='x', pady=(0,10))

    def _build_results_section(self, parent):
        """Results display - always visible"""
        section = CollapsibleFrame(parent, "Backtest Results", collapsed=True)
        content = section.get_content_frame()
        
        # Results text box
        self.bt_result_box = tk.Text(content, height=15, state='disabled', wrap='word', font=('Consolas', 14))
        results_scroll = ttk.Scrollbar(content, orient="vertical", command=self.bt_result_box.yview)
        self.bt_result_box.configure(yscrollcommand=results_scroll.set)
        
        self.bt_result_box.pack(side='left', fill='both', expand=True)
        results_scroll.pack(side='right', fill='y')
        
        section.pack(fill='both', expand=True, pady=(0,10))

    def _create_ui_styles(self):
        """Create comprehensive standardized styles for all UI elements"""
        style = ttk.Style()
        
        # === FONT STANDARDS ===
        # Main title: 12pt bold (section headers)
        # Subheading: 10pt bold (group headers, indicator names) 
        # Body text: 9pt normal (labels, descriptions)
        # Input fields: 9pt normal (entries, consistent with labels)
        # Small text: 8pt normal (notes, hints)
        
        # === SECTION HEADERS ===
        style.configure('SectionHeader.TLabel', 
                       font=('Segoe UI', 18, 'bold', 'underline'), 
                       foreground='pink')
        
        # === GROUP HEADERS ===  
        style.configure('GroupHeader.TLabel', 
                       font=('Segoe UI', 14, 'bold', 'underline'), 
                       foreground='darkblue')
        
        # === GROUP HEADER CHECKBUTTONS ===
        style.configure('GroupHeader.TCheckbutton', 
                       font=('Segoe UI', 13, 'bold', 'underline' ,), 
                       foreground='darkblue')
        
        # === LABELFRAME HEADERS ===
        style.configure('TLabelframe.Label', 
                       font=('Segoe UI', 13, 'bold', 'underline'), 
                       foreground='darkblue')
        
        # === INDICATOR LABELS ===
        style.configure('Indicator.TLabel', 
                       font=('Segoe UI', 14, 'bold'), 
                       foreground='violet')
        
        # === STANDARD LABELS ===
        style.configure('Standard.TLabel', 
                       font=('Segoe UI', 14), 
                       foreground='brown')
        
        # === PARAMETER LABELS ===
        style.configure('Parameter.TLabel', 
                       font=('Segoe UI', 14), 
                       foreground='pink')
        
        # === INPUT ENTRIES ===
        style.configure('Standard.TEntry', 
                       font=('Segoe UI', 14))
        
        # === BUTTONS ===
        style.configure('Standard.TButton', 
                       font=('Segoe UI', 15, ))
        
        # === RUN BACKTEST BUTTON ===
        style.configure('RunBacktest.TButton', 
                       font=('Segoe UI', 18, 'bold'))  # Double size button (9*2=18)
        
        # === CHECKBUTTONS ===
        style.configure('Standard.TCheckbutton', 
                       font=('Segoe UI', 15))
        
        # === NOTEBOOK TABS ===
        style.configure('TNotebook.Tab', 
                       font=('Segoe UI', 18, 'bold'))  # Double size tabs (9*2=18)
        
        # === COLLAPSIBLE FRAME HEADERS ===
        style.configure('CollapsibleHeader.TCheckbutton', 
                       font=('Segoe UI', 14, 'bold', 'underline'),
                       foreground='red')
        
        # === ENABLED/ACTIVE STATES (LIGHTER GREEN) ===
        style.configure('Enabled.TCheckbutton', 
                       font=('Segoe UI', 17, 'bold underline' ),
                       foreground='#4CAF50')  # Even lighter green for enabled functionality
        
        style.configure('Enabled.TLabel', 
                       font=('Segoe UI', 14, 'bold'),
                       foreground='green')  # Even lighter green for enabled parameter labels
        
        style.configure('EnabledGroup.TLabel', 
                       font=('Segoe UI', 15, 'bold'),
                       foreground='darkblue')  # Even lighter green for enabled group headers
        
        # === SMALL TEXT/NOTES ===
        style.configure('Note.TLabel', 
                       font=('Segoe UI', 14), 
                       foreground='darkblue')
        
        # === SEPARATORS/GRID LINES ===
        style.configure('Separator.TSeparator', 
                       background='#CCCCCC',
                       borderwidth=1,
                       relief='solid')
        
        style.configure('SectionSeparator.TFrame', 
                       background='#CCCCCC',
                       relief='solid',
                       borderwidth=1)
        
        # === INFO TEXT ===
        style.configure('Info.TLabel', 
                       font=('Segoe UI', 14), 
                       foreground='blue')
        
        # === DISABLED ELEMENTS ===
        style.configure('Disabled.TLabel', 
                       font=('Segoe UI', 14), 
                       foreground='gray')
        
        style.configure('Disabled.TEntry', 
                       font=('Segoe UI', 14),
                       fieldbackground='lightgray')

    def _add_section_separator(self, parent, pady=10):
        """Add a visual separator line between sections"""
        separator = ttk.Separator(parent, orient='horizontal', style='Separator.TSeparator')
        separator.pack(fill='x', pady=pady, padx=20)
        return separator
    
    def _add_grid_separator(self, parent, row, column=0, columnspan=2, pady=(10,10)):
        """Add a visual separator line in a grid-based layout"""
        separator = ttk.Separator(parent, orient='horizontal', style='Separator.TSeparator')
        separator.grid(row=row, column=column, columnspan=columnspan, sticky='ew', pady=pady, padx=20)
        return separator
    
    def _add_grid_frame(self, parent, pady=(10,10)):
        """Add a frame with grid-like borders for section separation"""
        grid_frame = ttk.Frame(parent, style='SectionSeparator.TFrame', height=2)
        grid_frame.pack(fill='x', pady=pady, padx=10)
        return grid_frame

    def _sync_collapsible_with_indicator(self, group_name, section):
        """Sync CollapsibleFrame state with indicator enable/disable variable and colors"""
        # Get the appropriate variable name
        if group_name == 'consecutive_green':
            var_name = 'bt_use_consecutive_green'
        elif group_name == 'control_base_sl':
            var_name = 'bt_control_base_sl_enabled'
        elif group_name in ['stop_loss', 'take_profit']:
            var_name = f'bt_use_{group_name}'
        elif group_name == 'trail_stop':
            var_name = 'bt_use_trail_stop'
        else:
            var_name = f'bt_use_{group_name}'
        
        # Get the checkbox variable
        checkbox_var = getattr(self, var_name)
        
        # Sync the checkbox variable with the CollapsibleFrame state
        # When section is expanded (toggle_var = True), indicator should be enabled
        # When section is collapsed (toggle_var = False), indicator should be disabled
        checkbox_var.set(section.toggle_var.get())
        
        # Call the original toggle_content to handle the expand/collapse AND color changes
        section.toggle_content()
        
        # Additional visual feedback: Update any group headers if they exist
        self._update_group_header_colors(group_name, section.toggle_var.get())
    
    def _update_group_header_colors(self, group_name, is_enabled):
        """Update group header colors based on enabled state"""
        try:
            # Find and update group headers related to this functionality
            group_headers = {
                'ema_crossover': '� INDICATORS',
                'macd': '� INDICATORS', 
                'vwap': '📊 INDICATORS',
                'consecutive_green': '📊 INDICATORS',
                'rsi_filter': '🔧 OPTIONAL INDICATORS',
                'htf_trend': '🔧 OPTIONAL INDICATORS',
                'bollinger_bands': '� OPTIONAL INDICATORS',
                'stochastic': '� OPTIONAL INDICATORS',
                'atr': '🔧 OPTIONAL INDICATORS'
            }
            
            if group_name in group_headers:
                header_text = group_headers[group_name]
                style = 'EnabledGroup.TLabel' if is_enabled else 'GroupHeader.TLabel'
                
                # Find the header widget and update its style
                # This is a best-effort update for visual consistency
                self._find_and_update_header_style(header_text, style)
                
        except Exception:
            # Visual feedback is nice-to-have, don't break functionality if it fails
            pass
    
    def _find_and_update_header_style(self, header_text, style):
        """Find header widget by text and update its style"""
        try:
            # This is a helper method to find specific header labels
            # Implementation can be enhanced as needed for specific cases
            pass
        except Exception:
            pass

    def _create_action_buttons(self, parent):
        """Action buttons at top of interface"""
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(5,10))
        
        # Run button
        ttk.Button(button_frame, text="Run Backtest", command=self._bt_run_backtest, style='RunBacktest.TButton').pack(side='left')

    def _build_monitor_tab(self):
        """Build the dedicated monitoring tab for forward test visual feedback"""
        frame = self.monitor_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        
        # Header with status info
        header_frame = ttk.Frame(frame)
        header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        header_frame.columnconfigure(1, weight=1)
        
        ttk.Label(header_frame, text="🚀 Forward Test - Live Monitor", style='SectionHeader.TLabel').grid(row=0, column=0, sticky="w")
        
        # Status summary in header
        status_summary = ttk.Frame(header_frame)
        status_summary.grid(row=0, column=1, sticky="e")
        
        ttk.Label(status_summary, text="Status:").grid(row=0, column=0, padx=5)
        self.monitor_trading_status = tk.StringVar(value="⏸️ Stopped")
        ttk.Label(status_summary, textvariable=self.monitor_trading_status, style='Enabled.TLabel').grid(row=0, column=1, padx=5)
        
        ttk.Label(status_summary, text="P&L:").grid(row=0, column=2, padx=(15,5))
        self.monitor_pnl_status = tk.StringVar(value="₹0.00") 
        self.monitor_pnl_label = ttk.Label(status_summary, textvariable=self.monitor_pnl_status)
        self.monitor_pnl_label.grid(row=0, column=3, padx=5)

        # Main monitoring content with tabs
        monitor_notebook = ttk.Notebook(frame)
        monitor_notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0,10))
        
        # Live Updates Tab
        live_frame = ttk.Frame(monitor_notebook)
        monitor_notebook.add(live_frame, text="📊 Live Updates")
        
        live_frame.columnconfigure(0, weight=1)
        live_frame.rowconfigure(0, weight=1)
        
        # Create the text boxes for monitoring (shared with popup window)
        if not hasattr(self, 'ft_result_box'):
            self.ft_result_box = tk.Text(live_frame, height=25, state='disabled', wrap='word', font=('Consolas', 14))
        
        ft_results_scroll = ttk.Scrollbar(live_frame, orient="vertical", command=self.ft_result_box.yview)
        self.ft_result_box.configure(yscrollcommand=ft_results_scroll.set)
        
        self.ft_result_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
        ft_results_scroll.grid(row=0, column=1, sticky='ns')

        # Signals Tab
        signals_frame = ttk.Frame(monitor_notebook)
        monitor_notebook.add(signals_frame, text="🎯 Trading Signals")
        
        signals_frame.columnconfigure(0, weight=1)
        signals_frame.rowconfigure(0, weight=1)
        
        if not hasattr(self, 'ft_signals_box'):
            self.ft_signals_box = tk.Text(signals_frame, height=25, state='disabled', wrap='word', font=('Consolas', 14))
        
        signals_scroll = ttk.Scrollbar(signals_frame, orient="vertical", command=self.ft_signals_box.yview)
        self.ft_signals_box.configure(yscrollcommand=signals_scroll.set)
        
        self.ft_signals_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
        signals_scroll.grid(row=0, column=1, sticky='ns')

        # Performance Tab
        perf_frame = ttk.Frame(monitor_notebook)
        monitor_notebook.add(perf_frame, text="📈 Performance")
        
        perf_frame.columnconfigure(0, weight=1)
        perf_frame.rowconfigure(0, weight=1)
        
        if not hasattr(self, 'ft_performance_box'):
            self.ft_performance_box = tk.Text(perf_frame, height=25, state='disabled', wrap='word', font=('Consolas', 14))
        
        perf_scroll = ttk.Scrollbar(perf_frame, orient="vertical", command=self.ft_performance_box.yview)
        self.ft_performance_box.configure(yscrollcommand=perf_scroll.set)
        
        self.ft_performance_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
        perf_scroll.grid(row=0, column=1, sticky='ns')

        # Status Details Tab
        status_frame = ttk.Frame(monitor_notebook)
        monitor_notebook.add(status_frame, text="📋 Status Details")
        
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(0, weight=1)
        
        # Detailed status display
        status_detail_frame = ttk.LabelFrame(status_frame, text="Detailed Trading Status")
        status_detail_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        status_detail_frame.columnconfigure(1, weight=1)
        
        # Connection details
        ttk.Label(status_detail_frame, text="Connection Status:", style='GroupHeader.TLabel').grid(row=0, column=0, columnspan=2, sticky="w", pady=(5,10))
        
        ttk.Label(status_detail_frame, text="SmartAPI:").grid(row=1, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_connection_status).grid(row=1, column=1, sticky="w", padx=10)
        
        ttk.Label(status_detail_frame, text="Symbol:").grid(row=2, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_symbol).grid(row=2, column=1, sticky="w", padx=10)
        
        # Trading details  
        ttk.Label(status_detail_frame, text="Trading Details:", style='GroupHeader.TLabel').grid(row=3, column=0, columnspan=2, sticky="w", pady=(15,10))
        
        ttk.Label(status_detail_frame, text="Current Price:").grid(row=4, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_current_price).grid(row=4, column=1, sticky="w", padx=10)
        
        ttk.Label(status_detail_frame, text="Position:").grid(row=5, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_position_status).grid(row=5, column=1, sticky="w", padx=10)
        
        ttk.Label(status_detail_frame, text="Available Capital:").grid(row=6, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_capital_display).grid(row=6, column=1, sticky="w", padx=10)
        
        ttk.Label(status_detail_frame, text="Trades Today:").grid(row=7, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_trades_today).grid(row=7, column=1, sticky="w", padx=10)
        
        ttk.Label(status_detail_frame, text="Ticks Processed:").grid(row=8, column=0, sticky="w", padx=10)
        ttk.Label(status_detail_frame, textvariable=self.ft_tick_count).grid(row=8, column=1, sticky="w", padx=10)

    def _build_log_tab(self):
        """Build the logging tab"""
        frame = self.log_tab
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        
        # Create control frame for buttons
        control_frame = ttk.Frame(frame)
        control_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=(0, 5))
        
        # Add clear button
        clear_btn = ttk.Button(control_frame, text="Clear Logs", command=self._clear_logs)
        clear_btn.pack(side="left", padx=(0, 10))
        
        # Add log level info
        ttk.Label(control_frame, text="Showing INFO level and above").pack(side="right")
        
        # Create scrolled text widget for logs
        self.log_text = tk.Text(frame, wrap="word", state="disabled", 
                               bg="white", fg="black", font=("Consolas", 13))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=5)
        
        # Set up GUI log handler to display logs in this text widget
        self._setup_gui_logging()
        
        # Add initial message to show that logging is active
        self._append_initial_log_message()

    def _setup_gui_logging(self):
        """Set up GUI log handler to display logs in the text widget"""
        try:
            # Create and configure GUI log handler
            self.gui_log_handler = GuiLogHandler(self.log_text)
            
            # Set formatter to match other handlers
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S"
            )
            self.gui_log_handler.setFormatter(formatter)
            
            # Set level to INFO to avoid too much debug noise in GUI
            self.gui_log_handler.setLevel(logging.INFO)
            
            # Add handler to root logger
            root_logger = logging.getLogger()
            root_logger.addHandler(self.gui_log_handler)
            
        except Exception as e:
            logger.error(f"Failed to setup GUI logging: {e}")
    
    def _append_initial_log_message(self):
        """Add initial message to log tab to show it's working"""
        try:
            self.log_text.configure(state="normal")
            init_msg = f"GUI Log initialized at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            self.log_text.insert(tk.END, f"{init_msg}\n")
            self.log_text.insert(tk.END, "=" * 50 + "\n")
            self.log_text.configure(state="disabled")
            
            # Test log message to verify handler is working
            logger.info("GUI log display is now active")
            
        except Exception as e:
            logger.error(f"Failed to add initial log message: {e}")

    def _clear_logs(self):
        """Clear the log display in the GUI"""
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete(1.0, tk.END)
            self.log_text.configure(state="disabled")
            
            # Add a cleared message
            self._append_initial_log_message()
            
        except Exception as e:
            logger.error(f"Failed to clear logs: {e}")

    def _get_gui_log_text(self):
        """Get all log text from the GUI Logs tab for Excel export"""
        try:
            # Get all text from the log widget
            log_text = self.log_text.get("1.0", "end-1c").strip()
            return log_text
        except Exception as e:
            logger.error(f"Failed to get GUI log text: {e}")
            return f"Error retrieving log text: {e}"

    def _ft_refresh_cache(self):
        """Refresh symbol cache for forward testing"""
        try:
            refresh_symbol_cache()
            self.ft_cache_status.set("Cache refreshed successfully")
            logger.info("Symbol cache refreshed")
        except Exception as e:
            self.ft_cache_status.set(f"Cache refresh failed: {e}")
            logger.error(f"Cache refresh failed: {e}")

    def _ft_load_symbols(self):
        """Load and filter symbols based on user input"""
        try:
            symbol_filter = self.ft_symbol.get().strip().upper()
            if not symbol_filter:
                messagebox.showwarning("Input Required", "Please enter a symbol filter")
                return
                
            # Load symbol cache
            cache = load_symbol_cache()
            if not cache:
                messagebox.showerror("Cache Error", "Symbol cache not loaded. Please refresh cache first.")
                return
                
            # Filter symbols
            matching_symbols = [symbol for symbol in cache.keys() if symbol_filter in symbol]
            
            # Clear and populate listbox
            self.ft_symbols_listbox.delete(0, tk.END)
            for symbol in sorted(matching_symbols)[:100]:  # Limit to 100 results
                self.ft_symbols_listbox.insert(tk.END, symbol)
                
            self.ft_cache_status.set(f"Found {len(matching_symbols)} matches for '{symbol_filter}'")
            logger.info(f"Loaded {len(matching_symbols)} symbols matching '{symbol_filter}'")
            
        except Exception as e:
            self.ft_cache_status.set(f"Symbol loading failed: {e}")
            logger.error(f"Symbol loading failed: {e}")

    def _ft_update_symbol_details(self, event):
        """Update symbol details when selection changes"""
        try:
            selection = self.ft_symbols_listbox.curselection()
            if not selection:
                return
                
            selected_symbol = self.ft_symbols_listbox.get(selection[0])
            self.ft_symbol.set(selected_symbol)
            
            # Clear token first
            self.ft_token.set("")
            
            # Load token information
            cache = load_symbol_cache()
            if cache and selected_symbol in cache:
                # cache[selected_symbol] is the token string directly (simple mapping)
                token = cache[selected_symbol]
                self.ft_token.set(str(token))
                logger.info(f"Selected symbol: {selected_symbol}, Token: {token}")
            else:
                logger.warning(f"Token not found for symbol: {selected_symbol}")
            
        except Exception as e:
            logger.warning(f"Symbol details update error: {e}")
            self.ft_token.set("")

    def _ft_on_instrument_change(self, event=None):
        """Callback when instrument type is changed in dropdown"""
        try:
            selected_instrument = self.ft_instrument_type.get()
            if selected_instrument in self.instrument_mappings:
                instrument_info = self.instrument_mappings[selected_instrument]
                
                # Update lot size automatically - STRICT ACCESS (fail-fast if missing)
                if 'lot_size' not in instrument_info:
                    raise KeyError(f"lot_size not found for instrument '{selected_instrument}' in instrument_mappings SSOT")
                self.ft_lot_size.set(str(instrument_info['lot_size']))
                
                # Update exchange - STRICT ACCESS (fail-fast if missing)
                if 'exchange' not in instrument_info:
                    raise KeyError(f"exchange not found for instrument '{selected_instrument}' in instrument_mappings SSOT")
                new_exchange = instrument_info['exchange']
                if new_exchange != self.ft_exchange.get():
                    self.ft_exchange.set(new_exchange)
                
                # Store selected instrument info for configuration building
                self.ft_selected_instrument_info = instrument_info
                
                # Clear symbol and token since we changed instrument type
                self.ft_symbol.set("")
                self.ft_token.set("")
                
                # Update cache status
                self.ft_cache_status.set(f"Select instrument: {selected_instrument}")
                
                logger.info(f"Instrument changed to {selected_instrument}: lot_size={instrument_info.get('lot_size')}, exchange={new_exchange}")
                
        except Exception as e:
            logger.warning(f"Instrument change error: {e}")

    def _initialize_instrument_selection(self):
        """Initialize instrument selection with default values after GUI is built"""
        try:
            # Trigger the instrument change callback to set initial values
            self._ft_on_instrument_change()
            logger.info("Instrument selection initialized with default NIFTY settings")
        except Exception as e:
            logger.warning(f"Failed to initialize instrument selection: {e}")

    def _ft_build_config_from_gui(self):
        """Build forward test specific configuration from GUI state - FRESH BUILD EVERY TIME"""
        logger.info("Building fresh configuration from current GUI state...")
        
        # 🚀 FRESH BUILD: Start with clean defaults, no caching
        from ..config.defaults import DEFAULT_CONFIG
        config_dict = deepcopy(DEFAULT_CONFIG)  # Fresh baseline from defaults
        
        # 1. INSTRUMENT SELECTION (Primary - determines lot_size from SSOT)
        selected_instrument = self.ft_instrument_type.get()
        
        if selected_instrument not in self.instrument_mappings:
            raise KeyError(
                f"FATAL: Instrument '{selected_instrument}' not found in instrument_mappings SSOT. "
                f"Available: {list(self.instrument_mappings.keys())}"
            )
        
        instrument_info = self.instrument_mappings[selected_instrument]
        
        # Get metadata from SSOT - STRICT ACCESS
        required_params = ["lot_size", "tick_size", "exchange", "type"]
        for param in required_params:
            if param not in instrument_info:
                raise KeyError(
                    f"FATAL: {param} not found for instrument '{selected_instrument}' "
                    f"in instrument_mappings SSOT"
                )
        
        config_dict["instrument"]["lot_size"] = instrument_info["lot_size"]
        config_dict["instrument"]["tick_size"] = instrument_info["tick_size"]
        # Store instrument key (NIFTY, BANKNIFTY, etc.) not type (Index Options)
        config_dict["instrument"]["instrument_type"] = selected_instrument
        
        # 2. EXCHANGE/INSTRUMENT TYPE (Secondary - F&O vs Cash)
        config_dict["instrument"]["exchange"] = self.ft_exchange.get()
        
        # 3. SYMBOL SELECTION (Only required for live trading, not data simulation)
        user_symbol = self.ft_symbol.get().strip()
        user_token = self.ft_token.get().strip()
        
        # Only validate symbols/tokens for live trading
        if not self.ft_use_file_simulation.get():  # Live trading requires symbols/tokens
            if not user_symbol:
                raise ValueError("FATAL: Symbol must be selected from cache before starting live trading")
            
            if not user_token:
                raise ValueError("FATAL: Token must be available for selected symbol in live trading")
        else:
            # For data simulation, use placeholder values (not actually used)
            if not user_symbol:
                user_symbol = "DATA_SIMULATION_PLACEHOLDER"
            if not user_token:
                user_token = "0"
        
        # OPTIONAL: Validate symbol compatibility with selected instrument (LIVE TRADING ONLY)
        # This is a soft validation - ask user confirmation if mismatch detected
        if not self.ft_use_file_simulation.get() and not user_symbol.startswith(selected_instrument):
            mismatch_msg = (
                f"Symbol Compatibility Warning\n\n"
                f"Selected Symbol: {user_symbol}\n"
                f"Selected Instrument: {selected_instrument}\n\n"
                f"The symbol '{user_symbol}' does not appear to match "
                f"the selected instrument '{selected_instrument}'.\n\n"
                f"This may result in incorrect lot size calculations or trading parameters.\n\n"
                f"Do you want to proceed anyway?"
            )
            
            user_choice = messagebox.askyesno(
                "Symbol Mismatch Warning", 
                mismatch_msg,
                icon='warning'
            )
            
            if not user_choice:
                logger.info(f"User cancelled live trading due to symbol mismatch: {user_symbol} vs {selected_instrument}")
                return None
            else:
                logger.warning(
                    f"User confirmed proceeding with mismatched symbol '{user_symbol}' "
                    f"for instrument '{selected_instrument}'"
                )
        
        config_dict["instrument"]["symbol"] = user_symbol
        config_dict["instrument"]["token"] = user_token
        
        # Update strategy parameters from forward test GUI
        config_dict['strategy']['use_ema_crossover'] = self.ft_use_ema_crossover.get()
        config_dict['strategy']['use_macd'] = self.ft_use_macd.get()
        config_dict['strategy']['use_vwap'] = self.ft_use_vwap.get()
        config_dict['strategy']['use_rsi_filter'] = self.ft_use_rsi_filter.get()
        config_dict['strategy']['use_htf_trend'] = self.ft_use_htf_trend.get()
        config_dict['strategy']['use_bollinger_bands'] = self.ft_use_bollinger_bands.get()
        config_dict['strategy']['use_stochastic'] = self.ft_use_stochastic.get()
        config_dict['strategy']['use_atr'] = self.ft_use_atr.get()
        
        # Update strategy parameters from forward test GUI
        config_dict['strategy']['fast_ema'] = int(self.ft_fast_ema.get())
        config_dict['strategy']['slow_ema'] = int(self.ft_slow_ema.get())
        config_dict['strategy']['macd_fast'] = int(self.ft_macd_fast.get())
        config_dict['strategy']['macd_slow'] = int(self.ft_macd_slow.get())
        config_dict['strategy']['macd_signal'] = int(self.ft_macd_signal.get())
        config_dict['strategy']['rsi_length'] = int(self.ft_rsi_length.get())
        config_dict['strategy']['rsi_oversold'] = float(self.ft_rsi_oversold.get())
        config_dict['strategy']['rsi_overbought'] = float(self.ft_rsi_overbought.get())
        config_dict['strategy']['htf_period'] = int(self.ft_htf_period.get())
        config_dict['strategy']['consecutive_green_bars'] = int(self.ft_consecutive_green_bars.get())
        
        # Control Base SL parameters from forward test GUI
        config_dict['strategy']['control_base_sl_enabled'] = self.ft_control_base_sl_enabled.get()
        config_dict['strategy']['control_base_sl_green_ticks'] = int(self.ft_control_base_sl_green_ticks.get())
        
        # Update risk management from forward test GUI
        config_dict['risk']['base_sl_points'] = float(self.ft_base_sl_points.get()) if self.ft_use_stop_loss.get() else 0.0
        config_dict['risk']['use_trail_stop'] = self.ft_use_trail_stop.get()
        config_dict['risk']['trail_activation_points'] = float(self.ft_trail_activation.get()) if self.ft_use_trail_stop.get() else 0.0
        config_dict['risk']['trail_distance_points'] = float(self.ft_trail_distance.get()) if self.ft_use_trail_stop.get() else 0.0
        config_dict['risk']['risk_per_trade_percent'] = float(self.ft_risk_per_trade.get())
        
        # Update Price-Above-Exit Filter from forward test GUI
        config_dict['risk']['price_above_exit_filter_enabled'] = self.ft_price_above_exit_filter_enabled.get()
        config_dict['risk']['price_buffer_points'] = float(self.ft_price_buffer_points.get())
        config_dict['risk']['filter_duration_seconds'] = int(self.ft_filter_duration_seconds.get())

        # Update take profit from forward test GUI
        if self.ft_use_take_profit.get():
            config_dict['risk']['tp_points'] = [float(var.get()) for var in self.ft_tp_points]
            config_dict['risk']['tp_percents'] = [float(var.get())/100.0 for var in self.ft_tp_percents]
        else:
            config_dict['risk']['tp_points'] = []
            config_dict['risk']['tp_percents'] = []

        # Update session management from forward test GUI
        config_dict['session']['is_intraday'] = self.ft_is_intraday.get()
        config_dict['session']['start_hour'] = int(self.ft_session_start_hour.get())
        config_dict['session']['start_min'] = int(self.ft_session_start_min.get())
        config_dict['session']['end_hour'] = int(self.ft_session_end_hour.get())
        config_dict['session']['end_min'] = int(self.ft_session_end_min.get())
        config_dict['session']['auto_stop_enabled'] = self.ft_auto_stop_enabled.get()
        config_dict['session']['max_loss_per_day'] = float(self.ft_max_loss_per_day.get())
        
        # Update trade blocks from forward test GUI
        config_dict['session']['trade_block_enabled'] = self.ft_trade_block_enabled.get()
        config_dict['session']['trade_blocks'] = []
        for block in self.ft_trade_blocks:
            config_dict['session']['trade_blocks'].append({
                'start_hour': int(block['start_hour'].get()),
                'start_min': int(block['start_min'].get()),
                'end_hour': int(block['end_hour'].get()),
                'end_min': int(block['end_min'].get())
            })
        
        # Fix: Set max trades in the correct location that strategy expects
        config_dict['risk']['max_positions_per_day'] = int(self.ft_max_trades_per_day.get())
        
        # 🚀 LOG FRESH CONFIG: Verify GUI values are captured
        logger.info(f"📋 Fresh GUI Configuration Captured:")
        logger.info(f"   Max Trades/Day: {config_dict['risk']['max_positions_per_day']} (from GUI: {self.ft_max_trades_per_day.get()})")
        logger.info(f"   Symbol: {self.ft_symbol.get()}")
        logger.info(f"   Capital: {self.ft_initial_capital.get()}")

        # Update data simulation settings from forward test GUI (OPTIONAL - does not affect live trading)
        config_dict['data_simulation'] = {
            'enabled': self.ft_use_file_simulation.get(),
            'file_path': self.ft_data_file_path.get() if self.ft_use_file_simulation.get() else ""
        }
        
        # Log data source for user confirmation
        if config_dict['data_simulation']['enabled']:
            logger.info(f"   Data Source: File Simulation ({config_dict['data_simulation']['file_path']})")
        else:
            logger.info(f"   Data Source: Live WebStream")

        # Update capital management from forward test GUI
        config_dict['capital']['initial_capital'] = float(self.ft_initial_capital.get())  # Use the GUI initial capital field
        config_dict['capital']['position_size_method'] = self.ft_position_size_method.get()
        config_dict['capital']['fixed_amount'] = float(self.ft_fixed_amount.get())
        config_dict['capital']['fixed_quantity'] = int(self.ft_fixed_quantity.get())
        config_dict['capital']['max_positions'] = int(self.ft_max_positions.get())

        # Update instrument settings from forward test GUI (lot_size comes from SSOT, not GUI)
        # Note: lot_size is read-only and sourced from instrument_mappings

        # Add live trading specific configuration
        config_dict['live'] = {
            'feed_type': self.ft_feed_type.get(),
            'paper_trading': True,  # Always use paper trading for safety
            'max_positions': int(self.ft_max_positions.get()),
            'reconnect_attempts': 3,
            'tick_timeout': 30
        }
        
        # Load SmartAPI credentials for live data streaming (required for both live and paper trading)
        from ..config.defaults import load_live_trading_credentials
        credentials = load_live_trading_credentials()
        config_dict['live'].update(credentials)
        
        logger.info(f"Credentials loaded: api_key={'LOADED' if credentials.get('api_key') else 'EMPTY'}, "
                   f"client_code={'LOADED' if credentials.get('client_code') else 'EMPTY'}")
        
        # Validate and freeze the forward test configuration
        try:
            # 🚀 FINAL CONFIRMATION: Log complete config summary
            logger.info("✅ Fresh Configuration Ready for Forward Test:")
            logger.info(f"   📊 Strategy: Max Trades = {config_dict['risk']['max_positions_per_day']}")
            logger.info(f"   💰 Capital: {config_dict['capital']['initial_capital']}")
            logger.info(f"   📈 Symbol: {config_dict['instrument']['symbol']}")
            logger.info(f"   🏢 Exchange: {config_dict['instrument']['exchange']}")
            logger.info(f"   ⏰ Trade Blocks: Enabled={config_dict['session']['trade_block_enabled']}, Count={len(config_dict['session']['trade_blocks'])}")
            if config_dict['session']['trade_blocks']:
                for idx, block in enumerate(config_dict['session']['trade_blocks'], 1):
                    logger.info(f"      Block #{idx}: {block['start_hour']:02d}:{block['start_min']:02d}-{block['end_hour']:02d}:{block['end_min']:02d}")
            
            validation = validate_config(config_dict)
            frozen_config = freeze_config(config_dict)
            logger.info("🔒 Configuration frozen and validated successfully")
            return frozen_config
            
        except Exception as e:
            logger.exception(f"Forward test config validation failed: {e}")
            messagebox.showerror("Configuration Error", f"Failed to validate forward test config: {e}")
            return None

    def _ft_run_forward_test(self):
        """Run forward test with current configuration using frozen config"""
        try:
            # Validate symbol/token ONLY for live trading (not needed for data simulation)
            if not self.ft_use_file_simulation.get():  # Live trading requires symbols/tokens
                if not self.ft_symbol.get().strip():
                    messagebox.showerror("Missing Symbol", "Please select a symbol for live trading")
                    return
                    
                if not self.ft_token.get().strip():
                    messagebox.showerror("Missing Token", "Please select a valid symbol with token information for live trading")
                    return
            
            # 🚀 BUILD FRESH CONFIG: Always build from current GUI state
            logger.info("Building fresh forward test configuration...")
            try:
                ft_frozen_config = self._ft_build_config_from_gui()
            except Exception as e:
                logger.error(f"Configuration build failed: {e}")
                import traceback
                logger.debug(f"Traceback: {traceback.format_exc()}")
                raise e
            if ft_frozen_config is None:
                logger.warning("❌ Forward test aborted: configuration validation failed")
                return
            
            # 🚀 CONFIRM FRESH CONFIG: Log key parameters user will see
            logger.info("🎯 Forward Test Starting with Fresh Configuration:")
            logger.info(f"   Max Trades/Day: {ft_frozen_config['risk']['max_positions_per_day']}")
            logger.info(f"   Symbol: {ft_frozen_config['instrument']['symbol']}")
            logger.info(f"   Capital: ${ft_frozen_config['capital']['initial_capital']:,.2f}")
            if ft_frozen_config['data_simulation']['enabled']:
                logger.info(f"   Data: File Simulation ({ft_frozen_config['data_simulation']['file_path']})")
            else:
                logger.info(f"   Data: Live WebStream")
            
            # Verify we have a proper frozen config
            if not isinstance(ft_frozen_config, MappingProxyType):
                logger.error("Forward test config is not properly frozen")
                messagebox.showerror("Configuration Error", "Forward test configuration is not properly frozen")
                return
            
            # Import LiveTrader here to avoid circular imports
            try:
                from ..live.trader import LiveTrader
            except ImportError as e:
                logger.error(f"Failed to import LiveTrader: {e}")
                messagebox.showerror("Import Error", f"Could not import LiveTrader: {e}")
                return
            
            # Initialize status displays
            self._update_ft_status(
                connection="🟡 Connecting...",
                trading="⏸️ Preparing...",
                price="--",
                position="📭 No Position",
                pnl=0.0,
                tick_count=0
            )
            
            # Clear previous results in all tabs
            for tab_name, box_attr in [("live", "ft_result_box"), ("signals", "ft_signals_box"), ("performance", "ft_performance_box")]:
                if hasattr(self, box_attr):
                    box = getattr(self, box_attr)
                    box.config(state="normal")
                    box.delete(1.0, tk.END)
                    box.config(state="disabled")
            
            # Determine data source mode and show confirmation
            if self.ft_use_file_simulation.get():
                data_source_msg = "📁 FILE DATA SIMULATION"
                data_detail_msg = f"Historical file: {self.ft_data_file_path.get()}"
                warning_msg = "\n⚠️ This will use HISTORICAL data, not live market prices!"
            else:
                data_source_msg = "🌐 LIVE WEBSTREAM TRADING" 
                data_detail_msg = f"Live market feed: {self.ft_feed_type.get()}"
                warning_msg = "\n⚠️ This will connect to LIVE market data streams!"
            
            # Build configuration text BEFORE showing dialog (we'll need it for Excel export)
            config_text = self._build_config_summary(ft_frozen_config, data_source_msg, data_detail_msg, warning_msg)
            
            # 🔍 DEBUG: Log config_text generation
            logger.info(f"🔍 GUI generated config_text - type: {type(config_text)}, length: {len(config_text) if config_text else 0}")
            if config_text:
                logger.info(f"✅ config_text generated successfully - first 100 chars: {config_text[:100]}")
            else:
                logger.error(f"❌ config_text is empty or None: {repr(config_text)}")
            
            # Show comprehensive configuration review dialog
            confirmed = self._show_config_review_dialog(ft_frozen_config, data_source_msg, data_detail_msg, warning_msg)
            
            if not confirmed:
                logger.info("Forward test cancelled by user")
                return
            
            # Add initial message
            self._update_ft_result_box(f"🚀 Starting forward test for {self.ft_symbol.get()}...\n", "live")
            
            # Reconfigure logging with actual user configuration (not just defaults)
            logger.info("🔄 Reconfiguring logging with user settings from GUI...")
            setup_from_config(ft_frozen_config)
            logger.info("✅ Logging reconfigured with fresh user configuration")
            
            # Create LiveTrader with frozen config and dialog text
            try:
                trader = LiveTrader(frozen_config=ft_frozen_config, dialog_text=config_text)
                # Set consumption mode from GUI toggle
                trader.use_direct_callbacks = self.ft_use_direct_callbacks.get()
                logger.info(f"🎯 Consumption mode set: {'⚡ Callback (Fast)' if trader.use_direct_callbacks else '📊 Polling (Safe)'}")
            except Exception as e:
                logger.error(f"Failed to create LiveTrader: {e}")
                messagebox.showerror("LiveTrader Error", f"Could not create LiveTrader: {e}")
                return
            
            # Use trader's ForwardTestResults (already created with dialog_text)
            self.forward_test_results = trader.results_exporter
            
            # Give ForwardTestResults access to GUI log data for automatic export
            trader.results_exporter.gui_instance = self
            
            # Check for performance testing hook and inject if enabled
            try:
                from myQuant.utils.performance_test_hook import get_performance_hook
                perf_hook = get_performance_hook()
                if perf_hook.enabled:
                    logger.info("🔬 Performance testing mode detected - FORCING WebSocket callback mode")
                    # CRITICAL: Force WebSocket callback mode for performance testing
                    trader.use_direct_callbacks = True
                    logger.info("⚡ Callback mode FORCED ON - WebSocket direct callbacks (no polling)")
                    perf_hook.inject_into_trader(trader)
            except ImportError:
                pass  # Performance testing module not available
            except Exception as e:
                logger.warning(f"⚠️ Could not inject performance testing: {e}")
            
            # Start forward test in background thread to avoid blocking GUI
            import threading
            def run_forward_test():
                try:
                    logger.info(f"Starting forward test for {self.ft_symbol.get()} with frozen configuration")
                    
                    # Update status to show connection in progress
                    self.after(0, lambda: self._update_ft_status(
                        connection="🟡 Connecting...",
                        trading="🔄 Starting..."
                    ))
                    
                    # Start the trader with performance callback
                    performance_callback = lambda trader: self.after(0, lambda: self._update_performance_summary(trader))
                    trader.start(run_once=False, result_box=self.ft_result_box, performance_callback=performance_callback)
                    
                    # Initial performance summary
                    self.after(0, lambda: self._update_performance_summary(trader))
                    
                    # Update status to show active trading
                    self.after(0, lambda: self._update_ft_status(
                        connection="🟢 Connected",
                        trading="▶️ Active"
                    ))
                    
                except Exception as e:
                    logger.exception(f"Forward test thread failed: {e}")
                    # Update GUI from thread safely
                    self.after(0, lambda: self._update_ft_result_box(f"❌ ERROR: Forward test failed: {e}\n", "live"))
                    self.after(0, lambda: self._update_ft_status(
                        connection="🔴 Disconnected",
                        trading="❌ Failed"
                    ))
            
            # Store trader and thread references for proper cleanup
            self.active_trader = trader
            self.active_thread = threading.Thread(target=run_forward_test, daemon=True)
            self.active_thread.start()
            
            logger.info(f"Forward test initiated for {self.ft_symbol.get()} with frozen MappingProxyType config")
            
        except Exception as e:
            logger.exception(f"Forward test failed: {e}")
            messagebox.showerror("Forward Test Error", f"Failed to start forward test: {e}")
    

    
    def _update_ft_result_box(self, message, tab="live"):
        """Thread-safe update of forward test result boxes"""
        try:
            target_box = None
            if tab == "live" and hasattr(self, 'ft_result_box'):
                target_box = self.ft_result_box
            elif tab == "signals" and hasattr(self, 'ft_signals_box'):
                target_box = self.ft_signals_box
            elif tab == "performance" and hasattr(self, 'ft_performance_box'):
                target_box = self.ft_performance_box
            
            if target_box:
                target_box.config(state="normal")
                target_box.insert(tk.END, message)
                target_box.see(tk.END)
                target_box.config(state="disabled")
        except Exception as e:
            logger.warning(f"Failed to update forward test {tab} box: {e}")

    def _update_ft_status(self, connection=None, trading=None, price=None, position=None, pnl=None, tick_count=None, 
                         current_capital=None, trades_today=None, max_trades=None):
        """Update forward test status indicators across all interfaces"""
        try:
            if connection is not None and hasattr(self, 'ft_connection_status'):
                self.ft_connection_status.set(connection)
            
            if trading is not None:
                if hasattr(self, 'ft_trading_status'):
                    self.ft_trading_status.set(trading)
                # Also update monitor tab status
                if hasattr(self, 'monitor_trading_status'):
                    self.monitor_trading_status.set(trading)
                    
            if price is not None and hasattr(self, 'ft_current_price'):
                self.ft_current_price.set(f"₹{price:.2f}" if isinstance(price, (int, float)) else str(price))
            
            if position is not None and hasattr(self, 'ft_position_status'):
                self.ft_position_status.set(position)
            
            if pnl is not None:
                pnl_text = f"₹{pnl:.2f}" if isinstance(pnl, (int, float)) else str(pnl)
                
                # Update main tab P&L
                if hasattr(self, 'ft_pnl_status') and hasattr(self, 'ft_pnl_label'):
                    self.ft_pnl_status.set(pnl_text)
                    # Color code P&L: green for profit, red for loss
                    if isinstance(pnl, (int, float)):
                        color = '#4CAF50' if pnl > 0 else 'red' if pnl < 0 else 'black'
                        self.ft_pnl_label.configure(foreground=color)
                
                # Update monitor tab P&L
                if hasattr(self, 'monitor_pnl_status') and hasattr(self, 'monitor_pnl_label'):
                    self.monitor_pnl_status.set(pnl_text)
                    if isinstance(pnl, (int, float)):
                        color = '#4CAF50' if pnl > 0 else 'red' if pnl < 0 else 'black'
                        self.monitor_pnl_label.configure(foreground=color)
            
            if tick_count is not None and hasattr(self, 'ft_tick_count'):
                self.ft_tick_count.set(str(tick_count))
            
            if current_capital is not None and hasattr(self, 'ft_capital_display'):
                capital_text = f"₹{current_capital:,.0f}" if isinstance(current_capital, (int, float)) else str(current_capital)
                self.ft_capital_display.set(capital_text)
            
            if trades_today is not None and hasattr(self, 'ft_trades_today'):
                max_val = max_trades if max_trades is not None else self.ft_max_trades_per_day.get()
                self.ft_trades_today.set(f"{trades_today}/{max_val}")
                
        except Exception as e:
            logger.warning(f"Failed to update forward test status: {e}")

    def _update_performance_summary(self, trader=None):
        """Update performance tab with comprehensive trading results"""
        try:
            if not hasattr(self, 'ft_performance_box'):
                return
                
            if not trader or not hasattr(trader, 'position_manager'):
                return
                
            # Get performance data from position manager
            perf_data = trader.position_manager.get_performance_summary()
            
            # Generate performance report
            performance_text = self._generate_performance_report(perf_data, trader.position_manager)
            
            # Update performance tab
            self.ft_performance_box.config(state="normal")
            self.ft_performance_box.delete(1.0, tk.END)
            self.ft_performance_box.insert(1.0, performance_text)
            self.ft_performance_box.config(state="disabled")
            
        except Exception as e:
            logger.warning(f"Failed to update performance summary: {e}")

    def _generate_performance_report(self, perf_data, position_manager):
        """Generate comprehensive performance report text"""
        
        lines = []
        lines.append("=" * 80)
        lines.append("                          PERFORMANCE SUMMARY")
        lines.append("=" * 80)
        lines.append("")
        
        # Overall Statistics
        lines.append("OVERALL STATISTICS")
        lines.append("-" * 40)
        lines.append(f"Total Trades:        {perf_data['total_trades']}")
        lines.append(f"Winning Trades:      {perf_data['winning_trades']}")
        lines.append(f"Losing Trades:       {perf_data['losing_trades']}")
        lines.append(f"Win Rate:            {perf_data['win_rate']:.1f}%")
        lines.append("")
        
        # Financial Summary
        lines.append("FINANCIAL SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Total P&L:           ₹{perf_data['total_pnl']:,.2f}")
        lines.append(f"Average Win:         ₹{perf_data['avg_win']:,.2f}")
        lines.append(f"Average Loss:        ₹{perf_data['avg_loss']:,.2f}")
        lines.append(f"Largest Win:         ₹{perf_data['max_win']:,.2f}")
        lines.append(f"Largest Loss:        ₹{perf_data['max_loss']:,.2f}")
        lines.append(f"Profit Factor:       {perf_data['profit_factor']:.2f}")
        lines.append(f"Total Commission:    ₹{perf_data['total_commission']:,.2f}")
        lines.append("")
        
        # Capital Performance
        lines.append("CAPITAL PERFORMANCE")
        lines.append("-" * 40)
        initial_capital = position_manager.initial_capital
        # Use P&L-based capital calculation for display consistency
        # (position_manager.current_capital uses reservation system, not suitable for display)
        total_pnl = perf_data['total_pnl']  # Already calculated correctly from trades
        current_capital = initial_capital + total_pnl
        capital_change = total_pnl  # Capital change equals total P&L
        capital_change_pct = (capital_change / initial_capital) * 100 if initial_capital > 0 else 0
        
        lines.append(f"Initial Capital:     ₹{initial_capital:,.2f}")
        lines.append(f"Current Capital:     ₹{current_capital:,.2f}")
        lines.append(f"Capital Change:      ₹{capital_change:,.2f} ({capital_change_pct:+.2f}%)")
        lines.append("")
        
        # Trade Details (if any trades exist)
        if perf_data['total_trades'] > 0:
            lines.append("RECENT TRADES")
            lines.append("-" * 40)
            
            # Show last few completed trades
            completed_trades = position_manager.completed_trades
            recent_trades = completed_trades[-5:] if len(completed_trades) > 5 else completed_trades
            
            for i, trade in enumerate(recent_trades, 1):
                result = "WIN" if trade.net_pnl > 0 else "LOSS"
                lines.append(f"Trade {len(completed_trades) - len(recent_trades) + i}: "
                           f"{result} - ₹{trade.net_pnl:,.2f} "
                           f"(Entry: ₹{trade.entry_price:.2f}, Exit: ₹{trade.exit_price:.2f})")
            
            if len(completed_trades) > 5:
                lines.append(f"... and {len(completed_trades) - 5} earlier trades")
        else:
            lines.append("TRADE STATUS")
            lines.append("-" * 40)
            lines.append("No completed trades yet")
            
            # Show current position if any
            if hasattr(position_manager, 'current_position') and position_manager.current_position:
                pos = position_manager.current_position
                lines.append(f"Current Position: {pos.side} {pos.quantity} @ ₹{pos.entry_price:.2f}")
                lines.append(f"Unrealized P&L: ₹{pos.unrealized_pnl:,.2f}")
        
        lines.append("")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def _ft_stop_forward_test(self):
        """Stop running forward test with proper thread cleanup - requires user confirmation"""
        try:
            if hasattr(self, 'active_trader') and self.active_trader:
                # CRITICAL: Ask for user confirmation before stopping
                # Robustness priority - avoid accidental disconnection
                confirm = messagebox.askyesno(
                    "⚠️ Confirm Stop",
                    "Are you sure you want to stop the live data stream?\n\n"
                    "⚠️ WARNING: This will disconnect from live market data.\n"
                    "✓ Stream will auto-reconnect if network recovers.\n"
                    "✓ Position will be force-closed safely.\n\n"
                    "Stop forward test?",
                    icon='warning'
                )
                
                if not confirm:
                    logger.info("❌ Stop cancelled by user - stream continues")
                    return
                
                logger.info("🛑 User confirmed stop - stopping forward test...")
                
                # Update status displays
                self._update_ft_status(
                    connection="🟡 Disconnecting...",
                    trading="⏹️ Stopping..."
                )
                
                # Stop the trader (sets is_running = False)
                self.active_trader.stop()
                
                # Export results if available
                if hasattr(self, 'forward_test_results') and self.forward_test_results:
                    try:
                        self.forward_test_results.finalize()
                        excel_file = self.forward_test_results.export_to_excel()
                        
                        self._update_ft_result_box(f"📄 Results exported:\n", "live")
                        self._update_ft_result_box(f"  Excel: {os.path.basename(excel_file)}\n", "live")
                        logger.info(f"Forward test results exported successfully")
                    except Exception as e:
                        logger.warning(f"Failed to export results: {e}")
                        self._update_ft_result_box(f"⚠️ Results export failed: {e}\n", "live")
                
                # Wait for thread to finish (with timeout to prevent hanging)
                if hasattr(self, 'active_thread') and self.active_thread.is_alive():
                    logger.info("Waiting for trading thread to finish...")
                    self.active_thread.join(timeout=10.0)  # 10 second timeout (increased)
                    
                    if self.active_thread.is_alive():
                        logger.warning("Trading thread did not finish within timeout - forcing cleanup")
                        # Force thread cleanup - this should not happen in normal operation
                        logger.warning("Thread may be stuck - GUI will continue but thread resources may not be fully cleaned")
                    else:
                        logger.info("Trading thread finished cleanly")
                
                # Clear references
                self.active_trader = None
                if hasattr(self, 'active_thread'):
                    self.active_thread = None
                self.forward_test_results = None
                
                # Add stop message to results
                self._update_ft_result_box("⏹️ Forward test stopped by user.\n", "live")
                
                # Final status update
                self._update_ft_status(
                    connection="🔴 Disconnected",
                    trading="⏸️ Stopped"
                )
                
                logger.info("✅ Forward test stopped successfully")
                messagebox.showinfo("Forward Test Stopped", "Forward test has been stopped successfully.")
            else:
                messagebox.showinfo("No Active Test", "No forward test is currently running")
        except Exception as e:
            logger.exception(f"Failed to stop forward test: {e}")
            messagebox.showerror("Stop Error", f"Failed to stop forward test: {e}")
            
            # Update status to show error
            self._update_ft_status(
                connection="🔴 Error",
                trading="❌ Stop Failed"
            )
    
    def _ft_export_results(self):
        """Export current forward test results using background thread for GUI responsiveness"""
        try:
            if not hasattr(self, 'active_trader') or not self.active_trader:
                messagebox.showinfo("No Results", "No active forward test results to export.\nPlease run a forward test first.")
                return
            
            # Check if ForwardTestResults instance exists
            if not hasattr(self.active_trader, 'results_exporter') or not self.active_trader.results_exporter:
                messagebox.showerror("Export Error", "Results exporter not available.\nPlease restart the forward test.")
                return
            
            # Show progress indication
            self._update_ft_result_box("Starting Excel export in background...\n", "live")
            
            # Start background export
            worker = self.active_trader.results_exporter.export_results()
            logger.info("Started background Excel export")
            
            # Define completion handler
            def on_export_done():
                """Called when export is complete"""
                self._update_ft_result_box("Excel export completed successfully\n", "live")
                messagebox.showinfo("Export Complete", "Forward test results have been exported to Excel.")
            
            # Monitor completion in background
            from threading import Thread
            monitor_thread = Thread(target=lambda: (worker.join(), on_export_done()), daemon=True)
            monitor_thread.start()
                
        except Exception as e:
            logger.exception(f"Export initiation failed: {e}")
            messagebox.showerror("Export Error", f"Failed to start export:\n{e}")
            self._update_ft_result_box(f"Export error: {e}\n", "live")

    def _show_config_review_dialog(self, config, data_source_msg, data_detail_msg, warning_msg):
        """Show comprehensive configuration review dialog before starting forward test"""
        

        # Create dialog window
        dialog = tk.Toplevel(self)
        dialog.title("Configuration Review")
        dialog.geometry("800x600")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        
        # Center the dialog
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (400)
        y = (dialog.winfo_screenheight() // 2) - (300)
        dialog.geometry(f"800x600+{x}+{y}")
        
        # Main frame
        main_frame = ttk.Frame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # Build configuration text (safe now after validation)
        config_text = self._build_config_summary(config, data_source_msg, data_detail_msg, warning_msg)
        
        # Text widget to display configuration
        text_widget = tk.Text(main_frame, wrap=tk.WORD, font=('Consolas', 10))
        text_widget.pack(fill='both', expand=True, pady=(0, 15))
        text_widget.insert(1.0, config_text)
        text_widget.config(state='disabled')
        
        # Buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill='x')
        
        # Result variable
        self.dialog_result = False
        
        def on_confirm():
            self.dialog_result = True
            dialog.destroy()
            
        def on_cancel():
            self.dialog_result = False
            dialog.destroy()
        
        # Buttons
        ttk.Button(button_frame, text="Cancel", 
                  command=on_cancel).pack(side='right', padx=(10, 0))
        ttk.Button(button_frame, text="Start Forward Test", 
                  command=on_confirm).pack(side='right')
        
        # Wait for dialog to close
        dialog.wait_window()
        
        return self.dialog_result



    def _build_config_summary(self, config, data_source_msg, data_detail_msg, warning_msg):
        """Build a comprehensive configuration summary as formatted text"""
        
        lines = []
        lines.append("=" * 80)
        lines.append("                    FORWARD TEST CONFIGURATION REVIEW")
        lines.append("=" * 80)
        lines.append("")
        lines.append(f"DATA SOURCE: {data_source_msg}")
        lines.append(f"{data_detail_msg}")
        lines.append(f"{warning_msg}")
        lines.append("")
        
        # Consumption Mode (Performance Setting)
        consumption_mode = "⚡ Callback Mode (Fast)" if self.ft_use_direct_callbacks.get() else "📊 Polling Mode (Safe)"
        consumption_latency = "~50ms latency, Wind-style" if self.ft_use_direct_callbacks.get() else "~70ms latency, Queue-based"
        lines.append(f"CONSUMPTION MODE: {consumption_mode}")
        lines.append(f"Expected Performance: {consumption_latency}")
        lines.append("")
        
        # Instrument & Session
        lines.append("INSTRUMENT & SESSION")
        lines.append("-" * 40)
        lines.append(f"Symbol:              {config['instrument']['symbol']}")
        lines.append(f"Exchange:            {config['instrument']['exchange']}")
        lines.append(f"Product Type:        {config['instrument']['product_type']}")
        lines.append(f"Lot Size:            {config['instrument']['lot_size']}")
        lines.append(f"Tick Size:           ₹{config['instrument']['tick_size']}")
        lines.append(f"Session Start:       {config['session']['start_hour']:02d}:{config['session']['start_min']:02d}")
        lines.append(f"Session End:         {config['session']['end_hour']:02d}:{config['session']['end_min']:02d}")
        lines.append(f"Auto Stop:           {'Enabled' if config['session']['auto_stop_enabled'] else 'Disabled'}")
        lines.append(f"Max Loss/Day:        ₹{config['session']['max_loss_per_day']}")
        
        # Trade Blocks Configuration
        # DEBUG: Log what we're receiving
        try:
            trade_block_enabled = config['session'].get('trade_block_enabled', False)
            trade_blocks = config['session'].get('trade_blocks', [])
            logger.info(f"[CONFIG DIALOG] Trade blocks - enabled: {trade_block_enabled}, count: {len(trade_blocks)}")
            if trade_blocks:
                for idx, block in enumerate(trade_blocks, 1):
                    logger.info(f"[CONFIG DIALOG]   Block #{idx}: {block}")
        except Exception as e:
            logger.error(f"[CONFIG DIALOG] Error accessing trade blocks: {e}", exc_info=True)
            trade_block_enabled = False
            trade_blocks = []
        
        if trade_block_enabled and trade_blocks:
            lines.append(f"Trade Blocks:        ENABLED ({len(trade_blocks)} blocks)")
            for idx, block in enumerate(trade_blocks, 1):
                block_str = f"{block['start_hour']:02d}:{block['start_min']:02d}-{block['end_hour']:02d}:{block['end_min']:02d}"
                lines.append(f"  Block #{idx}:        {block_str}")
        elif trade_block_enabled and not trade_blocks:
            lines.append(f"Trade Blocks:        ENABLED (0 blocks configured)")
        else:
            lines.append(f"Trade Blocks:        DISABLED")
        
        lines.append("")
        
        # Risk & Capital
        lines.append("RISK & CAPITAL MANAGEMENT")
        lines.append("-" * 40)
        lines.append(f"Initial Capital:     ₹{config['capital']['initial_capital']:,}")
        lines.append(f"Max Trades/Day:      {config['risk']['max_positions_per_day']}")
        lines.append(f"Base Stop Loss:      {config['risk']['base_sl_points']} points")
        lines.append(f"Take Profit Levels:  {len(config['risk']['tp_points'])} levels")
        lines.append(f"TP Points:           {config['risk']['tp_points']}")
        lines.append(f"TP Percentages:      {config['risk']['tp_percents']}")
        lines.append(f"Trail Stop:          {'Enabled' if config['risk']['use_trail_stop'] else 'Disabled'}")
        if config['risk']['use_trail_stop']:
            lines.append(f"Trail Activation:    {config['risk']['trail_activation_points']} points")
            lines.append(f"Trail Distance:      {config['risk']['trail_distance_points']} points")
        lines.append(f"Risk per Trade:      {config['risk']['risk_per_trade_percent']}%")
        lines.append(f"Commission:          {config['risk']['commission_percent']}%")
        lines.append("")
        
        # Strategy & Indicators  
        lines.append("STRATEGY & INDICATORS")
        lines.append("-" * 40)
        strategy_cfg = config['strategy']
        lines.append(f"Strategy Version:    {strategy_cfg['strategy_version']}")
        lines.append(f"Green Bars Required: {strategy_cfg['consecutive_green_bars']}")
        
        # Control Base SL Configuration
        if strategy_cfg.get('control_base_sl_enabled', False):
            lines.append(f"Control Base SL:     ENABLED")
            lines.append(f"  Normal Green Ticks: {strategy_cfg['consecutive_green_bars']}")
            lines.append(f"  After Base SL:      {strategy_cfg['control_base_sl_green_ticks']} green ticks")
        else:
            lines.append(f"Control Base SL:     DISABLED")
        
        lines.append("")
        lines.append("Enabled Indicators:")
        
        # Only show enabled indicators with their parameters
        if strategy_cfg['use_ema_crossover']:
            lines.append(f"  EMA Crossover:     Fast={strategy_cfg['fast_ema']}, Slow={strategy_cfg['slow_ema']}")
        
        if strategy_cfg['use_macd']:
            lines.append(f"  MACD:              Fast={strategy_cfg['macd_fast']}, Slow={strategy_cfg['macd_slow']}, Signal={strategy_cfg['macd_signal']}")
        
        if strategy_cfg['use_rsi_filter']:
            lines.append(f"  RSI Filter:        Length={strategy_cfg['rsi_length']}, OB={strategy_cfg['rsi_overbought']}, OS={strategy_cfg['rsi_oversold']}")
        
        if strategy_cfg['use_bollinger_bands']:
            lines.append(f"  Bollinger Bands:   Enabled")
        
        if strategy_cfg['use_vwap']:
            lines.append(f"  VWAP:              Enabled")
        
        if strategy_cfg['use_htf_trend']:
            lines.append(f"  HTF Trend:         Period={strategy_cfg['htf_period']}")
        
        if strategy_cfg['use_stochastic']:
            lines.append(f"  Stochastic:        Enabled")
        
        if strategy_cfg['use_atr']:
            lines.append(f"  ATR:               Length={strategy_cfg.get('atr_len', 'N/A')}")
        
        if strategy_cfg['use_consecutive_green']:
            lines.append(f"  Consecutive Green: {strategy_cfg['consecutive_green_bars']} bars required")
        
        # Show noise filter if enabled
        if strategy_cfg['noise_filter_enabled']:
            lines.append(f"  Noise Filter:      {strategy_cfg['noise_filter_percentage']*100}% threshold")
        
        lines.append("")
        
        # Price-Above-Exit Filter Configuration
        lines.append("PRICE-ABOVE-EXIT FILTER")
        lines.append("-" * 40)
        risk_cfg = config['risk']
        if risk_cfg.get('price_above_exit_filter_enabled', False):
            lines.append(f"Status:              ENABLED")
            lines.append(f"Buffer Points:       {risk_cfg['price_buffer_points']} points")
            lines.append(f"Filter Duration:     {risk_cfg['filter_duration_seconds']} seconds")
            lines.append(f"Description:         After Base SL or Trailing Stop exit, blocks re-entry")
            lines.append(f"                     until price > exit_price + {risk_cfg['price_buffer_points']} points")
            lines.append(f"                     or {risk_cfg['filter_duration_seconds']}s elapsed")
        else:
            lines.append(f"Status:              DISABLED")
            lines.append(f"Description:         Re-entry allowed immediately after any exit")
        
        lines.append("")
        
        # Data Source Details
        lines.append("DATA SOURCE DETAILS")
        lines.append("-" * 40)
        if config['data_simulation']['enabled']:
            lines.append(f"Mode:                File Simulation")
            lines.append(f"File Path:           {config['data_simulation']['file_path']}")
            lines.append(f"Status:              Historical data replay")
        else:
            lines.append(f"Mode:                Live WebStream")
            lines.append(f"Feed Type:           Real-time market data")
            lines.append(f"Status:              Live trading mode")
        
        lines.append("")
        lines.append("=" * 80)
        lines.append("Review the configuration above. Click 'Start Forward Test' to proceed.")
        lines.append("=" * 80)
        
        return "\n".join(lines)

    def _ft_open_status_monitor(self):
        """Open status monitor window for detailed real-time monitoring"""
        try:
            # Check if status window already exists
            if hasattr(self, 'status_window') and self.status_window.winfo_exists():
                self.status_window.lift()
                self.status_window.focus()
                return
                
            # Create status monitor window
            self.status_window = tk.Toplevel(self)
            self.status_window.title("🚀 Forward Test - Live Status Monitor")
            self.status_window.geometry("800x600")
            self.status_window.minsize(600, 400)
            
            # Create tabbed interface in status window
            monitor_notebook = ttk.Notebook(self.status_window)
            monitor_notebook.pack(fill='both', expand=True, padx=10, pady=10)
            
            # Live Updates Tab
            live_frame = ttk.Frame(monitor_notebook)
            monitor_notebook.add(live_frame, text="📊 Live Updates")
            
            live_frame.columnconfigure(0, weight=1)
            live_frame.rowconfigure(0, weight=1)
            
            # Initialize text boxes if they don't exist yet
            if not hasattr(self, 'ft_result_box'):
                self.ft_result_box = tk.Text(live_frame, height=20, state='disabled', wrap='word', font=('Consolas', 14))
                ft_results_scroll = ttk.Scrollbar(live_frame, orient="vertical", command=self.ft_result_box.yview)
                self.ft_result_box.configure(yscrollcommand=ft_results_scroll.set)
                
                self.ft_result_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
                ft_results_scroll.grid(row=0, column=1, sticky='ns')

            # Signals Tab
            signals_frame = ttk.Frame(monitor_notebook)
            monitor_notebook.add(signals_frame, text="🎯 Trading Signals")
            
            signals_frame.columnconfigure(0, weight=1)
            signals_frame.rowconfigure(0, weight=1)
            
            if not hasattr(self, 'ft_signals_box'):
                self.ft_signals_box = tk.Text(signals_frame, height=20, state='disabled', wrap='word', font=('Consolas', 14))
                signals_scroll = ttk.Scrollbar(signals_frame, orient="vertical", command=self.ft_signals_box.yview)
                self.ft_signals_box.configure(yscrollcommand=signals_scroll.set)
                
                self.ft_signals_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
                signals_scroll.grid(row=0, column=1, sticky='ns')

            # Performance Tab
            perf_frame = ttk.Frame(monitor_notebook)
            monitor_notebook.add(perf_frame, text="📈 Performance")
            
            perf_frame.columnconfigure(0, weight=1)
            perf_frame.rowconfigure(0, weight=1)
            
            if not hasattr(self, 'ft_performance_box'):
                self.ft_performance_box = tk.Text(perf_frame, height=20, state='disabled', wrap='word', font=('Consolas', 14))
                perf_scroll = ttk.Scrollbar(perf_frame, orient="vertical", command=self.ft_performance_box.yview)
                self.ft_performance_box.configure(yscrollcommand=perf_scroll.set)
                
                self.ft_performance_box.grid(row=0, column=0, sticky='nsew', padx=(0,2))
                perf_scroll.grid(row=0, column=1, sticky='ns')
                
            # Set window close behavior
            self.status_window.protocol("WM_DELETE_WINDOW", lambda: self.status_window.destroy())
            
        except Exception as e:
            logger.error(f"Error opening status monitor: {e}")
            messagebox.showerror("Status Monitor Error", f"Failed to open status monitor: {e}")

    def _ft_demo_updates(self):
        """Demo method to show how live updates work (for testing purposes)"""
        try:
            import time
            import random
            
            # Simulate connection process
            self._update_ft_status(connection="🟡 Connecting...")
            self._update_ft_result_box("📡 Establishing connection to SmartAPI...\n", "live")
            
            time.sleep(1)
            
            # Simulate successful connection
            self._update_ft_status(
                connection="🟢 Connected", 
                trading="▶️ Active",
                price=45250.50
            )
            self._update_ft_result_box("✅ Connected successfully! Starting tick data stream...\n", "live")
            
            # Simulate some trading signals
            signals = [
                "🟢 BUY Signal: EMA Crossover detected at ₹45,251.25",
                "📊 MACD: Bullish crossover confirmed",
                "⚡ RSI: 45.2 (Neutral zone)",
                "📈 VWAP: Price above VWAP - Bullish bias"
            ]
            
            for signal in signals:
                self._update_ft_result_box(f"{signal}\n", "signals")
                time.sleep(0.5)
            
            # Simulate position entry
            self._update_ft_status(
                position="📈 LONG 25 qty @ ₹45,251.25",
                pnl=-15.50  # Initial negative due to spread
            )
            
            self._update_ft_result_box("🚀 Position OPENED: LONG 25 @ ₹45,251.25\n", "live")
            self._update_ft_result_box("📊 Initial Performance: Entry complete, monitoring for targets...\n", "performance")
            
            # Simulate some price movement and tick updates
            base_price = 45251.25
            tick_count = 0
            
            for i in range(10):
                # Simulate price movement
                price_change = random.uniform(-5, 8)  # Slight upward bias
                new_price = base_price + price_change
                tick_count += 1
                
                # Calculate P&L (assuming 25 qty)
                pnl = (new_price - base_price) * 25
                
                self._update_ft_status(
                    price=new_price,
                    pnl=pnl,
                    tick_count=tick_count
                )
                
                if i % 3 == 0:  # Update log every few ticks
                    self._update_ft_result_box(f"📊 Tick {tick_count}: ₹{new_price:.2f} | P&L: ₹{pnl:.2f}\n", "live")
                
                base_price = new_price
                time.sleep(0.3)
            
            # Simulate exit
            final_pnl = pnl
            self._update_ft_status(
                position="📭 No Position",
                pnl=final_pnl
            )
            
            self._update_ft_result_box(f"🎯 Position CLOSED: Final P&L: ₹{final_pnl:.2f}\n", "live")
            self._update_ft_result_box(f"📈 Trade Summary: +₹{final_pnl:.2f} in {tick_count} ticks\n", "performance")
            
        except Exception as e:
            logger.exception(f"Demo updates failed: {e}")
            self._update_ft_result_box(f"❌ Demo error: {e}\n", "live")

    def _on_closing(self):
        """
        Handle window close event with confirmation dialog.
        
        Warns user if forward test is running and confirms intention to close.
        Stops active trader if user confirms closure.
        """
        # Check if forward test is running
        is_running = hasattr(self, 'active_trader') and self.active_trader is not None
        
        if is_running:
            # Show warning with details about running bot
            response = messagebox.askyesno(
                "Confirm Close",
                "⚠️ WARNING: Forward test is currently running!\n\n"
                "Closing the GUI will:\n"
                "• Stop the trading bot immediately\n"
                "• Close all active positions\n"
                "• Export current results\n\n"
                "Do you wish to close and stop the bot?",
                icon='warning'
            )
            
            if response:
                # User confirmed - stop the bot first
                logger.info("User confirmed GUI closure - stopping active trader")
                try:
                    self._ft_stop_forward_test()
                except Exception as e:
                    logger.error(f"Error stopping forward test during closure: {e}")
                # Proceed with closing
                self.destroy()
            else:
                # User cancelled - do nothing
                logger.info("User cancelled GUI closure - keeping bot running")
                return
        else:
            # No active trading - confirm close without warning
            response = messagebox.askyesno(
                "Confirm Close",
                "Are you sure you want to close the application?",
                icon='question'
            )
            
            if response:
                logger.info("User confirmed GUI closure - no active trading")
                self.destroy()
            else:
                logger.info("User cancelled GUI closure")
                return

    def destroy(self):
        """Override destroy to clean up GUI log handler"""
        try:
            # Remove GUI log handler to prevent memory leaks
            if hasattr(self, 'gui_log_handler'):
                root_logger = logging.getLogger()
                root_logger.removeHandler(self.gui_log_handler)
        except Exception as e:
            logger.error(f"Error cleaning up GUI log handler: {e}")
        finally:
            super().destroy()


if __name__ == "__main__":
    try:
        app = UnifiedTradingGUI()
        logger.info("Starting GUI main loop")
        app.mainloop()
    except Exception as e:
        logger.exception("Failed to start GUI application: %s", e)
        print(f"Failed to start GUI application: {e}")