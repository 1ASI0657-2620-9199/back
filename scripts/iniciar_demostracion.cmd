@echo off
setlocal
pushd "%~dp0.."
docker info >nul 2>&1
if errorlevel 1 (
    echo Abre Docker Desktop y espera a que el motor este listo.
    popd
    pause
    exit /b 1
)
docker compose up -d --wait --wait-timeout 240
if errorlevel 1 (
    echo Revisa el resultado de Docker Compose antes de abrir el panel.
    popd
    pause
    exit /b 1
)
docker compose ps
echo.
echo FinanZen: http://127.0.0.1:8080
echo Manual: repositorio report, carpeta entrega.
echo Para otra cuenta de ensayo: python scripts/preparar_demostracion.py
start "" "http://127.0.0.1:8080"
popd
pause
