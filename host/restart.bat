@echo off
chcp 65001 >nul
title Ghost Display - 재시작

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 관리자 권한이 필요합니다. 우클릭 → 관리자 권한으로 실행
    pause
    exit /b 1
)

echo 서비스 재시작 중...
python service.py restart
echo 완료!
pause
