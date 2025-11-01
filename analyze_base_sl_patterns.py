"""
Analyze Base SL Exit Patterns:
1. Frequency of Base SL exits by 0.5-hour time buckets
2. Percentage of Trailing Stop exits followed by Base SL exits
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# LOAD DATA
# ============================================================================

csv_folder = "csvResults"
all_trades = []

print("=" * 80)
print("BASE STOP LOSS PATTERN ANALYSIS")
print("=" * 80)
print(f"\nLoading CSV files from: {csv_folder}")

for filename in os.listdir(csv_folder):
    if filename.endswith('.csv') and filename.startswith('ft-'):
        filepath = os.path.join(csv_folder, filename)
        try:
            df = pd.read_csv(filepath, skiprows=17)
            if 'Exit Time' in df.columns and 'Exit Reason' in df.columns:
                df['source_file'] = filename
                all_trades.append(df)
        except Exception as e:
            pass

if not all_trades:
    print("❌ No valid CSV files found!")
    exit(1)

combined = pd.concat(all_trades, ignore_index=True)
combined.columns = combined.columns.str.strip()

print(f"✅ Loaded {len(all_trades)} files with {len(combined)} total trades")

# Parse timestamps
combined['Exit Time'] = pd.to_datetime(combined['Exit Time'], errors='coerce')
combined = combined.dropna(subset=['Exit Time'])

# Parse Net PnL
if 'Net PnL' in combined.columns:
    combined['Net PnL'] = pd.to_numeric(
        combined['Net PnL'].astype(str).str.replace(',', ''), 
        errors='coerce'
    )

print(f"Total trades with valid exit times: {len(combined)}")

# ============================================================================
# ANALYSIS 1: BASE SL FREQUENCY BY 0.5-HOUR TIME BUCKETS
# ============================================================================

print("\n" + "=" * 80)
print("ANALYSIS 1: BASE SL EXITS BY 30-MINUTE TIME BUCKETS")
print("=" * 80)

# Check what exit reasons exist
print("\nExit Reasons Distribution:")
print(combined['Exit Reason'].value_counts())

# Filter Base SL exits (try both "Base SL" and "Stop Loss")
base_sl_exits = combined[combined['Exit Reason'].str.strip().isin(['Base SL', 'Stop Loss'])].copy()
print(f"\nTotal Base SL/Stop Loss exits: {len(base_sl_exits)}")

if len(base_sl_exits) == 0:
    print("❌ No Base SL/Stop Loss exits found!")
else:
    # Create 30-minute time buckets
    base_sl_exits['Exit_Hour'] = base_sl_exits['Exit Time'].dt.hour
    base_sl_exits['Exit_Minute'] = base_sl_exits['Exit Time'].dt.minute
    
    # Create time bucket label (e.g., 09:00-09:29, 09:30-09:59)
    def get_time_bucket(row):
        hour = row['Exit_Hour']
        minute = row['Exit_Minute']
        
        # Determine if first or second half-hour
        if minute < 30:
            return f"{hour:02d}:00-{hour:02d}:29"
        else:
            return f"{hour:02d}:30-{hour:02d}:59"
    
    base_sl_exits['Time_Bucket'] = base_sl_exits.apply(get_time_bucket, axis=1)
    
    # Aggregate by time bucket
    bucket_summary = base_sl_exits.groupby('Time_Bucket').agg({
        'Exit Time': 'count',
        'Net PnL': 'sum'
    }).rename(columns={'Exit Time': 'Count', 'Net PnL': 'Total_PnL'})
    
    # Calculate percentage
    total_base_sl = len(base_sl_exits)
    bucket_summary['Percentage'] = (bucket_summary['Count'] / total_base_sl * 100).round(1)
    
    # Sort by time
    def sort_key(time_bucket):
        hour = int(time_bucket.split(':')[0])
        minute = int(time_bucket.split(':')[1].split('-')[0])
        return hour * 60 + minute
    
    bucket_summary['Sort_Key'] = bucket_summary.index.map(sort_key)
    bucket_summary = bucket_summary.sort_values('Sort_Key').drop('Sort_Key', axis=1)
    
    print(f"\nBASE SL EXITS BY 30-MINUTE TIME BUCKETS")
    print("-" * 80)
    print(f"{'Time Bucket':<20} {'Count':>8} {'% of Total':>12} {'Total P&L':>15}")
    print("-" * 80)
    
    for time_bucket, row in bucket_summary.iterrows():
        pnl = row['Total_PnL'] if pd.notna(row['Total_PnL']) else 0
        print(f"{time_bucket:<20} {int(row['Count']):>8} {row['Percentage']:>11.1f}% ₹{pnl:>14,.2f}")
    
    # Find top 5 worst buckets
    print(f"\n{'=' * 80}")
    print("TOP 5 WORST TIME BUCKETS (Most Base SL Exits)")
    print("=" * 80)
    
    worst_buckets = bucket_summary.nlargest(5, 'Count')
    for idx, (time_bucket, row) in enumerate(worst_buckets.iterrows(), 1):
        pnl = row['Total_PnL'] if pd.notna(row['Total_PnL']) else 0
        print(f"{idx}. {time_bucket}: {int(row['Count'])} exits ({row['Percentage']:.1f}%), ₹{pnl:,.2f}")
    
    # Hourly summary
    print(f"\n{'=' * 80}")
    print("HOURLY SUMMARY (Combined 30-min buckets)")
    print("=" * 80)
    
    base_sl_exits['Hour'] = base_sl_exits['Exit_Hour']
    hourly = base_sl_exits.groupby('Hour').agg({
        'Exit Time': 'count',
        'Net PnL': 'sum'
    }).rename(columns={'Exit Time': 'Count', 'Net PnL': 'Total_PnL'})
    
    hourly['Percentage'] = (hourly['Count'] / total_base_sl * 100).round(1)
    
    print(f"{'Hour':<10} {'Count':>8} {'% of Total':>12} {'Total P&L':>15}")
    print("-" * 80)
    for hour, row in hourly.iterrows():
        pnl = row['Total_PnL'] if pd.notna(row['Total_PnL']) else 0
        print(f"{int(hour):02d}:00     {int(row['Count']):>8} {row['Percentage']:>11.1f}% ₹{pnl:>14,.2f}")

# ============================================================================
# ANALYSIS 2: TRAILING STOP FOLLOWED BY BASE SL
# ============================================================================

print("\n" + "=" * 80)
print("ANALYSIS 2: TRAILING STOP EXITS FOLLOWED BY BASE SL")
print("=" * 80)

# Sort by source file and exit time to maintain chronological order within each file
combined_sorted = combined.sort_values(['source_file', 'Exit Time']).reset_index(drop=True)

# Filter Trailing Stop exits
trail_exits = combined_sorted[combined_sorted['Exit Reason'].str.strip() == 'Trailing Stop'].copy()
print(f"\nTotal Trailing Stop exits: {len(trail_exits)}")

if len(trail_exits) == 0:
    print("❌ No Trailing Stop exits found!")
else:
    # For each Trailing Stop exit, check if the NEXT exit is Base SL
    followed_by_base_sl = 0
    followed_by_other = 0
    no_next_exit = 0
    
    details = []
    
    for idx, trail_row in trail_exits.iterrows():
        # Find the next exit in the same file
        same_file = combined_sorted[
            (combined_sorted['source_file'] == trail_row['source_file']) &
            (combined_sorted.index > idx)
        ]
        
        if len(same_file) > 0:
            next_exit = same_file.iloc[0]
            next_reason = next_exit['Exit Reason'].strip()
            
            if next_reason in ['Base SL', 'Stop Loss']:
                followed_by_base_sl += 1
                details.append({
                    'File': trail_row['source_file'],
                    'Trail_Exit_Time': trail_row['Exit Time'],
                    'Next_Exit_Time': next_exit['Exit Time'],
                    'Time_Diff_Minutes': (next_exit['Exit Time'] - trail_row['Exit Time']).total_seconds() / 60,
                    'Trail_PnL': trail_row.get('Net PnL', 0),
                    'Base_SL_PnL': next_exit.get('Net PnL', 0)
                })
            else:
                followed_by_other += 1
        else:
            no_next_exit += 1
    
    # Calculate percentage
    total_trail_with_next = followed_by_base_sl + followed_by_other
    if total_trail_with_next > 0:
        percentage = (followed_by_base_sl / total_trail_with_next * 100)
    else:
        percentage = 0
    
    print(f"\nRESULTS:")
    print("-" * 80)
    print(f"Trailing Stop exits with a subsequent exit:  {total_trail_with_next}")
    print(f"  Followed by Base SL:                        {followed_by_base_sl}")
    print(f"  Followed by other exit type:                {followed_by_other}")
    print(f"Trailing Stop exits with no subsequent exit:  {no_next_exit}")
    print(f"\n📊 PERCENTAGE: {percentage:.1f}% of Trailing Stop exits are followed by Base SL")
    
    # Show some examples
    if details:
        details_df = pd.DataFrame(details)
        
        print(f"\n{'=' * 80}")
        print("SAMPLE CASES (First 10)")
        print("=" * 80)
        print(f"{'File':<45} {'Trail→Base Time':<20} {'Δ Minutes':>10} {'Trail P&L':>12} {'Base P&L':>12}")
        print("-" * 80)
        
        for idx, row in details_df.head(10).iterrows():
            file_short = row['File'][:40] + '...' if len(row['File']) > 40 else row['File']
            time_str = f"{row['Trail_Exit_Time'].strftime('%H:%M')}→{row['Next_Exit_Time'].strftime('%H:%M')}"
            trail_pnl = row['Trail_PnL'] if pd.notna(row['Trail_PnL']) else 0
            base_pnl = row['Base_SL_PnL'] if pd.notna(row['Base_SL_PnL']) else 0
            
            print(f"{file_short:<45} {time_str:<20} {row['Time_Diff_Minutes']:>10.1f} ₹{trail_pnl:>11,.2f} ₹{base_pnl:>11,.2f}")
        
        # Statistics on time between Trail and Base SL
        print(f"\n{'=' * 80}")
        print("TIME GAP STATISTICS (Trail → Base SL)")
        print("=" * 80)
        print(f"Average time gap:  {details_df['Time_Diff_Minutes'].mean():.1f} minutes")
        print(f"Median time gap:   {details_df['Time_Diff_Minutes'].median():.1f} minutes")
        print(f"Min time gap:      {details_df['Time_Diff_Minutes'].min():.1f} minutes")
        print(f"Max time gap:      {details_df['Time_Diff_Minutes'].max():.1f} minutes")
        
        # P&L impact
        total_trail_pnl = details_df['Trail_PnL'].sum()
        total_base_pnl = details_df['Base_SL_PnL'].sum()
        
        print(f"\n{'=' * 80}")
        print("P&L IMPACT (Trail → Base SL sequences)")
        print("=" * 80)
        print(f"Total Trailing Stop P&L:  ₹{total_trail_pnl:,.2f}")
        print(f"Total Base SL P&L:        ₹{total_base_pnl:,.2f}")
        print(f"Combined impact:          ₹{total_trail_pnl + total_base_pnl:,.2f}")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
