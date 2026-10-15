@echo off
rem Launches Visual Code Builder. Double-click, or drop a .py file onto this
rem file to open it directly.
cd /d "%~dp0"
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw main.py %*
) else (
    python main.py %*
)
