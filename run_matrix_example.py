"""
run_matrix_example.py

Run this from the project root directory.
Demonstrates matrix testing with EMA crossover parameters.
"""

import sys
import os
from pathlib import Path

# Change to myQuant directory for imports
os.chdir(Path(__file__).parent / 'myQuant')
sys.path.insert(0, str(Path(__file__).parent / 'myQuant'))

from live.matrix_forward_test import MatrixTestRunner

def main():
    """Run example matrix test for EMA crossover parameters."""
    
    # ========================================================================
    # CONFIGURATION
    # ========================================================================
    
    # CSV file path - USE BIG DATASET
    csv_path = r"C:\Users\user\projects\PerplexityCombinedTest\aTest.csv"
    
    # Output directory - Save to BotResults
    output_dir = r"C:\Users\user\Desktop\BotResults\resultsMatrix"
    
    # ========================================================================
    # INITIALIZE RUNNER
    # ========================================================================
    
    runner = MatrixTestRunner(csv_path, output_dir)
    
    # ========================================================================
    # PRICE ABOVE EXIT FILTER OPTIMIZATION (12 combinations)
    # ========================================================================
    
    print("\n" + "="*60)
    print("PRICE ABOVE EXIT FILTER OPTIMIZATION")
    print("BIG DATASET: 32,591 ticks from aTest.csv")
    print("="*60)
    
    # Test multiple price filter combinations
    runner.add_parameter_grid('price_buffer_points', [2.0, 3.0, 4.0])  # 3 buffer levels
    runner.add_parameter_grid('filter_duration_seconds', [120, 180, 240, 300])  # 4 durations
    # Total: 3 x 4 = 12 tests
    
    # Fixed EMA parameters (use best from previous test)
    runner.set_fixed_parameter('fast_ema', 9)   # Best performer from EMA optimization
    runner.set_fixed_parameter('slow_ema', 21)  # Best performer from EMA optimization
    
    # Fixed risk management
    runner.set_fixed_parameter('risk_per_trade_percent', 5.0)
    runner.set_fixed_parameter('base_sl_points', 15)
    runner.set_fixed_parameter('use_trail_stop', True)
    runner.set_fixed_parameter('trail_activation_points', 8)
    runner.set_fixed_parameter('trail_distance_points', 5)
    
    # Consecutive green - ENABLED
    runner.set_fixed_parameter('use_consecutive_green', True)
    runner.set_fixed_parameter('consecutive_green_bars', 3)
    
    # Control Base SL - ENABLED
    runner.set_fixed_parameter('Enable_control_base_sl_green_ticks', True)
    runner.set_fixed_parameter('control_base_sl_green_ticks', 5)
    
    # Other indicators - DISABLED
    runner.set_fixed_parameter('use_macd', False)
    runner.set_fixed_parameter('use_vwap', False)
    runner.set_fixed_parameter('use_rsi_filter', False)
    runner.set_fixed_parameter('use_htf_trend', False)
    
    # Price filter - ENABLED (parameters will be tested)
    runner.set_fixed_parameter('price_above_exit_filter_enabled', True)
    # price_buffer_points and filter_duration_seconds are in parameter grid
    
    # Calculate test count
    test_count = runner.calculate_test_count()
    print(f"\nTotal tests to run: {test_count}")
    print(f"\n📊 Test Configuration:")
    print(f"  EMA: 9-21 (fixed - best from previous test)")
    print(f"  Risk: 5% per trade")
    print(f"  Trailing Stop: 8pt activation, 5pt distance")
    print(f"  Consecutive Green: 3 bars ENABLED")
    print(f"  Control Base SL: 5 ticks ENABLED")
    print(f"\n🔍 Testing Price Above Exit Filter:")
    print(f"  Buffer Points: 2.0, 3.0, 4.0 pts")
    print(f"  Duration: 120s (2m), 180s (3m), 240s (4m), 300s (5m)")
    print(f"\n⏱️  Estimated time: ~{test_count * 1.7:.0f} minutes")
    
    # Skip input prompt for automated testing
    # input("\nPress Enter to start testing...")
    print("\nStarting testing...")
    
    # Run tests
    results_df = runner.run(
        phase_name='Price Above Exit Filter Optimization',
        description='Testing 12 combinations of buffer points (2-4) and duration (120-300s) with EMA 9-21',
        output_filename='matrix_price_filter_optimization.xlsx'
    )
    
    # Display summary
    print("\n" + "="*60)
    print("MATRIX TEST SUMMARY")
    print("="*60)
    print(f"Total tests completed: {len(results_df)}")
    
    if len(results_df) > 0:
        # Sort by total_pnl to show best performers
        best_results = results_df.nlargest(3, 'total_pnl')
        
        print(f"\n🏆 TOP 3 PERFORMERS (by Net P&L):")
        for idx, (_, row) in enumerate(best_results.iterrows(), 1):
            avg_per_trade = row['total_pnl'] / row['total_trades'] if row['total_trades'] > 0 else 0
            print(f"\n#{idx} - {row['test_tag']}")
            print(f"  Net P&L: ₹{row['total_pnl']:.2f}")
            print(f"  Total Trades: {row['total_trades']}")
            print(f"  Win Rate: {row['win_rate']*100:.1f}%")
            print(f"  Max Drawdown: ₹{row['max_drawdown']:.2f}")
            print(f"  Avg Per Trade: ₹{avg_per_trade:.2f}")
        
        print(f"\n📊 Full Results Overview:")
        print(f"  Best P&L: ₹{results_df['total_pnl'].max():.2f}")
        print(f"  Worst P&L: ₹{results_df['total_pnl'].min():.2f}")
        print(f"  Avg Trades: {results_df['total_trades'].mean():.0f}")
        print(f"  Avg Win Rate: {results_df['win_rate'].mean()*100:.1f}%")
    
    print(f"\n📁 Results exported to: {output_dir}\\matrix_price_filter_optimization.xlsx")
    print("\nCheck the Excel file for comprehensive analysis:")
    print("  • Summary - Overall comparison of 12 filter combinations")
    print("  • Top 10 - Best performers")
    print("  • Configuration - Parameter details")
    print("  • Detailed Metrics - Full breakdown")
    print("  • Sensitivity - Parameter impact analysis")
    print("  • Validation - Quality checks")
    print("  • Metadata - Test information")
    print("\n💡 This test will show optimal re-entry timing after exits!")


if __name__ == '__main__':
    # Configure logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    main()
