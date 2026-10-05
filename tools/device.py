#!/usr/bin/env python3
"""USB diagnostics; firmware reports no Wi-Fi password or bearer token."""
import argparse
import json
import os
import time
from pathlib import Path
import serial
from serial.tools import list_ports
def default_port():
    configured = os.environ.get('AI_MONITOR_PORT')
    if configured:
        return configured
    ports = [p.device for p in list_ports.comports() if p.vid == 0x10c4 and p.pid == 0xea60]
    if len(ports) == 1:
        return ports[0]
    if len(ports) > 1:
        raise RuntimeError('Multiple CP210x devices: set AI_MONITOR_PORT explicitly')
    return '/dev/ttyUSB0'

PORT = default_port()

def connect(port=PORT):
    s=serial.Serial(port=None,baudrate=115200,timeout=2)
    s.dtr=False;s.rts=False;s.port=port;s.open()
    time.sleep(.2);s.reset_input_buffer()
    return s

def info(s):
    s.write(b'INFO\n');until=time.monotonic()+5
    while time.monotonic()<until:
        line=s.readline().decode(errors='replace').strip()
        if line.startswith('INFO '):return json.loads(line[5:])
    raise TimeoutError('firmware INFO did not respond')

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['info','screen']);p.add_argument('--output',type=Path,default=Path('tools/device-screen.ppm'));a=p.parse_args()
    with connect() as s:
        if a.command=='info':print(json.dumps(info(s),ensure_ascii=False));return
        s.write(b'SCREEN\n')
        until=time.monotonic()+8
        while time.monotonic()<until:
            header=s.readline().decode(errors='replace').strip()
            if header.startswith('RGB '):break
        else:raise TimeoutError('screen header missing')
        _,w,h=header.split();length=int(w)*int(h)*3;pixels=bytearray()
        s.timeout=5
        while len(pixels)<length:
            block=s.read(length-len(pixels))
            if not block:raise TimeoutError('screen capture incomplete')
            pixels.extend(block)
        a.output.write_bytes(f'P6\n{w} {h}\n255\n'.encode()+pixels)
        print('LCD capture:',a.output.resolve())

if __name__=='__main__':main()
