"""
Entry Quality Analysis - Deep Dive into Quick Losses

Purpose: Analyze the 149 quick losses (<2 min) to understand:
1. What causes immediate losses?
2. What conditions preceded these losses?
3. What entry quality filters would prevent them?
4. Practical implementation strategies
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
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
    
    # Add derived columns
    combined['Hour'] = combined['Entry Time'].dt.hour
    combined['Minute'] = combined['Entry Time'].dt.minute
    combined['Date'] = combined['Entry Time'].dt.date
    combined['Is_Win'] = combined['Net PnL'] > 0
    combined['Is_Loss'] = combined['Net PnL'] < 0
    combined['Price_Move_Pct'] = ((combined['Exit Price'] - combined['Entry Price']) / combined['Entry Price'] * 100)
    
    return combined


# ============================================================================
# QUICK LOSS ANALYSIS
# ============================================================================

def analyze_quick_losses(df: pd.DataFrame):
    """Analyze trades that lost money quickly (<2 minutes)"""
    
    # Identify quick losses
    quick_losses = df[(df['Is_Loss']) & (df['Duration (min)'] < 2)].copy()
    
    # Compare to successful quick exits (quick wins)
    quick_wins = df[(df['Is_Win']) & (df['Duration (min)'] < 2)].copy()
    
    # All other losses (longer duration)
    slow_losses = df[(df['Is_Loss']) & (df['Duration (min)'] >= 2)].copy()
    
    print("\n" + "=" * 100)
    print("QUICK LOSS ANALYSIS - Understanding Immediate Failures")
    print("=" * 100)
    
    print(f"\n📊 TRADE BREAKDOWN:")
    print(f"  Quick Losses (<2 min):  {len(quick_losses):,} trades (₹{quick_losses['Net PnL'].sum():,.2f})")
    print(f"  Quick Wins (<2 min):    {len(quick_wins):,} trades (₹{quick_wins['Net PnL'].sum():,.2f})")
    print(f"  Slow Losses (≥2 min):   {len(slow_losses):,} trades (₹{slow_losses['Net PnL'].sum():,.2f})")
    
    # Average loss comparison
    print(f"\n💰 AVERAGE LOSS COMPARISON:")
    print(f"  Quick Loss Avg:  ₹{quick_losses['Net PnL'].mean():,.2f}")
    print(f"  Slow Loss Avg:   ₹{slow_losses['Net PnL'].mean():,.2f}")
    print(f"  Difference:      {abs(quick_losses['Net PnL'].mean() / slow_losses['Net PnL'].mean() - 1) * 100:.1f}% {'worse' if abs(quick_losses['Net PnL'].mean()) > abs(slow_losses['Net PnL'].mean()) else 'better'}")
    
    # Exit reason breakdown
    print(f"\n🚪 EXIT REASONS FOR QUICK LOSSES:")
    exit_breakdown = quick_losses['Exit Reason'].value_counts()
    for reason, count in exit_breakdown.items():
        pct = count / len(quick_losses) * 100
        avg_loss = quick_losses[quick_losses['Exit Reason'] == reason]['Net PnL'].mean()
        print(f"  {reason:<20} {count:>4} trades ({pct:>5.1f}%)  Avg: ₹{avg_loss:>10,.2f}")
    
    # Time distribution
    print(f"\n⏰ HOURLY DISTRIBUTION OF QUICK LOSSES:")
    hourly = quick_losses.groupby('Hour').agg({
        'Net PnL': ['count', 'sum', 'mean']
    }).round(2)
    hourly.columns = ['Count', 'Total', 'Avg']
    print(hourly.to_string())
    
    # Price movement analysis
    print(f"\n📉 PRICE MOVEMENT ANALYSIS:")
    print(f"  Avg Price Drop:     {quick_losses['Price_Move_Pct'].mean():.2f}%")
    print(f"  Median Price Drop:  {quick_losses['Price_Move_Pct'].median():.2f}%")
    print(f"  Worst Price Drop:   {quick_losses['Price_Move_Pct'].min():.2f}%")
    
    # Compare to quick wins
    if len(quick_wins) > 0:
        print(f"\n📈 COMPARISON: QUICK WINS vs QUICK LOSSES:")
        print(f"  Quick Wins Avg Move:    {quick_wins['Price_Move_Pct'].mean():>6.2f}%")
        print(f"  Quick Losses Avg Move:  {quick_losses['Price_Move_Pct'].mean():>6.2f}%")
        print(f"  Entry Price Diff:       ₹{quick_wins['Entry Price'].mean() - quick_losses['Entry Price'].mean():,.2f}")
    
    return quick_losses, quick_wins, slow_losses


def identify_entry_quality_patterns(quick_losses: pd.DataFrame, all_trades: pd.DataFrame):
    """Identify patterns that could be filtered"""
    
    print("\n" + "=" * 100)
    print("ENTRY QUALITY PATTERNS - What Could We Have Avoided?")
    print("=" * 100)
    
    # Pattern 1: Consecutive entries (chasing)
    quick_losses = quick_losses.sort_values('Entry Time')
    quick_losses['Time_Since_Prev'] = quick_losses['Entry Time'].diff().dt.total_seconds()
    
    rapid_entries = quick_losses[quick_losses['Time_Since_Prev'] < 60]  # Within 1 minute of previous
    
    print(f"\n1️⃣  RAPID CONSECUTIVE ENTRIES (chasing):")
    print(f"    Count: {len(rapid_entries)} quick losses followed another entry within 60 seconds")
    print(f"    Impact: ₹{rapid_entries['Net PnL'].sum():,.2f}")
    print(f"    → Filter: Wait 60+ seconds between entries")
    
    # Pattern 2: Zero-duration trades (immediate exit)
    instant_losses = quick_losses[quick_losses['Duration (min)'] == 0]
    
    print(f"\n2️⃣  INSTANT EXITS (0 minutes duration):")
    print(f"    Count: {len(instant_losses)} trades exited immediately")
    print(f"    Impact: ₹{instant_losses['Net PnL'].sum():,.2f}")
    print(f"    Avg Loss: ₹{instant_losses['Net PnL'].mean():,.2f}")
    print(f"    → Likely: Entry at exact SL price or gaps")
    print(f"    → Filter: Require price > SL by safety buffer (2-3 points)")
    
    # Pattern 3: Large position sizes in quick losses
    avg_qty_all = all_trades['Qty'].mean()
    avg_qty_quick_loss = quick_losses['Qty'].mean()
    
    print(f"\n3️⃣  POSITION SIZING:")
    print(f"    Avg Qty (all trades):     {avg_qty_all:.0f}")
    print(f"    Avg Qty (quick losses):   {avg_qty_quick_loss:.0f}")
    print(f"    Difference:                {((avg_qty_quick_loss / avg_qty_all) - 1) * 100:+.1f}%")
    
    # Pattern 4: Price levels (entry price clustering)
    print(f"\n4️⃣  ENTRY PRICE ANALYSIS:")
    print(f"    Avg Entry Price (quick losses): ₹{quick_losses['Entry Price'].mean():,.2f}")
    print(f"    Std Dev:                         ₹{quick_losses['Entry Price'].std():,.2f}")
    
    # High volatility entries (large price range in same minute)
    quick_losses['Entry_Minute'] = quick_losses['Entry Time'].dt.floor('T')
    minute_groups = quick_losses.groupby('Entry_Minute')['Entry Price'].agg(['min', 'max', 'count'])
    minute_groups['Range'] = minute_groups['max'] - minute_groups['min']
    minute_groups['Range_Pct'] = (minute_groups['Range'] / minute_groups['min'] * 100)
    
    volatile_minutes = minute_groups[minute_groups['Range_Pct'] > 1.0]  # >1% range in 1 minute
    
    print(f"\n5️⃣  HIGH VOLATILITY ENTRIES:")
    print(f"    Minutes with >1% price range: {len(volatile_minutes)}")
    print(f"    → Trades in volatile minutes likely fail faster")
    print(f"    → Filter: Skip entry if price range in last minute > 1%")
    
    return {
        'rapid_entries': len(rapid_entries),
        'instant_exits': len(instant_losses),
        'volatile_minutes': len(volatile_minutes)
    }


# ============================================================================
# PRACTICAL FILTER RECOMMENDATIONS
# ============================================================================

def generate_filter_recommendations(quick_losses: pd.DataFrame, all_trades: pd.DataFrame):
    """Generate practical, implementable filter recommendations"""
    
    print("\n" + "=" * 100)
    print("PRACTICAL ENTRY QUALITY FILTERS - Implementation Ready")
    print("=" * 100)
    
    print("\n" + "=" * 60)
    print("FILTER #1: Entry Cooldown Period")
    print("=" * 60)
    
    print("""
