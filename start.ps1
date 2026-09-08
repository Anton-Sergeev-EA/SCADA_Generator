# start.ps1 - SCADA Generator Auto-start Script

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "🚀 SCADA Generator - Auto Startup" -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Host "⚠ .env file not found, creating from template..." -ForegroundColor Yellow
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "✅ .env file created from template" -ForegroundColor Green
        Write-Host "📝 Please edit .env file with your database password" -ForegroundColor Cyan
    } else {
        Write-Host "❌ .env.example not found" -ForegroundColor Red
        exit 1
    }
}

# Activate virtual environment
Write-Host "`n1. Activating virtual environment..." -ForegroundColor White
if (Test-Path "venv") {
    .\venv\Scripts\Activate.ps1
    Write-Host "   ✅ Virtual environment activated" -ForegroundColor Green
} else {
    Write-Host "   ⚠ Virtual environment not found" -ForegroundColor Yellow
    Write-Host "   Run: python -m venv venv" -ForegroundColor Cyan
}

# Check dependencies
Write-Host "`n2. Checking dependencies..." -ForegroundColor White
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt
    Write-Host "   ✅ Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "   ❌ requirements.txt not found" -ForegroundColor Red
    exit 1
}

# Check PostgreSQL
Write-Host "`n3. Checking PostgreSQL..." -ForegroundColor White
$pgService = Get-Service postgresql* -ErrorAction SilentlyContinue
if ($pgService) {
    if ($pgService.Status -eq 'Running') {
        Write-Host "   ✅ PostgreSQL is running" -ForegroundColor Green
    } else {
        Write-Host "   ⚠ PostgreSQL is not running, starting..." -ForegroundColor Yellow
        Start-Service $pgService.Name
        Start-Sleep 3
    }
} else {
    Write-Host "   ⚠ PostgreSQL service not found" -ForegroundColor Yellow
}

# Start application
Write-Host "`n4. Starting SCADA Generator..." -ForegroundColor White
Write-Host "   ▶ Launching background polling service (run.py)..." -ForegroundColor Yellow
Write-Host "   Press Ctrl+C to stop." -ForegroundColor Cyan

python run.py

Write-Host "`n==================================================" -ForegroundColor Cyan
Write-Host "🔚 SCADA Generator finished" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan