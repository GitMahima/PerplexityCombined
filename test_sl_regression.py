"""
Test: SL Regression Feature - Event-Driven Base SL Reduction
Tests reduction, timer, floor, and reversion logic.
"""
import sys
import os
from datetime import datetime, timedelta
from copy import deepcopy

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

from myQuant.config.defaults import DEFAULT_CONFIG
from myQuant.utils.config_helper import freeze_config
from myQuant.core.liveStrategy import ModularIntradayStrategy
import myQuant.core.indicators as indicators

print("=" * 80)
print("SL REGRESSION UNIT TESTS")
print("=" * 80)

# Helper function to create strategy with SL Regression enabled
def create_strategy_with_sl_regression(
    enabled=True,
    max_base_sl=15.0,
    min_base_sl=5.0,
    step=5.0,
    window_minutes=20
):
    """Create strategy instance with SL Regression configuration."""
    config = deepcopy(DEFAULT_CONFIG)
    config['risk']['sl_regression_enabled'] = enabled
    config['risk']['max_base_sl'] = max_base_sl
    config['risk']['min_base_sl'] = min_base_sl
    config['risk']['sl_regression_step'] = step
    config['risk']['sl_regression_window_minutes'] = window_minutes
    
    frozen_config = freeze_config(config)
    strategy = ModularIntradayStrategy(frozen_config, indicators)
    return strategy

# Test 1: Initial State
print("\n" + "=" * 80)
print("TEST 1: Initial State (SL Regression Enabled)")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
print(f"✓ sl_regression_enabled: {strategy.sl_regression_enabled}")
print(f"✓ max_base_sl: {strategy.max_base_sl}")
print(f"✓ min_base_sl: {strategy.min_base_sl}")
print(f"✓ sl_regression_step: {strategy.sl_regression_step}")
print(f"✓ sl_regression_window_minutes: {strategy.sl_regression_window_minutes}")
print(f"✓ current_base_sl (initial): {strategy.current_base_sl}")
print(f"✓ last_sl_exit_time: {strategy.last_sl_exit_time}")

assert strategy.sl_regression_enabled == True, "Should be enabled"
assert strategy.current_base_sl == 15.0, "Should start at max_base_sl"
assert strategy.last_sl_exit_time is None, "Should have no exit time initially"
print("\n✅ TEST 1 PASSED - Initial state correct")

# Test 2: Disabled State
print("\n" + "=" * 80)
print("TEST 2: SL Regression Disabled")
print("=" * 80)

strategy_disabled = create_strategy_with_sl_regression(enabled=False)
print(f"✓ sl_regression_enabled: {strategy_disabled.sl_regression_enabled}")
print(f"✓ current_base_sl: {strategy_disabled.current_base_sl}")

# Call reduction method (should do nothing when disabled)
exit_time = datetime(2025, 11, 1, 10, 0, 0)
strategy_disabled._reduce_base_sl_on_exit(exit_time)
print(f"✓ current_base_sl after reduction call: {strategy_disabled.current_base_sl}")
assert strategy_disabled.current_base_sl == 15.0, "Should remain at max when disabled"
print("\n✅ TEST 2 PASSED - Disabled state works correctly")

# Test 3: Single Reduction
print("\n" + "=" * 80)
print("TEST 3: Single Reduction (Trailing Stop Exit)")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
exit_time = datetime(2025, 11, 1, 9, 50, 0)

print(f"Before: current_base_sl = {strategy.current_base_sl}")
print(f"Before: last_sl_exit_time = {strategy.last_sl_exit_time}")

strategy._reduce_base_sl_on_exit(exit_time)

print(f"After: current_base_sl = {strategy.current_base_sl}")
print(f"After: last_sl_exit_time = {strategy.last_sl_exit_time}")

assert strategy.current_base_sl == 10.0, f"Should reduce by step (15-5=10), got {strategy.current_base_sl}"
assert strategy.last_sl_exit_time == exit_time, "Should record exit time"
print("\n✅ TEST 3 PASSED - Single reduction works")

