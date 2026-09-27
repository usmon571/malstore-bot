@echo off
rem Botni ishga tushiradi va chiqishni bot.log ga yozadi.
rem Yiqilsa Windows vazifa rejalashtiruvchisi qayta ishga tushiradi.
cd /d "%~dp0"
echo. >> bot.log
echo ===== ishga tushdi: %date% %time% ===== >> bot.log
venv\Scripts\python.exe bot.py >> bot.log 2>&1
