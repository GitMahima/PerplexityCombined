"""
utils/time_utils.py - Lean timezone utilities for trading system

DESIGN PHILOSOPHY: Keep only functions actually called from outside this module.
No abstraction for abstraction's sake. SSOT is defaults.py, not time_utils.py.

This module provides 6 core utilities:
- now_ist() - Get current time in IST
- ensure_tz_aware() - Make datetime timezone-aware
- is_within_session() - Check if within trading session
- apply_buffer_to_time() - Apply time buffer
- format_timestamp() - Format for file naming
- is_time_to_exit() - Legacy session exit check (migrate to is_within_session)
"""

import pytz
from datetime import datetime, time, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# IST timezone constant - used by broker_adapter, websocket_stream
IST = pytz.timezone('Asia/Kolkata')

# ============================================================================
# CORE FUNCTIONS - Called from outside this module
# ============================================================================

def now_ist() -> datetime:
    """
    Get current time in IST (Asia/Kolkata) timezone.
    
    This is the standard "now" for the trading system.
    Always returns timezone-aware datetime.
    
    Used by: trader.py, data_simulator.py, position_manager.py
    
    Returns:
        Timezone-aware datetime in IST
    """
    return datetime.now(IST)

def ensure_tz_aware(dt: datetime, fallback_tz=None, default_tz=None) -> datetime:
    """
    Ensure datetime is timezone-aware.
    
    Used by: liveStrategy.py, researchStrategy.py, backtest_runner.py
    
    Args:
        dt: Datetime to check/convert
        fallback_tz: Timezone to copy from if available
        default_tz: Timezone to use if dt is naive (defaults to IST)
    
    Returns:
        Timezone-aware datetime
    """
    if dt is None:
        return None
    
    if dt.tzinfo is not None:
        return dt  # Already timezone-aware
    
    # Try fallback timezone first
    if fallback_tz and hasattr(fallback_tz, 'tzinfo') and fallback_tz.tzinfo:
        return dt.replace(tzinfo=fallback_tz.tzinfo)
    
    # Use default timezone (IST if not specified)
    tz = default_tz if default_tz else IST
    if isinstance(tz, str):
        tz = pytz.timezone(tz)
    
    return tz.localize(dt)

def is_within_session(current_time: datetime, session_start: time, session_end: time) -> bool:
    """
    Check if current time is within session (handles overnight sessions).
    
    Used by: liveStrategy.py, researchStrategy.py, position_manager.py, backtest_runner.py
    
    Args:
        current_time: Time to check (must be timezone-aware)
        session_start: Session start time
        session_end: Session end time
    
    Returns:
        True if within session
    
    Raises:
        ValueError: If current_time is timezone-naive
    """
    if current_time.tzinfo is None:
        raise ValueError(
            "current_time must be timezone-aware. "
            "Use ensure_tz_aware() or now_ist() to create timezone-aware datetime."
        )
    
    current = current_time.time()
    
    if session_start <= session_end:
        # Normal session (e.g., 9:15 to 15:30)
        return session_start <= current <= session_end
    else:
        # Overnight session (e.g., 15:30 to 9:15 next day)
        return current >= session_start or current <= session_end

def apply_buffer_to_time(base_time: time, buffer_minutes: int, is_start: bool = True) -> time:
    """
    Apply time buffer to session boundaries.
    
    Used by: liveStrategy.py, researchStrategy.py, position_manager.py, backtest_runner.py
    
    Args:
        base_time: Base time to adjust
        buffer_minutes: Minutes to add/subtract
        is_start: If True, add buffer (start later); if False, subtract buffer (end earlier)
    
    Returns:
        Adjusted time object
    
    Example:
        >>> start = time(9, 15)
        >>> buffered = apply_buffer_to_time(start, 5, is_start=True)
        >>> print(buffered)  # 09:20
    """
    # Use arbitrary date for calculation
    dummy_date = datetime(2000, 1, 1)
    dt = datetime.combine(dummy_date, base_time)
    
    if is_start:
        dt += timedelta(minutes=buffer_minutes)
    else:
        dt -= timedelta(minutes=buffer_minutes)
    
    return dt.time()

def format_timestamp(dt: datetime, include_timezone: bool = False) -> str:
    """
    Format datetime for file/log naming.
    
    Used by: backtest/results.py
    
    Args:
        dt: Datetime to format
        include_timezone: Whether to include timezone abbreviation
        
    Returns:
        Formatted timestamp string (YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_TZ)
    """
    if include_timezone:
        return dt.strftime("%Y%m%d_%H%M%S_%Z")
    return dt.strftime("%Y%m%d_%H%M%S")

# ============================================================================
# LEGACY FUNCTIONS - For backward compatibility (consider migrating away)
# ============================================================================

def normalize_datetime_to_ist(dt: datetime, timezone_str: str = 'Asia/Kolkata') -> datetime:
    """
    LEGACY: Convert datetime to IST timezone.
    
    DEPRECATED: Use ensure_tz_aware() instead for better clarity.
    Kept for backward compatibility with broker_adapter.py and liveStrategy.py.
    
    Used by: broker_adapter.py, liveStrategy.py
    
    Args:
        dt: Input datetime (naive or aware)
        timezone_str: Target timezone (defaults to IST)
    
    Returns:
        Timezone-aware datetime in specified timezone
    """
    tz = pytz.timezone(timezone_str)
    
    if dt.tzinfo is None:
        # Naive datetime - assume it's in target timezone
        return tz.localize(dt)
    else:
        # Aware datetime - convert to target timezone
        return dt.astimezone(tz)

def is_time_to_exit(current_time: datetime, exit_buffer: int, end_hour: int, end_min: int) -> bool:
    """
    LEGACY: Check if it's time to exit based on session end buffer.
    
    DEPRECATED: Use is_within_session() with apply_buffer_to_time() instead.
    Kept for backward compatibility (imported by liveStrategy.py but not actively used).
    
    Args:
        current_time: Current datetime (timezone-aware)
        exit_buffer: Minutes before session end to trigger exit
        end_hour: Session end hour
        end_min: Session end minute
    
    Returns:
        True if within exit buffer window
    """
    if current_time.tzinfo is None:
        current_time = ensure_tz_aware(current_time)
    
    session_end = time(end_hour, end_min)
    exit_time = apply_buffer_to_time(session_end, exit_buffer, is_start=False)
    
    current_time_only = current_time.time()
    return current_time_only >= exit_time


# ============================================================================
# END OF MODULE - ~230 lines (71% reduction from 747 lines)
# 
# REMOVED FUNCTIONS (14 functions, ~500 lines of abstraction bloat):
# All had 0 external calls - only used internally or not used at all.
#
# - get_market_session_times() → 0 external, 3 internal
# - is_market_session() → 0 external, 1 internal
# - is_weekday() → 0 external, 2 internal
# - get_market_close_time() → 0 external
# - get_session_remaining_minutes() → 0 external
# - calculate_session_progress() → 0 external
# - get_next_trading_day() → 0 external
# - get_previous_trading_day() → 0 external
# - is_pre_market() → 0 external
# - is_post_market() → 0 external
# - wait_for_market_open() → 0 external
# - get_trading_session_info() → 0 external
# - format_duration() → 0 external
# - get_market_calendar() → 0 external
#
# PHILOSOPHY: If it's not called from outside, don't create a function.
# Inline 2-3 line logic where needed. Don't abstract for abstraction's sake.
# Your SSOT principle says "keep it simple" - we applied it here!
# ============================================================================
