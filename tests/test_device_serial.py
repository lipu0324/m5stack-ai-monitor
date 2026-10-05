import importlib.util
import os
import pty
import termios
import unittest
from pathlib import Path


class DeviceSerialTests(unittest.TestCase):
    def test_passive_uart_frames_binary_and_timeout(self):
        path=Path(__file__).resolve().parents[1]/'tools/device.py'
        spec=importlib.util.spec_from_file_location('monitor_device',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        master,slave=pty.openpty()
        try:
            with module.PassiveSerial(os.ttyname(slave),timeout=.03) as uart:
                settings=termios.tcgetattr(uart.fd)
                self.assertFalse(settings[2]&termios.HUPCL)
                os.write(master,b'INFO {}\nRGB 1 1\n\x00\x80\xff')
                self.assertEqual(uart.readline(),b'INFO {}\n')
                self.assertEqual(uart.readline(),b'RGB 1 1\n')
                self.assertEqual(uart.read(3),b'\x00\x80\xff')
                self.assertEqual(uart.read(1),b'')
                self.assertEqual(uart.write(b'NET_INFO\n'),9)
                self.assertEqual(os.read(master,1024),b'NET_INFO\n')
        finally:
            os.close(master);os.close(slave)
