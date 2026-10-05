#!/usr/bin/env python3
"""USB diagnostics; firmware reports no Wi-Fi password or bearer token."""
import argparse
import json
import os
import time
import select
import termios
from pathlib import Path
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

class PassiveSerial:
    """POSIX UART access without toggling the ESP32's DTR/RTS reset pins."""
    def __init__(self, port, timeout=2):
        self.timeout=timeout
        self.buffer=bytearray()
        self.fd=os.open(port,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
        try:
            settings=termios.tcgetattr(self.fd)
            settings[0]=settings[1]=settings[3]=0
            settings[2]=termios.CS8|termios.CREAD|termios.CLOCAL
            settings[4]=settings[5]=termios.B115200
            settings[6][termios.VMIN]=settings[6][termios.VTIME]=0
            termios.tcsetattr(self.fd,termios.TCSANOW,settings)
        except BaseException:
            self.close()
            raise

    def _fill(self, deadline):
        left=max(0,deadline-time.monotonic())
        if not select.select([self.fd],[],[],left)[0]:return False
        chunk=os.read(self.fd,65536)
        self.buffer.extend(chunk)
        return bool(chunk)

    def read(self, size=1):
        deadline=time.monotonic()+self.timeout
        while len(self.buffer)<size and self._fill(deadline):pass
        data=bytes(self.buffer[:size]);del self.buffer[:len(data)]
        return data

    def readline(self):
        deadline=time.monotonic()+self.timeout
        while b'\n' not in self.buffer and self._fill(deadline):pass
        end=self.buffer.find(b'\n')+1 or len(self.buffer)
        data=bytes(self.buffer[:end]);del self.buffer[:end]
        return data

    def write(self, data):
        written=0;deadline=time.monotonic()+self.timeout
        while written<len(data):
            if not select.select([],[self.fd],[],max(0,deadline-time.monotonic()))[1]:
                raise TimeoutError('serial write timed out')
            written+=os.write(self.fd,data[written:])
        return written

    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def connect(port=PORT):
    return PassiveSerial(port)

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
