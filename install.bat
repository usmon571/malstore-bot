@echo off
rem ============================================================
rem   MalStore Bot - bitta fayl bilan to'liq avtomatik o'rnatish
rem ------------------------------------------------------------
rem   Shu faylni Windows 10/11 kompyuterda ikki marta bosing.
rem   U o'zi: Python'ni o'rnatadi, botni yuklaydi, kutubxonalarni
rem   quradi, sozlamalarni so'raydi, avtostart va "uxlamaslik"ni
rem   yoqadi. Oxirida botni ishga tushiradi.
rem ============================================================
setlocal EnableDelayedExpansion
title MalStore Bot - o'rnatish
cd /d "%~dp0"

echo(
echo ============================================
echo    MalStore Bot - avtomatik o'rnatish
echo ============================================
echo(

rem --- 0. Qayerga o'rnatamiz ---
if exist "%~dp0bot.py" (
    set "APPDIR=%~dp0"
    echo [i] Loyiha shu papkada topildi.
) else (
    set "APPDIR=%USERPROFILE%\malstore-bot\"
    echo [i] Loyiha internetdan yuklab olinadi:
    echo     !APPDIR!
    call :download_repo || goto :fail
)
cd /d "!APPDIR!"
echo(

rem --- 1. Python 3.12 ---
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY="%LocalAppData%\Programs\Python\Python312\python.exe""
if not defined PY (
    echo [1/5] Python 3.12 o'rnatilmoqda ^(bir martalik, ~30 MB^)...
    call :install_python || goto :fail
    set "PY="%LocalAppData%\Programs\Python\Python312\python.exe""
) else (
    echo [1/5] Python topildi.
)

rem --- 2. Virtual muhit ---
echo [2/5] Virtual muhit tayyorlanmoqda...
if not exist "venv\Scripts\python.exe" (
    %PY% -m venv venv || goto :fail
)

rem --- 3. Kutubxonalar + Chromium ---
echo [3/5] Kutubxonalar o'rnatilmoqda ^(biroz vaqt oladi^)...
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python -m pip install -r requirements.txt || goto :fail
echo     Chromium brauzeri yuklanmoqda ^(~150 MB, sabr qiling^)...
venv\Scripts\python -m playwright install chromium || goto :fail

rem --- 4. Sozlamalar (config.json) ---
if exist "config.json" (
    echo [4/5] config.json allaqachon mavjud - o'tkazib yuborildi.
) else (
    echo [4/5] Sozlamalar. Quyidagilarni kiriting ^(Enter bilan tasdiqlang^):
    echo(
    set /p "BOT_TOKEN=   BotFather tokeni        : "
    set /p "CHANNEL=   Kanal (masalan @dukon)  : "
    set /p "ALLOWED=   Username(lar) vergul b-n: "
    set /p "ADMINID=   Admin ID (ixtiyoriy)    : "
    set /p "CARDNUM=   Karta raqami (ixtiyoriy): "
    set /p "CARDHOLDER=   Karta egasi (ixtiyoriy) : "
    call :write_config || goto :fail
    echo(
    echo     config.json yozildi. Keyinroq uni Notepad bilan tahrirlashingiz mumkin.
)

rem --- 5. Avtostart + quvvat ---
echo [5/5] Avtostart va "uxlamaslik" sozlanmoqda...
call :setup_autostart
call :setup_power

echo(
echo ============================================
echo    TAYYOR! Bot o'rnatildi.
echo ============================================
echo(
echo  - Avtostart: kompyuter yonganda bot o'zi ishga tushadi.
echo  - Yiqilsa o'zi qayta ko'tariladi.
echo  - Loglar:  "!APPDIR!bot.log"
echo(
echo  Botni HOZIR ishga tushiramanmi? Keyin Telegram'da tekshiring.
echo  ^(Botni to'xtatish uchun ochilgan oynani yoping.^)
echo(
pause
start "MalStore Bot" "!APPDIR!start_bot.bat"
echo(
echo  Bot alohida oynada ishga tushdi. Endi Railway'dagi eski nusxani
echo  o'chiring/to'xtating, aks holda ikki nusxa bir-biriga xalaqit beradi.
echo(
pause
exit /b 0


rem ================= quyi dasturlar =================

:download_repo
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; $zip=Join-Path $env:TEMP 'malstore.zip'; Invoke-WebRequest 'https://github.com/usmon571/malstore-bot/archive/refs/heads/main.zip' -OutFile $zip; $tmp=Join-Path $env:TEMP 'malstore_ex'; if(Test-Path $tmp){Remove-Item $tmp -Recurse -Force}; Expand-Archive $zip $tmp -Force; $src=Join-Path $tmp 'malstore-bot-main'; New-Item -ItemType Directory -Force -Path $env:APPDIR | Out-Null; Copy-Item (Join-Path $src '*') $env:APPDIR -Recurse -Force; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
exit /b %errorlevel%

:install_python
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; $u='https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'; $f=Join-Path $env:TEMP 'py312-setup.exe'; Invoke-WebRequest $u -OutFile $f; $p=Start-Process $f -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_test=0' -Wait -PassThru; exit $p.ExitCode } catch { Write-Host $_.Exception.Message; exit 1 }"
exit /b %errorlevel%

:write_config
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $admin=$null; $a=([string]$env:ADMINID).Trim(); if($a){ $admin=[int]$a }; $allowed=@(([string]$env:ALLOWED) -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }); $cfg=[ordered]@{ bot_token=([string]$env:BOT_TOKEN).Trim(); channel=([string]$env:CHANNEL).Trim(); allowed_usernames=$allowed; admin_id=$admin; card_number=([string]$env:CARDNUM).Trim(); card_holder=([string]$env:CARDHOLDER).Trim(); prepay_share=0.7; webapp_url='https://usmon571.github.io/malstore/'; brand_label='Original'; max_images=12; cny_rate=1761; order_timeout_minutes=30; delivery_rates=[ordered]@{ avto_kargo=83880; avia_brend=156000 } }; $json=$cfg | ConvertTo-Json -Depth 5; [System.IO.File]::WriteAllText((Join-Path $PWD 'config.json'), $json, (New-Object System.Text.UTF8Encoding($false))); exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
exit /b %errorlevel%

:setup_autostart
rem Startup papkasiga yorliq qo'yamiz - administrator huquqisiz ishlaydi.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ws=New-Object -ComObject WScript.Shell; $lnk=Join-Path ([Environment]::GetFolderPath('Startup')) 'MalStore Bot.lnk'; $s=$ws.CreateShortcut($lnk); $s.TargetPath=(Join-Path $env:APPDIR 'start_bot.bat'); $s.WorkingDirectory=$env:APPDIR; $s.WindowStyle=7; $s.Save(); Write-Host 'Avtostart yorlig''i qo''yildi.' } catch { Write-Host ('Avtostart o''rnatilmadi: '+$_.Exception.Message) }"
exit /b 0

:setup_power
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
powercfg /change disk-timeout-ac 0 >nul 2>&1
echo     Quvvat: tarmoqdan ishlaganda kompyuter uxlamaydi.
exit /b 0

:fail
echo(
echo ============================================
echo   [XATO] O'rnatishda muammo yuz berdi.
echo   Yuqoridagi matnni nusxalab yuboring - yordam beramiz.
echo ============================================
echo(
pause
exit /b 1
