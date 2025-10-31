"""
live/matrix_forward_test.py

Main orchestration module for matrix parameter testing.

CRITICAL PRINCIPLES:
- Zero modifications to existing code
- Uses data_simulator for tick-by-tick processing
- Validates all parameters before testing
- Progress tracking with real-time ETA
- Fail-first on errors
- CLI and programmatic interfaces

USAGE:
    # Programmatic
    from .matrix_forward_test import MatrixTestRunner
    
    runner = MatrixTestRunner('C:\\Data\\nifty.csv')
    runner.add_parameter_grid('fast_ema', [9, 12, 18])
    runner.add_parameter_grid('slow_ema', [21, 26, 42])
    results_df = runner.run(phase_name='Phase 1: EMA Crossover')
    
    # CLI
    python -m live.matrix_forward_test --csv nifty.csv --phase "Phase 1" --fast-ema 9,12,18 --slow-ema 21,26,42
"""

import logging
logger = logging.getLogger(__name__)
import sys
import time
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from itertools import product
from datetime import datetime

import pandas as pd
from .matrix_config_builder import build_config_from_parameters, generate_test_tag, validate_parameter_combination
from .matrix_results_exporter import export_matrix_results
from ..utils.config_helper import freeze_config, validate_config
from .data_simulator import DataSimulator
from .broker_adapter import BrokerAdapter
from .trader import LiveTrader


# ============================================================================
# MATRIX TEST RUNNER
# ============================================================================

