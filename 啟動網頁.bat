@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ===============================================
echo   有你蒸好 - 財報分析
echo  ===============================================
echo.
echo   啟動中... 好了會自動打開瀏覽器
echo   要關掉的話：直接關這個黑色視窗
echo.
start "" http://localhost:8000
venv\Scripts\python.exe -m uvicorn api:app --app-dir src --port 8000 --reload --reload-dir src
pause
