import pandas as pd

df = pd.read_excel('results/matrix_ema_optimization.xlsx', sheet_name='Summary')
print('\n=== MATRIX TEST RESULTS ===\n')
print(df.to_string())
print('\n')
print(f'Best P&L: ₹{df["total_pnl"].max():.2f}')
print(f'Worst P&L: ₹{df["total_pnl"].min():.2f}')
print(f'Avg Trades: {df["total_trades"].mean():.0f}')
print(f'Avg Win Rate: {df["win_rate"].mean()*100:.1f}%')
