@echo off
setlocal

rem micro_mem command wrapper (Windows): one-line forwarder to the single
rem `mem` entry (python -m micro_mem). Repo root is derived from this
rem script's own location; override it with MEMORY_HOME.
rem
rem NOTE: keep this file ASCII-only. cmd.exe parses .cmd files in the OEM
rem codepage (cp936 on zh-CN Windows), not UTF-8. A multi-byte comment whose
rem trailing byte looks like a GBK lead byte swallows the CRLF, merging the
rem next line into the comment -- which then runs as a bogus command.

for %%I in ("%~dp0..\..") do set "MEM_ROOT=%%~fI"
if defined MEMORY_HOME set "MEM_ROOT=%MEMORY_HOME%"

set "PYTHONPATH=%MEM_ROOT%\src"
python -m micro_mem %*
exit /b %errorlevel%