PROBLEM: Chasing losses with rapid re-entries
SOLUTION: Enforce minimum time between entries

Implementation:
""")
    
    print("""```python
class ModularIntradayStrategy:
    def __init__(self, ...):
        self.last_entry_time = None
        self.min_entry_cooldown_seconds = 60  # From defaults.py
    
    def evaluate_entry(self, tick):
        # ... existing entry logic ...
        
        # FILTER 1: Entry cooldown
        if self.last_entry_time:
            time_since_last = (tick['timestamp'] - self.last_entry_time).total_seconds()
            if time_since_last < self.min_entry_cooldown_seconds:
                logger.debug(f"Entry blocked: Cooldown active "
                           f"({time_since_last:.0f}s / {self.min_entry_cooldown_seconds}s)")
                return None
        
        # ... rest of entry logic ...
        
        if entry_signal:
            self.last_entry_time = tick['timestamp']
            return entry_signal
```""")
    
    print("\nConfiguration (add to defaults.py):")
    print("""```python
"strategy": {
    "min_entry_cooldown_seconds": 60,  # Wait 60s between entries
}
```""")
    
    # Estimate impact
    quick_losses['Time_Since_Prev'] = quick_losses['Entry Time'].diff().dt.total_seconds()
    prevented = quick_losses[quick_losses['Time_Since_Prev'] < 60]
    
    print(f"\nESTIMATED IMPACT:")
    print(f"  Trades Prevented: {len(prevented)}")
    print(f"  Losses Avoided:   ₹{prevented['Net PnL'].sum():,.2f}")
    print(f"  Benefit:          {len(prevented) / len(quick_losses) * 100:.1f}% of quick losses prevented")
    
    # ========================================================================
    
    print("\n" + "=" * 60)
    print("FILTER #2: Price Safety Buffer")
    print("=" * 60)
    
    print("""
