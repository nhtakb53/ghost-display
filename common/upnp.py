"""
UPnP 자동 포트포워딩
- 프로그램 시작 시 공유기에 UDP 포트 자동 등록
- 종료 시 자동 해제
"""

import atexit

_upnp = None
_mapped_ports = []


def setup_upnp(ports, protocol="UDP", description="Ghost Display"):
    """
    UPnP로 포트포워딩 자동 설정.
    ports: [(external_port, internal_port), ...] 또는 [port, ...]
    반환: (성공 여부, 외부 IP)
    """
    global _upnp

    try:
        import miniupnpc
    except ImportError:
        print("  [UPnP] miniupnpc 미설치 (pip install miniupnpc)")
        return False, None

    try:
        _upnp = miniupnpc.UPnP()
        _upnp.discoverdelay = 1000
        devices = _upnp.discover()
        if devices == 0:
            print("  [UPnP] UPnP 장치를 찾을 수 없음")
            return False, None

        _upnp.selectigd()
        external_ip = _upnp.externalipaddress()
        internal_ip = _upnp.lanaddr
        print(f"  [UPnP] 공유기 발견: 외부 IP={external_ip}, 내부 IP={internal_ip}")

        for port_spec in ports:
            if isinstance(port_spec, tuple):
                ext_port, int_port = port_spec
            else:
                ext_port = int_port = port_spec

            # 기존 매핑 확인
            existing = _upnp.getspecificportmapping(ext_port, protocol)
            if existing:
                if existing[0] == internal_ip and int(existing[1]) == int_port:
                    print(f"  [UPnP] {protocol} :{ext_port} -> {internal_ip}:{int_port} (이미 등록됨)")
                    _mapped_ports.append((ext_port, protocol))
                    continue
                else:
                    # 다른 IP로 매핑되어 있으면 삭제 후 재등록
                    print(f"  [UPnP] 기존 매핑 제거: {protocol} :{ext_port} -> {existing[0]}:{existing[1]}")
                    _upnp.deleteportmapping(ext_port, protocol)

            result = _upnp.addportmapping(
                ext_port, protocol, internal_ip, int_port,
                f"{description} {protocol} {ext_port}", ""
            )

            if result:
                print(f"  [UPnP] {protocol} :{ext_port} -> {internal_ip}:{int_port} (등록 완료)")
                _mapped_ports.append((ext_port, protocol))
            else:
                print(f"  [UPnP] {protocol} :{ext_port} 등록 실패")

        # 프로그램 종료 시 자동 해제
        atexit.register(cleanup_upnp)

        return len(_mapped_ports) > 0, external_ip

    except Exception as e:
        print(f"  [UPnP] 오류: {e}")
        return False, None


def cleanup_upnp():
    """등록한 포트 매핑 해제"""
    global _upnp, _mapped_ports

    if not _upnp or not _mapped_ports:
        return

    for ext_port, protocol in _mapped_ports:
        try:
            _upnp.deleteportmapping(ext_port, protocol)
            print(f"  [UPnP] {protocol} :{ext_port} 매핑 해제")
        except Exception:
            pass

    _mapped_ports.clear()
