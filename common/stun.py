"""
경량 STUN 클라이언트
- 외부 STUN 서버로 자신의 공인 IP:Port 확인
- NAT 홀펀칭에 필요한 정보 제공
"""

import socket
import struct
import os
import time

# STUN 메시지 타입
STUN_BINDING_REQUEST = 0x0001
STUN_BINDING_RESPONSE = 0x0101

# STUN 속성 타입
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020

# STUN 매직 쿠키
MAGIC_COOKIE = 0x2112A442

# 공개 STUN 서버 목록
STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun.cloudflare.com", 3478),
    ("stun.stunprotocol.org", 3478),
]


def build_binding_request():
    """STUN Binding Request 패킷 생성"""
    msg_type = STUN_BINDING_REQUEST
    msg_length = 0
    magic = MAGIC_COOKIE
    txn_id = os.urandom(12)

    header = struct.pack("!HHI", msg_type, msg_length, magic) + txn_id
    return header, txn_id


def parse_binding_response(data, txn_id):
    """STUN Binding Response 파싱 → (ip, port) 반환"""
    if len(data) < 20:
        return None

    msg_type, msg_length, magic = struct.unpack("!HHI", data[:8])
    resp_txn = data[8:20]

    if msg_type != STUN_BINDING_RESPONSE:
        return None
    if magic != MAGIC_COOKIE:
        return None
    if resp_txn != txn_id:
        return None

    # 속성 파싱
    offset = 20
    while offset + 4 <= len(data):
        attr_type, attr_length = struct.unpack("!HH", data[offset:offset + 4])
        attr_data = data[offset + 4:offset + 4 + attr_length]

        if attr_type == ATTR_XOR_MAPPED_ADDRESS and attr_length >= 8:
            family = attr_data[1]
            xor_port = struct.unpack("!H", attr_data[2:4])[0] ^ (MAGIC_COOKIE >> 16)
            if family == 0x01:  # IPv4
                xor_ip_bytes = struct.unpack("!I", attr_data[4:8])[0] ^ MAGIC_COOKIE
                ip = socket.inet_ntoa(struct.pack("!I", xor_ip_bytes))
                return (ip, xor_port)

        elif attr_type == ATTR_MAPPED_ADDRESS and attr_length >= 8:
            family = attr_data[1]
            port = struct.unpack("!H", attr_data[2:4])[0]
            if family == 0x01:  # IPv4
                ip = socket.inet_ntoa(attr_data[4:8])
                return (ip, port)

        # 4바이트 정렬
        offset += 4 + attr_length
        if attr_length % 4:
            offset += 4 - (attr_length % 4)

    return None


def stun_get_mapped_address(sock=None, server=None, timeout=2.0):
    """
    STUN 서버를 통해 자신의 공인 IP:Port를 알아냄.

    sock: 사용할 UDP 소켓 (None이면 임시 생성)
    server: (host, port) STUN 서버 (None이면 자동 선택)
    timeout: 응답 대기 시간

    반환: (public_ip, public_port) 또는 None
    """
    own_sock = False
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        own_sock = True

    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)

    servers = [server] if server else STUN_SERVERS
    result = None

    for stun_server in servers:
        try:
            request, txn_id = build_binding_request()
            sock.sendto(request, stun_server)

            data, addr = sock.recvfrom(1024)
            result = parse_binding_response(data, txn_id)
            if result:
                break
        except (socket.timeout, OSError):
            continue

    sock.settimeout(old_timeout)
    if own_sock:
        sock.close()

    return result


def stun_discover(local_port=0, retries=2):
    """
    STUN으로 공인 주소 확인 (편의 함수).

    local_port: 바인딩할 로컬 포트 (0이면 OS 자동 할당)
    반환: (public_ip, public_port, local_port) 또는 None
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", local_port))
        actual_port = sock.getsockname()[1]

        for attempt in range(retries):
            result = stun_get_mapped_address(sock)
            if result:
                return (result[0], result[1], actual_port)
            time.sleep(0.5)

        return None
    finally:
        sock.close()
