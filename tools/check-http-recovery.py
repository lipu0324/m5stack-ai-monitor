#!/usr/bin/env python3
"""Short hardware check of streamed AI/host responses and speaker allocation."""
import json
import time
from device import connect, info


def command(serial, value):
    serial.write((value + '\n').encode())
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        line = serial.readline().decode(errors='replace').strip()
        if line.startswith(('UI ', 'HTTP ', 'PERF ')):
            return line
    raise TimeoutError(value)


def diagnostic(serial):
    result = json.loads(command(serial, 'HTTP_DIAG')[5:])
    assert result['snapshot_errors'] == result['host_errors'] == 0, result
    assert result['received'] == result['expected'] > 0, result
    return result


with connect() as serial:
    deadline = time.monotonic() + 40
    while True:
        state = info(serial)
        if state['uptime'] > 8 and state['wifi'] == 3 and state['message'] == '已连接':
            break
        if time.monotonic() > deadline:
            raise TimeoutError(json.dumps(state, ensure_ascii=False))
        time.sleep(1)
    command(serial, 'TAB 4')
    deadline = time.monotonic() + 15
    while True:
        state = info(serial)
        if state['host_available'] and state['host_samples'] > 1:
            break
        if time.monotonic() > deadline:
            raise TimeoutError(json.dumps(state, ensure_ascii=False))
        time.sleep(.5)
    before = diagnostic(serial)
    print('Before sound', json.dumps(dict(state, http=before), ensure_ascii=False), flush=True)
    # Honors the user's mute setting; does not create a fake AI failure/event.
    serial.write(b'TEST_SOUND\n')
    time.sleep(1)
    last_uptime = state['uptime']
    for index in range(25):
        time.sleep(2)
        if index % 5 == 0:
            command(serial, 'HOST_MODE')
            command(serial, 'SETTINGS')
            command(serial, 'BACK')
        state = info(serial)
        assert state['uptime'] >= last_uptime and state['page'] == 4, state
        last_uptime = state['uptime']
        assert state['message'] == '已连接' and state['age_ms'] < 5000, state
        assert state['host_available'] and state['host_age_ms'] < 5000, state
        assert state['visible_tasks'] == min(state['tasks'], 10), state
        print('Healthy', json.dumps(dict(state, http=diagnostic(serial)), ensure_ascii=False), flush=True)
    after = diagnostic(serial)
    assert after['snapshot_reads'] >= before['snapshot_reads'] + 10, after
    assert after['host_reads'] >= before['host_reads'] + 10, after
    print('PASS: fresh AI/host data after sound; exact lengths, no short reads or reboot.', flush=True)
