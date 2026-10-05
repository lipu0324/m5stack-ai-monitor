#!/usr/bin/env python3
"""Short, read-only host-chart review; keeps one USB connection open."""
import json
import time
from pathlib import Path
from PIL import Image
from device import connect, info

output = Path('.private')
output.mkdir(exist_ok=True)


def command(serial, value):
    serial.write((value + '\n').encode())
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        line = serial.readline().decode(errors='replace').strip()
        if line.startswith(('UI ', 'PERF ')):
            return line
    raise TimeoutError(value)


def wait(serial, predicate, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = info(serial)
        if predicate(state):
            return state
        time.sleep(.5)
    raise TimeoutError(json.dumps(state, ensure_ascii=False))


def capture(serial, name):
    serial.write(b'SCREEN\n')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if serial.readline().strip() == b'RGB 320 240':
            break
    else:
        raise TimeoutError('LCD header missing')
    pixels = bytearray()
    serial.timeout = 5
    while len(pixels) < 230400:
        chunk = serial.read(230400 - len(pixels))
        if not chunk:
            raise TimeoutError('LCD capture incomplete')
        pixels.extend(chunk)
    Image.frombytes('RGB', (320, 240), bytes(pixels)).save(output / (name + '.png'))
    print('Captured', name, flush=True)


with connect() as serial:
    baseline = wait(serial, lambda s: s['uptime'] > 8 and s['wifi'] == 3
                    and s['message'] == '已连接' and s['age_ms'] < 5000)
    for index in (0, 1, 2, 3, 4, 5, -1, 0):
        command(serial, f'TAB {index}')
        assert info(serial)['page'] == index % 5
    for origin in range(5):
        command(serial, f'TAB {origin}')
        command(serial, 'SETTINGS')
        assert info(serial)['page'] == 5
        command(serial, 'BACK')
        assert info(serial)['page'] == origin
        command(serial, 'SETTINGS')
        command(serial, 'SELECT_BACK')
        assert info(serial)['page'] == origin
    print('Five-tab wrap and modal settings return: PASS', flush=True)
    command(serial, 'TAB 4')
    ready = wait(serial, lambda s: s['host_available'] and s['host_samples'] > 1
                 and s['host_age_ms'] < 5000)
    assert ready['visible_tasks'] == min(ready['tasks'], 10), ready
    assert ready['events'] >= baseline['events'], ready
    print('Host ready', json.dumps(ready, ensure_ascii=False), flush=True)
    time.sleep(.4)
    capture(serial, 'v4-host-resources')
    print(command(serial, 'PERF'), flush=True)
    command(serial, 'HOST_MODE')
    time.sleep(.4)
    capture(serial, 'v4-host-network')
    print(command(serial, 'PERF'), flush=True)
    restored = wait(serial, lambda s: s['age_ms'] < 5000 and s['host_age_ms'] < 5000)
    assert restored['uptime'] > baseline['uptime']
    assert restored['host_samples'] <= 60 and restored['message'] == '已连接'
    for _ in range(6):
        time.sleep(2)
        state = info(serial)
        assert state['uptime'] >= restored['uptime']
        assert state['host_available'] and state['age_ms'] < 5000 and state['host_age_ms'] < 5000
        print('Healthy', json.dumps(state, ensure_ascii=False), flush=True)
    command(serial, 'HOST_MODE')
    print('Final', json.dumps(info(serial), ensure_ascii=False), flush=True)