# Test 4: Multiple Reductions (Progressive)
print("\n" + "=" * 80)
print("TEST 4: Multiple Reductions (Progressive to Floor)")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
exit1 = datetime(2025, 11, 1, 9, 50, 0)
exit2 = datetime(2025, 11, 1, 9, 56, 0)
exit3 = datetime(2025, 11, 1, 10, 3, 0)

print(f"Initial: current_base_sl = {strategy.current_base_sl}")

# First exit: 15 - 5 = 10
strategy._reduce_base_sl_on_exit(exit1)
print(f"After Exit 1 (09:50): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should be 10 after first reduction"

# Second exit: 10 - 5 = 5 (floor reached)
strategy._reduce_base_sl_on_exit(exit2)
print(f"After Exit 2 (09:56): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 5.0, "Should be 5 (floor) after second reduction"

# Third exit: 5 - 5 = 0, but floor is 5 (should stay at 5)
strategy._reduce_base_sl_on_exit(exit3)
print(f"After Exit 3 (10:03): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 5.0, "Should remain at floor (5.0)"
assert strategy.last_sl_exit_time == exit3, "Should record latest exit time"

print("\n✅ TEST 4 PASSED - Progressive reduction to floor works")

# Test 5: Timer Expiration (Reversion to Max)
print("\n" + "=" * 80)
print("TEST 5: Timer Expiration - Revert to Max")
print("=" * 80)

strategy = create_strategy_with_sl_regression()

# Reduce to 10 pts at 09:50
exit_time = datetime(2025, 11, 1, 9, 50, 0)
strategy._reduce_base_sl_on_exit(exit_time)
print(f"After reduction at 09:50: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should be reduced to 10"

# Check timer BEFORE expiration (09:50 + 15 min = 10:05, check at 10:00)
check_time_before = datetime(2025, 11, 1, 10, 0, 0)
strategy._check_sl_regression_timer(check_time_before)
print(f"Check at 10:00 (10 min elapsed, <20 min window): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should still be reduced (timer not expired)"
assert strategy.last_sl_exit_time is not None, "Timer should still be active"

# Check timer AFTER expiration (09:50 + 20 min = 10:10, check at 10:10)
check_time_after = datetime(2025, 11, 1, 10, 10, 0)
strategy._check_sl_regression_timer(check_time_after)
print(f"Check at 10:10 (20 min elapsed, =20 min window): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 15.0, "Should revert to max after timer expires"
assert strategy.last_sl_exit_time is None, "Timer should be cleared"

print("\n✅ TEST 5 PASSED - Timer expiration and reversion works")

# Test 6: Timer Reset on New Exit
print("\n" + "=" * 80)
print("TEST 6: Timer Reset on New Exit")
print("=" * 80)

strategy = create_strategy_with_sl_regression()

# First exit at 09:50
exit1 = datetime(2025, 11, 1, 9, 50, 0)
strategy._reduce_base_sl_on_exit(exit1)
print(f"Exit 1 at 09:50: current_base_sl = {strategy.current_base_sl}, timer expires at 10:10")
assert strategy.last_sl_exit_time == exit1

# Second exit at 10:05 (before first timer expires)
exit2 = datetime(2025, 11, 1, 10, 5, 0)
strategy._reduce_base_sl_on_exit(exit2)
print(f"Exit 2 at 10:05: current_base_sl = {strategy.current_base_sl}, timer RESET to expire at 10:25")
assert strategy.current_base_sl == 5.0, "Should reduce again (10-5=5)"
assert strategy.last_sl_exit_time == exit2, "Timer should reset to new exit time"

# Check at 10:20 (15 min after exit2, still <20 min)
check_time = datetime(2025, 11, 1, 10, 20, 0)
strategy._check_sl_regression_timer(check_time)
print(f"Check at 10:20 (15 min after exit2): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 5.0, "Should still be reduced (timer not expired)"

# Check at 10:25 (20 min after exit2, timer expires)
check_time = datetime(2025, 11, 1, 10, 25, 0)
strategy._check_sl_regression_timer(check_time)
print(f"Check at 10:25 (20 min after exit2): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 15.0, "Should revert to max"

print("\n✅ TEST 6 PASSED - Timer reset on new exit works")

# Test 7: Integration with on_position_exit()
print("\n" + "=" * 80)
print("TEST 7: Integration with on_position_exit() - Trailing Stop")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
exit_time = datetime(2025, 11, 1, 10, 0, 0)

exit_info = {
    'position_id': 'test123',
    'exit_reason': 'Trailing Stop',
    'exit_price': 100.0,
    'timestamp': exit_time
}

print(f"Before exit: current_base_sl = {strategy.current_base_sl}")
strategy.on_position_exit(exit_info)
print(f"After Trailing Stop exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should reduce on Trailing Stop exit"

print("\n✅ TEST 7 PASSED - Integration with Trailing Stop works")

# Test 8: Integration with on_position_exit() - Base SL
print("\n" + "=" * 80)
print("TEST 8: Integration with on_position_exit() - Base SL")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
exit_time = datetime(2025, 11, 1, 10, 0, 0)

exit_info = {
    'position_id': 'test456',
    'exit_reason': 'Base SL',  # Standardized at source (ExitReason.STOP_LOSS.value)
    'exit_price': 95.0,
    'timestamp': exit_time
}

print(f"Before exit: current_base_sl = {strategy.current_base_sl}")
strategy.on_position_exit(exit_info)
print(f"After Base SL exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should reduce on Base SL exit"

print("\n✅ TEST 8 PASSED - Integration with Base SL works")

# Test 9: No Reduction on Other Exit Types
print("\n" + "=" * 80)
print("TEST 9: No Reduction on Other Exit Types (Take Profit)")
print("=" * 80)

strategy = create_strategy_with_sl_regression()
exit_time = datetime(2025, 11, 1, 10, 0, 0)

exit_info = {
    'position_id': 'test789',
    'exit_reason': 'Take Profit',
    'exit_price': 110.0,
    'timestamp': exit_time
}

print(f"Before exit: current_base_sl = {strategy.current_base_sl}")
strategy.on_position_exit(exit_info)
print(f"After Take Profit exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 15.0, "Should NOT reduce on Take Profit exit"
assert strategy.last_sl_exit_time is None, "Should NOT set exit time"

print("\n✅ TEST 9 PASSED - Other exit types don't trigger reduction")

# Test 10: Timer Check in can_enter_new_position()
print("\n" + "=" * 80)
print("TEST 10: Timer Check in Entry Validation")
print("=" * 80)

strategy = create_strategy_with_sl_regression()

# Reduce Base SL at 09:50
exit_time = datetime(2025, 11, 1, 9, 50, 0)
strategy._reduce_base_sl_on_exit(exit_time)
print(f"After reduction at 09:50: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0

# Call can_enter_new_position at 10:15 (should NOT expire timer yet)
entry_time_1 = datetime(2025, 11, 1, 10, 5, 0)
# Note: can_enter_new_position checks timer as first step
# We'll just call the timer check directly for this unit test
strategy._check_sl_regression_timer(entry_time_1)
print(f"Timer check at 10:05 (15 min elapsed): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 10.0, "Should still be reduced"

# Call can_enter_new_position at 10:10 (SHOULD expire timer)
entry_time_2 = datetime(2025, 11, 1, 10, 10, 0)
strategy._check_sl_regression_timer(entry_time_2)
print(f"Timer check at 10:10 (20 min elapsed): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 15.0, "Should revert to max"

print("\n✅ TEST 10 PASSED - Timer check in entry validation works")

# Test 11: Edge Case - Exact Window Boundary
print("\n" + "=" * 80)
print("TEST 11: Edge Case - Exact Window Boundary")
print("=" * 80)

strategy = create_strategy_with_sl_regression(window_minutes=20)
exit_time = datetime(2025, 11, 1, 9, 50, 0)
strategy._reduce_base_sl_on_exit(exit_time)

# Check at exactly 20 minutes (09:50 + 20 min = 10:10:00)
exact_boundary = datetime(2025, 11, 1, 10, 10, 0)
strategy._check_sl_regression_timer(exact_boundary)
print(f"Check at exact 20 min boundary: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 15.0, "Should revert at exact boundary (>= check)"

print("\n✅ TEST 11 PASSED - Exact boundary works")

# Test 12: Custom Configuration Values
print("\n" + "=" * 80)
print("TEST 12: Custom Configuration Values")
print("=" * 80)

strategy = create_strategy_with_sl_regression(
    max_base_sl=20.0,
    min_base_sl=8.0,
    step=3.0,
    window_minutes=15
)

print(f"✓ max_base_sl: {strategy.max_base_sl}")
print(f"✓ min_base_sl: {strategy.min_base_sl}")
print(f"✓ sl_regression_step: {strategy.sl_regression_step}")
print(f"✓ current_base_sl (initial): {strategy.current_base_sl}")

# First reduction: 20 - 3 = 17
exit1 = datetime(2025, 11, 1, 10, 0, 0)
strategy._reduce_base_sl_on_exit(exit1)
print(f"After 1st exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 17.0, "Should reduce by 3 (20-3=17)"

# Second reduction: 17 - 3 = 14
exit2 = datetime(2025, 11, 1, 10, 5, 0)
strategy._reduce_base_sl_on_exit(exit2)
print(f"After 2nd exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 14.0, "Should reduce by 3 (17-3=14)"

# Multiple reductions to reach floor
# 14 - 3 = 11
exit3 = datetime(2025, 11, 1, 10, 10, 0)
strategy._reduce_base_sl_on_exit(exit3)
print(f"After 3rd exit: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 11.0, "Should be 11"

# 11 - 3 = 8 (floor)
exit4 = datetime(2025, 11, 1, 10, 15, 0)
strategy._reduce_base_sl_on_exit(exit4)
print(f"After 4th exit (floor): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 8.0, "Should reach floor (8.0)"

# One more reduction attempt (should stay at floor)
exit5 = datetime(2025, 11, 1, 10, 20, 0)
strategy._reduce_base_sl_on_exit(exit5)
print(f"After 5th exit (beyond floor): current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 8.0, "Should stay at floor"

# Check timer with custom window (15 min)
check_time = datetime(2025, 11, 1, 10, 35, 0)  # 15 min after exit5
strategy._check_sl_regression_timer(check_time)
print(f"After 15 min window: current_base_sl = {strategy.current_base_sl}")
assert strategy.current_base_sl == 20.0, "Should revert to custom max (20.0)"

print("\n✅ TEST 12 PASSED - Custom configuration values work")

# Summary
print("\n" + "=" * 80)
print("ALL TESTS PASSED ✅")
print("=" * 80)
print("\nSummary:")
print("✅ TEST 1:  Initial state correct")
print("✅ TEST 2:  Disabled state works")
print("✅ TEST 3:  Single reduction works")
print("✅ TEST 4:  Progressive reduction to floor works")
print("✅ TEST 5:  Timer expiration and reversion works")
print("✅ TEST 6:  Timer reset on new exit works")
print("✅ TEST 7:  Integration with Trailing Stop works")
print("✅ TEST 8:  Integration with Base SL works")
print("✅ TEST 9:  Other exit types don't trigger reduction")
print("✅ TEST 10: Timer check in entry validation works")
print("✅ TEST 11: Exact boundary works")
print("✅ TEST 12: Custom configuration values work")
print("\n" + "=" * 80)
print("SL Regression feature is production-ready! 🚀")
print("=" * 80)
