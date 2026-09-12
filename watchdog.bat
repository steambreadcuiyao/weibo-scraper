@echo off
rem 微博爬虫看门狗 —— 由 Windows 计划任务定时调用
rem 探测服务健康度，不健康则拉起
chcp 65001 >nul
cd /d "C:\Users\Administrator\WorkBuddy\weibo"
"C:\Users\Administrator\.workbuddy\binaries\python\envs\weibo\Scripts\pythonw.exe" watchdog.py
