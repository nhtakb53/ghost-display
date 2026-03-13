@echo off
cd /d C:\Users\nhtak\IdeaProjects\ghost-display\host
C:\Users\nhtak\AppData\Local\Programs\Python\Python313\python.exe main.py --capture-mode dxgi
echo EXIT CODE: %errorlevel%
pause
