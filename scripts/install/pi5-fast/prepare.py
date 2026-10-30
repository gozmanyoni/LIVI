#!/usr/bin/env python3
"""Fetch pinned, checksum-verified inputs. Requires only Python's standard library."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import urllib.request

root = Path(__file__).resolve().parents[3]
cache = root / '.cache/pi5-fast'
cache.mkdir(parents=True, exist_ok=True)
inputs = [
    ('raspios-trixie-arm64-lite.img.xz',
     'https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz',
     '483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843'),
    ('LIVI-10.2.0-linux-arm64.AppImage',
     'https://github.com/f-io/LIVI/releases/download/nightly/LIVI-10.2.0-linux-arm64.AppImage',
     'ed861717a2c40835b8069be59666ee021a8e81fc9870455dc8e8c1cd7534a970'),
]

def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()

def fetch(item):
    name, url, expected = item
    path = cache / name
    if not path.exists():
        print('Downloading ' + name, flush=True)
        tmp = path.with_suffix(path.suffix + '.partial')
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(path)
    if digest(path) != expected:
        raise RuntimeError(f'Checksum mismatch: {path}; rolling nightly may have changed. Refusing to use it.')
    print('Verified ' + name, flush=True)

with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(fetch, inputs))
base = cache / 'base.img'
stamp = cache / 'base-image.json'
valid = base.exists() and stamp.exists() and json.loads(stamp.read_text()).get('raw_sha256') == digest(base)
if not valid:
    print('Unpacking the verified base image.', flush=True)
    with lzma.open(cache / inputs[0][0], 'rb') as src, base.open('wb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    stamp.write_text(json.dumps({'raw_sha256': digest(base), 'compressed_sha256': inputs[0][2]}) + '\n')
output = root / 'dist/pi5-fastboot'
output.mkdir(parents=True, exist_ok=True)
output.chmod(0o700)
