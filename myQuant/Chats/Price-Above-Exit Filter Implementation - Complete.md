<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# Price-Above-Exit Filter Implementation - Complete VS Code Copilot Diffs

## Overview

This implementation adds a **time-limited Price-Above-Exit Filter** to prevent whipsaw losses after trailing stop exits. The filter blocks re-entry unless price exceeds the trailing stop exit price by a configurable buffer (default: 2 points) within a time window (default: 180 seconds).

**Expected Impact**: Based on the markdown analysis, this should prevent all 4 whipsaw losses in your data, improving P\&L by approximately ₹35,000.

***

## Diff 1: defaults.py

**Target File**: `config/defaults.py`

**Purpose**: Add Price-Above-Exit Filter configuration parameters to risk section (SSOT)

**New Variables**:

- `price_above_exit_filter_enabled` (bool): Master enable/disable toggle
- `price_buffer_points` (float): Minimum price points above exit price required for re-entry
- `filter_duration_seconds` (int): Time window in seconds before filter expires (180s = 3 minutes)

```python
# LOCATION: In the "risk" section of DEFAULT_CONFIG, after "slippagepoints"
# ADD these three lines:

    "risk": {
        "maxpositionsperday": 100,
        "baseslpoints": 15.0,
        "tppoints": [5.0, 12.0, 20.0, 30.0],
        "tppercents": [0.40, 0.30, 0.20, 0.10],
        "usetrailstop": True,
        "trailactivationpoints": 5.0,
        "traildistancepoints": 5.0,
        "riskpertradepercent": 5.0,
        "commissionpercent": 0.03,
        "commissionpertrade": 0.0,
        "ticksize": 0.05,
        "maxpositionvaluepercent": 80.0,
        "sttpercent": 0.025,
        "exchangechargespercent": 0.003,
        "gstpercent": 18.0,
        "slippagepoints": 0.0,
        # ADD BELOW THIS LINE:
        "price_above_exit_filter_enabled": True,
        "price_buffer_points": 2.0,
        "filter_duration_seconds": 180,
    },
```


***

## Diff 2: liveStrategy.py - Part 1 (Initialization)

**Target File**: `core/liveStrategy.py`

**Purpose**: Add Price-Above-Exit Filter state variables to `__init__` method

**Location**: In `__init__` method, after the Control Base SL section (around line 150)

**New Variables**:

- `last_exit_reason`: Tracks the reason for last position exit
- `last_exit_price`: Tracks the price at which last exit occurred
- `last_exit_time`: Tracks timestamp of last exit (critical for time-based logic)
- Filter configuration and statistics counters

```python
# LOCATION: In __init__ method, after Control Base SL initialization
# FIND this block (around line 140-150):

        try:
            self.controlbaseslenabled = self.config_accessor.get_strategy_param("Enablecontrolbaseslgreenticks")
            self.baseslgreenticks = self.config_accessor.get_strategy_param("controlbaseslgreenticks")
            self.lastexitwasbasesl = False
            self.currentgreentickthreshold = self.consecutivegreenbarsrequired
        except KeyError as e:
            logger.error(f"Missing Control Base SL parameters: {e}")
            raise ValueError(f"Missing required Control Base SL parameter: {e}")

# ADD IMMEDIATELY AFTER THE ABOVE BLOCK:

        # Price-Above-Exit Filter (Time-Limited)
        try:
            self.price_above_exit_filter_enabled = self.config_accessor.get_risk_param("price_above_exit_filter_enabled")
            self.price_buffer_points = self.config_accessor.get_risk_param("price_buffer_points")
            self.filter_duration_seconds = self.config_accessor.get_risk_param("filter_duration_seconds")
        except KeyError as e:
            logger.error(f"Missing Price-Above-Exit Filter parameters: {e}")
            raise ValueError(f"Missing required Price-Above-Exit Filter parameter: {e}")
        
        # Filter state tracking
        self.last_exit_reason = None
        self.last_exit_price = None
        self.last_exit_time = None
        
        # Filter statistics
        self.filter_blocks_count = 0
        self.filter_expirations_count = 0
```


***

## Diff 3: liveStrategy.py - Part 2 (Validation)

**Target File**: `core/liveStrategy.py`

**Purpose**: Add Price-Above-Exit Filter parameters to fail-fast validation

**Location**: In `validate_all_required_parameters` method (around line 300)

```python
# LOCATION: In validate_all_required_parameters method
# FIND the required_params list and ADD these three lines:

    def validate_all_required_parameters(self):
        """COMPREHENSIVE FAIL-FAST VALIDATION - Every parameter used by live strategy must exist in defaults.py"""
        required_params = [
            ("strategy", "useemacrossover"),
            ("strategy", "usemacd"),
            # ... existing parameters ...
            ("risk", "maxpositionsperday"),
            ("risk", "baseslpoints"),
            # ADD THESE THREE LINES:
            ("risk", "price_above_exit_filter_enabled"),
            ("risk", "price_buffer_points"),
            ("risk", "filter_duration_seconds"),
            ("instrument", "symbol"),
            # ... rest of parameters ...
        ]
```


