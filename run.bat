@echo off
title PlayStation World Multan - WhatsApp AI Agent
echo ========================================================
echo    PLAYSTATION WORLD MULTAN - WHATSAPP AI AGENT
echo ========================================================
echo.
echo Starting Webhook & AI Agent Server on http://localhost:8000 ...
echo.

set PYTHON_PATH="C:\Users\junai\AppData\Local\Programs\Python\Python311\python.exe"

if exist %PYTHON_PATH% (
    %PYTHON_PATH% -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
) else (
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
)

pause
