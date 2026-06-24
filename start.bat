@echo off
cd /d "C:\Users\Administrator\WorkBuddy\weibo"
set FLASK_HOST=127.0.0.1
start "微博爬虫" "C:\Users\Administrator\.workbuddy\binaries\python\envs\weibo\Scripts\python.exe" app.py
