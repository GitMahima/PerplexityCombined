"""
scripts/run_matrix_test_example.py

Example script demonstrating matrix testing usage.

Run this script from the myQuant directory:
    cd myQuant
    python ..\scripts\run_matrix_test_example.py

Or use the module execution:
    cd myQuant
    python -c "import sys; sys.path.insert(0, '..\\scripts'); exec(open('..\\scripts\\run_matrix_test_example.py').read())"
"""

import sys
from pathlib import Path

# Ensure we're running from myQuant directory context
from live.matrix_forward_test import MatrixTestRunner

def main():
    """Run example matrix test for EMA crossover parameters."""
    
    # ========================================================================
    # CONFIGURATION
    # ========================================================================
    
    # CSV file path (CHANGE THIS to your actual data file)
    csv_path = r"C:\Users\user\projects\PerplexityCombinedTest\aTest.csv"
    
    # Output directory
    output_dir = r"C:\Users\user\projects\PerplexityCombinedTest\results"
    
    # ========================================================================
    # INITIALIZE RUNNER
    # ========================================================================
    
    runner = MatrixTestRunner(csv_path, output_dir)
    
    # ========================================================================
    # PHASE 1: EMA CROSSOVER TESTING (~2-3 minutes for 6 tests)
    # ========================================================================
    
    print("\n" + "="*60)
    print("EXAMPLE: Phase 1 - EMA Crossover Testing")
    print("="*60)
    
    # Define parameter grids to test
    runner.add_parameter_grid('fast_ema', [9, 12, 18])      # 3 values
    runner.add_parameter_grid('slow_ema', [21, 26])         # 2 values
    # Total: 3 × 2 = 6 tests
    
    # Set fixed parameters (held constant)
    runner.set_fixed_parameter('risk_per_trade_percent', 2.0)
    runner.set_fixed_parameter('base_sl_points', 15)
    runner.set_fixed_parameter('use_trail_stop', True)
    runner.set_fixed_parameter('trail_activation_points', 9)
    runner.set_fixed_parameter('trail_distance_points', 5)
    
    # Calculate test count
    test_count = runner.calculate_test_count()
    print(f"\nTotal tests to run: {test_count}")
    print(f"Parameters tested:")
    print(f"  - fast_ema: [9, 12, 18]")
    print(f"  - slow_ema: [21, 26]")
    print(f"\nFixed parameters:")
    print(f"  - risk_per_trade_percent: 2.0")
    print(f"  - base_sl_points: 15")
    print(f"  - use_trail_stop: True")
    print(f"  - trail_activation_points: 9")
    print(f"  - trail_distance_points: 5")
    
    input("\nPress Enter to start testing...")
    
    # Run tests
    results_df = runner.run(
        phase_name='Phase 1: EMA Crossover',
        description='Testing fast and slow EMA parameter combinations',
        output_filename='phase1_ema_crossover.xlsx'
    )
    
    # Display summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Total tests completed: {len(results_df)}")
    print(f"\nTop 3 Performers by PnL:")
    top3 = results_df.nlargest(3, 'total_pnl')[['test_tag', 'total_pnl', 'total_trades', 'win_rate']]
    for idx, row in top3.iterrows():
        print(f"  {row['test_tag']}: PnL={row['total_pnl']:.2f}, Trades={row['total_trades']}, WinRate={row['win_rate']*100:.1f}%")
    
    print(f"\nResults exported to: {output_dir}\\phase1_ema_crossover.xlsx")
    print("Check the Excel file for comprehensive analysis with 7 sheets!")


if __name__ == '__main__':
    # Configure logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    main()
