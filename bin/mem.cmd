@echo off
setlocal

rem micro_mem command wrapper (Windows)
rem Repo root is derived from this script's own location (bin\..);
rem override it with the MEMORY_HOME environment variable.
rem
rem NOTE: keep this file ASCII-only. cmd.exe parses .cmd files in the OEM
rem codepage (cp936 on zh-CN Windows), not UTF-8. A multi-byte comment whose
rem trailing byte looks like a GBK lead byte swallows the CRLF, merging the
rem next line into the comment -- which then runs as a bogus command.

for %%I in ("%~dp0..") do set "MEM_ROOT=%%~fI"
if defined MEMORY_HOME set "MEM_ROOT=%MEMORY_HOME%"

set "CMD=%~1"
if "%CMD%"=="" goto help
if /i "%CMD%"=="anchor"  goto distill
if /i "%CMD%"=="distill" goto distill
if /i "%CMD%"=="confirm" goto distill
goto main

:help
echo Usage:
echo   mem search ^<keywords^>      - search memory
echo   mem get ^<id^>               - get knowledge detail
echo   mem rebuild                 - rebuild index from md
echo   mem anchor/distill/confirm  - distill session
exit /b 1

:distill
python "%MEM_ROOT%\distill_this_session.py" %*
exit /b %errorlevel%

:main
python "%MEM_ROOT%\src\main.py" %*
exit /b %errorlevel%
