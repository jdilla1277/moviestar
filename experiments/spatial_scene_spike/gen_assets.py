from pathlib import Path
import math, struct, subprocess, zlib

root = Path(__file__).resolve().parent
font = root.parents[1] / 'app/src/moviestar/fonts/Inter-Bold.ttf'
for label, color in [('HOST', '0x254f8d'), ('ALICE', '0xa54457'), ('BOB', '0x397453'), ('CIVIC', '0x626698')]:
    out = root / f'{label.lower()}.mp4'
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=6', '-vf', f'drawtext=fontfile={font}:text={label}:fontsize=54:fontcolor=white:box=1:boxcolor={color}@0.8:boxborderw=15:x=(w-text_w)/2:y=(h-text_h)/2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(out)]
    subprocess.run(cmd, check=True)

def chunk(tag, body):
    return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff)

w = h = 96
rows = []
for y in range(h):
    row = bytearray([0])
    for x in range(w):
        radius = math.hypot(x - 48, y - 48)
        a = max(0, min(255, round((43 - radius) * 255)))
        row.extend((255, 204, 58, a))
    rows.append(bytes(row))
data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b'')
(root / 'logo.png').write_bytes(data)
