"""
Detailed Results Analysis - Comprehensive Trading Performance Insights

Analyzes all forward test results to identify:
1. Overall performance metrics
2. Win/loss patterns and clustering
3. Time-based performance (intraday patterns)
4. Exit reason effectiveness
5. Position sizing patterns
6. Risk/reward ratios
7. Drawdown analysis
8. Trade duration patterns
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, time
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# DATA LOADING
# ============================================================================

def load_all_trades(csv_folder: str) -> pd.DataFrame:
    """Load all trade CSV files and combine into single DataFrame"""
    all_trades = []
    
    for filename in os.listdir(csv_folder):
        if filename.endswith('.csv') and filename.startswith('ft-'):
            filepath = os.path.join(csv_folder, filename)
            
            try:
                df = pd.read_csv(filepath, skiprows=17)
                
                if 'Entry Time' in df.columns and 'Exit Time' in df.columns:
                    df['source_file'] = filename
                    all_trades.append(df)
                    
            except Exception as e:
                pass
    
    if not all_trades:
        raise ValueError("No valid trade data found!")
    
    combined = pd.concat(all_trades, ignore_index=True)
    combined.columns = combined.columns.str.strip()
    
    # Parse timestamps
    combined['Entry Time'] = pd.to_datetime(combined['Entry Time'], errors='coerce')
    combined['Exit Time'] = pd.to_datetime(combined['Exit Time'], errors='coerce')
    
    # Parse numeric columns
    numeric_cols = ['Entry Price', 'Exit Price', 'Qty', 'Gross PnL', 'Commission', 'Net PnL', 'Duration (min)']
    for col in numeric_cols:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col].astype(str).str.replace(',', ''), errors='coerce')
    
    combined = combined.dropna(subset=['Entry Time', 'Exit Time'])
    combined = combined.sort_values('Exit Time').reset_index(drop=True)
    
    # Add derived columns
    combined['Hour'] = combined['Entry Time'].dt.hour
    combined['Minute'] = combined['Entry Time'].dt.minute
    combined['Date'] = combined['Entry Time'].dt.date
    combined['Day_of_Week'] = combined['Entry Time'].dt.day_name()
    combined['Is_Win'] = combined['Net PnL'] > 0
    combined['Is_Loss'] = combined['Net PnL'] < 0
    
    # Categorize exit reasons
    combined['Exit_Category'] = combined['Exit Reason'].apply(categorize_exit)
    
    return combined


def categorize_exit(reason):
    """Categorize exit reasons into broader categories"""
    if pd.isna(reason):
        return 'Unknown'
    reason = str(reason).lower()
    if 'stop' in reason and 'loss' in reason:
        return 'Base SL'
    elif 'trail' in reason:
        return 'Trailing Stop'
    elif 'profit' in reason or 'take profit' in reason:
        return 'Take Profit'
    elif 'session' in reason:
        return 'Session End'
    else:
        return 'Other'


# ============================================================================
# ANALYSIS FUNCTIONS
# ============================================================================

def analyze_overall_performance(df: pd.DataFrame):
    """Overall performance summary"""
    total_trades = len(df)
    winning_trades = len(df[df['Is_Win']])
    losing_trades = len(df[df['Is_Loss']])
    breakeven_trades = total_trades - winning_trades - losing_trades
    
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    total_pnl = df['Net PnL'].sum()
    total_wins = df[df['Is_Win']]['Net PnL'].sum()
    total_losses = df[df['Is_Loss']]['Net PnL'].sum()
    
    avg_win = df[df['Is_Win']]['Net PnL'].mean() if winning_trades > 0 else 0
    avg_loss = df[df['Is_Loss']]['Net PnL'].mean() if losing_trades > 0 else 0
    
    profit_factor = abs(total_wins / total_losses) if total_losses != 0 else float('inf')
    
    best_trade = df['Net PnL'].max()
    worst_trade = df['Net PnL'].min()
    
    avg_trade = df['Net PnL'].mean()
    median_trade = df['Net PnL'].median()
    
    # Drawdown analysis
    cumulative_pnl = df['Net PnL'].cumsum()
    running_max = cumulative_pnl.expanding().max()
    drawdown = cumulative_pnl - running_max
    max_drawdown = drawdown.min()
    
    return {
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'breakeven_trades': breakeven_trades,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'avg_trade': avg_trade,
        'median_trade': median_trade,
        'max_drawdown': max_drawdown,
        'risk_reward_ratio': abs(avg_win / avg_loss) if avg_loss != 0 else 0
    }


def analyze_exit_reasons(df: pd.DataFrame):
    """Analyze performance by exit reason"""
    exit_stats = []
    
    for exit_cat in df['Exit_Category'].unique():
        subset = df[df['Exit_Category'] == exit_cat]
        
        exit_stats.append({
            'Exit_Reason': exit_cat,
            'Count': len(subset),
            'Percent': len(subset) / len(df) * 100,
            'Total_PnL': subset['Net PnL'].sum(),
            'Avg_PnL': subset['Net PnL'].mean(),
            'Win_Rate': (len(subset[subset['Is_Win']]) / len(subset) * 100) if len(subset) > 0 else 0,
            'Avg_Duration': subset['Duration (min)'].mean()
        })
    
    return pd.DataFrame(exit_stats).sort_values('Count', ascending=False)


def analyze_time_patterns(df: pd.DataFrame):
    """Analyze performance by time of day"""
    # Group by hour
    hourly = df.groupby('Hour').agg({
        'Net PnL': ['count', 'sum', 'mean'],
        'Is_Win': 'sum'
    }).round(2)
    
    hourly.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Wins']
    hourly['Win_Rate'] = (hourly['Wins'] / hourly['Trades'] * 100).round(1)
    hourly = hourly.reset_index()
    
    return hourly


def analyze_daily_patterns(df: pd.DataFrame):
    """Analyze performance by day"""
    daily = df.groupby('Date').agg({
        'Net PnL': ['count', 'sum', 'mean'],
        'Is_Win': 'sum',
        'Is_Loss': 'sum'
    }).round(2)
    
    daily.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Wins', 'Losses']
    daily['Win_Rate'] = (daily['Wins'] / daily['Trades'] * 100).round(1)
    daily = daily.reset_index()
    daily['Date'] = pd.to_datetime(daily['Date'])
    
    return daily.sort_values('Date')


def analyze_consecutive_patterns(df: pd.DataFrame):
    """Analyze consecutive wins/losses"""
    df = df.sort_values('Exit Time').copy()
    
    # Calculate streaks
    df['Win_Streak'] = 0
    df['Loss_Streak'] = 0
    
    win_streak = 0
    loss_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    
    for idx, row in df.iterrows():
        if row['Is_Win']:
            win_streak += 1
            loss_streak = 0
            max_win_streak = max(max_win_streak, win_streak)
        elif row['Is_Loss']:
            loss_streak += 1
            win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            win_streak = 0
            loss_streak = 0
        
        df.at[idx, 'Win_Streak'] = win_streak
        df.at[idx, 'Loss_Streak'] = loss_streak
    
    return {
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'avg_win_streak': df[df['Win_Streak'] > 0]['Win_Streak'].mean(),
        'avg_loss_streak': df[df['Loss_Streak'] > 0]['Loss_Streak'].mean()
    }


def analyze_trade_duration(df: pd.DataFrame):
    """Analyze trade duration patterns"""
    duration_stats = df.groupby('Exit_Category').agg({
        'Duration (min)': ['count', 'mean', 'median', 'min', 'max']
    }).round(2)
    
    duration_stats.columns = ['Count', 'Avg_Min', 'Median_Min', 'Min_Min', 'Max_Min']
    
    return duration_stats


def identify_problem_areas(df: pd.DataFrame):
    """Identify specific problem patterns"""
    problems = []
    
    # Large losses (> ₹5000)
    large_losses = df[df['Net PnL'] < -5000]
    if len(large_losses) > 0:
        problems.append({
            'Issue': 'Large Losses (>₹5000)',
            'Count': len(large_losses),
            'Total_Impact': large_losses['Net PnL'].sum(),
            'Avg_Loss': large_losses['Net PnL'].mean(),
            'Main_Exit_Reason': large_losses['Exit_Category'].mode()[0] if len(large_losses) > 0 else 'N/A'
        })
    
    # Quick losses (< 2 minutes)
    quick_losses = df[(df['Is_Loss']) & (df['Duration (min)'] < 2)]
    if len(quick_losses) > 0:
        problems.append({
            'Issue': 'Quick Losses (<2 min)',
            'Count': len(quick_losses),
            'Total_Impact': quick_losses['Net PnL'].sum(),
            'Avg_Loss': quick_losses['Net PnL'].mean(),
            'Main_Exit_Reason': quick_losses['Exit_Category'].mode()[0] if len(quick_losses) > 0 else 'N/A'
        })
    
    # High commission trades (commission > 10% of PnL)
    high_comm = df[abs(df['Commission']) > abs(df['Net PnL']) * 0.1]
    if len(high_comm) > 0:
        problems.append({
            'Issue': 'High Commission Impact',
            'Count': len(high_comm),
            'Total_Impact': high_comm['Commission'].sum(),
            'Avg_Loss': high_comm['Commission'].mean(),
            'Main_Exit_Reason': 'Commission Erosion'
        })
    
    # Losses during profitable hours
    hourly_pnl = df.groupby('Hour')['Net PnL'].sum()
    profitable_hours = hourly_pnl[hourly_pnl > 0].index
    losses_in_good_hours = df[(df['Is_Loss']) & (df['Hour'].isin(profitable_hours))]
    if len(losses_in_good_hours) > 0:
        problems.append({
            'Issue': 'Losses During Profitable Hours',
            'Count': len(losses_in_good_hours),
            'Total_Impact': losses_in_good_hours['Net PnL'].sum(),
            'Avg_Loss': losses_in_good_hours['Net PnL'].mean(),
            'Main_Exit_Reason': losses_in_good_hours['Exit_Category'].mode()[0] if len(losses_in_good_hours) > 0 else 'N/A'
        })
    
    return pd.DataFrame(problems)


# ============================================================================
# REPORTING
# ============================================================================

def print_comprehensive_report(df: pd.DataFrame):
    """Print comprehensive analysis report"""
    
    print("\n" + "=" * 100)
    print("COMPREHENSIVE TRADING RESULTS ANALYSIS")
    print("=" * 100)
    
    print(f"\n📊 DATA SUMMARY")
    print(f"  Total Trades: {len(df):,}")
    print(f"  Date Range: {df['Entry Time'].min().strftime('%Y-%m-%d')} to {df['Exit Time'].max().strftime('%Y-%m-%d')}")
    print(f"  Trading Days: {df['Date'].nunique()}")
    print(f"  Source Files: {df['source_file'].nunique()}")
    
    # Overall Performance
    print("\n" + "=" * 100)
    print("1. OVERALL PERFORMANCE")
    print("=" * 100)
    
    overall = analyze_overall_performance(df)
    
    print(f"\n📈 Trade Statistics:")
    print(f"  Total Trades:      {overall['total_trades']:,}")
    print(f"  Winning Trades:    {overall['winning_trades']:,} ({overall['win_rate']:.1f}%)")
    print(f"  Losing Trades:     {overall['losing_trades']:,} ({100-overall['win_rate']:.1f}%)")
    print(f"  Breakeven Trades:  {overall['breakeven_trades']:,}")
    
    print(f"\n💰 P&L Analysis:")
    print(f"  Total P&L:         ₹{overall['total_pnl']:,.2f}")
    print(f"  Total Wins:        ₹{overall['total_wins']:,.2f}")
    print(f"  Total Losses:      ₹{overall['total_losses']:,.2f}")
    print(f"  Average Win:       ₹{overall['avg_win']:,.2f}")
    print(f"  Average Loss:      ₹{overall['avg_loss']:,.2f}")
    print(f"  Average Trade:     ₹{overall['avg_trade']:,.2f}")
    print(f"  Median Trade:      ₹{overall['median_trade']:,.2f}")
    
    print(f"\n📊 Performance Metrics:")
    print(f"  Profit Factor:     {overall['profit_factor']:.2f}")
    print(f"  Risk/Reward Ratio: {overall['risk_reward_ratio']:.2f}")
    print(f"  Best Trade:        ₹{overall['best_trade']:,.2f}")
    print(f"  Worst Trade:       ₹{overall['worst_trade']:,.2f}")
    print(f"  Max Drawdown:      ₹{overall['max_drawdown']:,.2f}")
    
    # Exit Reason Analysis
    print("\n" + "=" * 100)
    print("2. EXIT REASON ANALYSIS")
    print("=" * 100)
    
    exit_analysis = analyze_exit_reasons(df)
    print("\n" + exit_analysis.to_string(index=False))
    
    # Time Patterns
    print("\n" + "=" * 100)
    print("3. HOURLY PERFORMANCE PATTERNS")
    print("=" * 100)
    
    hourly = analyze_time_patterns(df)
    print("\n" + hourly.to_string(index=False))
    
    # Best/Worst Hours
    best_hour = hourly.loc[hourly['Total_PnL'].idxmax()]
    worst_hour = hourly.loc[hourly['Total_PnL'].idxmin()]
    
    print(f"\n🌟 Best Hour: {int(best_hour['Hour']):02d}:00 (₹{best_hour['Total_PnL']:,.2f} total, {best_hour['Win_Rate']:.1f}% win rate)")
    print(f"⚠️  Worst Hour: {int(worst_hour['Hour']):02d}:00 (₹{worst_hour['Total_PnL']:,.2f} total, {worst_hour['Win_Rate']:.1f}% win rate)")
    
    # Daily Patterns
    print("\n" + "=" * 100)
    print("4. DAILY PERFORMANCE PATTERNS")
    print("=" * 100)
    
    daily = analyze_daily_patterns(df)
    
    print(f"\n  Total Trading Days: {len(daily)}")
    print(f"  Profitable Days:    {len(daily[daily['Total_PnL'] > 0])} ({len(daily[daily['Total_PnL'] > 0])/len(daily)*100:.1f}%)")
    print(f"  Losing Days:        {len(daily[daily['Total_PnL'] < 0])} ({len(daily[daily['Total_PnL'] < 0])/len(daily)*100:.1f}%)")
    
    print(f"\n  Best Day:           {daily.loc[daily['Total_PnL'].idxmax(), 'Date'].strftime('%Y-%m-%d')} (₹{daily['Total_PnL'].max():,.2f})")
    print(f"  Worst Day:          {daily.loc[daily['Total_PnL'].idxmin(), 'Date'].strftime('%Y-%m-%d')} (₹{daily['Total_PnL'].min():,.2f})")
    print(f"  Avg Daily P&L:      ₹{daily['Total_PnL'].mean():,.2f}")
    print(f"  Avg Trades/Day:     {daily['Trades'].mean():.1f}")
    
    # Consecutive Patterns
    print("\n" + "=" * 100)
    print("5. CONSECUTIVE WIN/LOSS PATTERNS")
    print("=" * 100)
    
    streaks = analyze_consecutive_patterns(df)
    
    print(f"\n  Max Win Streak:     {streaks['max_win_streak']:.0f} consecutive wins")
    print(f"  Max Loss Streak:    {streaks['max_loss_streak']:.0f} consecutive losses")
    print(f"  Avg Win Streak:     {streaks['avg_win_streak']:.1f}")
    print(f"  Avg Loss Streak:    {streaks['avg_loss_streak']:.1f}")
    
    # Trade Duration
    print("\n" + "=" * 100)
    print("6. TRADE DURATION ANALYSIS")
    print("=" * 100)
    
    duration = analyze_trade_duration(df)
    print("\n" + duration.to_string())
    
    # Problem Areas
    print("\n" + "=" * 100)
    print("7. PROBLEM AREAS & IMPROVEMENT OPPORTUNITIES")
    print("=" * 100)
    
    problems = identify_problem_areas(df)
    if len(problems) > 0:
        print("\n" + problems.to_string(index=False))
    else:
        print("\n  ✅ No major problem patterns identified")
    
    # Key Insights
    print("\n" + "=" * 100)
    print("8. KEY INSIGHTS & RECOMMENDATIONS")
    print("=" * 100)
    
    print(f"\n✅ Strengths:")
    if overall['win_rate'] > 60:
        print(f"  • Strong win rate of {overall['win_rate']:.1f}% (above 60%)")
    if overall['profit_factor'] > 1.5:
        print(f"  • Good profit factor of {overall['profit_factor']:.2f} (above 1.5)")
    if overall['risk_reward_ratio'] > 1.2:
        print(f"  • Favorable risk/reward ratio of {overall['risk_reward_ratio']:.2f}")
    
    # Find most profitable exit strategy
    top_exit = exit_analysis.loc[exit_analysis['Total_PnL'].idxmax()]
    print(f"  • {top_exit['Exit_Reason']} exits most profitable (₹{top_exit['Total_PnL']:,.2f} total)")
    
    print(f"\n⚠️  Areas for Improvement:")
    if overall['win_rate'] < 50:
        print(f"  • Win rate below 50% ({overall['win_rate']:.1f}%) - focus on entry quality")
    if abs(overall['avg_loss']) > overall['avg_win']:
        print(f"  • Average loss (₹{abs(overall['avg_loss']):,.2f}) exceeds average win (₹{overall['avg_win']:,.2f})")
    if overall['profit_factor'] < 1.0:
        print(f"  • Profit factor below 1.0 ({overall['profit_factor']:.2f}) - system unprofitable")
    
    # Check Base SL performance
    base_sl_perf = exit_analysis[exit_analysis['Exit_Reason'] == 'Base SL']
    if len(base_sl_perf) > 0 and base_sl_perf.iloc[0]['Total_PnL'] < -50000:
        print(f"  • Base SL exits causing significant losses (₹{base_sl_perf.iloc[0]['Total_PnL']:,.2f})")
        print(f"    → SL Regression feature would address this! (saves ₹1.5M based on analysis)")
    
    # Check commission impact
    total_commission = df['Commission'].sum()
    commission_pct = abs(total_commission / overall['total_wins']) * 100 if overall['total_wins'] > 0 else 0
    if commission_pct > 5:
        print(f"  • Commission eating {commission_pct:.1f}% of gross wins (₹{total_commission:,.2f} total)")
    
    print("\n" + "=" * 100)


# ============================================================================
# MAIN
# ============================================================================

def main():
    csv_folder = r"c:\Users\user\projects\PerplexityCombinedTest\csvResults"
    
    print("\nLoading trade data...")
    df = load_all_trades(csv_folder)
    
    print_comprehensive_report(df)
    
    # Save detailed daily performance
    daily = analyze_daily_patterns(df)
    output_file = r"c:\Users\user\projects\PerplexityCombinedTest\daily_performance.csv"
    daily.to_csv(output_file, index=False)
    print(f"\n💾 Daily performance data saved to: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