PROBLEM: Entry at or near SL price → Instant exit
SOLUTION: Require minimum distance from SL before entry

Implementation:
""")
    
    print("""```python
class ModularIntradayStrategy:
    def __init__(self, ...):
        self.entry_safety_buffer_points = 2.0  # From defaults.py
    
    def evaluate_entry(self, tick):
        current_price = tick['price']
        
        # Calculate where SL would be
        proposed_sl = current_price - self.base_sl_points  # For long-only
        
        # FILTER 2: Price safety buffer
        # Require price to be safely above SL
        min_safe_price = proposed_sl + self.entry_safety_buffer_points
        
        if current_price < min_safe_price:
            logger.debug(f"Entry blocked: Price ₹{current_price} too close to SL "
                       f"₹{proposed_sl} (need ₹{min_safe_price}+)")
            return None
        
        # ... rest of entry logic ...
```""")
    
    print("\nConfiguration (add to defaults.py):")
    print("""```python
"risk": {
    "entry_safety_buffer_points": 2.0,  # Require 2pts cushion above SL
}
```""")
    
    instant_exits = quick_losses[quick_losses['Duration (min)'] == 0]
    
    print(f"\nESTIMATED IMPACT:")
    print(f"  Instant Exits: {len(instant_exits)}")
    print(f"  Losses Avoided: ₹{instant_exits['Net PnL'].sum():,.2f}")
    print(f"  Benefit: Prevents gap-down entries")
    
    # ========================================================================
    
    print("\n" + "=" * 60)
    print("FILTER #3: Consecutive Loss Limiter")
    print("=" * 60)
    
    print("""
PROBLEM: Multiple quick losses in succession drain capital
SOLUTION: Pause/reduce after N consecutive quick losses

