' 微博爬虫 - 无窗口静默启动
Set WshShell = CreateObject("WScript.Shell")
Set Env = WshShell.Environment("PROCESS")
Env("FLASK_HOST") = "127.0.0.1"

WshShell.CurrentDirectory = "C:\Users\Administrator\WorkBuddy\weibo"
WshShell.Run """C:\Users\Administrator\.workbuddy\binaries\python\envs\weibo\Scripts\python.exe"" app.py", 0, False
