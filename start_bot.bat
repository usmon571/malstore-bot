@echo off
rem Botni ishga tushiradi va chiqishni bot.log ga yozadi.
rem Bot qandaydir sababga ko'ra to'xtab qolsa, 10 soniyadan keyin
rem o'zi qayta ko'tariladi (cheksiz tsikl). Butunlay yopish uchun shu
rem oynani yoping yoki Ctrl+C bosing.
cd /d "%~dp0"

:loop
echo. >> bot.log
echo ===== ishga tushdi: %date% %time% ===== >> bot.log
venv\Scripts\python.exe bot.py >> bot.log 2>&1
echo ===== to'xtadi: %date% %time% - 10s dan keyin qayta >> bot.log
timeout /t 10 /nobreak >nul
goto loop