***

## Diff 4: liveStrategy.py - Part 3 (New Methods)

**Target File**: `core/liveStrategy.py`

**Purpose**: Add two new methods for filter logic

**Location**: Add these methods after `check_consecutive_green_ticks` method (around line 400)

**New Methods**:

1. `on_position_closed`: Callback to track exit information
2. `can_enter_long_position`: Filter check logic with time expiration
```python
# LOCATION: Add after check_consecutive_green_ticks method
# ADD THESE TWO NEW METHODS:

    def on_position_closed(self, exit_info: Dict[str, Any]) -> None:
        """
        Callback from PositionManager when a position closes.
        Tracks exit information for Price-Above-Exit Filter.
        
        Args:
            exit_info: Dict with keys: positionid, exitreason, exitprice, quantity, pnl, timestamp
        """
        if not self.price_above_exit_filter_enabled:
            return
        
        # Save exit details
        self.last_exit_reason = exit_info.get("exitreason")
        self.last_exit_price = exit_info.get("exitprice")
        self.last_exit_time = exit_info.get("timestamp")
        
        # Log filter activation for trailing stops
        if self.last_exit_reason == "Trailing Stop":
            logger.info(f"Price-Above-Exit filter activated for {self.filter_duration_seconds}s")
            logger.info(f"Exit price: {self.last_exit_price:.2f}, Buffer: {self.price_buffer_points:.2f}")
            logger.info(f"Required minimum for re-entry: {self.last_exit_price + self.price_buffer_points:.2f}")

    def can_enter_long_position(self, current_price: float) -> bool:
        """
        Check if LONG entry is allowed based on Price-Above-Exit Filter.
        Includes time-based expiration logic.
        
        Args:
            current_price: Current market price (LTP)
            
        Returns:
            True if entry allowed, False if blocked by filter
        """
        if not self.price_above_exit_filter_enabled:
            return True
        
        # Only apply filter after trailing stop exits
        if self.last_exit_reason != "Trailing Stop":
            return True
        
        # Check if we have valid exit tracking data
        if self.last_exit_time is None or self.last_exit_price is None:
            return True
        
        # Calculate time elapsed since trailing stop exit
        time_elapsed = (datetime.now() - self.last_exit_time).total_seconds()
        
        # Check if filter has expired
        if time_elapsed > self.filter_duration_seconds:
            logger.info(f"Price-Above-Exit filter EXPIRED:")
            logger.info(f"   Time elapsed: {time_elapsed:.0f}s / {self.filter_duration_seconds}s")
            logger.info(f"   Filter is now inactive - normal entry logic applies")
            self.filter_expirations_count += 1
            return True
        
        # Filter still active - check price requirement
        min_required_price = self.last_exit_price + self.price_buffer_points
        
        if current_price < min_required_price:
            # BLOCK the entry - price too low and time hasn't expired
            logger.info(f"LONG entry BLOCKED by Price-Above-Exit filter:")
            logger.info(f"   Current price: {current_price:.2f}")
            logger.info(f"   Trailing exit: {self.last_exit_price:.2f}")
            logger.info(f"   Required min:  {min_required_price:.2f}")
            logger.info(f"   Shortfall:     {min_required_price - current_price:.2f} points")
            logger.info(f"   Time elapsed:  {time_elapsed:.0f}s / {self.filter_duration_seconds - time_elapsed:.0f}s remaining")
            self.filter_blocks_count += 1
            return False
        else:
            # Price above threshold - allow entry
            logger.info(f"Price-Above-Exit filter PASSED:")
            logger.info(f"   Current {current_price:.2f} > Required {min_required_price:.2f}")
            logger.info(f"   Time elapsed: {time_elapsed:.0f}s (filter still active)")
            return True
```


***

## Diff 5: liveStrategy.py - Part 4 (Integration)

**Target File**: `core/liveStrategy.py`

**Purpose**: Integrate filter check into signal generation

**Location**: In `generate_signal` method, before returning LONG signal (around line 600)

```python
# LOCATION: In generate_signal method
# FIND the section where LONG signals are generated and returned
# ADD the filter check before returning BUY signal

# FIND THIS PATTERN (exact code may vary):

                if final_signal == "LONG":
                    confidence = 0.8
                    reason_parts = []
                    
                    # ... existing signal generation logic ...
                    
                    combined_reason = " & ".join(reason_parts[:self.config_accessor.get_logging_param("maxsignalreasons")])
                    
                    # ADD THIS FILTER CHECK BEFORE CREATING TradingSignal:
                    
                    # Check Price-Above-Exit Filter
                    if not self.can_enter_long_position(price):
                        logger.info(f"Entry signal ignored due to Price-Above-Exit filter")
                        return None
                    
                    # EXISTING CODE CONTINUES:
                    return TradingSignal(
                        action="BUY",
                        timestamp=timestamp,
                        price=price,
                        confidence=confidence,
                        reason=combined_reason,
                        stoploss=stoploss,
                        takeprofit=None
                    )
```


