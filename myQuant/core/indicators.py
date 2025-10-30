"""
core/indicators.py
Unified, parameter-driven indicator library for both backtest and live trading bot.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple, Any
from ..utils.config_helper import ConfigAccessor

logger = logging.getLogger(__name__)

def safe_divide(numerator, denominator, default=0.0):
    """Enhanced safe division with comprehensive error handling."""
    try:
        if pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
            return default
        return numerator / denominator
    except Exception:
        return default

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    fast_ema = calculate_ema(series, fast)
    slow_ema = calculate_ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({'macd': macd_line, 'signal': signal_line, 'histogram': histogram})

def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical_price = (high + low + close) / 3
    vwap = (typical_price * volume).cumsum() / volume.cumsum()
    return vwap

def calculate_ema_crossover_signals(fast_ema: pd.Series, slow_ema: pd.Series) -> pd.DataFrame:
    above = fast_ema > slow_ema
    prev = above.shift(1, fill_value=False)
    return pd.DataFrame({
        'macd_buy_signal': above & (~prev),
        'macd_sell_signal': (~above) & prev,
        'macd_bullish': above
    })

def calculate_macd_signals(macd_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate signal conditions from MACD dataframe"""
    return pd.DataFrame({
        'macd_bullish': macd_df['macd'] > macd_df['signal'],
        'macd_histogram_positive': macd_df['histogram'] > 0
    })

def calculate_htf_trend(close: pd.Series, period: int = 50) -> pd.Series:
    """Calculate higher timeframe trend using EMA"""
    return calculate_ema(close, period)

def calculate_htf_signals(close: pd.Series, htf_ema: pd.Series) -> pd.DataFrame:
    bullish = (close > htf_ema).fillna(False)
    return pd.DataFrame({
        'htf_bullish': bullish,
        'htf_bearish': ~bullish
    })

def calculate_vwap_signals(close: pd.Series, vwap: pd.Series) -> pd.DataFrame:
    bullish = (close > vwap).fillna(False)
    return pd.DataFrame({
        'vwap_bullish': bullish,
        'vwap_bearish': ~bullish
    })

def calculate_rsi_signals(rsi: pd.Series, overbought: float = 70, oversold: float = 30) -> pd.DataFrame:
    return pd.DataFrame({
        'rsi_oversold': (rsi <= oversold).fillna(False),
        'rsi_overbought': (rsi >= overbought).fillna(False),
        'rsi_neutral': ((rsi > oversold) & (rsi < overbought)).fillna(False)
    })

