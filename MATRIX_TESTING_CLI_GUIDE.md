# Matrix Testing CLI Guide

## Quick Start

### Basic Command Structure
```powershell
python run_matrix_cli.py `
    --csv "path/to/data.csv" `
    --phase "Test Name" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

**Note**: Use `run_matrix_cli.py` launcher script instead of `python -m myQuant.live.matrix_forward_test` to avoid module import issues.

---

## 1. Testing Different Parameter Combinations

### EMA Optimization
```powershell
# Test 9 EMA combinations (3x3 grid)
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "EMA Optimization" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42"
```
**Result**: Tests all combinations (9×21, 9×26, 9×42, 12×21, etc.)

### Price Filter Optimization
```powershell
# Test 12 price filter combinations (3x4 grid)
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "Price Filter Test" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --price-buffer "2.0,3.0,4.0" `
    --filter-duration "120,180,240,300" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21
```
**Result**: Tests 3 buffer points × 4 durations = 12 combinations

### Stop Loss & Trailing Stop Optimization
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "SL & Trailing Optimization" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --green-bars "6,3,4,5" `
    --fixed use_consecutive_green=true 
    --fixed fast_ema=18 `
    --fixed slow_ema=42
    --fixed risk_per_trade_percent=50.0
    --fixed base_sl_points=15
    --fixed use_trail_stop=true
    --fixed trail_activation_points=5
    --fixed trail_distance_points=5
```
**Result**: 3×3×3 = 27 combinations

### Consecutive Green Bars Test
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "Green Bars Test" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --green-bars "2,3,4,5" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --fixed use_consecutive_green=true
```

---

## 2. Enabling/Disabling Indicators & Features

### Using Fixed Parameters

All configuration can be controlled with `--fixed name=value`:

### Disable ALL Optional Indicators
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "EMA Only Test" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42" `
    --fixed use_macd=false `
    --fixed use_vwap=false `
    --fixed use_rsi_filter=false `
    --fixed use_htf_trend=false `
    --fixed use_consecutive_green=false `
    --fixed price_above_exit_filter_enabled=false `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### Enable MACD Testing
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "MACD Test" `
    --macd-fast "12,15" `
    --macd-slow "26,30" `
    --macd-signal "9,11" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --fixed use_macd=true `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### Enable RSI Filter
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "RSI Filter Test" `
    --fast-ema "9,12" `
    --slow-ema "21,26" `
    --fixed use_rsi_filter=true `
    --fixed rsi_period=14 `
    --fixed rsi_overbought=70 `
    --fixed rsi_oversold=30 `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### Disable Trailing Stop
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "No Trailing Test" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42" `
    --fixed use_trail_stop=false `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### Disable Price Above Exit Filter
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "No Re-entry Filter" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42" `
    --fixed price_above_exit_filter_enabled=false `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

---

## 3. Available Parameters

### Parameter Grids (Test Multiple Values)

| CLI Flag | Parameter Name | Description | Example |
|----------|----------------|-------------|---------|
| `--fast-ema` | `fast_ema` | Fast EMA period | `--fast-ema "9,12,18"` |
| `--slow-ema` | `slow_ema` | Slow EMA period | `--slow-ema "21,26,42"` |
| `--macd-fast` | `macd_fast` | MACD fast period | `--macd-fast "12,15"` |
| `--macd-slow` | `macd_slow` | MACD slow period | `--macd-slow "26,30"` |
| `--macd-signal` | `macd_signal` | MACD signal period | `--macd-signal "9,11"` |
| `--base-sl` | `base_sl_points` | Stop loss points | `--base-sl "10,15,20"` |
| `--trail-activation` | `trail_activation_points` | Trailing stop activation | `--trail-activation "5,8,10"` |
| `--trail-distance` | `trail_distance_points` | Trailing stop distance | `--trail-distance "3,5,7"` |
| `--green-bars` | `consecutive_green_bars` | Consecutive green bars | `--green-bars "2,3,4,5"` |
| `--control-base-sl-ticks` | `control_base_sl_green_ticks` | Control base SL ticks | `--control-base-sl-ticks "3,5,7"` |
| `--price-buffer` | `price_buffer_points` | Price above exit buffer | `--price-buffer "2.0,3.0,4.0"` |
| `--filter-duration` | `filter_duration_seconds` | Price filter duration | `--filter-duration "120,180,240"` |

### Fixed Parameters (Single Value)

Use `--fixed name=value` for any parameter in `defaults.py`:

#### Risk Management
```powershell
--fixed risk_per_trade_percent=5.0
--fixed base_sl_points=15
--fixed use_trail_stop=true
--fixed trail_activation_points=5
--fixed trail_distance_points=5
```

#### Indicators (Enable/Disable)
```powershell
--fixed use_macd=true
--fixed use_vwap=true
--fixed use_rsi_filter=true
--fixed use_htf_trend=false
--fixed use_consecutive_green=true
```

#### Green Tick Control
```powershell
--fixed use_consecutive_green=true
--fixed consecutive_green_bars=3
--fixed Enable_control_base_sl_green_ticks=true
--fixed control_base_sl_green_ticks=5
```

#### Price Above Exit Filter
```powershell
--fixed price_above_exit_filter_enabled=true
--fixed price_buffer_points=3.0
--fixed filter_duration_seconds=240
```

#### MACD Parameters
```powershell
--fixed macd_fast=12
--fixed macd_slow=26
--fixed macd_signal=9
--fixed macd_histogram_threshold=0.0
```

#### RSI Parameters
```powershell
--fixed rsi_period=14
--fixed rsi_overbought=70
--fixed rsi_oversold=30
```

#### Session Filters
```powershell
--fixed enable_no_trade_zones=true
--fixed session_start_buffer_minutes=5
--fixed session_end_buffer_minutes=20
```

---

## 4. Complete Examples

### Example 1: Comprehensive EMA + Filter Test
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "EMA + Filter Optimization" `
    --description "Testing 18 combinations: 6 EMA pairs × 3 price buffers" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --fast-ema "9,12,18" `
    --slow-ema "21,42" `
    --price-buffer "2.0,3.0,4.0" `
    --fixed risk_per_trade_percent=5.0 `
    --fixed use_trail_stop=true `
    --fixed trail_activation_points=8 `
    --fixed trail_distance_points=5 `
    --fixed use_consecutive_green=true `
    --fixed consecutive_green_bars=3 `
    --fixed price_above_exit_filter_enabled=true `
    --fixed filter_duration_seconds=240
