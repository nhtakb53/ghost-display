"""
Ghost Display - Windows 서비스 래퍼
SYSTEM 권한으로 실행되어 잠금화면에서도 DXGI 캡처 가능

사용법:
  python service.py install    서비스 등록
  python service.py start      서비스 시작
  python service.py stop       서비스 중지
  python service.py remove     서비스 삭제
  python service.py restart    서비스 재시작
  python service.py debug      디버그 모드 (콘솔에서 실행)
"""

import sys
import os
import time
import json
import threading
import logging

import win32serviceutil
import win32service
import win32event
import servicemanager

# 서비스에서 실행 시 작업 디렉토리를 스크립트 위치로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

# 설정 파일 경로
CONFIG_FILE = os.path.join(SCRIPT_DIR, "service_config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "ghost-host.log")

DEFAULT_CONFIG = {
    "monitor": 0,
    "fps": 60,
    "bitrate": "8M",
    "video_port": 9000,
    "control_port": 9001,
    "software": False,
    "scale": 0,
    "no_virtual_display": False,
    "sendinput": False,
    "capture_mode": "dxgi",
}


def load_config():
    """설정 파일 로드 (없으면 기본값 생성)"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 누락된 키 채우기
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config
    else:
        # 기본 설정 파일 생성
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()


class ConfigNamespace:
    """argparse.Namespace 대체 — dict를 attribute로 접근"""
    def __init__(self, config_dict):
        for k, v in config_dict.items():
            setattr(self, k, v)


def setup_logging():
    """파일 로깅 설정"""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # print를 로그로 리다이렉트
    class LogWriter:
        def __init__(self, logger, level):
            self.logger = logger
            self.level = level
            self.buf = ""
        def write(self, msg):
            if msg.strip():
                self.logger.log(self.level, msg.strip())
        def flush(self):
            pass

    sys.stdout = LogWriter(logging.getLogger(), logging.INFO)
    sys.stderr = LogWriter(logging.getLogger(), logging.ERROR)


class GhostDisplayService(win32serviceutil.ServiceFramework):
    _svc_name_ = "GhostDisplay"
    _svc_display_name_ = "Ghost Display Host"
    _svc_description_ = "Ghost Display 원격 화면 스트리밍 서비스 (DXGI Desktop Duplication)"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.host = None

    def SvcStop(self):
        """서비스 중지"""
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.host:
            self.host.stop()

    def SvcDoRun(self):
        """서비스 메인"""
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, "")
        )
        setup_logging()

        try:
            self._run_host()
        except Exception as e:
            logging.error(f"Service error: {e}", exc_info=True)
            servicemanager.LogErrorMsg(f"Ghost Display error: {e}")

    def _run_host(self):
        """GhostHost 실행"""
        from main import GhostHost

        config = load_config()
        args = ConfigNamespace(config)

        logging.info(f"Ghost Display service starting (capture: {config['capture_mode']})")

        self.host = GhostHost(args)

        # 별도 스레드에서 호스트 실행
        host_thread = threading.Thread(target=self._start_host, daemon=True)
        host_thread.start()

        # 중지 신호 대기
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

        if self.host:
            self.host.stop()

        logging.info("Ghost Display service stopped")

    def _start_host(self):
        try:
            self.host.start()
        except Exception as e:
            logging.error(f"Host error: {e}", exc_info=True)


def print_usage():
    print("""
Ghost Display Service 관리
==========================
  python service.py install    서비스 등록 (관리자 권한 필요)
  python service.py start      서비스 시작
  python service.py stop       서비스 중지
  python service.py remove     서비스 삭제
  python service.py restart    서비스 재시작
  python service.py debug      디버그 모드 (콘솔에서 실행)
  python service.py config     현재 설정 확인/변경

설정 파일: service_config.json
로그 파일: ghost-host.log
""")


def handle_config():
    """설정 확인/수정"""
    config = load_config()
    print(f"\n현재 설정 ({CONFIG_FILE}):")
    print(json.dumps(config, indent=2))
    print(f"\n설정을 변경하려면 {CONFIG_FILE} 파일을 직접 수정 후 서비스 재시작")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print_usage()
    elif sys.argv[1] == "config":
        handle_config()
    else:
        win32serviceutil.HandleCommandLine(GhostDisplayService)
