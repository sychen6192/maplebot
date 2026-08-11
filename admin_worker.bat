@echo off
REM maplebot elevated worker launcher.
REM The game client runs as Administrator, so key input must come from an
REM elevated process too. Double-click this file and answer YES to UAC.
REM A blue PowerShell window titled "maplebot elevated worker ONLINE" should
REM stay open. Close that window anytime to stop.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0admin_worker.ps1'"
