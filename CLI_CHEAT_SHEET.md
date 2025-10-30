# Matrix Testing CLI - Quick Cheat Sheet

## Basic Command
```powershell
python -m myQuant.live.matrix_forward_test --csv DATA.csv --fast-ema "9,12" --slow-ema "21,26"
```

## Enable/Disable Features

### Disable a Feature
```powershell
--fixed use_consecutive_green=false
--fixed use_macd=false
--fixed use_trail_stop=false
--fixed price_above_exit_filter_enabled=false
```

### Enable a Feature
```powershell
--fixed use_consecutive_green=true
--fixed use_macd=true
--fixed use_rsi_filter=true
```

## Common Tests

### 1. EMA Optimization (9 tests)
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --fast-ema "9,12,18" `
    --slow-ema "21,26,42" `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### 2. Price Filter Test (12 tests)
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --price-buffer "2.0,3.0,4.0" `
    --filter-duration "120,180,240,300" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### 3. Stop Loss Test (27 tests)
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --base-sl "10,15,20" `
    --trail-activation "5,8,10" `
    --trail-distance "3,5,7" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

### 4. Green Bars Test (15 tests)
```powershell
python -m myQuant.live.matrix_forward_test `
    --csv "C:\Users\user\projects\PerplexityCombinedTest\aTest.csv" `
    --green-bars "2,3,4,5,6" `
    --control-base-sl-ticks "3,5,7" `
    --fixed fast_ema=9 `
    --fixed slow_ema=21 `
    --fixed use_consecutive_green=true `
    --output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
```

## Available Parameters

### Grid Parameters (Multiple Values)
- `--fast-ema` → Fast EMA period
- `--slow-ema` → Slow EMA period
- `--macd-fast` → MACD fast
- `--macd-slow` → MACD slow
- `--macd-signal` → MACD signal
- `--base-sl` → Stop loss points
- `--trail-activation` → Trail activation
- `--trail-distance` → Trail distance
- `--green-bars` → Green bar count
- `--price-buffer` → Re-entry buffer
- `--filter-duration` → Re-entry duration

### Fixed Parameters (Single Value)
```powershell
--fixed risk_per_trade_percent=5.0
--fixed use_macd=true/false
--fixed use_vwap=true/false
--fixed use_rsi_filter=true/false
--fixed use_consecutive_green=true/false
--fixed price_above_exit_filter_enabled=true/false
--fixed use_trail_stop=true/false
```

## Output Control
```powershell
--output-dir "C:\Users\user\Desktop\BotResults\resultsMatrix"
--phase "Test Name"
--description "What you're testing"
```

## Time Estimates
- 1 test ≈ 1.7 minutes (32k ticks)
- 10 tests ≈ 17 minutes
- 50 tests ≈ 1.4 hours

## Full Documentation
See: `MATRIX_TESTING_CLI_GUIDE.md`
