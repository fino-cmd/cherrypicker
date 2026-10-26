@echo off
setlocal
title CherryPick - launcher

rem ======================================================================
rem  CherryPick - lanzador
rem
rem  Levanta los dos servidores y abre la app en el navegador:
rem    . backend FastAPI  (MLB, NBA y Futbol)  -> puerto 8002
rem    . servidor estatico del frontend        -> puerto 5500
rem
rem  Si alguno ya esta corriendo, NO levanta otro (dos backends a la vez
rem  duplican el vigia de formaciones y las consultas a API-Football).
rem
rem  Escuchan tambien en la red de tu casa, para usar la app desde el
rem  celular: abri la direccion "Desde el celular" que muestra esta ventana
rem  (el celular tiene que estar en el mismo Wi-Fi).
rem
rem  En la PC abri siempre http://localhost:5500/index.html
rem  (nunca con doble clic en index.html: con file:// no anda el backend).
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

rem IP de la PC en la red de la casa (para el celular)
set "LANIP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback|VirtualBox|VMware' } | Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress"`) do set "LANIP=%%i"

echo.
echo  CherryPick
echo  ==========
echo  Raiz del proyecto: %ROOT%
echo.

set "BACK_UP=0"
netstat -ano | findstr /r /c:":8002 .*LISTENING" >nul && set "BACK_UP=1"
if "%BACK_UP%"=="1" (
    echo  [1/3] Backend: ya hay uno corriendo en el puerto 8002, no levanto otro.
) else (
    echo  [1/3] Backend FastAPI  -^>  puerto 8002
    start "CherryPick backend (8002)" /d "%ROOT%\backend" cmd /k python -m uvicorn main:app --host 0.0.0.0 --port 8002
)

set "FRONT_UP=0"
netstat -ano | findstr /r /c:":5500 .*LISTENING" >nul && set "FRONT_UP=1"
if "%FRONT_UP%"=="1" (
    echo  [2/3] Frontend: ya hay uno corriendo en el puerto 5500, no levanto otro.
) else (
    echo  [2/3] Frontend estatico -^>  puerto 5500
    start "CherryPick frontend (5500)" /d "%ROOT%" cmd /k python -m http.server 5500 --bind 0.0.0.0
)

if "%BACK_UP%"=="0" (
    echo  [3/3] Esperando a que el backend cargue el modelo ^(unos 15s^)...
    timeout /t 15 /nobreak >nul
) else (
    echo  [3/3] Abriendo la app...
)

start "" "http://localhost:5500/index.html"

echo.
echo  Listo. La app se abrio en el navegador.
echo.
echo    En esta PC:        http://localhost:5500/index.html
if defined LANIP echo    Desde el celular:  http://%LANIP%:5500/index.html   ^(mismo Wi-Fi^)
echo.
echo  Para apagar todo: cerra las ventanas negras "CherryPick backend" y
echo  "CherryPick frontend" (si las abrio este lanzador).
echo.
pause
