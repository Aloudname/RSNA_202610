@echo off
rem Entry point for downloading dataset.
rem Works from cmd.exe, PowerShell, Git-Bash and by double-clicking.
rem It only picks an interpreter and forwards everything to
rem scripts\bootstrap.py, which does the real work.
rem
rem   download.bat --dest D:/rsna
rem   download.bat --help
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if "%ROOT:~-1%"==":" set "ROOT=%ROOT%\"
set "BOOTSTRAP=%ROOT%\scripts\bootstrap.py"

if not exist "%BOOTSTRAP%" goto missing
where uv >nul 2>nul
if not errorlevel 1 goto useuv
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul
if not errorlevel 1 goto useplain
goto nointerp

:useuv
uv run --project "%ROOT%" --frozen python "%BOOTSTRAP%" %*
goto done

:usepy
py -3 "%BOOTSTRAP%" %*
goto done

:useplain
python "%BOOTSTRAP%" %*
goto done

:missing
echo download.bat: cannot find "%BOOTSTRAP%"
echo Make sure you cloned the whole repository.
set "RC=1"
goto fail

:nointerp
echo download.bat: neither "uv" nor Python 3.8+ was found on PATH.
echo.
echo   Install uv, recommended because it also fetches the right Python:
echo     powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
echo   Or install Python 3.9 or newer from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" in the installer.
echo.
echo Open a NEW terminal afterwards, then run download.bat again.
set "RC=1"
goto fail

:done
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" exit /b 0
echo.

:fail
echo download.bat: stopped with exit code %RC%.
echo If the download had already started, re-run this file to resume; the
echo path of its log file was printed above.
pause
exit /b %RC%
