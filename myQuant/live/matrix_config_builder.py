"""
live/matrix_config_builder.py

Configuration builder and validator for matrix parameter testing.

CRITICAL PRINCIPLES:
- Uses defaults.py as SSOT
- Validates all parameter combinations before testing
- Fail-first on invalid configurations
- Handles list-type parameters (tp_points, tp_percents)
- Zero modification to existing code

USAGE:
    from .matrix_config_builder import build_config_from_parameters, validate_parameter_combination
    
    param_values = {'fast_ema': 12, 'slow_ema': 26, 'base_sl_points': 15}
    config = build_config_from_parameters(param_values)
    is_valid, error = validate_parameter_combination(param_values)
"""

import logging
from typing import Dict, Any, Tuple
from copy import deepcopy

from ..utils.config_helper import create_config_from_defaults

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION BUILDING
# ============================================================================

def build_config_from_parameters(
    param_values: Dict[str, Any],
    fixed_params: Dict[str, Any] = None
) -> Dict:
    """
    Build complete configuration from parameter values.
    
    Workflow:
    1. Start with defaults.py (SSOT)
    2. Apply fixed parameters first
    3. Apply test parameter values
    4. Return complete config ready for validation
    
    Args:
        param_values: Parameters to test in this iteration
        fixed_params: Parameters that remain constant across all tests
        
    Returns:
        Complete configuration dictionary
        
    Example:
        >>> param_values = {'fast_ema': 12, 'slow_ema': 26}
        >>> config = build_config_from_parameters(param_values)
        >>> config['strategy']['fast_ema']
        12
    """
    # Start from defaults.py SSOT
    config = create_config_from_defaults()
    
    # Apply fixed parameters first
    if fixed_params:
        for param_name, value in fixed_params.items():
            _inject_parameter(config, param_name, value)
    
    # Apply test parameter values
    for param_name, value in param_values.items():
        _inject_parameter(config, param_name, value)
    
    return config


def _inject_parameter(config: Dict, param_name: str, value: Any):
    """
    Inject parameter value into appropriate config section.
    
    Searches through config sections (strategy, risk, capital, etc.) to find
    where parameter belongs, then injects value.
    
    Args:
        config: Configuration dictionary to modify
        param_name: Parameter name (must exist in defaults.py)
        value: Parameter value to inject
        
    Raises:
        KeyError: If parameter not found in any config section
    """
    # Search for parameter in config sections
    for section in ['strategy', 'risk', 'capital', 'instrument', 'session']:
        if section in config and param_name in config[section]:
            config[section][param_name] = value
            logger.debug(f"Injected {param_name}={value} into config['{section}']")
            return
    
    # Parameter not found
    raise KeyError(
        f"Parameter '{param_name}' not found in config. "
        f"Valid parameters must be defined in defaults.py SSOT."
    )


# ============================================================================
# PARAMETER VALIDATION
# ============================================================================

