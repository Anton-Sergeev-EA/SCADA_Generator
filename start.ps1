# start.ps1 - SCADA Generator: запуск на Windows
#   .\start.ps1          — демо-режим (модель установки + ИИ + интерфейс)
#   .\start.ps1 -Prod    — реальные устройства из configs\config.yaml
param([switch]$Prod)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " SCADA Generator" -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан .env из шаблона — укажите пароль PostgreSQL и SCADA_API_TOKEN" -ForegroundColor Yellow
}

if (-not (Test-Path "venv")) {
    Write-Host "1. Создание виртуального окружения..." -ForegroundColor White
    python -m venv venv
}
.\venv\Scripts\Activate.ps1

Write-Host "2. Установка зависимостей..." -ForegroundColor White
pip install -q -r requirements.txt

Write-Host "3. C++ ядро аналитики..." -ForegroundColor White
if (-not (Get-ChildItem "scada_core\ml\_native*.pyd" -ErrorAction SilentlyContinue)) {
    pip install -q pybind11 cmake ninja
    python scripts\build_native.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   Не удалось собрать (нужен Visual Studio Build Tools) — работаем на Python-ядре" -ForegroundColor Yellow
    }
}

Write-Host "4. Запуск..." -ForegroundColor White
if ($Prod) {
    python run.py --open
} else {
    python run.py --demo --open
}
