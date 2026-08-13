"""이미지 픽셀 크기 읽기 (fix42) — 추가 패키지 없이 헤더만 파싱.

왜 필요한가:
  OBS 브라우저 소스는 그림을 '화면에 보이는 크기'가 아니라 '원본 픽셀 수'만큼
  메모리에 펼쳐서 들고 있다. 그래서 오버레이가 실제로 쓰는 메모리는
  (가로 × 세로 × 4바이트) 의 합이다. 조작앱 [이미지 관리] 탭에서 이 값을
  보여 주려고, 파일을 통째로 읽지 않고 앞부분 헤더만 읽어 크기를 얻는다.

  Pillow 를 쓰면 간단하지만 패키지가 늘고 exe 용량이 커진다. 지원 형식이
  PNG · JPEG · WEBP 세 개뿐(STICKER_EXTS)이라 직접 읽는 편이 싸다.

읽지 못하면 None 을 돌려준다 (표시만 못 할 뿐 기능에는 영향 없음).
"""
from __future__ import annotations

import struct
from pathlib import Path

BYTES_PER_PIXEL = 4          # 디코드된 비트맵 1픽셀 = RGBA 4바이트
_HEAD = 65536                # 헤더 파싱용으로 읽는 최대 바이트


def _png(b: bytes):
    # 시그니처 8 + 길이 4 + "IHDR" 4 → 가로 4 + 세로 4 (빅엔디안)
    if len(b) < 24 or not b.startswith(b"\x89PNG\r\n\x1a\n") or b[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", b[16:24])
    return (w, h) if w and h else None


def _jpeg(b: bytes):
    if len(b) < 4 or b[0:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(b)
    while i + 9 < n:
        if b[i] != 0xFF:                      # 마커 정렬이 깨지면 한 칸씩 밀어 본다
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:                    # EOI
            return None
        seglen = struct.unpack(">H", b[i + 2:i + 4])[0]
        # SOF0~SOF15 중 DHT(C4)·JPG(C8)·DAC(CC) 제외 = 크기 정보가 있는 프레임 헤더
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return (w, h) if w and h else None
        if seglen < 2:
            return None
        i += 2 + seglen
    return None


def _webp(b: bytes):
    if len(b) < 30 or b[0:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    tag = b[12:16]
    if tag == b"VP8X":                        # 확장 — 캔버스 크기가 그대로 들어 있다
        w = int.from_bytes(b[24:27], "little") + 1
        h = int.from_bytes(b[27:30], "little") + 1
        return (w, h)
    if tag == b"VP8 ":                        # 손실 — 키프레임 시작 코드 뒤 14비트씩
        if b[23:26] != b"\x9d\x01\x2a":
            return None
        w = struct.unpack("<H", b[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", b[28:30])[0] & 0x3FFF
        return (w, h) if w and h else None
    if tag == b"VP8L":                        # 무손실 — 서명 0x2f 뒤 14비트씩
        if b[20] != 0x2F:
            return None
        bits = int.from_bytes(b[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return (w, h)
    return None


def image_size(path: Path):
    """(가로, 세로) 또는 None. 파일이 없거나 형식을 못 읽으면 None."""
    try:
        with open(path, "rb") as f:
            b = f.read(_HEAD)
    except OSError:
        return None
    for fn in (_png, _jpeg, _webp):
        try:
            wh = fn(b)
        except Exception:
            wh = None
        if wh:
            return wh
    return None


def pixel_bytes(size) -> int:
    """(가로, 세로) → 디코드됐을 때 차지하는 바이트 수."""
    if not size:
        return 0
    return int(size[0]) * int(size[1]) * BYTES_PER_PIXEL
