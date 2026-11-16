@echo off
rem ============================================================
rem   Bot rasxodov - ustanovka v odin klik (na PK s malstore-bot)
rem   1) sprashivaet token novogo bota  2) avtozapusk s Windows
rem   3) pravilo brandmauera dlya porta 8090  4) zapuskaet bota
rem ============================================================
setlocal EnableDelayedExpansion
title Bot rasxodov - ustanovka
cd /d "%~dp0.."
set "ROOT=%cd%"

if not exist "%ROOT%\venv\Scripts\python.exe" (
    echo [!] Ne nayden venv. Snachala zapustite install.bat ot malstore-bot.
    pause & exit /b 1
)

rem --- 1. config.json ---
if not exist "%ROOT%\expenses\config.json" (
    echo.
    echo Sozdayte NOVOGO bota: Telegram - @BotFather - /newbot
    echo i vstavte syuda ego token ^(ne token malstore-bot^).
    set /p "TOKEN=Token: "
    if "!TOKEN!"=="" ( echo Token pustoy. & pause & exit /b 1 )
    powershell -NoProfile -Command "$c=[ordered]@{bot_token=$env:TOKEN; owner_ids=@(); port=8090; public_url=''; secret=''; usd_rate=12500; daily_report_time='22:00'}; $c | ConvertTo-Json | Set-Content -Encoding UTF8 '%ROOT%\expenses\config.json'"
    echo [ok] expenses\config.json sozdan.
) else (
    echo [i] expenses\config.json uzhe est.
)

rem --- 2. avtozapusk ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ws=New-Object -ComObject WScript.Shell; $lnk=Join-Path ([Environment]::GetFolderPath('Startup')) 'MalStore Rasxody.lnk'; $s=$ws.CreateShortcut($lnk); $s.TargetPath='%ROOT%\expenses\start_expenses.bat'; $s.WorkingDirectory='%ROOT%'; $s.WindowStyle=7; $s.Save(); Write-Host '[ok] Avtozapusk dobavlen.' } catch { Write-Host ('[!] Avtozapusk ne dobavlen: '+$_.Exception.Message) }"

rem --- 3. brandmauer (nuzhny prava administratora; esli net - Windows sprosit sam pri pervom zapuske) ---
netsh advfirewall firewall show rule name="MalStore Rasxody 8090" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="MalStore Rasxody 8090" dir=in action=allow protocol=TCP localport=8090 profile=private >nul 2>&1
    if errorlevel 1 ( echo [i] Pravilo brandmauera ne dobavleno ^(net prav admina^). Pri pervom zapuske nazhmite "Razreshit". ) else ( echo [ok] Port 8090 otkryt dlya domashney seti i Tailscale. )
)

rem --- 4. Tailscale ---
where tailscale >nul 2>&1 || if not exist "C:\Program Files\Tailscale\tailscale.exe" (
    echo.
    echo [i] Tailscale ne ustanovlen. Besplatno: https://tailscale.com/download
    echo     Ustanovite na PK i iPhone, voydite odnim akkauntom - dashbord i Apple Pay zarabotayut vne doma.
)

rem --- 5. zapusk ---
echo.
echo Zapuskayu bota... Napishite emu /start v Telegram.
start "Bot rasxodov" /min "%ROOT%\expenses\start_expenses.bat"
timeout /t 3 >nul
endlocal
