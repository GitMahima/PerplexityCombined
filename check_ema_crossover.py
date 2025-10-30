"""
Simple diagnostic: Check if EMAs are calculated and if crossover happens
"""
import pandas as pd

# Simulate EMA calculation
data = []
for i in range(51):
    price = 100.0 + i
    data.append({'tick': i, 'price': price})

df = pd.DataFrame(data)

# Calculate EMAs manually
fast_span = 3
slow_span = 5

df['ema_fast'] = df['price'].ewm(span=fast_span, adjust=False).mean()
df['ema_slow'] = df['price'].ewm(span=slow_span, adjust=False).mean()
df['fast_above_slow'] = df['ema_fast'] > df['ema_slow']

print("="*80)
print("EMA CALCULATION CHECK")
print("="*80)
print("\nFirst 15 ticks:")
print(df[['tick', 'price', 'ema_fast', 'ema_slow', 'fast_above_slow']].head(15).to_string())

print("\n" + "="*80)
print(f"Does fast EVER cross above slow? {df['fast_above_slow'].any()}")
print(f"First crossover at tick: {df[df['fast_above_slow']].index[0] if df['fast_above_slow'].any() else 'NEVER'}")
print("="*80)
