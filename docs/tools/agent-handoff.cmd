@echo off
rem agent-handoff - session handoff generator (Windows wrapper)
rem Usage: agent-handoff [repo-path] [options]
rem   agent-handoff .
rem   agent-handoff E:\output\myproj --skip-tests
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
python "%~dp0agent-handoff.py" %*
exit /b %errorlevel%
