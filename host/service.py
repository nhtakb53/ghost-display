"""
Ghost Display - Windows 서비스 래퍼
서비스(Session 0)에서 사용자 세션의 데스크톱에 접근하여 DXGI 캡처 가능

방식: 서비스가 활성 사용자 세션을 찾아 그 세션에서 호스트 프로세스를 실행
      → SYSTEM 권한 + 사용자 데스크톱 접근 동시 달성

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
import subprocess
import ctypes
import logging

import win32serviceutil
import win32service
import win32event
import win32ts
import win32process
import win32con
import win32api
import servicemanager

# 서비스에서 실행 시 작업 디렉토리를 스크립트 위치로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))

# 설정 파일 경로
CONFIG_FILE = os.path.join(SCRIPT_DIR, "service_config.json")
# 로그 파일: SYSTEM 권한에서도 쓸 수 있는 ProgramData 또는 스크립트 폴더
_programdata_log = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "GhostDisplay")
if not os.path.exists(_programdata_log):
    try:
        os.makedirs(_programdata_log, exist_ok=True)
    except:
        _programdata_log = SCRIPT_DIR
LOG_FILE = os.path.join(_programdata_log, "ghost-host.log")

DEFAULT_CONFIG = {
    "monitor": 0,
    "fps": 60,
    "bitrate": "20M",
    "video_port": 9000,
    "control_port": 9001,
    "software": False,
    "scale": 0,
    "no_virtual_display": False,
    "input_mode": "kse",
    "capture_mode": "dxgi",
}


def load_config():
    """설정 파일 로드 (없으면 기본값 생성)"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            if k not in config:
                config[k] = v
        return config
    else:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()


def find_python():
    """python.exe 경로 찾기 (pythonservice.exe가 아닌 실제 python)"""
    exe = sys.executable
    # 서비스에서 실행 시 pythonservice.exe가 될 수 있음
    if "pythonservice" in os.path.basename(exe).lower():
        python_dir = os.path.dirname(exe)
        for name in ["python.exe", "python3.exe"]:
            candidate = os.path.join(python_dir, name)
            if os.path.exists(candidate):
                return candidate
        # Scripts 상위 폴더 확인
        parent = os.path.dirname(python_dir)
        for name in ["python.exe", "python3.exe"]:
            candidate = os.path.join(parent, name)
            if os.path.exists(candidate):
                return candidate
    return exe


def build_command(config):
    """설정에서 main.py 실행 명령어 생성"""
    python_exe = find_python()
    main_py = os.path.join(SCRIPT_DIR, "main.py")

    log_file = os.path.join(SCRIPT_DIR, "ghost-host.log")

    cmd = f'"{python_exe}" "{main_py}"'
    cmd += f' --capture-mode {config["capture_mode"]}'
    cmd += f' --monitor {config["monitor"]}'
    cmd += f' --fps {config["fps"]}'
    cmd += f' --bitrate {config["bitrate"]}'
    cmd += f' --video-port {config["video_port"]}'
    cmd += f' --control-port {config["control_port"]}'
    cmd += f' --scale {config["scale"]}'
    cmd += f' --log-file "{log_file}"'
    if config.get("software"):
        cmd += ' --software'
    if config.get("no_virtual_display"):
        cmd += ' --no-virtual-display'
    cmd += f' --input-mode {config.get("input_mode", "kse")}'

    return cmd


