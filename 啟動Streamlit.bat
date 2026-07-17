@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ===============================================
echo   有你蒸好 - Streamlit 版（備援）
echo  ===============================================
echo.
echo   啟動中... 好了會自動打開瀏覽器
echo   要關掉的話：直接關這個黑色視窗
echo.
venv\Scripts\python.exe -m streamlit run src/app.py
pause
