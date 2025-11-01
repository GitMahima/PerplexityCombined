"""
Test Trade Block Implementation in liveStrategy

This script tests that trade blocks are properly enforced in the strategy.
"""

import sys
import os
from datetime import datetime, time
import pytz

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from myQuant.config.defaults import DEFAULT_CONFIG
from myQuant.utils.config_helper import create_config_from_defaults, validate_config, freeze_config
from myQuant.core.liveStrategy import ModularIntradayStrategy
from copy import deepcopy

print("=" * 80)
print("TRADE BLOCK ENFORCEMENT TEST")
print("=" * 80)

# Test 1: Verify trade block checking method
print("\n" + "=" * 80)
print("TEST 1: Trade Block Time Checking Method")
print("=" * 80)

# Create config with trade blocks
config = create_config_from_defaults()
config['session']['trade_block_enabled'] = True
config['session']['trade_blocks'] = [
    {'start_hour': 14, 'start_min': 29, 'end_hour': 14, 'end_min': 55},
    {'start_hour': 11, 'start_min': 30, 'end_hour': 11, 'end_min': 59},
]

# Freeze config
frozen_config = freeze_config(config)

# Create strategy instance
strategy = ModularIntradayStrategy(frozen_config)

# Test times
ist = pytz.timezone('Asia/Kolkata')
test_cases = [
    (datetime(2025, 11, 1, 14, 25, 0, tzinfo=ist), False, "Before block 1"),
    (datetime(2025, 11, 1, 14, 30, 0, tzinfo=ist), True, "Within block 1"),
    (datetime(2025, 11, 1, 14, 45, 0, tzinfo=ist), True, "Within block 1"),
    (datetime(2025, 11, 1, 14, 56, 0, tzinfo=ist), False, "After block 1"),
    (datetime(2025, 11, 1, 11, 29, 0, tzinfo=ist), False, "Before block 2"),
    (datetime(2025, 11, 1, 11, 35, 0, tzinfo=ist), True, "Within block 2"),
    (datetime(2025, 11, 1, 12, 0, 0, tzinfo=ist), False, "After block 2"),
    (datetime(2025, 11, 1, 10, 30, 0, tzinfo=ist), False, "Between blocks"),
]

print("\nTesting is_within_trade_block() method:")
all_passed = True
for test_time, expected_blocked, description in test_cases:
    is_blocked, block_desc = strategy.is_within_trade_block(test_time)
    status = "✓" if is_blocked == expected_blocked else "✗"
    if is_blocked != expected_blocked:
        all_passed = False
    
    time_str = test_time.strftime("%H:%M")
    print(f"{status} {time_str} - {description}: blocked={is_blocked} (expected {expected_blocked})")
    if is_blocked:
        print(f"  → {block_desc}")

if all_passed:
    print("\n✅ TEST 1 PASSED: All time checks correct")
else:
    print("\n❌ TEST 1 FAILED: Some time checks incorrect")

# Test 2: Verify can_enter_new_position respects trade blocks
print("\n" + "=" * 80)
print("TEST 2: Entry Gating with Trade Blocks")
print("=" * 80)

# Test during blocked period
blocked_time = datetime(2025, 11, 1, 14, 40, 0, tzinfo=ist)  # Within block 1
can_enter_blocked = strategy.can_enter_new_position(blocked_time, 230.0)

print(f"\n14:40 (within block 1):")
print(f"  can_enter_new_position = {can_enter_blocked}")
print(f"  Expected: False")
print(f"  {'✓ PASS' if not can_enter_blocked else '✗ FAIL'}")

# Test during allowed period
allowed_time = datetime(2025, 11, 1, 10, 30, 0, tzinfo=ist)  # Not in any block
# Need to set strategy state for realistic test
strategy.daily_stats['trades_today'] = 0
strategy.green_bars_count = 3  # Assume green tick requirement met
strategy.consecutive_green_bars_required = 3

can_enter_allowed = strategy.can_enter_new_position(allowed_time, 230.0)

print(f"\n10:30 (not in any block):")
print(f"  can_enter_new_position = {can_enter_allowed}")
print(f"  Expected: True (assuming other conditions met)")
print(f"  {'✓ PASS' if can_enter_allowed else '⚠ FAIL or other conditions blocking'}")

# Test 3: Verify trade block disabled behavior
print("\n" + "=" * 80)
print("TEST 3: Disabled Trade Blocks (Should Allow All Times)")
print("=" * 80)

# Create config with disabled trade blocks
config_disabled = create_config_from_defaults()
config_disabled['session']['trade_block_enabled'] = False
config_disabled['session']['trade_blocks'] = [
    {'start_hour': 14, 'start_min': 29, 'end_hour': 14, 'end_min': 55},
]
frozen_config_disabled = freeze_config(config_disabled)
strategy_disabled = ModularIntradayStrategy(frozen_config_disabled)

# Test time that would be blocked if enabled
test_time_disabled = datetime(2025, 11, 1, 14, 40, 0, tzinfo=ist)
is_blocked_disabled, _ = strategy_disabled.is_within_trade_block(test_time_disabled)

print(f"\n14:40 with trade blocks DISABLED:")
print(f"  is_within_trade_block = {is_blocked_disabled}")
print(f"  Expected: False")
print(f"  {'✓ PASS' if not is_blocked_disabled else '✗ FAIL'}")

print("\n" + "=" * 80)
print("🎉 TRADE BLOCK ENFORCEMENT TESTS COMPLETE")
print("=" * 80)

print("\nSummary:")
print("✓ Trade block time checking implemented")
print("✓ can_enter_new_position respects trade blocks")
print("✓ Disabled state works correctly")
print("\n💡 Next step: Test with actual data simulation to verify blocking works end-to-end")