def transfer_disconnected_session_to_console():
    """Disconnected 상태의 사용자 세션을 콘솔로 전환 (tscon 자동 실행)

    RDP를 그냥 끊으면 세션이 Disconnected 상태로 남아
    콘솔에 데스크톱이 안 뜸 → DXGI 캡처 불가.
    tscon으로 해당 세션을 콘솔로 전환하면 데스크톱이 살아남.
    """
    try:
        sessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
        for session in sessions:
            if session['State'] == win32ts.WTSDisconnected and session['SessionId'] != 0:
                sid = session['SessionId']
                logging.info(f"Disconnected session {sid} found, transferring to console (tscon)...")
                result = subprocess.run(
                    ["tscon", str(sid), "/dest:console"],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    logging.info(f"tscon succeeded: session {sid} → console")
                    return True
                else:
                    logging.warning(f"tscon failed: {result.stderr.strip()}")
    except Exception as e:
        logging.error(f"transfer_disconnected_session_to_console error: {e}")
    return False


def get_active_session_id():
    """활성 사용자 세션 ID 반환 (콘솔 또는 RDP)"""
    # 먼저 콘솔 세션 확인
    console_session = win32ts.WTSGetActiveConsoleSessionId()
    if console_session != 0xFFFFFFFF:
        try:
            state = win32ts.WTSQuerySessionInformation(
                win32ts.WTS_CURRENT_SERVER_HANDLE,
                console_session,
                win32ts.WTSConnectState
            )
            if state == win32ts.WTSActive:
                return console_session
        except:
            pass

    # 모든 세션에서 활성 세션 찾기
    sessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
    for session in sessions:
        if session['State'] == win32ts.WTSActive and session['SessionId'] != 0:
            return session['SessionId']

    # 활성 세션 없음 → Disconnected 세션을 콘솔로 전환 시도 (쿨다운 60초)
    now = time.time()
    last_tscon = getattr(get_active_session_id, '_last_tscon', 0)
    if now - last_tscon > 60:
        if transfer_disconnected_session_to_console():
            get_active_session_id._last_tscon = now
            time.sleep(5)  # 세션 전환 안정화 대기
            # 전환 후 다시 확인
            console_session = win32ts.WTSGetActiveConsoleSessionId()
            if console_session != 0xFFFFFFFF:
                try:
                    state = win32ts.WTSQuerySessionInformation(
                        win32ts.WTS_CURRENT_SERVER_HANDLE,
                        console_session,
                        win32ts.WTSConnectState
                    )
                    if state == win32ts.WTSActive:
                        return console_session
                except:
                    pass
            # tscon 후에도 Active 안 되면 콘솔 세션이라도 반환
            if console_session != 0xFFFFFFFF:
                return console_session

    return None


def launch_in_session(session_id, cmd, log_file):
    """사용자 세션에서 프로세스 실행 (Sunshine/Parsec 방식)

    WTSQueryUserToken으로 사용자 토큰 획득 → CreateProcessAsUser
    실패 시 subprocess.Popen으로 폴백
    """
    # 먼저 CreateProcessAsUser 시도
    try:
        token = win32ts.WTSQueryUserToken(session_id)

        env = None
        try:
            import win32profile
            env = win32profile.CreateEnvironmentBlock(token, False)
        except:
            env = None

        si = win32process.STARTUPINFO()
        si.dwFlags = win32con.STARTF_USESHOWWINDOW
        si.wShowWindow = win32con.SW_HIDE
        si.lpDesktop = "winsta0\\default"

        proc_info = win32process.CreateProcessAsUser(
            token, None, cmd, None, None, False,
            win32con.CREATE_NEW_CONSOLE | win32con.CREATE_UNICODE_ENVIRONMENT,
            env, SCRIPT_DIR, si,
        )

        handle, thread_handle, pid, tid = proc_info
        win32api.CloseHandle(thread_handle)
        return handle, pid

    except Exception as e:
        logging.error(f"CreateProcessAsUser failed: {e}, trying subprocess")

        # 폴백: subprocess로 직접 실행 (stderr 캡처)
        import shlex
        proc = subprocess.Popen(
            cmd,
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            shell=True,
        )

        # subprocess의 핸들을 win32 핸들로 변환
        handle = win32api.OpenProcess(
            win32con.PROCESS_ALL_ACCESS, False, proc.pid
        )
        return handle, proc.pid


def setup_logging():
    """파일 로깅 설정"""
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s [Service] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class GhostDisplayService(win32serviceutil.ServiceFramework):
    _svc_name_ = "GhostDisplay"
    _svc_display_name_ = "Ghost Display Host"
    _svc_description_ = "Ghost Display 원격 화면 스트리밍 서비스 (DXGI Desktop Duplication)"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process_handle = None
        self.process_pid = None

    def SvcStop(self):
        """서비스 중지"""
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

        # 호스트 프로세스 종료
        if self.process_handle:
            try:
                win32process.TerminateProcess(self.process_handle, 0)
            except:
                pass

        # 포트 점유 프로세스도 정리
        self._kill_port_holders()

    def SvcDoRun(self):
        """서비스 메인"""
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, "")
        )
        setup_logging()

        try:
            self._run()
        except Exception as e:
            logging.error(f"Service error: {e}", exc_info=True)
            servicemanager.LogErrorMsg(f"Ghost Display error: {e}")

    def _kill_port_holders(self):
        """이전 호스트 프로세스가 포트를 점유하고 있으면 종료"""
        config = load_config()
        ports = [config["video_port"], config["control_port"]]
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True
            )
            pids = set()
            for line in result.stdout.splitlines():
                for port in ports:
                    if f":{port} " in line and ("LISTENING" in line or "UDP" in line):
                        parts = line.split()
                        pid = int(parts[-1])
                        if pid > 0:
                            pids.add(pid)
            for pid in pids:
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True)
                    logging.info(f"Killed port-holding process PID {pid}")
                except:
                    pass
        except:
            pass

    def _run(self):
        """사용자 세션에서 호스트 프로세스 실행 + 감시 + 세션 변경 감지"""
        config = load_config()
        cmd = build_command(config)
        logging.info(f"Command: {cmd}")

        # 시작 전 포트 점유 프로세스 정리
        self._kill_port_holders()

        current_session = None

        while True:
            # 중지 신호 확인
            if win32event.WaitForSingleObject(self.stop_event, 0) == win32event.WAIT_OBJECT_0:
                break

            # 활성 사용자 세션 찾기 (없으면 내부에서 tscon 자동 시도)
            session_id = get_active_session_id()
            if session_id is None:
                logging.info("No active user session, waiting...")
                if win32event.WaitForSingleObject(self.stop_event, 5000) == win32event.WAIT_OBJECT_0:
                    break
                continue

            # 세션이 바뀌면 기존 프로세스 종료 후 새 세션에서 재시작
            if current_session is not None and session_id != current_session:
                logging.info(f"Session changed {current_session} -> {session_id}, restarting host")
                if self.process_handle:
                    try:
                        win32process.TerminateProcess(self.process_handle, 0)
                        win32api.CloseHandle(self.process_handle)
                    except:
                        pass
                    self.process_handle = None

            current_session = session_id

            if self.process_handle is None:
                logging.info(f"Launching host in session {session_id} (SYSTEM)")

                try:
                    self.process_handle, self.process_pid = launch_in_session(
                        session_id, cmd, LOG_FILE
                    )
                    logging.info(f"Host process started (PID: {self.process_pid})")
                except Exception as e:
                    logging.error(f"Failed to launch: {e}", exc_info=True)
                    if win32event.WaitForSingleObject(self.stop_event, 10000) == win32event.WAIT_OBJECT_0:
                        break
                    continue

            # 프로세스 종료 또는 서비스 중지를 5초 단위로 확인 (세션 변경 감지용)
            handles = [self.process_handle, self.stop_event]
            result = win32event.WaitForMultipleObjects(handles, False, 5000)

            if result == win32event.WAIT_OBJECT_0:
                # 프로세스가 종료됨 → 재시작
                exit_code = win32process.GetExitCodeProcess(self.process_handle)
                logging.info(f"Host process exited (code: {exit_code}), restarting in 3s...")
                win32api.CloseHandle(self.process_handle)
                self.process_handle = None

                if win32event.WaitForSingleObject(self.stop_event, 3000) == win32event.WAIT_OBJECT_0:
                    break
            elif result == win32event.WAIT_OBJECT_0 + 1:
                # 서비스 중지 신호
                if self.process_handle:
                    try:
                        win32process.TerminateProcess(self.process_handle, 0)
                        win32api.CloseHandle(self.process_handle)
                    except:
                        pass
                break
            # WAIT_TIMEOUT → 루프 돌면서 세션 변경 확인

        logging.info("Service stopped")


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
