@echo off
rem Zapusk bota rasxodov s avtoperezapuskom. Log: expenses\expenses.log
cd /d "%~dp0.."
:loop
echo ===== start: %date% %time% ===== >> expenses\expenses.log
venv\Scripts\python.exe expenses\main.py >> expenses\expenses.log 2>&1
echo ===== stopped: %date% %time% - restart in 10s >> expenses\expenses.log
timeout /t 10 /nobreak >nul
goto loop
