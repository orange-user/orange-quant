@echo off
chcp 65001 >nul
cd /d C:\Users\Administrator\Desktop\quant_pulse

echo ================================
echo Pulse Orange HTTP 一键部署
echo ================================
echo.

:: 检测curl
where curl >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 curl，请安装 curl 或使用其他方式部署
    pause
    exit /b 1
)

echo 正在上传 engine.py ...
curl -X POST ^
  -H "X-Deploy-Token: po2024" ^
  -F "file=@engine.py" ^
  http://47.114.114.222/api/deploy

echo.
echo ================================
if %ERRORLEVEL% EQU 0 (
    echo 部署请求已发送
) else (
    echo [失败] 部署请求失败，请确认服务器地址是否正确
    echo 如果连不上，可尝试: curl -X POST -H "X-Deploy-Token: po2024" -F "file=@engine.py" http://47.114.114.222/api/deploy
)
echo ================================
pause
