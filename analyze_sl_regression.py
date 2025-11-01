"""
SL Regression Measurement Script

Purpose: Analyze historical trades to determine if SL regression feature would improve performance

What it does:
1. Loads all trade CSV files from csvResults folder
2. Identifies loss clustering patterns (multiple SL exits within time window)
3. Simulates what would happen WITH SL regression applied
4. Compares actual vs simulated results
5. Generates GO/NO-GO decision report

Configuration (matching defaults.py):
- Max SL: 15 points
- Step Size: 5 points
- Minimum SL: 5 points
- Time Window: 20 minutes (1200 seconds)
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION (matches defaults.py)
# ============================================================================

CONFIG = {
    'max_sl_points': 15.0,       # Initial SL (start of cycle)
    'step_size': 5.0,            # Reduction per loss
    'min_sl_points': 5.0,        # Floor (can't go below)
    'window_seconds': 1200,      # 20 minutes
    'base_sl_points': 15.0       # Original SL from defaults.py
}

# ============================================================================
# DATA LOADING
# ============================================================================

def load_all_trades(csv_folder: str) -> pd.DataFrame:
    """
    Load all trade CSV files and combine into single DataFrame
    
    Args:
        csv_folder: Path to folder containing CSV files
    
    Returns:
        Combined DataFrame with all trades
    """
    all_trades = []
    
    for filename in os.listdir(csv_folder):
        if filename.endswith('.csv') and filename.startswith('ft-'):
            filepath = os.path.join(csv_folder, filename)
            
            try:
                # Read CSV, skip header rows
                df = pd.read_csv(filepath, skiprows=17)  # Skip to trade data header
                
                # Check if valid trade data exists
                if 'Entry Time' in df.columns and 'Exit Time' in df.columns:
                    # Add source file for tracking
                    df['source_file'] = filename
                    all_trades.append(df)
                    
            except Exception as e:
                print(f"⚠️  Skipping {filename}: {e}")
    
    if not all_trades:
        raise ValueError("No valid trade data found in CSV files!")
    
    # Combine all trades
    combined = pd.concat(all_trades, ignore_index=True)
    
    # Clean up column names (remove leading/trailing spaces)
    combined.columns = combined.columns.str.strip()
    
    # Parse timestamps
    combined['Entry Time'] = pd.to_datetime(combined['Entry Time'], errors='coerce')
    combined['Exit Time'] = pd.to_datetime(combined['Exit Time'], errors='coerce')
    
    # Parse numeric columns (remove commas and convert)
    numeric_cols = ['Entry Price', 'Exit Price', 'Qty', 'Gross PnL', 'Commission', 'Net PnL']
    for col in numeric_cols:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col].astype(str).str.replace(',', ''), errors='coerce')
    
    # Remove invalid rows
    combined = combined.dropna(subset=['Entry Time', 'Exit Time'])
    
    # Sort by exit time
    combined = combined.sort_values('Exit Time').reset_index(drop=True)
    
    print(f"\n✓ Loaded {len(combined)} trades from {len(all_trades)} files")
    print(f"  Date range: {combined['Entry Time'].min()} to {combined['Exit Time'].max()}")
    
    return combined


# ============================================================================
# LOSS CLUSTERING ANALYSIS
# ============================================================================

def identify_loss_clusters(trades_df: pd.DataFrame, window_seconds: int = 1200) -> List[Dict]:
    """
    Identify periods where multiple SL exits occur within time window
    
    Args:
        trades_df: DataFrame with all trades
        window_seconds: Time window for clustering (default 20 min)
    
    Returns:
        List of cluster dictionaries
    """
    # Filter only loss exits (Stop Loss or Trailing Stop)
    loss_exits = trades_df[
        trades_df['Exit Reason'].str.contains('Stop|Trail', case=False, na=False)
    ].copy()
    
    if len(loss_exits) == 0:
        return []
    
    loss_exits = loss_exits.sort_values('Exit Time').reset_index(drop=True)
    
    clusters = []
    current_cluster = []
    
    for idx, row in loss_exits.iterrows():
        if not current_cluster:
            # Start new cluster
            current_cluster = [row]
        else:
            # Check if within window of cluster start
            time_diff = (row['Exit Time'] - current_cluster[0]['Exit Time']).total_seconds()
            
            if time_diff <= window_seconds:
                current_cluster.append(row)
            else:
                # Save cluster if it has 2+ losses
                if len(current_cluster) >= 2:
                    cluster_pnl = sum([r['Net PnL'] for r in current_cluster])
                    clusters.append({
                        'start_time': current_cluster[0]['Exit Time'],
                        'end_time': current_cluster[-1]['Exit Time'],
                        'loss_count': len(current_cluster),
                        'total_loss': cluster_pnl,
                        'trades': current_cluster
                    })
                
                # Start new cluster
                current_cluster = [row]
    
    # Don't forget last cluster
    if len(current_cluster) >= 2:
        cluster_pnl = sum([r['Net PnL'] for r in current_cluster])
        clusters.append({
            'start_time': current_cluster[0]['Exit Time'],
            'end_time': current_cluster[-1]['Exit Time'],
            'loss_count': len(current_cluster),
            'total_loss': cluster_pnl,
            'trades': current_cluster
        })
    
    return clusters


# ============================================================================
# REGRESSION SIMULATION
# ============================================================================

def simulate_sl_regression(trades_df: pd.DataFrame, config: Dict) -> pd.DataFrame:
    """
    Replay historical trades WITH regression applied
    
    This simulates what would have happened if SL regression was enabled:
    - After TSL or Base SL exit: Reduce SL points for next trades
    - After TP exit: Reset SL to max
    - After time window expires: Reset SL to max
    
    Args:
        trades_df: DataFrame with all trades
        config: Configuration dictionary
    
    Returns:
        DataFrame with simulation results
    """
    trades = trades_df.copy()
    trades = trades.sort_values('Exit Time').reset_index(drop=True)
    
    # Regression state
    current_sl = config['max_sl_points']
    regression_active_until = None
    
    results = []
    
    for idx, trade in trades.iterrows():
        exit_time = trade['Exit Time']
        
        # Check if regression expired BEFORE this trade
        if regression_active_until and exit_time > regression_active_until:
            current_sl = config['max_sl_points']  # Reset
            regression_active_until = None
        
        # Record SL used for this trade
        applied_sl = current_sl
        
        # Get actual P&L
        actual_pnl = trade['Net PnL']
        exit_reason = str(trade['Exit Reason'])
        
        # Simulate what P&L would be with regressed SL
        if actual_pnl < 0:  # This was a loss
            # Original SL was base_sl_points
            original_sl = config['base_sl_points']
            
            # Simulated loss = (applied_sl / original_sl) * actual_loss
            # Example: actual_loss=-4500 with 15pt SL
            #          applied_sl=10pts
            #          simulated_loss = (10/15) * -4500 = -3000
            simulated_pnl = (applied_sl / original_sl) * actual_pnl
        else:
            # Profit trades unchanged
            simulated_pnl = actual_pnl
        
        # Calculate savings (how much LESS we lost with regression)
        # For losses: if actual=-5758, simulated=-3839, savings=+1919 (improvement!)
        # For profits: savings=0 (unchanged)
        savings = simulated_pnl - actual_pnl  # Positive = improvement
        
        results.append({
            'trade_num': idx + 1,
            'entry_time': trade['Entry Time'],
            'exit_time': exit_time,
            'exit_reason': exit_reason,
            'applied_sl_pts': applied_sl,
            'actual_pnl': actual_pnl,
            'simulated_pnl': simulated_pnl,
            'savings': savings,
            'regression_active': regression_active_until is not None
        })
        
        # Update regression state based on exit reason
        if 'Stop' in exit_reason or 'Trail' in exit_reason:
            # Loss exit - trigger or step regression
            if current_sl > config['min_sl_points']:
                current_sl = max(current_sl - config['step_size'], config['min_sl_points'])
            
            # Reset/extend timer
            regression_active_until = exit_time + timedelta(seconds=config['window_seconds'])
            
        elif 'Profit' in exit_reason:
            # Profit exit - reset regression
            current_sl = config['max_sl_points']
            regression_active_until = None
    
    return pd.DataFrame(results)


# ============================================================================
# COMPARISON & REPORTING
# ============================================================================

def generate_comparison_report(trades_df: pd.DataFrame, simulation_df: pd.DataFrame, 
                               clusters: List[Dict], config: Dict) -> Dict:
    """
    Generate comprehensive comparison metrics
    
    Args:
        trades_df: Original trades
        simulation_df: Simulated trades with regression
        clusters: Loss clusters identified
        config: Configuration
    
    Returns:
        Dictionary with comparison metrics
    """
    # Without regression (actual)
    actual_total_pnl = trades_df['Net PnL'].sum()
    actual_losses = trades_df[trades_df['Net PnL'] < 0]['Net PnL'].sum()
    actual_max_dd = trades_df['Net PnL'].cumsum().min()
    actual_loss_count = len(trades_df[trades_df['Net PnL'] < 0])
    
    # With regression (simulated)
    sim_total_pnl = simulation_df['simulated_pnl'].sum()
    sim_losses = simulation_df[simulation_df['simulated_pnl'] < 0]['simulated_pnl'].sum()
    sim_max_dd = simulation_df['simulated_pnl'].cumsum().min()
    sim_loss_count = len(simulation_df[simulation_df['simulated_pnl'] < 0])
    
    # Improvements
    total_savings = simulation_df['savings'].sum()
    
    # Loss reduction: (actual_losses - sim_losses) / |actual_losses| * 100
    # actual_losses = -3.6M, sim_losses = -2.1M → reduction = 1.5M / 3.6M = 41.7%
    loss_reduction_pct = abs((sim_losses - actual_losses) / actual_losses) * 100 if actual_losses != 0 else 0
    
    # Drawdown improvement: (actual_dd - sim_dd) / |actual_dd| * 100  
    # actual_dd = -704K, sim_dd = +817 → improvement = huge (formula different for positive sim)
    if actual_max_dd < 0:
        if sim_max_dd >= 0:
            dd_improvement_pct = 100.0  # Complete elimination of drawdown!
        else:
            dd_improvement_pct = abs((sim_max_dd - actual_max_dd) / actual_max_dd) * 100
    else:
        dd_improvement_pct = 0.0
    
    return {
        'without_regression': {
            'total_pnl': actual_total_pnl,
            'total_losses': actual_losses,
            'max_drawdown': actual_max_dd,
            'loss_count': actual_loss_count
        },
        'with_regression': {
            'total_pnl': sim_total_pnl,
            'total_losses': sim_losses,
            'max_drawdown': sim_max_dd,
            'loss_count': sim_loss_count
        },
        'improvement': {
            'total_savings': total_savings,
            'loss_reduction_pct': loss_reduction_pct,
            'dd_improvement_pct': dd_improvement_pct,
            'pnl_improvement': sim_total_pnl - actual_total_pnl
        },
        'clusters': {
            'count': len(clusters),
            'avg_size': np.mean([c['loss_count'] for c in clusters]) if clusters else 0,
            'total_cluster_losses': sum([c['total_loss'] for c in clusters])
        }
    }


def print_report(report: Dict, config: Dict):
    """
    Print comprehensive analysis report
    
    Args:
        report: Report dictionary
        config: Configuration used
    """
    print("\n" + "=" * 80)
    print("SL REGRESSION FEATURE - MEASUREMENT PHASE REPORT")
    print("=" * 80)
    
    print("\n📊 CONFIGURATION")
    print(f"  Max SL Points:     {config['max_sl_points']:.1f}")
    print(f"  Step Size:         {config['step_size']:.1f}")
    print(f"  Minimum SL:        {config['min_sl_points']:.1f}")
    print(f"  Time Window:       {config['window_seconds']}s ({config['window_seconds']//60} minutes)")
    
    print("\n🔍 LOSS CLUSTERING ANALYSIS")
    print(f"  Clusters Found:    {report['clusters']['count']}")
    if report['clusters']['count'] > 0:
        print(f"  Avg Cluster Size:  {report['clusters']['avg_size']:.1f} losses")
        print(f"  Cluster Losses:    ₹{report['clusters']['total_cluster_losses']:,.2f}")
    
    print("\n📈 PERFORMANCE COMPARISON")
    print(f"\n  {'Metric':<25} {'WITHOUT Regression':>20} {'WITH Regression':>20} {'Improvement':>15}")
    print(f"  {'-'*25} {'-'*20} {'-'*20} {'-'*15}")
    
    wo = report['without_regression']
    wi = report['with_regression']
    imp = report['improvement']
    
    print(f"  {'Total P&L':<25} ₹{wo['total_pnl']:>18,.2f} ₹{wi['total_pnl']:>18,.2f} ₹{imp['pnl_improvement']:>13,.2f}")
    print(f"  {'Total Losses':<25} ₹{wo['total_losses']:>18,.2f} ₹{wi['total_losses']:>18,.2f} {imp['loss_reduction_pct']:>13.1f}%")
    print(f"  {'Max Drawdown':<25} ₹{wo['max_drawdown']:>18,.2f} ₹{wi['max_drawdown']:>18,.2f} {imp['dd_improvement_pct']:>13.1f}%")
    print(f"  {'Loss Count':<25} {wo['loss_count']:>20} {wi['loss_count']:>20} {'same':>15}")
    
    print(f"\n💰 TOTAL SAVINGS: ₹{imp['total_savings']:,.2f}")
    
    print("\n" + "=" * 80)
    print("🎯 DECISION CRITERIA")
    print("=" * 80)
    
    # Decision logic
    criteria_met = 0
    total_criteria = 4
    
    print(f"\n  1. Loss reduction > 15%:          ", end="")
    if imp['loss_reduction_pct'] > 15:
        print(f"✅ YES ({imp['loss_reduction_pct']:.1f}%)")
        criteria_met += 1
    else:
        print(f"❌ NO ({imp['loss_reduction_pct']:.1f}%)")
    
    print(f"  2. Drawdown improvement > 10%:    ", end="")
    if imp['dd_improvement_pct'] > 10:
        print(f"✅ YES ({imp['dd_improvement_pct']:.1f}%)")
        criteria_met += 1
    else:
        print(f"❌ NO ({imp['dd_improvement_pct']:.1f}%)")
    
    print(f"  3. At least 2 clustered periods:  ", end="")
    if report['clusters']['count'] >= 2:
        print(f"✅ YES ({report['clusters']['count']} clusters)")
        criteria_met += 1
    else:
        print(f"❌ NO ({report['clusters']['count']} clusters)")
    
    print(f"  4. Total savings > ₹5,000:        ", end="")
    if imp['total_savings'] > 5000:
        print(f"✅ YES (₹{imp['total_savings']:,.2f})")
        criteria_met += 1
    else:
        print(f"❌ NO (₹{imp['total_savings']:,.2f})")
    
    print(f"\n  Criteria Met: {criteria_met}/{total_criteria}")
    
    print("\n" + "=" * 80)
    if criteria_met >= 3:
        print("✅ RECOMMENDATION: IMPLEMENT SL REGRESSION FEATURE")
        print("\nRationale:")
        print("  - Meaningful loss reduction during clustered loss periods")
        print("  - Measurable improvement in drawdown management")
        print("  - Feature would activate frequently enough to be useful")
    else:
        print("❌ RECOMMENDATION: DO NOT IMPLEMENT SL REGRESSION")
        print("\nRationale:")
        print("  - Insufficient evidence of meaningful improvement")
        print("  - Complexity not justified by benefits")
        print("  - Consider alternative risk management approaches")
    print("=" * 80)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main execution"""
    
    print("\n" + "=" * 80)
    print("SL REGRESSION MEASUREMENT - PHASE 0")
    print("=" * 80)
    print("\nPurpose: Measure if SL regression would improve historical performance")
    print("\nWhat this does:")
    print("  ✓ Risk reduction during extended downtrends (NOT predictive)")
    print("  ✓ Time-based window: 20 minutes (configurable)")
    print("  ✓ Step reduction: 15→10→5 points (configurable)")
    print("  ✓ Simulates regression on historical data")
    
    # Load trades
    csv_folder = r"c:\Users\user\projects\PerplexityCombinedTest\csvResults"
    trades_df = load_all_trades(csv_folder)
    
    # Identify clusters
    print(f"\n🔍 Analyzing loss clustering patterns...")
    clusters = identify_loss_clusters(trades_df, CONFIG['window_seconds'])
    print(f"  Found {len(clusters)} clustered loss periods")
    
    # Simulate regression
    print(f"\n⚙️  Simulating SL regression on historical trades...")
    simulation_df = simulate_sl_regression(trades_df, CONFIG)
    print(f"  Processed {len(simulation_df)} trades")
    
    # Generate report
    report = generate_comparison_report(trades_df, simulation_df, clusters, CONFIG)
    
    # Print report
    print_report(report, CONFIG)
    
    # Save detailed results
    output_file = r"c:\Users\user\projects\PerplexityCombinedTest\sl_regression_analysis.csv"
    simulation_df.to_csv(output_file, index=False)
    print(f"\n💾 Detailed simulation results saved to: {output_file}")
    
    return report


if __name__ == "__main__":
    try:
        report = main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
