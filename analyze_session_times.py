"""
Session Time Analysis - Deep Dive into Intraday Profitability Patterns

Purpose: Analyze how different times during the trading session affect profitability
Focus Areas:
1. Minute-by-minute analysis (opening, mid-session, closing)
2. Market phase identification (trend, consolidation, reversal)
3. Volume/activity patterns
4. Time-of-day vs win rate correlation
5. Duration patterns by time of entry
6. Specific time windows that consistently profit/lose
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, time, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# DATA LOADING
# ============================================================================

def load_all_trades(csv_folder: str) -> pd.DataFrame:
    """Load all trade CSV files"""
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
    combined = combined.sort_values('Entry Time').reset_index(drop=True)
    
    # Add detailed time columns
    combined['Hour'] = combined['Entry Time'].dt.hour
    combined['Minute'] = combined['Entry Time'].dt.minute
    combined['Time_Bucket'] = combined['Entry Time'].dt.floor('30min').dt.time  # 30-min buckets
    combined['Time_Precise'] = combined['Entry Time'].dt.time
    combined['Date'] = combined['Entry Time'].dt.date
    combined['Is_Win'] = combined['Net PnL'] > 0
    combined['Is_Loss'] = combined['Net PnL'] < 0
    
    # Session phases
    combined['Session_Phase'] = combined['Hour'].apply(categorize_session_phase)
    
    return combined


def categorize_session_phase(hour):
    """Categorize trading hour into session phases"""
    if hour == 9:
        return 'Opening (9:00-9:59)'
    elif hour in [10, 11]:
        return 'Morning (10:00-11:59)'
    elif hour in [12, 13]:
        return 'Afternoon (12:00-13:59)'
    elif hour in [14, 15]:
        return 'Closing (14:00-15:30)'
    else:
        return 'Other'


# ============================================================================
# DETAILED TIME ANALYSIS
# ============================================================================

def analyze_hourly_breakdown(df: pd.DataFrame):
    """Detailed hourly breakdown with multiple metrics"""
    
    print("\n" + "=" * 120)
    print("HOURLY PERFORMANCE BREAKDOWN - Comprehensive Analysis")
    print("=" * 120)
    
    hourly = df.groupby('Hour').agg({
        'Net PnL': ['count', 'sum', 'mean', 'median', 'std'],
        'Is_Win': 'sum',
        'Is_Loss': 'sum',
        'Duration (min)': ['mean', 'median'],
        'Qty': 'mean',
        'Entry Price': 'mean'
    }).round(2)
    
    hourly.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Median_PnL', 'Std_PnL', 
                      'Wins', 'Losses', 'Avg_Duration', 'Median_Duration', 'Avg_Qty', 'Avg_Entry_Price']
    hourly['Win_Rate'] = (hourly['Wins'] / hourly['Trades'] * 100).round(1)
    hourly['Avg_Win'] = (hourly['Total_PnL'] / hourly['Wins']).round(2)
    hourly['Avg_Loss'] = (df[df['Is_Loss']].groupby('Hour')['Net PnL'].mean()).round(2)
    
    hourly = hourly.reset_index()
    
    # Add session labels
    hourly['Session'] = hourly['Hour'].apply(categorize_session_phase)
    
    print("\n" + hourly.to_string(index=False))
    
    # Identify best and worst hours
    best_hour = hourly.loc[hourly['Total_PnL'].idxmax()]
    worst_hour = hourly.loc[hourly['Total_PnL'].idxmin()]
    
    print(f"\n{'=' * 120}")
    print("KEY INSIGHTS FROM HOURLY ANALYSIS")
    print("=" * 120)
    
    print(f"\n🌟 BEST HOUR: {int(best_hour['Hour']):02d}:00")
    print(f"   Total P&L:      ₹{best_hour['Total_PnL']:,.2f}")
    print(f"   Trades:         {int(best_hour['Trades'])}")
    print(f"   Win Rate:       {best_hour['Win_Rate']:.1f}%")
    print(f"   Avg P&L/Trade:  ₹{best_hour['Avg_PnL']:,.2f}")
    print(f"   Session:        {best_hour['Session']}")
    
    print(f"\n⚠️  WORST HOUR: {int(worst_hour['Hour']):02d}:00")
    print(f"   Total P&L:      ₹{worst_hour['Total_PnL']:,.2f}")
    print(f"   Trades:         {int(worst_hour['Trades'])}")
    print(f"   Win Rate:       {worst_hour['Win_Rate']:.1f}%")
    print(f"   Avg P&L/Trade:  ₹{worst_hour['Avg_PnL']:,.2f}")
    print(f"   Session:        {worst_hour['Session']}")
    
    return hourly


def analyze_30min_buckets(df: pd.DataFrame):
    """Analyze 30-minute time buckets for finer granularity"""
    
    print("\n" + "=" * 120)
    print("30-MINUTE BUCKET ANALYSIS - Fine-Grained Time Patterns")
    print("=" * 120)
    
    buckets = df.groupby('Time_Bucket').agg({
        'Net PnL': ['count', 'sum', 'mean'],
        'Is_Win': 'sum'
    }).round(2)
    
    buckets.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Wins']
    buckets['Win_Rate'] = (buckets['Wins'] / buckets['Trades'] * 100).round(1)
    buckets = buckets.reset_index()
    buckets = buckets.sort_values('Time_Bucket')
    
    print("\n" + buckets.to_string(index=False))
    
    # Identify best and worst 30-min windows
    best_bucket = buckets.loc[buckets['Total_PnL'].idxmax()]
    worst_bucket = buckets.loc[buckets['Total_PnL'].idxmin()]
    
    print(f"\n🌟 BEST 30-MIN WINDOW: {best_bucket['Time_Bucket']}")
    print(f"   Total P&L:      ₹{best_bucket['Total_PnL']:,.2f}")
    print(f"   Trades:         {int(best_bucket['Trades'])}")
    print(f"   Win Rate:       {best_bucket['Win_Rate']:.1f}%")
    
    print(f"\n⚠️  WORST 30-MIN WINDOW: {worst_bucket['Time_Bucket']}")
    print(f"   Total P&L:      ₹{worst_bucket['Total_PnL']:,.2f}")
    print(f"   Trades:         {int(worst_bucket['Trades'])}")
    print(f"   Win Rate:       {worst_bucket['Win_Rate']:.1f}%")
    
    return buckets


def analyze_session_phases(df: pd.DataFrame):
    """Analyze performance by market session phase"""
    
    print("\n" + "=" * 120)
    print("SESSION PHASE ANALYSIS - Market Behavior Patterns")
    print("=" * 120)
    
    phases = df.groupby('Session_Phase').agg({
        'Net PnL': ['count', 'sum', 'mean', 'std'],
        'Is_Win': 'sum',
        'Is_Loss': 'sum',
        'Duration (min)': ['mean', 'median'],
        'Qty': 'mean'
    }).round(2)
    
    phases.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Std_PnL', 'Wins', 'Losses', 
                      'Avg_Duration', 'Median_Duration', 'Avg_Qty']
    phases['Win_Rate'] = (phases['Wins'] / phases['Trades'] * 100).round(1)
    phases['Trade_Pct'] = (phases['Trades'] / phases['Trades'].sum() * 100).round(1)
    phases = phases.reset_index()
    
    print("\n" + phases.to_string(index=False))
    
    # Calculate profitability per session
    print(f"\n{'=' * 120}")
    print("SESSION PROFITABILITY RANKING")
    print("=" * 120)
    
    phases_sorted = phases.sort_values('Total_PnL', ascending=False)
    for idx, row in phases_sorted.iterrows():
        print(f"\n{row['Session_Phase']}")
        print(f"   Total P&L:     ₹{row['Total_PnL']:>12,.2f}")
        print(f"   Avg P&L:       ₹{row['Avg_PnL']:>12,.2f}")
        print(f"   Win Rate:      {row['Win_Rate']:>6.1f}%")
        print(f"   Trade Volume:  {row['Trade_Pct']:>6.1f}% of total")
    
    return phases


def analyze_entry_vs_exit_timing(df: pd.DataFrame):
    """Analyze relationship between entry time and exit time"""
    
    print("\n" + "=" * 120)
    print("ENTRY vs EXIT TIMING ANALYSIS")
    print("=" * 120)
    
    # Add exit hour
    df['Exit_Hour'] = df['Exit Time'].dt.hour
    
    # Create cross-tab of entry hour vs exit hour
    crosstab = pd.crosstab(
        df['Hour'],
        df['Exit_Hour'],
        values=df['Net PnL'],
        aggfunc='sum'
    ).round(0)
    
    print("\nP&L by Entry Hour (rows) vs Exit Hour (columns):")
    print(crosstab.to_string())
    
    # Analyze trades that close in same hour
    same_hour = df[df['Hour'] == df['Exit_Hour']]
    cross_hour = df[df['Hour'] != df['Exit_Hour']]
    
    print(f"\n{'=' * 120}")
    print("SAME-HOUR vs CROSS-HOUR TRADES")
    print("=" * 120)
    
    print(f"\nSame-Hour Trades (entry and exit in same hour):")
    print(f"   Count:         {len(same_hour):,}")
    print(f"   Total P&L:     ₹{same_hour['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{same_hour['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(same_hour['Is_Win'].sum() / len(same_hour) * 100):.1f}%")
    print(f"   Avg Duration:  {same_hour['Duration (min)'].mean():.1f} minutes")
    
    print(f"\nCross-Hour Trades (entry and exit in different hours):")
    print(f"   Count:         {len(cross_hour):,}")
    print(f"   Total P&L:     ₹{cross_hour['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{cross_hour['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(cross_hour['Is_Win'].sum() / len(cross_hour) * 100):.1f}%")
    print(f"   Avg Duration:  {cross_hour['Duration (min)'].mean():.1f} minutes")


def analyze_opening_patterns(df: pd.DataFrame):
    """Deep dive into opening hour (9:00-9:59) patterns"""
    
    print("\n" + "=" * 120)
    print("OPENING HOUR (9:00-9:59) DEEP DIVE")
    print("=" * 120)
    
    opening = df[df['Hour'] == 9].copy()
    
    # 10-minute buckets within opening hour
    opening['Minute_Bucket'] = (opening['Minute'] // 10) * 10
    opening['Time_Label'] = opening['Minute_Bucket'].apply(
        lambda x: f"09:{x:02d}-09:{x+9:02d}"
    )
    
    buckets = opening.groupby('Time_Label').agg({
        'Net PnL': ['count', 'sum', 'mean'],
        'Is_Win': 'sum',
        'Duration (min)': 'mean'
    }).round(2)
    
    buckets.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Wins', 'Avg_Duration']
    buckets['Win_Rate'] = (buckets['Wins'] / buckets['Trades'] * 100).round(1)
    
    print("\n10-Minute Breakdown:")
    print(buckets.to_string())
    
    # First 5 trades vs rest
    first_5 = opening.head(5)
    rest = opening.iloc[5:]
    
    print(f"\n{'=' * 120}")
    print("FIRST 5 TRADES vs REST OF OPENING HOUR")
    print("=" * 120)
    
    print(f"\nFirst 5 Trades:")
    print(f"   Total P&L:     ₹{first_5['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{first_5['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(first_5['Is_Win'].sum() / len(first_5) * 100):.1f}%")
    
    print(f"\nRest of Opening Hour:")
    print(f"   Total P&L:     ₹{rest['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{rest['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(rest['Is_Win'].sum() / len(rest) * 100):.1f}%")


def analyze_closing_patterns(df: pd.DataFrame):
    """Deep dive into closing period (14:00-15:30) patterns"""
    
    print("\n" + "=" * 120)
    print("CLOSING PERIOD (14:00-15:30) DEEP DIVE")
    print("=" * 120)
    
    closing = df[df['Hour'].isin([14, 15])].copy()
    
    # 15-minute buckets
    closing['Time_Label'] = closing.apply(
        lambda row: f"{row['Hour']:02d}:{(row['Minute']//15)*15:02d}-{row['Hour']:02d}:{(row['Minute']//15)*15+14:02d}",
        axis=1
    )
    
    buckets = closing.groupby('Time_Label').agg({
        'Net PnL': ['count', 'sum', 'mean'],
        'Is_Win': 'sum',
        'Duration (min)': 'mean'
    }).round(2)
    
    buckets.columns = ['Trades', 'Total_PnL', 'Avg_PnL', 'Wins', 'Avg_Duration']
    buckets['Win_Rate'] = (buckets['Wins'] / buckets['Trades'] * 100).round(1)
    
    print("\n15-Minute Breakdown:")
    print(buckets.to_string())
    
    # Compare 14:00 hour vs 15:00 hour
    hour_14 = closing[closing['Hour'] == 14]
    hour_15 = closing[closing['Hour'] == 15]
    
    print(f"\n{'=' * 120}")
    print("HOUR 14 (2:00 PM) vs HOUR 15 (3:00 PM)")
    print("=" * 120)
    
    print(f"\nHour 14 (14:00-14:59):")
    print(f"   Trades:        {len(hour_14):,}")
    print(f"   Total P&L:     ₹{hour_14['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{hour_14['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(hour_14['Is_Win'].sum() / len(hour_14) * 100):.1f}%")
    
    print(f"\nHour 15 (15:00-15:30):")
    print(f"   Trades:        {len(hour_15):,}")
    print(f"   Total P&L:     ₹{hour_15['Net PnL'].sum():,.2f}")
    print(f"   Avg P&L:       ₹{hour_15['Net PnL'].mean():,.2f}")
    print(f"   Win Rate:      {(hour_15['Is_Win'].sum() / len(hour_15) * 100):.1f}%")


def identify_profitable_windows(df: pd.DataFrame):
    """Identify consistently profitable time windows"""
    
    print("\n" + "=" * 120)
    print("PROFITABLE TIME WINDOWS IDENTIFICATION")
    print("=" * 120)
    
    # Group by date and hour to see consistency
    daily_hourly = df.groupby(['Date', 'Hour']).agg({
        'Net PnL': 'sum'
    }).reset_index()
    
    # Count how many days each hour was profitable
    hour_consistency = daily_hourly.groupby('Hour').agg({
        'Net PnL': ['count', lambda x: (x > 0).sum(), 'mean']
    }).round(2)
    
    hour_consistency.columns = ['Trading_Days', 'Profitable_Days', 'Avg_Daily_PnL']
    hour_consistency['Consistency_Rate'] = (
        hour_consistency['Profitable_Days'] / hour_consistency['Trading_Days'] * 100
    ).round(1)
    hour_consistency = hour_consistency.reset_index()
    
    print("\nHourly Consistency Across Days:")
    print(hour_consistency.to_string(index=False))
    
    # Identify most consistent hours
    consistent = hour_consistency[hour_consistency['Consistency_Rate'] >= 50]
    inconsistent = hour_consistency[hour_consistency['Consistency_Rate'] < 50]
    
    print(f"\n{'=' * 120}")
    print("CONSISTENCY ANALYSIS")
    print("=" * 120)
    
    print(f"\n✅ CONSISTENT HOURS (profitable >50% of days):")
    for _, row in consistent.iterrows():
        print(f"   Hour {int(row['Hour']):02d}: {row['Consistency_Rate']:.1f}% "
              f"({int(row['Profitable_Days'])}/{int(row['Trading_Days'])} days), "
              f"Avg: ₹{row['Avg_Daily_PnL']:,.2f}/day")
    
    print(f"\n⚠️  INCONSISTENT HOURS (profitable <50% of days):")
    for _, row in inconsistent.iterrows():
        print(f"   Hour {int(row['Hour']):02d}: {row['Consistency_Rate']:.1f}% "
              f"({int(row['Profitable_Days'])}/{int(row['Trading_Days'])} days), "
              f"Avg: ₹{row['Avg_Daily_PnL']:,.2f}/day")


def analyze_duration_by_time(df: pd.DataFrame):
    """Analyze how trade duration varies by entry time"""
    
    print("\n" + "=" * 120)
    print("TRADE DURATION BY ENTRY TIME")
    print("=" * 120)
    
    duration_stats = df.groupby('Hour').agg({
        'Duration (min)': ['mean', 'median', 'min', 'max', 'std'],
        'Net PnL': 'count'
    }).round(2)
    
    duration_stats.columns = ['Avg_Duration', 'Median_Duration', 'Min_Duration', 
                              'Max_Duration', 'Std_Duration', 'Trades']
    duration_stats = duration_stats.reset_index()
    
    print("\n" + duration_stats.to_string(index=False))
    
    # Correlation between duration and profitability by hour
    print(f"\n{'=' * 120}")
    print("DURATION vs PROFITABILITY BY HOUR")
    print("=" * 120)
    
    for hour in sorted(df['Hour'].unique()):
        hour_data = df[df['Hour'] == hour]
        
        # Split into short (< median) and long (>= median) duration
        median_dur = hour_data['Duration (min)'].median()
        short = hour_data[hour_data['Duration (min)'] < median_dur]
        long = hour_data[hour_data['Duration (min)'] >= median_dur]
        
        print(f"\nHour {hour:02d}:00 (Median Duration: {median_dur:.1f} min)")
        print(f"   Short Trades (<{median_dur:.1f}min): "
              f"{len(short)} trades, ₹{short['Net PnL'].sum():,.2f} total, "
              f"₹{short['Net PnL'].mean():,.2f} avg")
        print(f"   Long Trades  (≥{median_dur:.1f}min): "
              f"{len(long)} trades, ₹{long['Net PnL'].sum():,.2f} total, "
              f"₹{long['Net PnL'].mean():,.2f} avg")


def generate_time_based_recommendations(df: pd.DataFrame):
    """Generate actionable recommendations based on time analysis"""
    
    print("\n" + "=" * 120)
    print("TIME-BASED TRADING RECOMMENDATIONS")
    print("=" * 120)
    
    # Calculate statistics by hour
    hourly_stats = df.groupby('Hour').agg({
        'Net PnL': ['sum', 'mean', 'count'],
        'Is_Win': 'sum'
    })
    hourly_stats.columns = ['Total_PnL', 'Avg_PnL', 'Trades', 'Wins']
    hourly_stats['Win_Rate'] = (hourly_stats['Wins'] / hourly_stats['Trades'] * 100)
    
    print("\n🎯 STRATEGIC RECOMMENDATIONS:")
    
    # Recommendation 1: Focus hours
    profitable_hours = hourly_stats[hourly_stats['Total_PnL'] > 0].index.tolist()
    print(f"\n1. FOCUS HOURS (Total P&L > 0):")
    print(f"   Hours to emphasize: {[f'{h:02d}:00' for h in profitable_hours]}")
    for hour in profitable_hours:
        stats = hourly_stats.loc[hour]
        print(f"   • Hour {hour:02d}: ₹{stats['Total_PnL']:,.2f} total, "
              f"{stats['Win_Rate']:.1f}% win rate, {int(stats['Trades'])} trades")
    
    # Recommendation 2: Avoid/restrict hours
    losing_hours = hourly_stats[hourly_stats['Total_PnL'] < 0].index.tolist()
    print(f"\n2. AVOID/RESTRICT HOURS (Total P&L < 0):")
    print(f"   Hours to avoid or use tighter SL: {[f'{h:02d}:00' for h in losing_hours]}")
    for hour in losing_hours:
        stats = hourly_stats.loc[hour]
        print(f"   • Hour {hour:02d}: ₹{stats['Total_PnL']:,.2f} loss, "
              f"{stats['Win_Rate']:.1f}% win rate, {int(stats['Trades'])} trades")
    
    # Recommendation 3: Session strategy
    print(f"\n3. SESSION-BASED STRATEGY:")
    print(f"   • Opening (9:00): {'✅ Trade actively' if 9 in profitable_hours else '⚠️ Wait for confirmation'}")
    print(f"   • Morning (10-11): {'✅ Best performance window' if 10 in profitable_hours else '⚠️ Use caution'}")
    print(f"   • Afternoon (12-13): {'✅ Continue trading' if 12 in profitable_hours else '⚠️ Reduce activity'}")
    print(f"   • Closing (14-15): {'✅ Active until close' if 14 in profitable_hours else '❌ Avoid or tighten risk'}")
    
    # Recommendation 4: Specific time windows
    buckets = df.groupby('Time_Bucket').agg({'Net PnL': 'sum'})
    best_buckets = buckets.nlargest(3, 'Net PnL')
    worst_buckets = buckets.nsmallest(3, 'Net PnL')
    
    print(f"\n4. SPECIFIC TIME WINDOWS:")
    print(f"   Best 30-min windows:")
    for time_bucket, row in best_buckets.iterrows():
        print(f"   • {time_bucket}: ₹{row['Net PnL']:,.2f}")
    
    print(f"   Worst 30-min windows:")
    for time_bucket, row in worst_buckets.iterrows():
        print(f"   • {time_bucket}: ₹{row['Net PnL']:,.2f}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    csv_folder = r"c:\Users\user\projects\PerplexityCombinedTest\csvResults"
    
    print("\nLoading trade data...")
    df = load_all_trades(csv_folder)
    
    print(f"\n{'=' * 120}")
    print(f"DATA LOADED: {len(df):,} trades from {df['Date'].nunique()} trading days")
    print(f"Date Range: {df['Entry Time'].min().strftime('%Y-%m-%d')} to {df['Exit Time'].max().strftime('%Y-%m-%d')}")
    print(f"Session Hours: {sorted(df['Hour'].unique())}")
    print(f"{'=' * 120}")
    
    # Run all analyses
    hourly = analyze_hourly_breakdown(df)
    buckets_30 = analyze_30min_buckets(df)
    phases = analyze_session_phases(df)
    analyze_entry_vs_exit_timing(df)
    analyze_opening_patterns(df)
    analyze_closing_patterns(df)
    identify_profitable_windows(df)
    analyze_duration_by_time(df)
    generate_time_based_recommendations(df)
    
    # Save results
    output_file = r"c:\Users\user\projects\PerplexityCombinedTest\session_time_analysis.csv"
    hourly.to_csv(output_file, index=False)
    print(f"\n💾 Hourly analysis saved to: {output_file}")
    
    print("\n" + "=" * 120)
    print("SESSION TIME ANALYSIS COMPLETE")
    print("=" * 120)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
