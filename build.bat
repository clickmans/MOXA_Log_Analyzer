@echo off
REM ============================================================
REM  MOXA 交换机日志智能分析工具 —— PyInstaller 打包脚本
REM  生成单文件 Windows 64 位 .exe（窗口模式，无控制台）
REM ============================================================
cd /d %~dp0

REM 若未安装依赖，可先执行：
REM pip install -r requirements.txt

if not exist "assets\app_icon.ico" (
    echo [INFO] 应用图标缺失，将在首次运行时由程序自动生成。
)

echo [INFO] 开始打包（PyInstaller）...
REM 注：Windows 文件系统大小写不敏感，PyInstaller 的 pyinstaller/PyInstaller 两包会冲突，
REM 故用 `python -m PyInstaller`（大写 P）以保证在 Windows 上可靠调用。
python -m PyInstaller --noconfirm --onefile --windowed ^
    --icon=assets/app_icon.ico ^
    --add-data "assets/app_icon.ico;assets" ^
    --name="MOXA_Log_Analyzer" ^
    --hidden-import=customtkinter ^
    --hidden-import=matplotlib.backends.backend_tkagg ^
    --hidden-import=reportlab.pdfbase.ttfonts ^
    main.py

if %errorlevel%==0 (
    echo [OK] 打包完成，exe 位于 dist\MOXA_Log_Analyzer.exe
) else (
    echo [ERR] 打包失败，请检查依赖与网络。
)

pause