Implementation:
""")
    
    print("""```python
class ModularIntradayStrategy:
    def __init__(self, ...):
        self.consecutive_quick_losses = 0
        self.max_consecutive_quick_losses = 2  # From defaults.py
        self.quick_loss_pause_seconds = 300  # 5 minutes
        self.pause_until = None
    
    def on_position_close(self, position, exit_reason, exit_time):
        # Track quick losses
        duration_minutes = (exit_time - position.entry_time).total_seconds() / 60
        
        if position.pnl < 0 and duration_minutes < 2:
            self.consecutive_quick_losses += 1
            logger.warning(f"Quick loss #{self.consecutive_quick_losses}: "
                         f"₹{position.pnl:.2f} in {duration_minutes:.1f} min")
            
            if self.consecutive_quick_losses >= self.max_consecutive_quick_losses:
                self.pause_until = exit_time + timedelta(seconds=self.quick_loss_pause_seconds)
                logger.warning(f"🛑 TRADING PAUSED until {self.pause_until} "
                             f"(after {self.consecutive_quick_losses} quick losses)")
        else:
            # Reset on any non-quick-loss
            self.consecutive_quick_losses = 0
    
    def evaluate_entry(self, tick):
        # FILTER 3: Pause after consecutive quick losses
        if self.pause_until and tick['timestamp'] < self.pause_until:
            remaining = (self.pause_until - tick['timestamp']).total_seconds()
            logger.debug(f"Entry blocked: Trading paused ({remaining:.0f}s remaining)")
            return None
        
        # ... rest of entry logic ...
```""")
    
    print("\nConfiguration (add to defaults.py):")
    print("""```python
"risk": {
    "max_consecutive_quick_losses": 2,      # Pause after 2 quick losses
    "quick_loss_pause_seconds": 300,        # Pause for 5 minutes
    "quick_loss_threshold_minutes": 2.0,    # Define "quick" as <2 min
}
```""")
    
    # Simulate impact
    df_sorted = all_trades.sort_values('Exit Time').copy()
    df_sorted['Is_Quick_Loss'] = (df_sorted['Is_Loss']) & (df_sorted['Duration (min)'] < 2)
    
    # Find sequences of 3+ consecutive quick losses
    consecutive_count = 0
    sequences = []
    
    for idx, row in df_sorted.iterrows():
        if row['Is_Quick_Loss']:
            consecutive_count += 1
        else:
            if consecutive_count >= 3:
                sequences.append(consecutive_count)
            consecutive_count = 0
    
    if sequences:
        print(f"\nESTIMATED IMPACT:")
        print(f"  Sequences of 3+ quick losses: {len(sequences)}")
        print(f"  Longest sequence: {max(sequences)} consecutive")
        print(f"  → Would have paused trading {len(sequences)} times")
        print(f"  → Prevented ~{sum(sequences) - len(sequences) * 2} additional quick losses")
    
    # ========================================================================
    
    print("\n" + "=" * 60)
    print("FILTER #4: Time-of-Day Restrictions (Optional)")
    print("=" * 60)
    
    print("""
PROBLEM: 14:00 hour shows worst performance
SOLUTION: Tighten entry requirements during poor hours

