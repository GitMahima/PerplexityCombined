"""
live/matrix_results_exporter.py

Excel export functionality for matrix testing results.

CRITICAL PRINCIPLES:
- Comprehensive 7-sheet Excel output
- Configuration section shows exact parameters used
- Trade-by-trade breakdown for analysis
- Performance metrics for quick assessment
- Sensitivity analysis for parameter impact
- Fail-first on export errors

USAGE:
    from .matrix_results_exporter import export_matrix_results
    
    export_matrix_results(
        results_df,
        output_path='C:\\Results\\phase1_ema_matrix.xlsx',
        phase_name='Phase 1: EMA Crossover'
    )
"""

import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================================
# MAIN EXPORT FUNCTION
# ============================================================================

def export_matrix_results(
    results_df: pd.DataFrame,
    output_path: str,
    phase_name: str = "Matrix Test",
    description: str = "",
    fixed_params: Dict[str, Any] = None
) -> str:
    """
    Export matrix test results to Excel with 7 comprehensive sheets.
    
    Sheets:
    1. Summary - Overview of all test runs
    2. Top 10 - Best performing configurations
    3. Configuration - Parameters tested and fixed values
    4. Detailed Metrics - Trade-level statistics for each run
    5. Sensitivity - Parameter impact analysis
    6. Validation - Validation pass/fail status
    7. Metadata - Test execution information
    
    Args:
        results_df: DataFrame with all test results
        output_path: Path to save Excel file
        phase_name: Name of testing phase (e.g., "Phase 1: EMA Crossover")
        description: Optional description of test purpose
        fixed_params: Dictionary of parameters held constant
        
    Returns:
        Path to created Excel file
        
    Raises:
        ValueError: If results_df is empty or missing required columns
        IOError: If file cannot be written
        
    Example:
        >>> export_matrix_results(
        ...     results_df,
        ...     'C:\\Results\\phase1_ema.xlsx',
        ...     'Phase 1: EMA Crossover',
        ...     fixed_params={'risk_per_trade_percent': 2.0}
        ... )
    """
    if results_df is None or results_df.empty:
        raise ValueError("Cannot export empty results DataFrame")
    
    # Validate required columns
    required_cols = ['test_tag', 'total_pnl', 'total_trades']
    missing_cols = [col for col in required_cols if col not in results_df.columns]
    if missing_cols:
        raise ValueError(f"Results missing required columns: {missing_cols}")
    
    # Create output directory if needed
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Exporting matrix results to: {output_path}")
    
    # Create Excel writer
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        
        # Sheet 1: Summary
        _write_summary_sheet(writer, results_df)
        
        # Sheet 2: Top 10
        _write_top10_sheet(writer, results_df)
        
        # Sheet 3: Configuration
        _write_configuration_sheet(writer, results_df, fixed_params, phase_name, description)
        
        # Sheet 4: Detailed Metrics
        _write_detailed_metrics_sheet(writer, results_df)
        
        # Sheet 5: Sensitivity
        _write_sensitivity_sheet(writer, results_df)
        
        # Sheet 6: Validation
        _write_validation_sheet(writer, results_df)
        
        # Sheet 7: Metadata
        _write_metadata_sheet(writer, results_df, phase_name, description)
    
    logger.info(f"✅ Matrix results exported successfully: {output_path}")
    return str(output_path)


# ============================================================================
# SHEET 1: SUMMARY
# ============================================================================

