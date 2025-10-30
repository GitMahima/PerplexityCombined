# Cleanup Script for Old Directories
# Run this AFTER verifying matrix testing works with new location

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Directory Cleanup Script" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

$resultsDir = "C:\Users\user\projects\PerplexityCombinedTest\myQuant\results"
$logsDir = "C:\Users\user\projects\PerplexityCombinedTest\myQuant\logs"

# Check results directory
if (Test-Path $resultsDir) {
    $resultsCount = (Get-ChildItem $resultsDir -File -Recurse).Count
    Write-Host "📁 Results Directory: $resultsDir" -ForegroundColor Yellow
    Write-Host "   Files: $resultsCount" -ForegroundColor Gray
    
    if ($resultsCount -eq 0) {
        Write-Host "   ✅ EMPTY - Safe to remove" -ForegroundColor Green
        $removeResults = Read-Host "   Remove empty results directory? (y/n)"
        if ($removeResults -eq 'y') {
            Remove-Item $resultsDir -Force
            Write-Host "   ✅ Removed!" -ForegroundColor Green
        }
    } else {
        Write-Host "   ⚠️  Contains files - review before removing" -ForegroundColor Yellow
    }
} else {
    Write-Host "📁 Results Directory: Not found (already removed?)" -ForegroundColor Gray
}

Write-Host ""

# Check logs directory
if (Test-Path $logsDir) {
    $logsSize = (Get-ChildItem $logsDir -File -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
    $logsCount = (Get-ChildItem $logsDir -File -Recurse).Count
    Write-Host "📁 Logs Directory: $logsDir" -ForegroundColor Yellow
    Write-Host "   Files: $logsCount" -ForegroundColor Gray
    Write-Host "   Size: $([math]::Round($logsSize, 2)) MB" -ForegroundColor Gray
    Write-Host "   ⚠️  Contains old logs (Oct 8-27, 2025)" -ForegroundColor Yellow
    Write-Host "   ℹ️  Logging is now DISABLED (console-only mode)" -ForegroundColor Cyan
    
    $removeLogs = Read-Host "   Remove old logs directory? (y/n)"
    if ($removeLogs -eq 'y') {
        Remove-Item $logsDir -Recurse -Force
        Write-Host "   ✅ Removed!" -ForegroundColor Green
    } else {
        Write-Host "   ℹ️  Kept for now" -ForegroundColor Gray
    }
} else {
    Write-Host "📁 Logs Directory: Not found (already removed?)" -ForegroundColor Gray
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Cleanup Complete!" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "✅ Matrix results now save to: C:\Users\user\Desktop\BotResults\resultsMatrix" -ForegroundColor Green
Write-Host "✅ Forward test results save to: C:\Users\user\Desktop\BotResults\results" -ForegroundColor Green
