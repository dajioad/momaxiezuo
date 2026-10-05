@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 找不到 .venv\Scripts\python.exe
    echo         请在项目根目录确认虚拟环境存在。
    pause
    exit /b 1
)

if not exist "release_gui.py" (
    echo [ERROR] 找不到 release_gui.py
    pause
    exit /b 1
)

.venv\Scripts\python.exe release_gui.py

if errorlevel 1 (
    echo.
    echo [ERROR] 发版工具异常退出（错误码 %errorlevel%）
    pause
)