def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands for a price series"""
    middle = calculate_sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower

def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Calculate Stochastic Oscillator"""
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d = k.rolling(window=d_period).mean()
    return k, d

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range"""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

# Removed legacy batch wrapper.
# calculate_all_indicators(df, config) has been intentionally removed to enforce
# incremental-only indicator calculation. Use incremental trackers (IncrementalEMA,
# IncrementalMACD, IncrementalVWAP, IncrementalATR) from this module.

# --- Incremental EMA ---
def update_ema(price: float, prev_ema: float, period: int) -> float:
    """
    Incremental EMA update formula.
    """
    alpha = 2 / (period + 1)
    return (price - prev_ema) * alpha + prev_ema

class IncrementalEMA:
    """
    Incremental EMA tracker holding its own state.
    """
    def __init__(self, period: int, first_price: float = None):
        self.period = period
        self.ema = first_price
        self.alpha = 2.0 / (period + 1)
        self.current_value = first_price if first_price is not None else None
        self.initialized = first_price is not None

    def reset(self):
        """Reset the EMA to uninitialized state"""
        self.ema = None
        self.current_value = None
        self.initialized = False
     
    def update(self, price: float) -> float:
        """
        Update EMA with new price
        """
        try:
            # Validate input
            if price is None or (isinstance(price, float) and np.isnan(price)):
                return self.current_value  # Return last value if available

            if self.ema is None:
                self.ema = price
            else:
                self.ema = update_ema(price, self.ema, self.period)
            # keep current_value in sync for callers that use that attribute
            self.current_value = self.ema
            self.initialized = True
            return self.ema
        except Exception as e:
            logger.error(f"EMA calculation error: {str(e)}")
            return self.current_value if self.current_value is not None else price

# --- Incremental MACD as previously integrated ---
class IncrementalMACD:
    """
    Incremental MACD, Signal line, Histogram.
    """
    def __init__(self, fast=12, slow=26, signal=9, first_price=None):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.initialized = False
        self.fast_ema = IncrementalEMA(fast, first_price)
        self.slow_ema = IncrementalEMA(slow, first_price)
        self.signal_ema = IncrementalEMA(signal, first_price)
 
    def reset(self):
        """Reset all MACD components"""
        self.fast_ema.reset()
        self.slow_ema.reset()
        self.signal_ema.reset()
        self.initialized = False
        
    def update(self, price: float) -> Tuple[float, float, float]:
        """
        Update MACD with new price
        """
        try:
            # Validate input
            if pd.isna(price):
                return 0.0, 0.0, 0.0
                
            fast_ema_val = self.fast_ema.update(price)
            slow_ema_val = self.slow_ema.update(price)
            macd_val = fast_ema_val - slow_ema_val
            signal_val = self.signal_ema.update(macd_val)
            histogram = macd_val - signal_val
                
            return macd_val, signal_val, histogram
        except Exception as e:
            logger.error(f"MACD calculation error: {str(e)}")
            return 0.0, 0.0, 0.0

# --- Incremental VWAP (per session/day) ---
class IncrementalVWAP:
    """
    Incremental VWAP for intraday/session use with robust error handling.
    """
    def __init__(self):
        self.volume_sum = 0.0
        self.pv_sum = 0.0
        self.initialized = False
        
    def reset(self):
        """Reset VWAP to initial state"""
        self.volume_sum = 0.0
        self.pv_sum = 0.0
        self.initialized = False
     
    def update(self, price: float, volume: int, **kwargs) -> float:
        """
        Update VWAP with new price and volume
        """
        try:
            if volume is None or volume <= 0:
                # nothing to accumulate; return last VWAP if available
                if not self.initialized:
                    return float('nan')
                return (self.pv_sum / self.volume_sum) if self.volume_sum > 0 else float('nan')

            if not self.initialized:
                self.volume_sum = float(volume)
                self.pv_sum = float(price) * float(volume)
                self.initialized = True
            else:
                self.volume_sum += float(volume)
                self.pv_sum += float(price) * float(volume)

            return self.pv_sum / self.volume_sum
        except Exception as e:
            logger.error(f"VWAP update error: {e}")
            return (self.pv_sum / self.volume_sum) if self.volume_sum > 0 else float('nan')

class IncrementalATR:
    """
    Incremental ATR, using Welles Wilder smoothing with robust error handling.
    """
    def __init__(self, period=14, first_close=None):
        self.period = period
        self.true_range_ema = IncrementalEMA(period)
        self.prev_close = first_close
        self.initialized = first_close is not None
        
    def reset(self):
        """Reset ATR to initial state"""
        self.true_range_ema.reset()
        self.prev_close = None
        self.initialized = False
     
    def update(self, high: float, low: float, close: float) -> float:
        """
        Update ATR with new high, low, close
        """
        try:
            if self.prev_close is None:
                # On first call set prev_close and return NaN (no TR history)
                tr = high - low
                val = self.true_range_ema.update(tr)
                self.prev_close = close
                self.initialized = True
                return val
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
            val = self.true_range_ema.update(tr)
            self.prev_close = close
            return val
        except Exception as e:
            logger.error(f"ATR update error: {e}")
            # best-effort return
            return self.true_range_ema.current_value if getattr(self.true_range_ema, "current_value", None) is not None else float('nan')

"""
PARAMETER NAMING CONVENTION:
- Main function: calculate_all_indicators(df: pd.DataFrame, params: Dict)
- Parameter name 'params' is MANDATORY for interface compatibility
- All internal usage: params.get('parameter_name', default)

INTERFACE REQUIREMENT:
- The 'params' parameter name CANNOT be changed as it's used by:
  * researchStrategy.py: calculate_all_indicators(df, self.config)  
  * liveStrategy.py: calculate_all_indicators(df, self.config)
  * Multiple indicator calculation functions

CRITICAL: 
- Strategy modules pass their self.config as 'params' to this module
- This creates the interface boundary between 'config' (strategies) and 'params' (indicators)
- Do NOT change 'params' parameter name without updating ALL calling code
"""
