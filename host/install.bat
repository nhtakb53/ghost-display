@echo off
chcp 65001 >nul
title Ghost Display - 설치

echo ==================================================
echo   Ghost Display - Host 설치
echo ==================================================
echo.

:: 관리자 권한 확인
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 관리자 권한이 필요합니다. 우클릭 → 관리자 권한으로 실행
    pause
    exit /b 1
)

:: Python 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python이 설치되어 있지 않습니다.
    pause
    exit /b 1
)

:: 기존 서비스 정리
sc query GhostDisplay >nul 2>&1
if %errorlevel% equ 0 (
    echo [*] 기존 서비스 제거 중...
    python service.py stop 2>nul
    python service.py remove 2>nul
    timeout /t 2 >nul
)

echo [1/3] 패키지 설치 중...
pip install -r ..\requirements.txt
if %errorlevel% neq 0 (
    echo [!] 패키지 설치 실패
    pause
    exit /b 1
)

echo.
echo [2/3] 서비스 등록 중...
python service.py install
if %errorlevel% neq 0 (
    echo [!] 서비스 등록 실패
    pause
    exit /b 1
)

echo.
echo [3/3] 서비스 시작 중...
python service.py start

echo.
echo ==================================================
echo   설치 완료!
echo   - 서비스명: GhostDisplay
echo   - 설정파일: service_config.json
echo   - 로그파일: ghost-host.log
echo   - 로그확인: Get-Content ghost-host.log -Wait -Tail 50
echo ==================================================
pause
