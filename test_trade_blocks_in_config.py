"""
Test: Verify trade blocks flow through config building and freezing
"""
import sys
sys.path.insert(0, 'myQuant')

from config.defaults import DEFAULT_CONFIG
from utils.config_helper import freeze_config, validate_config
from copy import deepcopy

print("=" * 80)
print("TRADE BLOCKS CONFIG FLOW TEST")
print("=" * 80)

# Step 1: Check defaults
print("\n1. DEFAULT_CONFIG (from defaults.py):")
print(f"   trade_block_enabled: {DEFAULT_CONFIG['session']['trade_block_enabled']}")
print(f"   trade_blocks: {DEFAULT_CONFIG['session']['trade_blocks']}")

# Step 2: Simulate GUI building config with trade blocks
print("\n2. SIMULATE GUI ADDING TRADE BLOCKS:")
config_dict = deepcopy(DEFAULT_CONFIG)

# Simulate user adding 2 trade blocks through GUI
config_dict['session']['trade_block_enabled'] = True
config_dict['session']['trade_blocks'] = [
    {'start_hour': 14, 'start_min': 30, 'end_hour': 14, 'end_min': 59},
    {'start_hour': 12, 'start_min': 30, 'end_hour': 12, 'end_min': 59}
]

print(f"   trade_block_enabled: {config_dict['session']['trade_block_enabled']}")
print(f"   trade_blocks: {config_dict['session']['trade_blocks']}")

# Step 3: Validate
print("\n3. VALIDATE CONFIG:")
validation = validate_config(config_dict)
print(f"   Valid: {validation['valid']}")
if not validation['valid']:
    print(f"   Errors: {validation['errors']}")

# Step 4: Freeze
print("\n4. FREEZE CONFIG:")
frozen_config = freeze_config(config_dict)
print(f"   Type: {type(frozen_config)}")
print(f"   Can access session? {('session' in frozen_config)}")

# Step 5: Access trade blocks from frozen config
print("\n5. ACCESS TRADE BLOCKS FROM FROZEN CONFIG:")
try:
    session = frozen_config['session']
    print(f"   session type: {type(session)}")
    
    trade_block_enabled = session.get('trade_block_enabled', False)
    trade_blocks = session.get('trade_blocks', [])
    
    print(f"   trade_block_enabled: {trade_block_enabled}")
    print(f"   trade_blocks count: {len(trade_blocks)}")
    print(f"   trade_blocks: {trade_blocks}")
    
    if trade_blocks:
        for idx, block in enumerate(trade_blocks, 1):
            print(f"   Block #{idx}: {block['start_hour']:02d}:{block['start_min']:02d}-{block['end_hour']:02d}:{block['end_min']:02d}")
    
    print("\n✅ SUCCESS: Trade blocks accessible from frozen config!")
    
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

# Step 6: Test dialog display logic
print("\n6. TEST DIALOG DISPLAY LOGIC:")
config = frozen_config  # Simulate what dialog receives

try:
    trade_block_enabled = config['session'].get('trade_block_enabled', False)
    trade_blocks = config['session'].get('trade_blocks', [])
    
    lines = []
    if trade_block_enabled and trade_blocks:
        lines.append(f"Trade Blocks:        ENABLED ({len(trade_blocks)} blocks)")
        for idx, block in enumerate(trade_blocks, 1):
            block_str = f"{block['start_hour']:02d}:{block['start_min']:02d}-{block['end_hour']:02d}:{block['end_min']:02d}"
            lines.append(f"  Block #{idx}:        {block_str}")
    elif trade_block_enabled and not trade_blocks:
        lines.append(f"Trade Blocks:        ENABLED (0 blocks configured)")
    else:
        lines.append(f"Trade Blocks:        DISABLED")
    
    print("   Dialog would show:")
    for line in lines:
        print(f"   {line}")
    
    print("\n✅ SUCCESS: Dialog display logic works!")
    
except Exception as e:
    print(f"\n❌ ERROR in dialog logic: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)
