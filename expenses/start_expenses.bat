@echo off
rem Zapusk bota rasxodov s avtoperezapuskom.
rem Log: expenses\expenses.log (3 fayla po 2 MB), padeniya: expenses\crash.log
cd /d "%~dp0.."
set PYTHONUTF8=1
set EXP_LOG_FILE=expenses\expenses.log
:loop
echo ===== start: %date% %time% ===== >> expenses\crash.log
venv\Scripts\python.exe expenses\main.py 2>> expenses\crash.log
echo ===== stopped: %date% %time% - restart in 10s >> expenses\crash.log
timeout /t 10 /nobreak >nul
goto loop
