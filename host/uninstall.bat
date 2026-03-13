@echo off
chcp 65001 >nul
title Ghost Display - 삭제

echo ==================================================
echo   Ghost Display - Host 삭제
echo ==================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 관리자 권한이 필요합니다. 우클릭 → 관리자 권한으로 실행
    pause
    exit /b 1
)

echo [1/2] 서비스 중지 중...
python service.py stop 2>nul

echo.
echo [2/2] 서비스 삭제 중...
python service.py remove

echo.
echo   삭제 완료!
pause