def _write_summary_sheet(writer, results_df: pd.DataFrame):
    """
    Create summary sheet with key metrics for all tests.
    
    Columns:
    - Test Tag (unique identifier)
    - Parameters (compact representation)
    - Total Trades
    - Total PnL
    - Win Rate
    - Avg Win / Avg Loss
    - Max Drawdown
    - Sharpe Ratio (if available)
    - Validation Status
    """
    summary_cols = [
        'test_tag',
        'total_trades',
        'total_pnl',
        'win_rate',
        'avg_win',
        'avg_loss',
        'max_drawdown',
        'validation_passed'
    ]
    
    # Include optional columns if available
    optional_cols = ['sharpe_ratio', 'profit_factor', 'longest_win_streak', 'longest_loss_streak']
    for col in optional_cols:
        if col in results_df.columns:
            summary_cols.insert(-1, col)  # Before validation_passed
    
    # Filter to available columns
    available_cols = [col for col in summary_cols if col in results_df.columns]
    summary_df = results_df[available_cols].copy()
    
    # Sort by total_pnl descending
    summary_df = summary_df.sort_values('total_pnl', ascending=False)
    
    # Format numeric columns
    if 'total_pnl' in summary_df.columns:
        summary_df['total_pnl'] = summary_df['total_pnl'].round(2)
    if 'win_rate' in summary_df.columns:
        summary_df['win_rate'] = (summary_df['win_rate'] * 100).round(1)
    if 'avg_win' in summary_df.columns:
        summary_df['avg_win'] = summary_df['avg_win'].round(2)
    if 'avg_loss' in summary_df.columns:
        summary_df['avg_loss'] = summary_df['avg_loss'].round(2)
    if 'max_drawdown' in summary_df.columns:
        summary_df['max_drawdown'] = summary_df['max_drawdown'].round(2)
    
    # Write to Excel
    summary_df.to_excel(writer, sheet_name='Summary', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Summary', summary_df)
    
    logger.debug(f"Summary sheet: {len(summary_df)} tests")


# ============================================================================
# SHEET 2: TOP 10
# ============================================================================

def _write_top10_sheet(writer, results_df: pd.DataFrame):
    """
    Create Top 10 sheet with best performing configurations.
    
    Ranked by total_pnl, showing comprehensive metrics.
    """
    # Sort by PnL and take top 10
    top10_df = results_df.sort_values('total_pnl', ascending=False).head(10).copy()
    
    # Select columns for display
    display_cols = [col for col in results_df.columns if col != 'test_number']
    top10_df = top10_df[display_cols]
    
    # Write to Excel
    top10_df.to_excel(writer, sheet_name='Top 10', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Top 10', top10_df)
    
    logger.debug(f"Top 10 sheet: Best performers")


# ============================================================================
# SHEET 3: CONFIGURATION
# ============================================================================

def _write_configuration_sheet(
    writer,
    results_df: pd.DataFrame,
    fixed_params: Dict[str, Any],
    phase_name: str,
    description: str
):
    """
    Create configuration sheet showing parameters tested and fixed values.
    
    Sections:
    - Test Phase Information
    - Fixed Parameters (held constant)
    - Tested Parameters (varied across runs)
    - Parameter Value Ranges
    """
    config_data = []
    
    # Section 1: Phase Information
    config_data.append(['=== TEST PHASE INFORMATION ===', ''])
    config_data.append(['Phase Name', phase_name])
    config_data.append(['Description', description or 'N/A'])
    config_data.append(['Total Tests', len(results_df)])
    config_data.append(['', ''])
    
    # Section 2: Fixed Parameters
    config_data.append(['=== FIXED PARAMETERS ===', ''])
    if fixed_params:
        for param, value in sorted(fixed_params.items()):
            config_data.append([param, _format_value(value)])
    else:
        config_data.append(['(None)', ''])
    config_data.append(['', ''])
    
    # Section 3: Tested Parameters
    config_data.append(['=== TESTED PARAMETERS ===', ''])
    
    # Extract parameter columns (exclude metrics)
    metric_cols = {
        'test_number', 'test_tag', 'total_trades', 'total_pnl', 'win_rate',
        'avg_win', 'avg_loss', 'max_drawdown', 'sharpe_ratio', 'profit_factor',
        'longest_win_streak', 'longest_loss_streak', 'validation_passed',
        'validation_error', 'runtime_seconds'
    }
    param_cols = [col for col in results_df.columns if col not in metric_cols]
    
    for param in sorted(param_cols):
        unique_values = results_df[param].unique()
        if len(unique_values) > 1:  # Only show if varied
            value_range = _format_value_range(unique_values)
            config_data.append([param, value_range])
    
    # Create DataFrame and write
    config_df = pd.DataFrame(config_data, columns=['Parameter', 'Value'])
    config_df.to_excel(writer, sheet_name='Configuration', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Configuration', config_df)
    
    logger.debug(f"Configuration sheet: {len(param_cols)} parameters tested")


# ============================================================================
# SHEET 4: DETAILED METRICS
# ============================================================================

def _write_detailed_metrics_sheet(writer, results_df: pd.DataFrame):
    """
    Create detailed metrics sheet with all available metrics.
    
    Full dataset with all columns for comprehensive analysis.
    """
    # Write full results
    results_df.to_excel(writer, sheet_name='Detailed Metrics', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Detailed Metrics', results_df)
    
    logger.debug(f"Detailed Metrics sheet: {len(results_df)} rows × {len(results_df.columns)} cols")


# ============================================================================
# SHEET 5: SENSITIVITY ANALYSIS
# ============================================================================

def _write_sensitivity_sheet(writer, results_df: pd.DataFrame):
    """
    Create sensitivity analysis showing parameter impact on performance.
    
    For each parameter that was varied, show:
    - Parameter value
    - Count of tests with that value
    - Average PnL
    - Average win rate
    - Average trades
    """
    sensitivity_data = []
    
    # Extract parameter columns
    metric_cols = {
        'test_number', 'test_tag', 'total_trades', 'total_pnl', 'win_rate',
        'avg_win', 'avg_loss', 'max_drawdown', 'sharpe_ratio', 'profit_factor',
        'longest_win_streak', 'longest_loss_streak', 'validation_passed',
        'validation_error', 'runtime_seconds'
    }
    param_cols = [col for col in results_df.columns if col not in metric_cols]
    
    for param in sorted(param_cols):
        unique_values = results_df[param].unique()
        if len(unique_values) > 1:  # Only analyze if varied
            
            sensitivity_data.append([f'=== {param.upper()} ===', '', '', '', ''])
            
            for value in sorted(unique_values, key=lambda x: (x is None, x)):
                subset = results_df[results_df[param] == value]
                
                avg_pnl = subset['total_pnl'].mean() if 'total_pnl' in subset else 0
                avg_wr = subset['win_rate'].mean() * 100 if 'win_rate' in subset else 0
                avg_trades = subset['total_trades'].mean() if 'total_trades' in subset else 0
                count = len(subset)
                
                sensitivity_data.append([
                    _format_value(value),
                    count,
                    round(avg_pnl, 2),
                    round(avg_wr, 1),
                    round(avg_trades, 1)
                ])
            
            sensitivity_data.append(['', '', '', '', ''])  # Blank row
    
    if sensitivity_data:
        sens_df = pd.DataFrame(
            sensitivity_data,
            columns=['Parameter Value', 'Test Count', 'Avg PnL', 'Avg Win Rate %', 'Avg Trades']
        )
        sens_df.to_excel(writer, sheet_name='Sensitivity', index=False)
        _autofit_columns(writer, 'Sensitivity', sens_df)
        logger.debug(f"Sensitivity sheet: {len(param_cols)} parameters analyzed")
    else:
        # No varied parameters
        pd.DataFrame({'Message': ['No parameters varied for sensitivity analysis']}).to_excel(
            writer, sheet_name='Sensitivity', index=False
        )


# ============================================================================
# SHEET 6: VALIDATION
# ============================================================================

def _write_validation_sheet(writer, results_df: pd.DataFrame):
    """
    Create validation sheet showing which tests passed/failed validation.
    
    Columns:
    - Test Tag
    - Validation Status
    - Validation Error (if failed)
    - Key Parameters
    """
    validation_cols = ['test_tag', 'validation_passed']
    if 'validation_error' in results_df.columns:
        validation_cols.append('validation_error')
    
    # Add parameter columns
    metric_cols = {
        'test_number', 'test_tag', 'total_trades', 'total_pnl', 'win_rate',
        'avg_win', 'avg_loss', 'max_drawdown', 'sharpe_ratio', 'profit_factor',
        'longest_win_streak', 'longest_loss_streak', 'validation_passed',
        'validation_error', 'runtime_seconds'
    }
    param_cols = [col for col in results_df.columns if col not in metric_cols]
    validation_cols.extend(param_cols)
    
    # Filter to available columns
    available_cols = [col for col in validation_cols if col in results_df.columns]
    validation_df = results_df[available_cols].copy()
    
    # Sort failed tests first
    if 'validation_passed' in validation_df.columns:
        validation_df = validation_df.sort_values('validation_passed')
    
    # Write to Excel
    validation_df.to_excel(writer, sheet_name='Validation', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Validation', validation_df)
    
    failed_count = (~results_df['validation_passed']).sum() if 'validation_passed' in results_df else 0
    logger.debug(f"Validation sheet: {failed_count} failed validations")


# ============================================================================
# SHEET 7: METADATA
# ============================================================================

def _write_metadata_sheet(
    writer,
    results_df: pd.DataFrame,
    phase_name: str,
    description: str
):
    """
    Create metadata sheet with test execution information.
    
    Information:
    - Export timestamp
    - Phase name
    - Total tests run
    - Total runtime
    - System information
    """
    metadata = []
    
    metadata.append(['Export Timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    metadata.append(['Phase Name', phase_name])
    metadata.append(['Description', description or 'N/A'])
    metadata.append(['', ''])
    
    metadata.append(['Total Tests', len(results_df)])
    
    if 'validation_passed' in results_df.columns:
        passed = results_df['validation_passed'].sum()
        failed = (~results_df['validation_passed']).sum()
        metadata.append(['Tests Passed Validation', passed])
        metadata.append(['Tests Failed Validation', failed])
    
    if 'runtime_seconds' in results_df.columns:
        total_runtime = results_df['runtime_seconds'].sum()
        avg_runtime = results_df['runtime_seconds'].mean()
        metadata.append(['Total Runtime (seconds)', round(total_runtime, 1)])
        metadata.append(['Average Runtime per Test (seconds)', round(avg_runtime, 2)])
    
    metadata.append(['', ''])
    
    # Performance summary
    if 'total_pnl' in results_df.columns:
        metadata.append(['Best PnL', round(results_df['total_pnl'].max(), 2)])
        metadata.append(['Worst PnL', round(results_df['total_pnl'].min(), 2)])
        metadata.append(['Average PnL', round(results_df['total_pnl'].mean(), 2)])
    
    if 'win_rate' in results_df.columns:
        metadata.append(['Best Win Rate', f"{results_df['win_rate'].max() * 100:.1f}%"])
        metadata.append(['Worst Win Rate', f"{results_df['win_rate'].min() * 100:.1f}%"])
        metadata.append(['Average Win Rate', f"{results_df['win_rate'].mean() * 100:.1f}%"])
    
    # Create DataFrame and write
    meta_df = pd.DataFrame(metadata, columns=['Metric', 'Value'])
    meta_df.to_excel(writer, sheet_name='Metadata', index=False)
    
    # Auto-adjust column widths
    _autofit_columns(writer, 'Metadata', meta_df)
    
    logger.debug(f"Metadata sheet: {len(metadata)} entries")


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _autofit_columns(writer, sheet_name: str, df: pd.DataFrame):
    """Auto-adjust column widths based on content."""
    try:
        worksheet = writer.sheets[sheet_name]
        for idx, col in enumerate(df.columns):
            max_length = max(
                df[col].astype(str).map(len).max(),
                len(str(col))
            ) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_length, 50)
    except Exception as e:
        logger.warning(f"Could not auto-fit columns for {sheet_name}: {e}")


def _format_value(value: Any) -> str:
    """Format value for display in Excel."""
    if isinstance(value, list):
        return f"[{', '.join(str(v) for v in value)}]"
    elif isinstance(value, float):
        return f"{value:.2f}" if not value.is_integer() else f"{int(value)}"
    elif isinstance(value, bool):
        return "Yes" if value else "No"
    elif value is None:
        return "N/A"
    else:
        return str(value)


def _format_value_range(values) -> str:
    """Format range of parameter values for display."""
    if len(values) == 1:
        return _format_value(values[0])
    
    # Check if all numeric
    try:
        numeric_values = [float(v) for v in values if v is not None]
        if len(numeric_values) == len(values):
            return f"{min(numeric_values):.1f} to {max(numeric_values):.1f} ({len(values)} values)"
    except (ValueError, TypeError):
        pass
    
    # Non-numeric or mixed
    return f"{len(values)} unique values"