```
**Result**: 3×2×3 = 18 tests

### Example 2: Risk Management Optimization
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "Risk Management Test" `
    --description "Testing 27 risk parameter combinations" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --base-sl "10,15,20" `
    --trail-activation "5,8,10" `
    --trail-distance "3,5,7" `
    --fixed fast_ema=18 `
    --fixed slow_ema=42 `
    --fixed risk_per_trade_percent=50.0 `
    --fixed use_trail_stop=true `
    --fixed use_consecutive_green=true `
    --fixed consecutive_green_bars=3
```
**Result**: 3×3×3 = 27 tests

### Example 3: Green Bar Threshold Test
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "Green Bar Optimization" `
    --description "Testing different green bar requirements" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --green-bars "2,3,4,5,6" `
    --control-base-sl-ticks "3,5,7" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --fixed use_consecutive_green=true `
    --fixed Enable_control_base_sl_green_ticks=true `
    --fixed risk_per_trade_percent=5.0
```
**Result**: 5×3 = 15 tests

### Example 4: Minimal Test (EMA Only, No Filters)
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --phase "Pure EMA Test" `
    --description "EMA crossover only - no filters or indicators" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42" `
    --fixed use_macd=false `
    --fixed use_vwap=false `
    --fixed use_rsi_filter=false `
    --fixed use_htf_trend=false `
    --fixed use_consecutive_green=false `
    --fixed price_above_exit_filter_enabled=false `
    --fixed use_trail_stop=false `
    --fixed risk_per_trade_percent=5.0
```
**Result**: 3×3 = 9 tests with absolutely minimal configuration

---

## 5. Understanding Output

Results are saved as Excel files with 7 sheets:

1. **Summary** - Comparison table of all tests
2. **Top 10** - Best performing combinations
3. **Configuration** - Parameter details for each test
4. **Detailed Metrics** - Complete breakdown
5. **Sensitivity** - Parameter impact analysis
6. **Validation** - Quality checks
7. **Metadata** - Test information

### Output File Location
```
C:\Users\user\Desktop\BotResults\resultsMatrix\
├── matrix_ema_optimization.xlsx
├── matrix_price_filter_optimization.xlsx
└── [phase_name].xlsx
```

---

## 6. Tips & Best Practices

### Start Small
```powershell
# Test 4 combinations first
--fast-ema "9,18" --slow-ema "21,42"
```

### Estimate Time
- Each test ≈ 1.7 minutes for 32k ticks
- 10 combinations ≈ 17 minutes
- 50 combinations ≈ 85 minutes (1.4 hours)

### Use Descriptive Names
```powershell
--phase "Week 1 - EMA Optimization" `
--description "Testing fast/slow EMA with default filters"
```

### Save Different Tests
Each run creates a new Excel file with timestamp-based naming if file exists.

### Check Progress
The test shows progress in console:
- Test count and ETA
- Current test progress (%)
- Trades and P&L for each test

---

## 7. Common Patterns

### Pattern 1: Two-Stage Optimization
```powershell
# Stage 1: Find best EMAs
python -m myQuant.live.matrix_forward_test ... --fast-ema "9,12,18" --slow-ema "21,26,42"

# Stage 2: Optimize filters with best EMA
python -m myQuant.live.matrix_forward_test ... --fixed fast_ema=9 --fixed slow_ema=21 --price-buffer "2,3,4"
```

### Pattern 2: Feature Impact Analysis
```powershell
# Test 1: With filter
python -m myQuant.live.matrix_forward_test ... --fixed price_above_exit_filter_enabled=true

# Test 2: Without filter
python -m myQuant.live.matrix_forward_test ... --fixed price_above_exit_filter_enabled=false
```

### Pattern 3: Sensitivity Analysis
```powershell
# Test small increments
--trail-activation "6,7,8,9,10"
```

---

## Quick Reference Card

```powershell
# Basic syntax
python -m myQuant.live.matrix_forward_test --csv FILE [OPTIONS]

# Grid parameters (test multiple)
--fast-ema "9,12,18"
--slow-ema "21,26,42"

# Fixed parameters (single value)
--fixed risk_per_trade_percent=5.0
--fixed use_macd=true

# Enable/disable features
--fixed use_consecutive_green=true/false
--fixed price_above_exit_filter_enabled=true/false
--fixed use_trail_stop=true/false

# Output
--output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
--phase "Test Name"
--description "Test details"
```

For complete list of parameters, see `myQuant/config/defaults.py`