def validate_parameter_combination(param_values: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate parameter combination before running test.
    
    Validation Rules:
    1. EMA: fast_ema < slow_ema (invalid if violated)
    2. Green Ticks: control_base_sl_green_ticks >= consecutive_green_bars (warning)
    3. Trailing Stop: trail_distance_points <= trail_activation_points
    4. TP Points: Must be positive, increasing list
    5. TP Percents: Must sum to ~1.0, match tp_points length
    6. Price Filter: Buffer and duration > 0
    7. SL vs TP: min(tp_points) should be > base_sl_points (warning)
    
    Args:
        param_values: Dictionary of parameters to validate
        
    Returns:
        Tuple of (is_valid: bool, error_message: str)
        - is_valid: True if valid, False if should skip test
        - error_message: Empty if valid, description if invalid
        
    Example:
        >>> is_valid, error = validate_parameter_combination({'fast_ema': 12, 'slow_ema': 9})
        >>> print(is_valid, error)
        False, "Invalid EMA: fast (12) >= slow (9)"
    """
    
    # 1. EMA Validation: fast < slow (BLOCKING)
    if 'fast_ema' in param_values and 'slow_ema' in param_values:
        fast = param_values['fast_ema']
        slow = param_values['slow_ema']
        if fast >= slow:
            return False, f"Invalid EMA: fast ({fast}) >= slow ({slow})"
    
    # 2. Green Tick Validation (WARNING ONLY)
    if 'control_base_sl_green_ticks' in param_values and 'consecutive_green_bars' in param_values:
        control = param_values['control_base_sl_green_ticks']
        normal = param_values['consecutive_green_bars']
        if control < normal:
            logger.warning(
                f"Control Base SL green ticks ({control}) < normal green bars ({normal}). "
                f"This may cause unexpected behavior."
            )
    
    # 3. Trailing Stop Validation (BLOCKING)
    if 'trail_distance_points' in param_values and 'trail_activation_points' in param_values:
        distance = param_values['trail_distance_points']
        activation = param_values['trail_activation_points']
        if distance > activation:
            return False, f"Invalid Trail: distance ({distance}) > activation ({activation})"
    
    # 4. TP Points Validation (BLOCKING)
    if 'tp_points' in param_values:
        tp_points = param_values['tp_points']
        
        if not isinstance(tp_points, list):
            return False, f"tp_points must be list, got {type(tp_points).__name__}"
        
        if len(tp_points) == 0:
            return False, "tp_points cannot be empty"
        
        if any(tp <= 0 for tp in tp_points):
            return False, f"All tp_points must be positive: {tp_points}"
        
        # Check if increasing (warning only)
        for i in range(len(tp_points) - 1):
            if tp_points[i] >= tp_points[i + 1]:
                logger.warning(f"TP points not strictly increasing: {tp_points}")
                break
    
    # 5. TP Percents Validation (BLOCKING)
    if 'tp_percents' in param_values:
        tp_percents = param_values['tp_percents']
        
        if not isinstance(tp_percents, list):
            return False, f"tp_percents must be list, got {type(tp_percents).__name__}"
        
        if any(p < 0 or p > 1 for p in tp_percents):
            return False, f"All tp_percents must be 0-1: {tp_percents}"
        
        # Check sum (warning only, allow small tolerance)
        total = sum(tp_percents)
        if abs(total - 1.0) > 0.05:
            logger.warning(f"TP percents sum to {total:.3f}, expected ~1.0: {tp_percents}")
    
    # 6. TP Points and Percents Alignment (BLOCKING)
    if 'tp_points' in param_values and 'tp_percents' in param_values:
        if len(param_values['tp_points']) != len(param_values['tp_percents']):
            return False, f"TP points length ({len(param_values['tp_points'])}) != TP percents length ({len(param_values['tp_percents'])})"
    
    # 7. Price Filter Validation (BLOCKING)
    if 'price_buffer_points' in param_values:
        buffer = param_values['price_buffer_points']
        if buffer <= 0:
            return False, f"price_buffer_points must be > 0, got {buffer}"
    
    if 'filter_duration_seconds' in param_values:
        duration = param_values['filter_duration_seconds']
        if duration <= 0:
            return False, f"filter_duration_seconds must be > 0, got {duration}"
    
    # 8. SL vs TP Sanity Check (WARNING ONLY)
    if 'base_sl_points' in param_values and 'tp_points' in param_values:
        sl = param_values['base_sl_points']
        min_tp = min(param_values['tp_points'])
        if min_tp <= sl:
            logger.warning(f"Min TP ({min_tp}) <= Base SL ({sl}). Risk:Reward may be poor.")
    
    # All validations passed
    return True, ""


# ============================================================================
# TEST TAG GENERATION
# ============================================================================

def generate_test_tag(param_values: Dict[str, Any]) -> str:
    """
    Generate unique identifier for parameter combination.
    
    Format: Abbreviated param names with values
    Example: "EMA12-42_MACD12-26-9_SL15_TP67_TA7_TD5_GB3_CBSL5_PF2-180"
    
    Abbreviations:
    - EMA{fast}-{slow}: EMA crossover
    - MACD{f}-{s}-{sig}: MACD parameters
    - SL{n}: Base stop loss points
    - TP{hash}: Take profit set (hash identifies unique set)
    - TPP{hash}: TP percentages (if non-default)
    - TA{n}: Trail activation points
    - TD{n}: Trail distance points
    - GB{n}: Green bars entry requirement
    - CBSL{n}: Control base SL green ticks
    - PF{buf}-{dur}: Price filter buffer-duration
    - R{n}: Risk per trade percent
    - HTF{n}: HTF period
    - RSI{n}: RSI period
    
    Args:
        param_values: Dictionary of parameters
        
    Returns:
        Unique test tag string
    """
    parts = []
    
    # EMA (combined)
    if 'fast_ema' in param_values and 'slow_ema' in param_values:
        parts.append(f"EMA{param_values['fast_ema']}-{param_values['slow_ema']}")
    elif 'fast_ema' in param_values:
        parts.append(f"FEMA{param_values['fast_ema']}")
    elif 'slow_ema' in param_values:
        parts.append(f"SEMA{param_values['slow_ema']}")
    
    # MACD (combined)
    if all(k in param_values for k in ['macd_fast', 'macd_slow', 'macd_signal']):
        parts.append(
            f"MACD{param_values['macd_fast']}-"
            f"{param_values['macd_slow']}-{param_values['macd_signal']}"
        )
    
    # HTF
    if 'htf_period' in param_values:
        parts.append(f"HTF{param_values['htf_period']}")
    
    # Base SL
    if 'base_sl_points' in param_values:
        sl = param_values['base_sl_points']
        parts.append(f"SL{int(sl) if float(sl).is_integer() else sl}")
    
    # TP Points (hash of values for identification)
    if 'tp_points' in param_values:
        tp_points = param_values['tp_points']
        tp_hash = abs(hash(str(tp_points))) % 10000
        parts.append(f"TP{tp_hash}")
    
    # TP Percents (only if non-default)
    if 'tp_percents' in param_values:
        tp_percents = param_values['tp_percents']
        default_percents = [0.40, 0.30, 0.20, 0.10]
        if tp_percents != default_percents:
            tpp_hash = abs(hash(str(tp_percents))) % 10000
            parts.append(f"TPP{tpp_hash}")
    
    # Trail Activation
    if 'trail_activation_points' in param_values:
        ta = param_values['trail_activation_points']
        parts.append(f"TA{int(ta) if float(ta).is_integer() else ta}")
    
    # Trail Distance
    if 'trail_distance_points' in param_values:
        td = param_values['trail_distance_points']
        parts.append(f"TD{int(td) if float(td).is_integer() else td}")
    
    # Green Bars Entry
    if 'consecutive_green_bars' in param_values:
        parts.append(f"GB{param_values['consecutive_green_bars']}")
    
    # Control Base SL Green Ticks
    if 'control_base_sl_green_ticks' in param_values:
        parts.append(f"CBSL{param_values['control_base_sl_green_ticks']}")
    
    # Price Above Exit Filter
    if 'price_buffer_points' in param_values and 'filter_duration_seconds' in param_values:
        buf = param_values['price_buffer_points']
        dur = param_values['filter_duration_seconds']
        parts.append(f"PF{buf}-{dur}")
    elif 'price_buffer_points' in param_values:
        buf = param_values['price_buffer_points']
        parts.append(f"PFB{buf}")
    elif 'filter_duration_seconds' in param_values:
        dur = param_values['filter_duration_seconds']
        parts.append(f"PFD{dur}")
    
    # Risk Percent
    if 'risk_per_trade_percent' in param_values:
        risk = param_values['risk_per_trade_percent']
        parts.append(f"R{risk}")
    
    # RSI
    if 'rsi_length' in param_values:
        parts.append(f"RSI{param_values['rsi_length']}")
    
    # ATR
    if 'atr_len' in param_values:
        parts.append(f"ATR{param_values['atr_len']}")
    
    # Join all parts
    return "_".join(parts) if parts else "DEFAULT"


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_parameter_category(param_name: str) -> str:
    """
    Determine which config section a parameter belongs to.
    
    Args:
        param_name: Parameter name to categorize
        
    Returns:
        Section name: 'strategy', 'risk', 'capital', 'instrument', or 'session'
        
    Raises:
        ValueError: If parameter not found in defaults.py
    """
    from ..config.defaults import create_default_config
    
    default_config = create_default_config()
    
    for section in ['strategy', 'risk', 'capital', 'instrument', 'session']:
        if section in default_config and param_name in default_config[section]:
            return section
    
    raise ValueError(
        f"Parameter '{param_name}' not found in defaults.py. "
        f"All parameters must be defined in SSOT."
    )


def format_parameter_value_for_display(param_name: str, value: Any) -> str:
    """
    Format parameter value for human-readable display.
    
    Args:
        param_name: Parameter name
        value: Parameter value
        
    Returns:
        Formatted string
        
    Example:
        >>> format_parameter_value_for_display('tp_points', [5, 12, 20, 30])
        '[5, 12, 20, 30]'
        >>> format_parameter_value_for_display('fast_ema', 12)
        '12'
    """
    if isinstance(value, list):
        return f"[{', '.join(str(v) for v in value)}]"
    elif isinstance(value, float):
        return f"{value:.1f}" if not value.is_integer() else f"{int(value)}"
    elif isinstance(value, bool):
        return "Yes" if value else "No"
    else:
        return str(value)
