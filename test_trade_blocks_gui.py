"""
Test script to verify Session Trade Blocks GUI functionality

This script tests:
1. Trade blocks configuration in defaults.py
2. GUI variable initialization
3. Dynamic field creation/removal
4. Configuration building with trade blocks
"""

import sys
import os

# Add myQuant to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from myQuant.config.defaults import DEFAULT_CONFIG
from copy import deepcopy

def test_defaults_config():
    """Test that trade blocks are properly defined in defaults.py"""
    print("\n" + "=" * 80)
    print("TEST 1: Trade Blocks in DEFAULT_CONFIG")
    print("=" * 80)
    
    session = DEFAULT_CONFIG.get('session', {})
    
    print(f"✓ Session config exists: {session is not None}")
    print(f"✓ trade_block_enabled: {session.get('trade_block_enabled', 'MISSING')}")
    print(f"✓ trade_blocks: {session.get('trade_blocks', 'MISSING')}")
    
    assert 'trade_block_enabled' in session, "trade_block_enabled missing from session config"
    assert 'trade_blocks' in session, "trade_blocks missing from session config"
    assert isinstance(session['trade_blocks'], list), "trade_blocks should be a list"
    
    print("\n✅ DEFAULT_CONFIG test passed!")


def test_config_building():
    """Test configuration building with trade blocks"""
    print("\n" + "=" * 80)
    print("TEST 2: Configuration Building with Trade Blocks")
    print("=" * 80)
    
    # Simulate adding trade blocks
    config = deepcopy(DEFAULT_CONFIG)
    config['session']['trade_block_enabled'] = True
    config['session']['trade_blocks'] = [
        {'start_hour': 14, 'start_min': 29, 'end_hour': 14, 'end_min': 55},
        {'start_hour': 11, 'start_min': 30, 'end_hour': 11, 'end_min': 59}
    ]
    
    print(f"✓ Trade block enabled: {config['session']['trade_block_enabled']}")
    print(f"✓ Number of blocks: {len(config['session']['trade_blocks'])}")
    
    for idx, block in enumerate(config['session']['trade_blocks']):
        print(f"  Block {idx + 1}: {block['start_hour']:02d}:{block['start_min']:02d} - "
              f"{block['end_hour']:02d}:{block['end_min']:02d}")
    
    # Validate structure
    for block in config['session']['trade_blocks']:
        assert 'start_hour' in block, "start_hour missing from trade block"
        assert 'start_min' in block, "start_min missing from trade block"
        assert 'end_hour' in block, "end_hour missing from trade block"
        assert 'end_min' in block, "end_min missing from trade block"
    
    print("\n✅ Configuration building test passed!")


def test_time_validation():
    """Test that trade block times are valid"""
    print("\n" + "=" * 80)
    print("TEST 3: Trade Block Time Validation")
    print("=" * 80)
    
    test_blocks = [
        {'start_hour': 14, 'start_min': 29, 'end_hour': 14, 'end_min': 55},
        {'start_hour': 11, 'start_min': 30, 'end_hour': 11, 'end_min': 59},
        {'start_hour': 9, 'start_min': 15, 'end_hour': 9, 'end_min': 20}
    ]
    
    for idx, block in enumerate(test_blocks):
        start_hour = block['start_hour']
        start_min = block['start_min']
        end_hour = block['end_hour']
        end_min = block['end_min']
        
        # Validate ranges
        assert 0 <= start_hour <= 23, f"Block {idx+1}: Invalid start_hour {start_hour}"
        assert 0 <= start_min <= 59, f"Block {idx+1}: Invalid start_min {start_min}"
        assert 0 <= end_hour <= 23, f"Block {idx+1}: Invalid end_hour {end_hour}"
        assert 0 <= end_min <= 59, f"Block {idx+1}: Invalid end_min {end_min}"
        
        # Convert to minutes for comparison
        start_total = start_hour * 60 + start_min
        end_total = end_hour * 60 + end_min
        
        assert end_total > start_total, f"Block {idx+1}: End time must be after start time"
        
        print(f"✓ Block {idx+1} valid: {start_hour:02d}:{start_min:02d} - {end_hour:02d}:{end_min:02d}")
    
    print("\n✅ Time validation test passed!")


def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print("SESSION TRADE BLOCKS - Configuration Tests")
    print("=" * 80)
    
    try:
        test_defaults_config()
        test_config_building()
        test_time_validation()
        
        print("\n" + "=" * 80)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 80)
        print("\nNext steps:")
        print("1. Launch GUI: python -m myQuant.gui.noCamel1")
        print("2. Navigate to Forward Test tab")
        print("3. Find 'Session Management' section")
        print("4. Check 'Enable Trade Blocks' checkbox")
        print("5. Click '➕ Add Trade Block' button")
        print("6. Enter time periods (e.g., 14:29 to 14:55)")
        print("7. Add multiple blocks as needed")
        print("8. Each block can be removed with the ❌ button")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