class MatrixTestRunner:
    """
    Main orchestration class for matrix parameter testing.
    
    Workflow:
    1. Define parameter grids and fixed parameters
    2. Generate all combinations
    3. Validate each combination
    4. Run forward tests sequentially
    5. Collect results
    6. Export to Excel
    
    Example:
        >>> runner = MatrixTestRunner('C:\\Data\\nifty.csv')
        >>> runner.add_parameter_grid('fast_ema', [9, 12, 18])
        >>> runner.add_parameter_grid('slow_ema', [21, 26, 42])
        >>> runner.set_fixed_parameter('risk_per_trade_percent', 2.0)
        >>> results_df = runner.run(phase_name='Phase 1: EMA')
        >>> # Creates Excel file in results/ directory
    """
    
    def __init__(self, csv_path: str, output_dir: str = None):
        """
        Initialize matrix test runner.
        
        Args:
            csv_path: Path to CSV file with historical tick data
            output_dir: Directory for results (default: results/)
            
        Raises:
            FileNotFoundError: If CSV file doesn't exist
        """
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        
        self.output_dir = Path(output_dir) if output_dir else Path('results')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Parameter grids (parameters to test)
        self.parameter_grids: Dict[str, List[Any]] = {}
        
        # Fixed parameters (held constant)
        self.fixed_parameters: Dict[str, Any] = {}
        
        # Results storage
        self.results: List[Dict[str, Any]] = []
        
        logger.info(f"Matrix Test Runner initialized with CSV: {self.csv_path}")
    
    # ========================================================================
    # CONFIGURATION
    # ========================================================================
    
    def add_parameter_grid(self, param_name: str, values: List[Any]):
        """
        Add parameter to test with list of values.
        
        Args:
            param_name: Parameter name (must exist in defaults.py)
            values: List of values to test
            
        Example:
            >>> runner.add_parameter_grid('fast_ema', [9, 12, 18, 21])
            >>> runner.add_parameter_grid('base_sl_points', [10, 15, 20])
        """
        if not isinstance(values, list):
            values = [values]
        
        self.parameter_grids[param_name] = values
        logger.debug(f"Added parameter grid: {param_name} = {values}")
    
    def set_fixed_parameter(self, param_name: str, value: Any):
        """
        Set parameter to fixed value (not tested, held constant).
        
        Args:
            param_name: Parameter name (must exist in defaults.py)
            value: Fixed value
            
        Example:
            >>> runner.set_fixed_parameter('risk_per_trade_percent', 2.0)
            >>> runner.set_fixed_parameter('use_trail_stop', True)
        """
        self.fixed_parameters[param_name] = value
        logger.debug(f"Set fixed parameter: {param_name} = {value}")
    
    def calculate_test_count(self) -> int:
        """
        Calculate total number of tests that will be run.
        
        Returns:
            Number of test combinations (Cartesian product of all grids)
            
        Example:
            >>> runner.add_parameter_grid('fast_ema', [9, 12, 18])
            >>> runner.add_parameter_grid('slow_ema', [21, 26])
            >>> runner.calculate_test_count()
            6
        """
        if not self.parameter_grids:
            return 0
        
        count = 1
        for values in self.parameter_grids.values():
            count *= len(values)
        
        return count
    
    # ========================================================================
    # EXECUTION
    # ========================================================================
    
    def run(
        self,
        phase_name: str = "Matrix Test",
        description: str = "",
        skip_validation: bool = False,
        output_filename: str = None
    ) -> pd.DataFrame:
        """
        Run all test combinations and export results.
        
        Args:
            phase_name: Name of testing phase (e.g., "Phase 1: EMA Crossover")
            description: Optional description of test purpose
            skip_validation: If True, skip parameter validation (NOT RECOMMENDED)
            output_filename: Custom filename for Excel export (default: auto-generated)
            
        Returns:
            DataFrame with all test results
            
        Raises:
            ValueError: If no parameter grids defined
            RuntimeError: If test execution fails
        """
        if not self.parameter_grids:
            raise ValueError("No parameter grids defined. Use add_parameter_grid() first.")
        
        logger.info(f"========================================")
        logger.info(f"Starting Matrix Test: {phase_name}")
        logger.info(f"========================================")
        
        # Generate all parameter combinations
        combinations = self._generate_combinations()
        total_tests = len(combinations)
        
        logger.info(f"Generated {total_tests} test combinations")
        logger.info(f"Fixed parameters: {self.fixed_parameters}")
        logger.info(f"CSV data file: {self.csv_path}")
        
        # Run tests
        self.results = []
        start_time = time.time()
        
        for i, param_values in enumerate(combinations, 1):
            test_start = time.time()
            
            # Generate test tag
            test_tag = generate_test_tag(param_values)
            
            logger.info(f"\n[Test {i}/{total_tests}] {test_tag}")
            logger.info(f"Parameters: {param_values}")
            
            # Validate parameter combination
            if not skip_validation:
                is_valid, error_msg = validate_parameter_combination(param_values)
                if not is_valid:
                    logger.warning(f"❌ Validation failed: {error_msg}")
                    self._record_failed_test(i, test_tag, param_values, error_msg)
                    continue
            
            # Run single test
            try:
                result = self._run_single_test(i, test_tag, param_values)
                self.results.append(result)
                
                test_elapsed = time.time() - test_start
                total_elapsed = time.time() - start_time
                avg_time_per_test = total_elapsed / i
                remaining_tests = total_tests - i
                eta_seconds = avg_time_per_test * remaining_tests
                
                logger.info(
                    f"✅ Test complete: PnL={result.get('total_pnl', 0):.2f}, "
                    f"Trades={result.get('total_trades', 0)}, "
                    f"Time={test_elapsed:.1f}s, ETA={eta_seconds:.0f}s"
                )
                
            except Exception as e:
                logger.error(f"❌ Test failed with exception: {e}", exc_info=True)
                self._record_failed_test(i, test_tag, param_values, str(e))
        
        # Convert results to DataFrame
        results_df = pd.DataFrame(self.results)
        
        # Export to Excel
        if output_filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Extract CSV filename (without extension) to include in output name
            csv_basename = self.csv_path.stem  # e.g., "aTest" from "aTest.csv"
            output_filename = f"matrix_{csv_basename}_{timestamp}.xlsx"
        
        output_path = self.output_dir / output_filename
        export_matrix_results(
            results_df,
            str(output_path),
            phase_name,
            description,
            self.fixed_parameters
        )
        
        # Summary
        total_elapsed = time.time() - start_time
        logger.info(f"\n========================================")
        logger.info(f"Matrix Test Complete: {phase_name}")
        logger.info(f"========================================")
        logger.info(f"Total tests: {total_tests}")
        logger.info(f"Successful: {len(results_df)}")
        logger.info(f"Failed: {total_tests - len(results_df)}")
        logger.info(f"Total runtime: {total_elapsed:.1f}s ({total_elapsed / 60:.1f}m)")
        logger.info(f"Results exported to: {output_path}")
        
        return results_df
    
    def _generate_combinations(self) -> List[Dict[str, Any]]:
        """
        Generate all parameter combinations (Cartesian product).
        
        Returns:
            List of parameter dictionaries
        """
        param_names = list(self.parameter_grids.keys())
        param_value_lists = [self.parameter_grids[name] for name in param_names]
        
        combinations = []
        for values in product(*param_value_lists):
            combination = dict(zip(param_names, values))
            combinations.append(combination)
        
        return combinations
    
    def _run_single_test(
        self,
        test_number: int,
        test_tag: str,
        param_values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run single forward test with given parameters.
        
        Args:
            test_number: Test sequence number
            test_tag: Unique test identifier
            param_values: Parameters for this test
            
        Returns:
            Dictionary with test results and metrics
        """
        # Build configuration
        config = build_config_from_parameters(param_values, self.fixed_parameters)
        
        # Enable file simulation with our CSV path
        if 'data_simulation' not in config:
            config['data_simulation'] = {}
        config['data_simulation']['enabled'] = True
        config['data_simulation']['file_path'] = str(self.csv_path)
        
        # Validate configuration
        validation = validate_config(config)
        if not validation.get('valid', False):
            raise ValueError(f"Config validation failed: {validation.get('errors')}")
        
        # Freeze configuration
        frozen_config = freeze_config(config)
        
        # Initialize LiveTrader (it will automatically set up file simulation)
        trader = LiveTrader(frozen_config=frozen_config)
        
        # Run simulation using LiveTrader's start method
        logger.debug(f"Starting simulation...")
        trader.start(run_once=False)  # run_once=False to process entire file
        
        # Collect results from trader's position manager
        pm = trader.position_manager
        
        # Calculate metrics from completed trades
        trades = pm.completed_trades
        total_trades = len(trades)
        
        if total_trades > 0:
            winning_trades = [t for t in trades if t.net_pnl > 0]
            losing_trades = [t for t in trades if t.net_pnl < 0]
            
            total_pnl = sum(t.net_pnl for t in trades)
            win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
            avg_win = sum(t.net_pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = sum(t.net_pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
            
            # Calculate max drawdown
            cumulative_pnl = 0
            peak = 0
            max_dd = 0
            for t in trades:
                cumulative_pnl += t.net_pnl
                if cumulative_pnl > peak:
                    peak = cumulative_pnl
                drawdown = peak - cumulative_pnl
                if drawdown > max_dd:
                    max_dd = drawdown
        else:
            total_pnl = 0
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            max_dd = 0
        
        # Calculate metrics
        result = {
            'test_number': test_number,
            'test_tag': test_tag,
            'validation_passed': True,
            'validation_error': '',
        }
        
        # Add parameter values
        result.update(param_values)
        
        # Add performance metrics
        result['total_trades'] = total_trades
        result['total_pnl'] = total_pnl
        result['win_rate'] = win_rate
        result['avg_win'] = avg_win
        result['avg_loss'] = avg_loss
        result['max_drawdown'] = max_dd
        
        # Optional metrics (if available)
        if hasattr(pm, 'longest_win_streak'):
            result['longest_win_streak'] = pm.longest_win_streak
        if hasattr(pm, 'longest_loss_streak'):
            result['longest_loss_streak'] = pm.longest_loss_streak
        if hasattr(pm, 'profit_factor'):
            result['profit_factor'] = pm.profit_factor
        
        return result
    
    def _record_failed_test(
        self,
        test_number: int,
        test_tag: str,
        param_values: Dict[str, Any],
        error_msg: str
    ):
        """Record test that failed validation or execution."""
        result = {
            'test_number': test_number,
            'test_tag': test_tag,
            'validation_passed': False,
            'validation_error': error_msg,
            'total_trades': 0,
            'total_pnl': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'max_drawdown': 0,
        }
        result.update(param_values)
        self.results.append(result)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def run_matrix_test(
    csv_path: str,
    parameter_grids: Dict[str, List[Any]],
    fixed_parameters: Dict[str, Any] = None,
    phase_name: str = "Matrix Test",
    description: str = "",
    output_dir: str = None
) -> pd.DataFrame:
    """
    Convenience function for simple matrix testing.
    
    Args:
        csv_path: Path to CSV file
        parameter_grids: Dictionary of parameter names to value lists
        fixed_parameters: Dictionary of fixed parameter values
        phase_name: Name of testing phase
        description: Optional description
        output_dir: Output directory for results
        
    Returns:
        DataFrame with test results
        
    Example:
        >>> results = run_matrix_test(
        ...     'C:\\Data\\nifty.csv',
        ...     parameter_grids={
        ...         'fast_ema': [9, 12, 18],
        ...         'slow_ema': [21, 26, 42]
        ...     },
        ...     fixed_parameters={'risk_per_trade_percent': 2.0},
        ...     phase_name='Phase 1: EMA Crossover'
        ... )
    """
    runner = MatrixTestRunner(csv_path, output_dir)
    
    # Add parameter grids
    for param_name, values in parameter_grids.items():
        runner.add_parameter_grid(param_name, values)
    
    # Add fixed parameters
    if fixed_parameters:
        for param_name, value in fixed_parameters.items():
            runner.set_fixed_parameter(param_name, value)
    
    # Run tests
    return runner.run(phase_name, description)


# ============================================================================
# CLI INTERFACE
# ============================================================================

def parse_cli_arguments():
    """Parse command-line arguments for matrix testing."""
    parser = argparse.ArgumentParser(
        description='Matrix Parameter Testing for myQuant Trading System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test EMA parameters
  python -m live.matrix_forward_test --csv data.csv --phase "Phase 1" \\
      --fast-ema 9,12,18 --slow-ema 21,26,42
  
  # Test risk parameters with fixed EMA
  python -m live.matrix_forward_test --csv data.csv --phase "Phase 2" \\
      --base-sl 10,15,20,25 --trail-activation 7,9,12 \\
      --fixed fast_ema=12 --fixed slow_ema=26
  
  # Test price filter
  python -m live.matrix_forward_test --csv data.csv --phase "Phase 3" \\
      --price-buffer 1.0,2.0,3.0,5.0 --filter-duration 120,180,300,600
        """
    )
    
    # Required arguments
    parser.add_argument('--csv', required=True, help='Path to CSV file with tick data')
    parser.add_argument('--phase', default='Matrix Test', help='Phase name')
    
    # Optional arguments
    parser.add_argument('--description', default='', help='Test description')
    parser.add_argument('--output-dir', default='results', help='Output directory')
    parser.add_argument('--skip-validation', action='store_true', help='Skip validation (NOT RECOMMENDED)')
    
    # Parameter grids (most common parameters)
    parser.add_argument('--fast-ema', help='Fast EMA values (comma-separated)')
    parser.add_argument('--slow-ema', help='Slow EMA values (comma-separated)')
    parser.add_argument('--macd-fast', help='MACD fast values (comma-separated)')
    parser.add_argument('--macd-slow', help='MACD slow values (comma-separated)')
    parser.add_argument('--macd-signal', help='MACD signal values (comma-separated)')
    parser.add_argument('--base-sl', help='Base SL points (comma-separated)')
    parser.add_argument('--trail-activation', help='Trail activation points (comma-separated)')
    parser.add_argument('--trail-distance', help='Trail distance points (comma-separated)')
    parser.add_argument('--green-bars', help='Consecutive green bars (comma-separated)')
    parser.add_argument('--control-base-sl-ticks', help='Control base SL green ticks (comma-separated)')
    parser.add_argument('--price-buffer', help='Price buffer points (comma-separated)')
    parser.add_argument('--filter-duration', help='Filter duration seconds (comma-separated)')
    parser.add_argument('--session-start-buffer-minutes', help='Session start buffer minutes (comma-separated)')
    
    # Fixed parameters
    parser.add_argument('--fixed', action='append', help='Fixed parameter (format: name=value)')
    
    return parser.parse_args()


