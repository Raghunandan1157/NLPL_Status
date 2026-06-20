@echo off
REM Single entry point for NLPL Status. `npm run dev` runs scripts/dev.mjs,
REM which starts the backend (port 5055) and frontend (port 5174) once each,
REM waits until both are healthy, then opens the UI in Chrome.
cd /d "%~dp0"
call npm run dev
