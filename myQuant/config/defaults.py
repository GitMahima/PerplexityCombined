"""
defaults.py - Single Source of Truth for All Configuration Defaults

This file contains all default values used by both GUI and non-GUI components.
Any changes to defaults should be made here only.
"""

import os
from typing import Dict, Any

# Environment variable loading moved to live trading authentication path only
# This ensures data simulation users don't see unnecessary dotenv warnings

def load_live_trading_credentials():
    """Load credentials for live trading authentication - called only when needed
    
    Uses direct file reading as primary method since python-dotenv is not available 
    in GUI environments. Falls back to environment variables if file is not available.
    """
    credentials = {}
    
    # Primary: Direct file reading from external credentials file
    angelalgo_env_path = r"C:\Users\user\projects\angelalgo\.env.trading"
    
    if os.path.exists(angelalgo_env_path):
        try:
            print(f"[SUCCESS] Loading credentials from: {angelalgo_env_path}")
            with open(angelalgo_env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key == 'API_KEY':
                            credentials["api_key"] = value
                        elif key == 'CLIENT_ID':
                            credentials["client_code"] = value
                        elif key == 'PASSWORD':
                            credentials["pin"] = value
                        elif key == 'SMARTAPI_TOTP_SECRET':
                            credentials["totp_secret"] = value
        except Exception as e:
            print(f"[ERROR] Failed to read credentials file: {e}")
            # Fall back to environment variables
            credentials["api_key"] = os.getenv("API_KEY", "")
            credentials["client_code"] = os.getenv("CLIENT_ID", "")
            credentials["pin"] = os.getenv("PASSWORD", "")
            credentials["totp_secret"] = os.getenv("SMARTAPI_TOTP_SECRET", "")
    else:
        # Fallback: Try environment variables and local .env file
        try:
            from dotenv import load_dotenv
            local_env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
            if os.path.exists(local_env_path):
                load_dotenv(local_env_path, override=True)
                print(f"[SUCCESS] Environment variables loaded from local: {local_env_path}")
        except ImportError:
            print("[INFO] python-dotenv not available, using system environment variables only")
        
        credentials["api_key"] = os.getenv("API_KEY", "")
        credentials["client_code"] = os.getenv("CLIENT_ID", "")
        credentials["pin"] = os.getenv("PASSWORD", "")
        credentials["totp_secret"] = os.getenv("SMARTAPI_TOTP_SECRET", "")
    
    return credentials

# Single Source of Truth for defaults used by GUI and runners.
DEFAULT_CONFIG: Dict[str, Any] = {
    "logging": {
        # Simplified logging - console only (Excel captures all important data)
        "logfile": "console_only.log",  # Dummy value for compatibility
        "log_file": "console_only.log",  # Legacy alias
        "log_progress": False,
        "max_signal_reasons": 5,
        "log_to_file": False,  # Disabled - Excel results capture everything
        "console_output": True,  # Keep console for development
        "verbosity": "INFO",
        "tick_log_interval": 2500,
        "max_file_size": 0,  # Not used
        "backup_count": 0,   # Not used
        "log_level_overrides": {},
        "json_event_log": False,  # Disabled
        "json_event_file": "not_used.jsonl"  # Dummy value
    },
    "debug": {
        # Environment-aware error handling configuration
        "environment": "DEVELOPMENT",  # DEVELOPMENT, TESTING, PRODUCTION
        "log_all_errors": True,
        "suppress_low_severity": False,
        "suppress_medium_severity": False,
        "performance_mode": False,
        "max_errors_per_minute": 1000,
        "detailed_green_tick_logging": True,
        "detailed_signal_logging": True,
        "enable_error_classification": True,
        "halt_on_critical_errors": True
    },
    # Production-optimized debug configuration (use via GUI toggle or config override)
    "debug_production": {
        "environment": "PRODUCTION",
        "log_all_errors": False,
        "suppress_low_severity": True,
        "suppress_medium_severity": True,
        "performance_mode": True,
        "max_errors_per_minute": 10,
        "detailed_green_tick_logging": False,
        "detailed_signal_logging": False,
        "enable_error_classification": True,
        "halt_on_critical_errors": True
    },
    "strategy": {
        "strategy_version": 1,
        "use_ema_crossover": True,
        "use_macd": False,
        "use_vwap": False,
        "use_rsi_filter": False,
        "use_htf_trend": False,
        "use_bollinger_bands": False,
        "use_stochastic": False,
        "use_atr": False,
        "use_consecutive_green": True,
        "fast_ema": 18,
        "slow_ema": 42,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_length": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "htf_period": 20,
        "consecutive_green_bars": 3,
        "atr_len": 14,
        "indicator_update_mode": "tick",
        "min_warmup_ticks": 50,  # Minimum ticks before trading starts (Phase 0)
        # Add noise filter parameters
        "noise_filter_enabled": False,  # DISABLED - Count all price movements as green/red ticks
        "noise_filter_percentage": 0.0001,  # 0.01% threshold
        "noise_filter_min_ticks": 1.0,      # Minimum number of ticks to consider
        # nan/recovery thresholds (kept here for future use)
        "nan_streak_threshold": 7,
        "nan_recovery_threshold": 3,
        # Control Base SL feature - dynamic green tick requirements after base SL exits
        "Enable_control_base_sl_green_ticks": True,      # Enable dynamic entry control after base SL
        "control_base_sl_green_ticks": 4      # Required green ticks after base SL (normal uses consecutive_green_bars)
    },
    "risk": {
        "max_positions_per_day": 100,
        "base_sl_points": 15.0,
        "tp_points": [5.0, 12.0, 15.0, 17.0],
        "tp_percents": [0.40, 0.30, 0.20, 0.10],
        "use_trail_stop": True,
        "trail_activation_points": 5.0,
        "trail_distance_points": 5.0,
        "risk_per_trade_percent": 5.0,
        "commission_percent": 0.03,
        "commission_per_trade": 0.0,
        "tick_size": 0.05,
        # PositionManager runtime expectations (single-source defaults)
        "max_position_value_percent": 80.0,  # 80% of capital for risk management (Phase 0 fix)
        "stt_percent": 0.025,
        "exchange_charges_percent": 0.003,
        "gst_percent": 18.0,
        "slippage_points": 0.0,
        # Price-Above-Exit Filter (prevents re-entry after Base SL or Trailing Stop until price recovers)
        "price_above_exit_filter_enabled": True,  # Enable filter
        "price_buffer_points": 2.0,              # Points above exit price required
        "filter_duration_seconds": 180           # Filter expires after this duration (3 min)
    },
    "capital": {
        "initial_capital": 100000.0
    },
    "performance": {
        "instrumentation_enabled": False,  # Enable for Phase 1 baseline measurement
        "instrumentation_window_size": 1000,  # Number of ticks to track
        "baseline_measurement_ticks": 1000,  # Ticks for baseline measurement
    },
    "instrument": {
        "symbol": "NIFTY",  # Default to Nifty options - lot_size now comes from instrument_mappings (SSOT)
        "exchange": "NFO",
        "product_type": "INTRADAY"
        # instrument_token: Dynamic per option contract, populated when user selects specific option
        # lot_size and tick_size removed - instrument_mappings is now SSOT
    },
    # Comprehensive instrument mapping with corrected lot sizes (as of Oct 2024)
    # Exchange codes map to Angel One API: NFO->1, NSE->2, BFO->3 (via exchange_mapper.py)
    "instrument_mappings": {
        # Index Options - Updated with current lot sizes and Angel One compatible exchange codes
        "NIFTY": {"lot_size": 75, "exchange": "NFO", "tick_size": 0.05, "type": "Index Options", "angel_exchange_type": 1},
        "BANKNIFTY": {"lot_size": 15, "exchange": "NFO", "tick_size": 0.05, "type": "Index Options", "angel_exchange_type": 1},
        "FINNIFTY": {"lot_size": 25, "exchange": "NFO", "tick_size": 0.05, "type": "Index Options", "angel_exchange_type": 1},
        "MIDCPNIFTY": {"lot_size": 50, "exchange": "NFO", "tick_size": 0.05, "type": "Index Options", "angel_exchange_type": 1},
        "SENSEX": {"lot_size": 10, "exchange": "BFO", "tick_size": 0.05, "type": "Index Options", "angel_exchange_type": 3},
        "BANKEX": {"lot_size": 15, "exchange": "BFO", "tick_size": 0.05, "type": "Index Options"},
        
        # Individual Stock Options - Updated with current lot sizes and CORRECT Angel One exchange codes
        "RELIANCE": {"lot_size": 250, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "HDFCBANK": {"lot_size": 550, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "ICICIBANK": {"lot_size": 775, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "INFY": {"lot_size": 300, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "TCS": {"lot_size": 150, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "SBIN": {"lot_size": 1500, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "LT": {"lot_size": 300, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "WIPRO": {"lot_size": 1200, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "MARUTI": {"lot_size": 100, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "BHARTIARTL": {"lot_size": 1081, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        
        # Additional Popular Stock Options with Current Lot Sizes
        "ADANIPORTS": {"lot_size": 1200, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "ASIANPAINT": {"lot_size": 300, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "AXISBANK": {"lot_size": 1200, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "BAJFINANCE": {"lot_size": 125, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "BAJAJFINSV": {"lot_size": 600, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "COALINDIA": {"lot_size": 2400, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "DRREDDY": {"lot_size": 150, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "EICHERMOT": {"lot_size": 200, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "GRASIM": {"lot_size": 450, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "HCLTECH": {"lot_size": 600, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "HEROMOTOCO": {"lot_size": 225, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "HINDALCO": {"lot_size": 1875, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "HINDUNILVR": {"lot_size": 300, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "INDUSINDBK": {"lot_size": 900, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "ITC": {"lot_size": 1600, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "JSWSTEEL": {"lot_size": 1200, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "KOTAKBANK": {"lot_size": 400, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "NESTLEIND": {"lot_size": 50, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "NTPC": {"lot_size": 2700, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "ONGC": {"lot_size": 3400, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "POWERGRID": {"lot_size": 2400, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "SUNPHARMA": {"lot_size": 600, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "TATAMOTORS": {"lot_size": 1500, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "TATASTEEL": {"lot_size": 6000, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "TECHM": {"lot_size": 600, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "TITAN": {"lot_size": 250, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        "ULTRACEMCO": {"lot_size": 100, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Options"},
        
        # Index Futures - Same lot sizes as options with CORRECT Angel One exchange codes
        "NIFTYFUT": {"lot_size": 75, "exchange": "NFO", "tick_size": 0.05, "type": "Index Futures"},
        "BANKNIFTYFUT": {"lot_size": 15, "exchange": "NFO", "tick_size": 0.05, "type": "Index Futures"},
        
        # Stock Futures - Same lot sizes as stock options with CORRECT Angel One exchange codes
        "RELIANCEFUT": {"lot_size": 250, "exchange": "NFO", "tick_size": 0.05, "type": "Stock Futures"},
        
        # Cash Market (lot size = 1 for cash) with CORRECT Angel One exchange codes
        "NIFTY_CASH": {"lot_size": 1, "exchange": "NSE", "tick_size": 0.05, "type": "Cash Market"},
        "BANKNIFTY_CASH": {"lot_size": 1, "exchange": "NSE", "tick_size": 0.05, "type": "Cash Market"},
        "RELIANCE_CASH": {"lot_size": 1, "exchange": "NSE", "tick_size": 0.05, "type": "Cash Market"},
        "HDFCBANK_CASH": {"lot_size": 1, "exchange": "NSE", "tick_size": 0.05, "type": "Cash Market"}
    },
    "session": {
        "is_intraday": True,
        "start_hour": 9,
        "start_min": 15,
        "end_hour": 15,
        "end_min": 30,
        "start_buffer_minutes": 20,
        "end_buffer_minutes": 40,
        "no_trade_start_minutes": 5,
        "no_trade_end_minutes": 10,
        "timezone": "Asia/Kolkata"
    },
    "backtest": {
        "allow_short": False,
        "close_at_session_end": True,
        "save_results": True,
        "results_dir": r"C:\Users\user\Desktop\BotResults\results\Back Test",
        "log_level": "INFO"
    },
    "live": {
        "paper_trading": True,
        "exchange_type": "NFO",
        "feed_type": "Quote",
        "log_ticks": False,
        "visual_indicator": True,
        "api_key": "",  # Loaded during live trading authentication only
        "client_code": "",  # Loaded during live trading authentication only
        "pin": "",  # Loaded during live trading authentication only
        "totp_secret": "",  # Loaded during live trading authentication only
        "allow_interactive_auth": False  # Enable interactive PIN/TOTP prompts when session expires
    }
}
