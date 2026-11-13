@echo off
rem ============================================================
rem   MalStore Bot - yangilash (o'rnatilgan botni yangi kodga o'tkazish)
rem ------------------------------------------------------------
rem   config.json, store.json (baza) va venv'ga tegilmaydi - faqat
rem   kod yangilanadi. Bot ishlab turgan bo'lsa, o'zi qayta ishga
rem   tushadi. Ikki marta bosing.
rem ============================================================
setlocal EnableDelayedExpansion
title MalStore Bot - yangilash
cd /d "%~dp0"

if exist "%~dp0bot.py" (
    set "APPDIR=%~dp0"
) else (
    set "APPDIR=%USERPROFILE%\malstore-bot\"
)
if not exist "!APPDIR!venv\Scripts\python.exe" (
    echo [XATO] Bot o'rnatilmagan: "!APPDIR!"
    echo Avval install.bat ni ishga tushiring.
    pause
    exit /b 1
)

echo [1/3] Yangi kod yuklanmoqda...
rem Ishlab turgan .bat fayllar almashtirilmaydi: cmd ularni qatorma-qator
rem o'qiydi va ustidan yozilsa adashib ketadi.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; $zip=Join-Path $env:TEMP 'malstore.zip'; Invoke-WebRequest 'https://github.com/usmon571/malstore-bot/archive/refs/heads/main.zip' -OutFile $zip; $tmp=Join-Path $env:TEMP 'malstore_ex'; if(Test-Path $tmp){Remove-Item $tmp -Recurse -Force}; Expand-Archive $zip $tmp -Force; Get-ChildItem (Join-Path $tmp 'malstore-bot-main') | Where-Object { $_.Name -notin 'start_bot.bat','update.bat' } | Copy-Item -Destination $env:APPDIR -Recurse -Force; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto :fail
cd /d "!APPDIR!"

echo [2/3] Kutubxonalar tekshirilmoqda...
venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail

echo [3/3] Bot qayta ishga tushirilmoqda...
rem start_bot.bat tsiklda ishlaydi: bot.py to'xtatilsa, 10 soniyada yangi kod
rem bilan o'zi qaytadan ko'tariladi. Tsikl yo'q bo'lsa - o'zimiz yoqamiz.
rem venv'dagi python.exe asl Python'ni bola jarayon qilib ishga tushiradi,
rem shuning uchun ikkalasi ham to'xtatiladi.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir=$env:APPDIR.TrimEnd('\'); $procs=Get-CimInstance Win32_Process; $loop=$procs | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like \"*$dir*start_bot.bat*\" }; $bots=$procs | Where-Object { $_.Name -eq 'python.exe' -and $_.ExecutablePath -like \"$dir\venv\*\" -and $_.CommandLine -like '*bot.py*' }; foreach ($b in $bots) { $procs | Where-Object { $_.ParentProcessId -eq $b.ProcessId } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Stop-Process -Id $b.ProcessId -Force -ErrorAction SilentlyContinue }; if ($loop) { exit 10 } else { exit 0 }"
if errorlevel 10 (
    echo     Bot 10 soniyada yangi kod bilan o'zi qayta yonadi.
) else (
    start "MalStore Bot" /min "!APPDIR!start_bot.bat"
    echo     Bot ishga tushirildi.
)

echo(
echo ============================================
echo    TAYYOR! Bot yangilandi.
echo ============================================
echo(
echo  Sayt shu kompyuterda ochiladi: http://localhost:8081
echo(
pause
exit /b 0

:fail
echo(
echo [XATO] Yangilashda muammo yuz berdi. Yuqoridagi matnni yuboring.
pause
exit /b 1
