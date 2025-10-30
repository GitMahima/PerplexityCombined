"""
Quick diagnostic to check if indicators are converging during simulation.
"""

import sys
import os
from pathlib import Path
import pandas as pd

# Change to myQuant directory
os.chdir(Path(__file__).parent / 'myQuant')
sys.path.insert(0, str(Path(__file__).parent / 'myQuant'))

from live.matrix_config_builder import build_config_from_parameters
from utils.config_helper import validate_config, freeze_config
from live.trader import LiveTrader

# Simple test with EMA 12-26
param_values = {'fast_ema': 12, 'slow_ema': 26}
fixed_params = {
    'risk_per_trade_percent': 2.0,
    'base_sl_points': 15,
    'no_trade_start_minutes': 0,
    'no_trade_end_minutes': 0,
    'use_consecutive_green': False
}

# Build and freeze config
config = build_config_from_parameters(param_values, fixed_params)
config['data_simulation'] = {
    'enabled': True,
    'file_path': r'C:\Users\user\projects\PerplexityCombinedTest\aTest.csv'
}
frozen_config = freeze_config(config)

# Run simulation with instrumentation
print("\n" + "="*80)
print("INDICATOR CONVERGENCE DIAGNOSTIC")
print("="*80)
print(f"Testing EMA {param_values['fast_ema']}-{param_values['slow_ema']}")
print(f"Data file: aTest.csv")
print("="*80 + "\n")

# Initialize trader
trader = LiveTrader(frozen_config=frozen_config)

# Access strategy and check initial state
strategy = trader.strategy
print(f"Strategy initialized")
print(f"Use EMA Crossover: {strategy.use_ema_crossover}")
print(f"Fast EMA period: {strategy.fast_ema_period}")
print(f"Slow EMA period: {strategy.slow_ema_period}")
print(f"Use Consecutive Green: {strategy.use_consecutive_green}")
print(f"\nStarting simulation...\n")

# Run simulation
trader.start(run_once=True)

# Check results
trades = trader.position_manager.completed_trades
print("\n" + "="*80)
print("RESULTS")
print("="*80)
print(f"Total trades taken: {len(trades)}")
if len(trades) > 0:
    total_pnl = sum(t.net_pnl for t in trades)
    print(f"Total P&L: ₹{total_pnl:.2f}")
    print("\nFirst 3 trades:")
    for i, trade in enumerate(trades[:3], 1):
        print(f"  Trade {i}: Entry={trade.entry_price:.2f}, Exit={trade.exit_price:.2f}, PnL={trade.net_pnl:.2f}")
else:
    print("No trades taken - checking why...")
    
    # Try to access indicator state
    if hasattr(strategy, 'indicators'):
        ind = strategy.indicators
        print(f"\nIndicator module: {type(ind)}")
        if hasattr(ind, 'ema_fast'):
            fast_val = ind.ema_fast.value if hasattr(ind.ema_fast, 'value') else 'N/A'
            slow_val = ind.ema_slow.value if hasattr(ind.ema_slow, 'value') else 'N/A'
            print(f"Fast EMA final value: {fast_val}")
            print(f"Slow EMA final value: {slow_val}")
    
    print("\n💡 Possible reasons for 0 trades:")
    print("   1. EMAs didn't converge (stayed None)")
    print("   2. Fast EMA never crossed above Slow EMA")
    print("   3. Price filter blocked all entries")
    print("   4. Start buffer blocked early entries")
    print("   5. Data outside session times")
    
print("="*80)
