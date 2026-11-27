@echo off
rem Windows launcher for herdr_secret.py: prefers the py launcher, falls back to python.
setlocal
rem Never run a py.exe or python.bat planted in the current directory.
set NoDefaultCurrentDirectoryInExePath=1
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -I "%~dp0herdr_secret.py" %*
) else (
  python -I "%~dp0herdr_secret.py" %*
)
exit /b %errorlevel%
