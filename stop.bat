@echo off
powershell -Command "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*weibo\\app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host '已停止微博爬虫服务 (PID: '$_.ProcessId')' }"
