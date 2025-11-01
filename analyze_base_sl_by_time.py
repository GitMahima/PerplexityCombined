"""
Analyze Base SL Exits by Time Block

This script analyzes all forward test results to count exits due to Base SL
grouped by 30-minute time blocks.
"""

import os
import pandas as pd
from datetime import datetime

# Folder containing results
results_folder = r"c:\Users\user\projects\PerplexityCombinedTest\csvResults"

print("=" * 80)
print("BASE STOP LOSS EXITS - 30-MINUTE TIME BLOCK ANALYSIS")
print("=" * 80)

# Collect all base SL exits
all_exits = []

# Process all CSV files
csv_files = [f for f in os.listdir(results_folder) if f.endswith('.csv')]
print(f"\nFound {len(csv_files)} result files to analyze...")

for filename in csv_files:
    filepath = os.path.join(results_folder, filename)
    
    try:
        # Read CSV, skipping header rows
        df = pd.read_csv(filepath, skiprows=17)
        
        # Check if required columns exist
        if 'Exit Time' not in df.columns or 'Exit Reason' not in df.columns:
            continue
        
        # Filter for Base SL exits
        base_sl_exits = df[df['Exit Reason'] == 'Stop Loss'].copy()
        
        if len(base_sl_exits) > 0:
            # Parse exit times
            base_sl_exits['Exit Time'] = pd.to_datetime(base_sl_exits['Exit Time'], errors='coerce')
            base_sl_exits = base_sl_exits.dropna(subset=['Exit Time'])
            
            # Extract hour and minute
            base_sl_exits['Hour'] = base_sl_exits['Exit Time'].dt.hour
            base_sl_exits['Minute'] = base_sl_exits['Exit Time'].dt.minute
            
            # Create 30-minute time block
            base_sl_exits['Time_Block'] = base_sl_exits.apply(
                lambda row: f"{row['Hour']:02d}:{(row['Minute']//30)*30:02d}-{row['Hour']:02d}:{(row['Minute']//30)*30+29:02d}",
                axis=1
            )
            
            # Parse Net PnL (remove commas and convert to float)
            if 'Net PnL' in base_sl_exits.columns:
                base_sl_exits['Net PnL'] = pd.to_numeric(
                    base_sl_exits['Net PnL'].astype(str).str.replace(',', ''), 
                    errors='coerce'
                )
            
            all_exits.append(base_sl_exits[['Exit Time', 'Time_Block', 'Net PnL']])
            
    except Exception as e:
        print(f"Error processing {filename}: {e}")
        continue

if not all_exits:
    print("\n❌ No Base SL exits found in any files")
    exit(0)

# Combine all exits
combined_exits = pd.concat(all_exits, ignore_index=True)

print(f"\n✅ Found {len(combined_exits):,} Base SL exits across all files")

# Group by time block
time_block_summary = combined_exits.groupby('Time_Block').agg({
    'Exit Time': 'count',
    'Net PnL': 'sum'
}).rename(columns={'Exit Time': 'Count', 'Net PnL': 'Total_PnL'})

# Sort by time (extract hour and minute for sorting)
time_block_summary['Sort_Key'] = time_block_summary.index.map(
    lambda x: int(x.split(':')[0]) * 60 + int(x.split(':')[1].split('-')[0])
)
time_block_summary = time_block_summary.sort_values('Sort_Key')
time_block_summary = time_block_summary.drop('Sort_Key', axis=1)

# Calculate percentages
total_exits = len(combined_exits)
time_block_summary['Percentage'] = (time_block_summary['Count'] / total_exits * 100).round(1)

print("\n" + "=" * 80)
print("BASE SL EXITS BY 30-MINUTE TIME BLOCKS")
print("=" * 80)
print(f"\nTotal Base SL Exits: {total_exits:,}")
print(f"Total P&L Impact: ₹{time_block_summary['Total_PnL'].sum():,.2f}")
print("\n")

# Display results
print(f"{'Time Block':<20} {'Count':>8} {'% of Total':>12} {'Total P&L':>15}")
print("-" * 80)

for time_block, row in time_block_summary.iterrows():
    print(f"{time_block:<20} {int(row['Count']):>8} {row['Percentage']:>11.1f}% ₹{row['Total_PnL']:>14,.2f}")

# Find worst time blocks
print("\n" + "=" * 80)
print("TOP 5 WORST TIME BLOCKS (Most Base SL Exits)")
print("=" * 80)

worst_blocks = time_block_summary.nlargest(5, 'Count')
for idx, (time_block, row) in enumerate(worst_blocks.iterrows(), 1):
    print(f"\n{idx}. {time_block}")
    print(f"   Exits:     {int(row['Count']):,}")
    print(f"   % Total:   {row['Percentage']:.1f}%")
    print(f"   Total P&L: ₹{row['Total_PnL']:,.2f}")

# Find most costly time blocks
print("\n" + "=" * 80)
print("TOP 5 MOST COSTLY TIME BLOCKS (Largest Losses)")
print("=" * 80)

costliest_blocks = time_block_summary.nsmallest(5, 'Total_PnL')
for idx, (time_block, row) in enumerate(costliest_blocks.iterrows(), 1):
    print(f"\n{idx}. {time_block}")
    print(f"   Exits:     {int(row['Count']):,}")
    print(f"   Total P&L: ₹{row['Total_PnL']:,.2f}")
    print(f"   Avg P&L:   ₹{(row['Total_PnL'] / row['Count']):,.2f}")

# Hourly summary
print("\n" + "=" * 80)
print("HOURLY SUMMARY")
print("=" * 80)

combined_exits['Hour'] = combined_exits['Exit Time'].dt.hour
hourly_summary = combined_exits.groupby('Hour').agg({
    'Exit Time': 'count',
    'Net PnL': 'sum'
}).rename(columns={'Exit Time': 'Count', 'Net PnL': 'Total_PnL'})

hourly_summary['Percentage'] = (hourly_summary['Count'] / total_exits * 100).round(1)

print(f"\n{'Hour':<10} {'Count':>8} {'% of Total':>12} {'Total P&L':>15}")
print("-" * 80)

for hour, row in hourly_summary.iterrows():
    print(f"{hour:02d}:00     {int(row['Count']):>8} {row['Percentage']:>11.1f}% ₹{row['Total_PnL']:>14,.2f}")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)

# Save detailed results
output_file = r"c:\Users\user\projects\PerplexityCombinedTest\base_sl_time_block_analysis.csv"
time_block_summary.to_csv(output_file)
print(f"\n💾 Detailed results saved to: {output_file}")