Implementation:
""")
    
    print("""```python
class ModularIntradayStrategy:
    def __init__(self, ...):
        self.poor_performance_hours = [14]  # From defaults.py
        self.skip_poor_hours = False        # Toggle
        self.tight_sl_poor_hours = True     # Use tighter SL instead
        self.poor_hour_sl_multiplier = 0.67 # 10pts instead of 15pts
    
    def evaluate_entry(self, tick):
        current_hour = tick['timestamp'].hour
        
        # FILTER 4A: Skip poor hours entirely (if enabled)
        if self.skip_poor_hours and current_hour in self.poor_performance_hours:
            logger.debug(f"Entry blocked: Poor performance hour ({current_hour}:00)")
            return None
        
        # FILTER 4B: Tighter SL during poor hours (alternative)
        sl_points = self.base_sl_points
        if self.tight_sl_poor_hours and current_hour in self.poor_performance_hours:
            sl_points = self.base_sl_points * self.poor_hour_sl_multiplier
            logger.debug(f"Tighter SL for hour {current_hour}: {sl_points:.1f} points")
        
        # Use adjusted sl_points in position sizing...
```""")
    
    print("\nConfiguration (add to defaults.py):")
    print("""```python
"strategy": {
    "poor_performance_hours": [14],         # Hours to restrict
    "skip_poor_hours": False,               # Set True to skip entirely
    "tight_sl_poor_hours": True,            # Use tighter SL instead
    "poor_hour_sl_multiplier": 0.67,        # 67% of normal SL
}
```""")
    
    poor_hour_losses = quick_losses[quick_losses['Hour'] == 14]
    
    print(f"\nESTIMATED IMPACT (Hour 14 only):")
    print(f"  Quick losses in hour 14: {len(poor_hour_losses)}")
    print(f"  Losses avoided: ₹{poor_hour_losses['Net PnL'].sum():,.2f}")
    print(f"  → Option A (skip): Prevent {len(poor_hour_losses)} losses")
    print(f"  → Option B (tighter SL): Reduce losses by ~33%")


def simulate_combined_filters(df: pd.DataFrame):
    """Simulate all filters applied together"""
    
    print("\n" + "=" * 100)
    print("COMBINED FILTER SIMULATION - Total Expected Impact")
    print("=" * 100)
    
    quick_losses = df[(df['Is_Loss']) & (df['Duration (min)'] < 2)].copy()
    quick_losses = quick_losses.sort_values('Entry Time')
    
    # Calculate time since previous entry
    quick_losses['Time_Since_Prev'] = quick_losses['Entry Time'].diff().dt.total_seconds()
    
    # Filter 1: Cooldown (60s)
    prevented_cooldown = quick_losses[quick_losses['Time_Since_Prev'] < 60]
    
    # Filter 2: Instant exits (0 duration)
    prevented_instant = quick_losses[quick_losses['Duration (min)'] == 0]
    
    # Filter 3: Hour 14
    prevented_hour14 = quick_losses[quick_losses['Hour'] == 14]
    
    # Combined (avoiding double-counting)
    all_prevented_ids = set()
    all_prevented_ids.update(prevented_cooldown.index)
    all_prevented_ids.update(prevented_instant.index)
    all_prevented_ids.update(prevented_hour14.index)
    
    prevented_combined = quick_losses.loc[list(all_prevented_ids)]
    
    print(f"\n📊 INDIVIDUAL FILTER IMPACT:")
    print(f"  Filter 1 (Cooldown):      {len(prevented_cooldown):>3} trades, ₹{prevented_cooldown['Net PnL'].sum():>10,.2f}")
    print(f"  Filter 2 (Safety Buffer): {len(prevented_instant):>3} trades, ₹{prevented_instant['Net PnL'].sum():>10,.2f}")
    print(f"  Filter 4 (Hour 14):       {len(prevented_hour14):>3} trades, ₹{prevented_hour14['Net PnL'].sum():>10,.2f}")
    
    print(f"\n💰 COMBINED FILTERS (avoiding double-count):")
    print(f"  Total Quick Losses:       {len(quick_losses)} trades, ₹{quick_losses['Net PnL'].sum():,.2f}")
    print(f"  Would Be Prevented:       {len(prevented_combined)} trades, ₹{prevented_combined['Net PnL'].sum():,.2f}")
    print(f"  Remaining Quick Losses:   {len(quick_losses) - len(prevented_combined)} trades")
    print(f"  Prevention Rate:          {len(prevented_combined) / len(quick_losses) * 100:.1f}%")
    
    # Overall system impact
    total_losses = df['Net PnL'][df['Is_Loss']].sum()
    prevented_pct = abs(prevented_combined['Net PnL'].sum() / total_losses * 100)
    
    print(f"\n🎯 SYSTEM-WIDE IMPACT:")
    print(f"  Total System Losses:      ₹{total_losses:,.2f}")
    print(f"  Prevented by Filters:     ₹{prevented_combined['Net PnL'].sum():,.2f}")
    print(f"  Reduction:                {prevented_pct:.1f}% of all losses")
    
    # New system metrics
    new_total_pnl = df['Net PnL'].sum() - prevented_combined['Net PnL'].sum()
    current_pnl = df['Net PnL'].sum()
    
    print(f"\n📈 PROJECTED SYSTEM PERFORMANCE:")
    print(f"  Current Total P&L:        ₹{current_pnl:,.2f}")
    print(f"  With Entry Filters:       ₹{new_total_pnl:,.2f}")
    print(f"  Improvement:              ₹{new_total_pnl - current_pnl:,.2f}")
    
    return prevented_combined


# ============================================================================
# MAIN
# ============================================================================

def main():
    csv_folder = r"c:\Users\user\projects\PerplexityCombinedTest\csvResults"
    
    print("\nLoading trade data...")
    df = load_all_trades(csv_folder)
    
    # Analyze quick losses
    quick_losses, quick_wins, slow_losses = analyze_quick_losses(df)
    
    # Identify patterns
    patterns = identify_entry_quality_patterns(quick_losses, df)
    
    # Generate recommendations
    generate_filter_recommendations(quick_losses, df)
    
    # Simulate combined impact
    prevented = simulate_combined_filters(df)
    
    print("\n" + "=" * 100)
    print("✅ NEXT STEPS:")
    print("=" * 100)
    print("""
1. Add filter parameters to defaults.py (see configurations above)
2. Implement filters in liveStrategy.py evaluate_entry() method
3. Test with file simulation first (verify filter activation)
4. Monitor filter activation frequency in logs
5. Adjust thresholds based on live performance
6. Consider A/B testing (enable filters for 50% of days)
    
Priority Implementation Order:
  1. Filter #2 (Safety Buffer) - Easiest, prevents instant losses
  2. Filter #1 (Cooldown) - Simple time check
  3. Filter #3 (Consecutive Loss Limiter) - More complex but high impact
  4. Filter #4 (Time Restrictions) - Optional, requires more testing
""")
    
    print("\n" + "=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
