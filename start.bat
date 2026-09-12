@echo off
chcp 65001 >nul
cd /d "C:\Users\Administrator\WorkBuddy\weibo"
set FLASK_HOST=0.0.0.0

echo ============================================
echo   微博爬虫监控系统
echo ============================================
echo.

rem 检查服务是否已在运行
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }" >nul 2>&1
if not errorlevel 1 goto :run

echo [提示] 服务已在运行中，无需重复启动。
echo        访问地址: http://127.0.0.1:5000
echo.
timeout /t 5 >nul
exit /b 0

:run
echo 正在启动服务... 启动后可关闭本窗口，服务不受影响。
echo 访问地址: http://127.0.0.1:5000
echo 日志文件: data\system.log
echo.
"C:\Users\Administrator\.workbuddy\binaries\python\envs\weibo\Scripts\python.exe" app.py

echo.
echo [服务已停止]
pause
