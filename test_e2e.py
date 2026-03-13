"""
End-to-end 테스트: Host + Viewer를 하나의 프로세스에서 실행
네트워크 연결 디버깅용
"""

import sys
import time
import threading
import socket
import struct

sys.path.insert(0, "host")
sys.path.insert(0, "viewer")

from host.capture import ScreenCapture
from host.encoder import H264Encoder
from host.network import StreamServer, HEADER_FMT, HEADER_SIZE, PKT_VIDEO, PKT_CONTROL
from host.network import FLAG_FRAGMENT, FLAG_LAST_FRAGMENT, FLAG_KEYFRAME

import json, cv2

VIDEO_PORT = 9100   # 충돌 방지
CTRL_PORT = 9101

print("=" * 50)
print("  E2E Test")
print("=" * 50)

# --- Host 측 ---
server = StreamServer("0.0.0.0", VIDEO_PORT, CTRL_PORT)
server.start()

cap = ScreenCapture(monitor_index=0)
cap.start()

print("[*] Waiting for capture...")
frame = None
for _ in range(30):
    frame = cap.get_frame(timeout=0.1)
    if frame is not None:
        break

if frame is None:
    print("[!] No frames")
    sys.exit(1)

cap_h, cap_w = frame.shape[:2]
enc_w = min(cap_w, 1920)
enc_h = int(cap_h * enc_w / cap_w) // 2 * 2
print(f"[+] Capture: {cap_w}x{cap_h}, Encode: {enc_w}x{enc_h}")

nal_count = [0]
nal_types_seen = set()
total_video_bytes = [0]

def on_nal(nal_data, nal_type, is_keyframe):
    nal_count[0] += 1
    nal_types_seen.add(nal_type)
    total_video_bytes[0] += len(nal_data)
    server.send_video_nal(nal_data, nal_type, is_keyframe)

enc = H264Encoder(width=enc_w, height=enc_h, fps=30, bitrate="4M")
enc.on_nal = on_nal
enc.start()

# --- Viewer 측 (같은 프로세스에서 TCP 연결) ---
print("\n[*] Connecting viewer...")
tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
tcp.connect(("127.0.0.1", CTRL_PORT))
print(f"[+] TCP connected")

# set_udp_port 전송
ctrl_msg = json.dumps({"cmd": "set_udp_port", "port": VIDEO_PORT}).encode()
header = struct.pack(HEADER_FMT, PKT_CONTROL, 0, 0, len(ctrl_msg))
tcp.sendall(header + ctrl_msg)

# UDP 수신 소켓
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
udp.bind(("0.0.0.0", VIDEO_PORT))
udp.settimeout(0.5)

time.sleep(1)
print(f"[*] Server viewer_addr: {server.viewer_addr}")
print(f"[*] Server connected: {server.viewer_addr is not None}")

# --- 메인 루프: 캡처 → 인코딩, UDP 수신 ---
print(f"\n[*] Running 5 seconds...")
start = time.time()
frames_sent = 0
pkts_received = 0
bytes_received = 0

while time.time() - start < 5:
    f = cap.get_frame(timeout=0.03)
    if f is not None:
        h, w = f.shape[:2]
        if w != enc_w or h != enc_h:
            f = cv2.resize(f, (enc_w, enc_h), interpolation=cv2.INTER_LINEAR)
        enc.encode_frame(f)
        frames_sent += 1

    # UDP 수신 체크
    try:
        data, addr = udp.recvfrom(65536)
        pkts_received += 1
        bytes_received += len(data)
    except socket.timeout:
        pass

# 정리
time.sleep(1)
enc.stop()
cap.stop()
server.stop()
tcp.close()
udp.close()

elapsed = time.time() - start
print(f"\n{'=' * 50}")
print(f"  Results:")
print(f"  Frames sent to encoder: {frames_sent}")
print(f"  NALs produced: {nal_count[0]}")
print(f"  NAL types seen: {nal_types_seen}")
print(f"  Video bytes from encoder: {total_video_bytes[0] / 1024:.0f} KB")
print(f"  UDP packets received: {pkts_received}")
print(f"  UDP bytes received: {bytes_received / 1024:.0f} KB")
net = server.get_stats()
print(f"  Network packets sent: {net['packets_sent']}")
print(f"  Network bytes sent: {net['bytes_sent'] / 1024:.0f} KB")
print(f"{'=' * 50}")

if enc.sps:
    print(f"\n  SPS cached: {len(enc.sps)} bytes")
if enc.pps:
    print(f"  PPS cached: {len(enc.pps)} bytes")
