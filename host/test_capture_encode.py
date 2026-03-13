"""
캡처 → 리사이즈 → 인코딩 → 파일 저장 테스트
"""

import time
import sys
import cv2
from capture import ScreenCapture
from encoder import H264Encoder

OUTPUT_FILE = "test_output.h264"
DURATION = 5

print("=" * 50)
print("  Capture + Encode Test (cv2 resize)")
print("=" * 50)

cap = ScreenCapture(monitor_index=0)
cap.start()

print("\n[*] Waiting for first frame...")
frame = None
for _ in range(30):
    frame = cap.get_frame(timeout=0.1)
    if frame is not None:
        break

if frame is None:
    print("[!] No frames!")
    sys.exit(1)

cap_h, cap_w = frame.shape[:2]
print(f"[+] Capture: {cap_w}x{cap_h}")

scale = 1920 / cap_w if cap_w > 1920 else 1.0
enc_w = int(cap_w * scale) // 2 * 2
enc_h = int(cap_h * scale) // 2 * 2
print(f"[+] Encode: {enc_w}x{enc_h}")

total_encoded = 0
out_file = open(OUTPUT_FILE, "wb")

def on_encoded(data):
    global total_encoded
    out_file.write(data)
    total_encoded += len(data)

# 입력=출력 해상도 (Python에서 미리 리사이즈)
enc = H264Encoder(width=enc_w, height=enc_h, fps=30, bitrate="4M")
enc.on_encoded = on_encoded
enc.start()

print(f"\n[*] Recording {DURATION}s...")
start = time.time()
frames = 0

while time.time() - start < DURATION:
    f = cap.get_frame(timeout=0.05)
    if f is not None:
        h, w = f.shape[:2]
        if w != enc_w or h != enc_h:
            f = cv2.resize(f, (enc_w, enc_h), interpolation=cv2.INTER_LINEAR)
        enc.encode_frame(f)
        frames += 1

time.sleep(1)
enc.stop()
cap.stop()
out_file.close()

elapsed = time.time() - start
print(f"\n{'=' * 50}")
print(f"  Frames: {frames} ({frames/elapsed:.1f} fps)")
print(f"  Size: {total_encoded / 1024:.0f} KB")
print(f"  Bitrate: {total_encoded * 8 / elapsed / 1024 / 1024:.1f} Mbps")
print(f"  Output: {OUTPUT_FILE}")
print(f"{'=' * 50}")