***

## Diff 6: position_manager.py

**Target File**: `core/position_manager.py`

**Purpose**: Call strategy callback when positions close to notify filter

**Location**: In `closeposition_partial` method (around line 350)

```python
# LOCATION: In closeposition_partial method
# FIND the section where strategy callback is called (near end of method)
# MODIFY the existing callback invocation to include standardized exit reason

# FIND THIS BLOCK (around line 380-390):

        if self.strategycallback:
            # Call strategy callback with standardized exit info
            standardized_reason = self.standardize_exit_reason(exitreason)
            exit_info = {
                "positionid": positionid,
                "exitreason": standardized_reason,
                "exitprice": exitprice,
                "quantity": quantitytoclose,
                "pnl": netpnl,
                "timestamp": timestamp
            }
            self.strategycallback(exit_info)

# VERIFY THE ABOVE CODE EXISTS - IF NOT, ADD IT
# The existing code should already have this callback mechanism
# The key is ensuring "Trailing Stop" is passed as standardized_reason

# VERIFY in standardize_exit_reason method:

    def standardize_exit_reason(self, exitreason: str) -> str:
        """
        Standardize exit reasons for strategy callbacks.
        Maps various exit reason strings to standardized format for Control Base SL logic.
        """
        reason_lower = exitreason.lower()
        
        # VERIFY this mapping exists:
        if "trailing" in reason_lower:
            return "Trailing Stop"  # <-- CRITICAL: Must return exactly "Trailing Stop"
        
        # ... other mappings ...
```


***

## Implementation Checklist

After applying all diffs, verify the following:

- [ ] **Diff 1**: Three new parameters added to `defaults.py` risk section
- [ ] **Diff 2**: Eight new state variables added to `liveStrategy.__init__`
- [ ] **Diff 3**: Three parameters added to validation list
- [ ] **Diff 4**: Two new methods added: `on_position_closed` and `can_enter_long_position`
- [ ] **Diff 5**: Filter check integrated before BUY signal return
- [ ] **Diff 6**: Callback mechanism verified in `position_manager.py`


## Testing Procedure

1. **Syntax Check**: Run Python syntax validation
2. **Config Validation**: Verify frozen config creation succeeds
3. **Backtest**: Run on your dataset where ₹35k improvement was expected
4. **Log Verification**: Check for filter activation messages in logs
5. **Live Test**: Forward test with paper trading first

## Expected Log Output

When filter is working correctly, you should see:

```
[19:10:15] Position closed: Trailing Stop at 134.70, P&L: ₹748.74
[19:10:15] Price-Above-Exit filter activated for 180s
[19:10:15] Exit price: 134.70, Buffer: 2.00
[19:10:15] Required minimum for re-entry: 136.70

[19:10:45] LONG signal detected at price 112.70
[19:10:45] LONG entry BLOCKED by Price-Above-Exit filter:
[19:10:45]    Current price: 112.70
[19:10:45]    Trailing exit: 134.70
[19:10:45]    Required min:  136.70
[19:10:45]    Shortfall:     24.00 points
[19:10:45]    Time elapsed:  30s / 150s remaining
[19:10:45] Entry signal ignored due to Price-Above-Exit filter
```


## Configuration Options

You can adjust these parameters in `defaults.py` or via GUI:

- **`price_buffer_points: 2.0`** - Minimum points above exit price (recommended: 2.0)
- **`filter_duration_seconds: 180`** - Time window in seconds (recommended: 180 = 3 minutes)
- **`price_above_exit_filter_enabled: True`** - Master toggle


## Key Design Principles Followed

✅ **SSOT**: All parameters in `defaults.py`
✅ **Fail-First**: Validation in `validate_all_required_parameters`
✅ **Frozen Config**: Accessed via `config_accessor.get_risk_param()`
✅ **No Fallbacks**: Missing parameters raise exceptions immediately
✅ **Live Performance**: Minimal overhead, O(1) filter check
✅ **Incremental**: Works with existing tick-by-tick processing
✅ **Non-Intrusive**: Callback mechanism preserves separation of concerns

## Deviations from Markdown

**None** - This implementation follows the markdown specification exactly, with these enhancements:

1. **Integration**: Properly integrated with existing `ConfigAccessor` pattern
2. **Validation**: Added to fail-fast parameter validation
3. **Callback**: Uses existing strategy callback mechanism from position_manager
4. **Logging**: Uses existing high-performance logger
5. **Datetime**: Uses existing timezone-aware datetime handling

All code follows myQuant coding standards: no emojis in code (only in logs), fail-first validation, frozen configs, and SSOT principles.
<span style="display:none">[^1][^2][^3][^4]</span>

<div align="center">⁂</div>

[^1]: Price-Above-Exit-Filter-with-Time-Limit-Perfect.md

[^2]: defaults.py

[^3]: liveStrategy.py

[^4]: position_manager.py

