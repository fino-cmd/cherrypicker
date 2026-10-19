@echo off
setlocal
title CherryPick - launcher

rem ======================================================================
rem  CherryPick - lanzador
rem
rem  Levanta los dos servidores y abre la app en el navegador:
rem    . backend FastAPI  (API MLB + endpoint AltLines NBA) -> puerto 8002
rem    . servidor estatico del frontend                     -> puerto 5500
rem
rem  IMPORTANTE: abri siempre la app por
rem        http://localhost:5500/index.html
rem  y NUNCA con doble clic en index.html. Con file:// el navegador no puede
rem  hablar con el backend y todo falla con URLs tipo "http://:8002".
rem
rem  Solo escucha en 127.0.0.1 (tu maquina), no queda expuesto a la red.
rem ======================================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] No encuentro "python" en el PATH.
    echo         Instala Python o agregalo al PATH y volve a intentar.
    echo.
    pause
    exit /b 1
)

echo.
echo  CherryPick
echo  ==========
echo  Raiz del proyecto: %ROOT%
echo.

echo  [1/3] Backend FastAPI  -^>  http://localhost:8002
start "CherryPick backend (8002)" /d "%ROOT%\backend" cmd /k python -m uvicorn main:app --host 127.0.0.1 --port 8002

echo  [2/3] Frontend estatico -^>  http://localhost:5500
start "CherryPick frontend (5500)" /d "%ROOT%" cmd /k python -m http.server 5500 --bind 127.0.0.1

echo  [3/3] Esperando a que el backend cargue el modelo (unos 15s)...
timeout /t 15 /nobreak >nul

start "" "http://localhost:5500/index.html"

echo.
echo  Listo. La app se abrio en el navegador.
echo.
echo  Para apagar todo: cerra las dos ventanas negras que se abrieron
echo  ("CherryPick backend" y "CherryPick frontend").
echo.
pause
