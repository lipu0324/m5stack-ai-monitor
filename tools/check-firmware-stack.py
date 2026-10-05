#!/usr/bin/env python3
"""Reject large network stack frames in the actual Xtensa firmware binary."""
import json
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
objdump = Path.home() / '.platformio/packages/toolchain-xtensa-esp32/bin/xtensa-esp32-elf-objdump'
elf = root / '.pio/build/m5stack-core/firmware.elf'
assembly = subprocess.check_output([str(objdump), '-d', '-C', str(elf)], text=True)
frames = {}
for function in ('fetch', 'resetSnapshot', 'networkWorker'):
    match = re.search(
        rf'^[0-9a-f]+ <{function}\([^\n]+>:\n[^\n]*\bentry\s+a1,\s*(0x[0-9a-f]+|\d+)',
        assembly, re.MULTILINE,
    )
    if not match:
        raise SystemExit(f'Cannot inspect {function}: build firmware first')
    frames[function] = int(match.group(1), 0)
    if frames[function] > 1024:
        raise SystemExit(f'{function} uses {frames[function]} bytes: oversized frame')
print(json.dumps({'stack_frame_bytes': frames, 'worker_stack_bytes': 8192}))
# This measures our frames, not every nested SDK call. INFO reports the real
# FreeRTOS high-water mark after HTTP requests for the full runtime check.