def main():
    """CLI entry point for matrix testing."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    args = parse_cli_arguments()
    
    # Initialize runner
    runner = MatrixTestRunner(args.csv, args.output_dir)
    
    # Add parameter grids
    param_mapping = {
        'fast_ema': args.fast_ema,
        'slow_ema': args.slow_ema,
        'macd_fast': args.macd_fast,
        'macd_slow': args.macd_slow,
        'macd_signal': args.macd_signal,
        'base_sl_points': args.base_sl,
        'trail_activation_points': args.trail_activation,
        'trail_distance_points': args.trail_distance,
        'consecutive_green_bars': args.green_bars,
        'control_base_sl_green_ticks': args.control_base_sl_ticks,
        'price_buffer_points': args.price_buffer,
        'filter_duration_seconds': args.filter_duration,
        'start_buffer_minutes': args.session_start_buffer_minutes,
    }
    
    for param_name, cli_value in param_mapping.items():
        if cli_value:
            # Parse comma-separated values
            values = [_parse_value(v.strip()) for v in cli_value.split(',')]
            runner.add_parameter_grid(param_name, values)
    
    # Add fixed parameters
    if args.fixed:
        for fixed_param in args.fixed:
            if '=' not in fixed_param:
                print(f"Error: Fixed parameter must be in format 'name=value': {fixed_param}")
                sys.exit(1)
            name, value = fixed_param.split('=', 1)
            runner.set_fixed_parameter(name.strip(), _parse_value(value.strip()))
    
    # Run tests
    try:
        runner.run(
            phase_name=args.phase,
            description=args.description,
            skip_validation=args.skip_validation
        )
    except Exception as e:
        logger.error(f"Matrix test failed: {e}", exc_info=True)
        sys.exit(1)


def _parse_value(value_str: str) -> Any:
    """Parse string value to appropriate type."""
    # Try boolean
    if value_str.lower() in ('true', 'yes', 'on'):
        return True
    if value_str.lower() in ('false', 'no', 'off'):
        return False
    
    # Try int
    try:
        return int(value_str)
    except ValueError:
        pass
    
    # Try float
    try:
        return float(value_str)
    except ValueError:
        pass
    
    # Return as string
    return value_str


if __name__ == '__main__':
    main()
