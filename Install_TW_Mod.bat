@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   蝙蝠俠：阿卡漢系列 — 台灣繁體中文化安裝程式 (Player Mode)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [錯誤] 未在 PATH 中找到 Python。請先安裝 Python 3.10 以上版本。
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [錯誤] 需要 Python 3.10 以上版本。請更新 Python 後再試。
    pause
    exit /b 1
)

python -c "import PIL, fontTools" >nul 2>nul
if errorlevel 1 (
    echo [錯誤] 缺少必要套件 ^(Pillow / fontTools^)。
    echo 請先執行：pip install -r requirements.txt
    pause
    exit /b 1
)

python arkham_mod_cli.py player status all
echo.
echo [1] 安裝／更新全部已偵測的阿卡漢系列遊戲
echo [2] 安裝／更新 瘋人院 (Batman: Arkham Asylum GOTY)
echo [3] 安裝／更新 阿卡漢城市 (Batman: Arkham City GOTY)
echo [4] 安裝／更新 始源 (Batman: Arkham Origins)
echo [5] 安裝／更新 阿卡漢騎士 (Batman: Arkham Knight)
echo [6] 更新全部已安裝的譯文包 (update-pack)
echo [7] 解除安裝並還原原版英文檔案
echo [0] 離開
echo.
set /p CHOICE=請選擇功能 [0-7]: 

set "CLI_RC=0"
if "%CHOICE%"=="1" (
    python arkham_mod_cli.py player install all
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="2" (
    python arkham_mod_cli.py player install asylum
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="3" (
    python arkham_mod_cli.py player install city
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="4" (
    python arkham_mod_cli.py player install origins
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="5" (
    python arkham_mod_cli.py player install knight
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="6" (
    python arkham_mod_cli.py player update-pack all
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
if "%CHOICE%"=="7" (
    set /p UNGAME=請輸入要還原的遊戲代碼 [asylum/city/origins/knight/all]: 
    python arkham_mod_cli.py player uninstall !UNGAME!
    set "CLI_RC=!ERRORLEVEL!"
    goto done
)
exit /b 0

:done
echo.
if not "%CLI_RC%"=="0" (
    echo [失敗] 執行未完成，結束代碼：%CLI_RC%
) else (
    echo [完成] 操作已成功完成。
)
pause
exit /b %CLI_RC%
