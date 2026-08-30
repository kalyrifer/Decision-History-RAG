# Запуск Decision History RAG
# 1) Поднимает Postgres, 2) проверяет окружение, 3) открывает Streamlit UI.

$ErrorActionPreference = "Stop"

Write-Host "=== Decision History RAG ===" -ForegroundColor Cyan

Write-Host "[1/3] Поднимаю Postgres (docker compose)..."
docker compose up -d
if (-not $?) { throw "docker compose up -d завершился с ошибкой" }
Write-Host "      Postgres запущен: docker ps"

Write-Host "[2/3] Проверяю окружение..."
& ".venv\Scripts\python.exe" scripts\check_env.py
if (-not $?) { Write-Warning "Есть предупреждения окружения (см. выше). Продолжаю..." }

Write-Host "[3/3] Запускаю UI..."
Write-Host "      Открой http://localhost:8501 в браузере" -ForegroundColor Green
& ".venv\Scripts\python.exe" -m streamlit run ui\app.py
