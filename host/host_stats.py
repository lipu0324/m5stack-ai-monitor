"""Bounded, non-blocking Linux host telemetry; no subprocesses or process names."""
import copy
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path


class HostStats:
    def __init__(self, proc='/proc', interface=None, clock=time.monotonic,
                 wall=time.time, disk=shutil.disk_usage):
        self.proc = Path(proc)
        self.interface = interface
        self.clock, self.wall, self.disk = clock, wall, disk
        self.lock = threading.Lock()
        self.history = deque(maxlen=60)
        self.previous = None
        self.current = {'available': False, 'observed_at': 0, 'detail': '等待主机采样'}

    def _cpu(self):
        fields = (self.proc / 'stat').read_text().splitlines()[0].split()
        if fields[0] != 'cpu' or len(fields) < 5:
            raise ValueError('invalid aggregate cpu counters')
        # guest/guest_nice are already included in user/nice. Do not add twice.
        counters = [int(v) for v in fields[1:9]]
        if any(v < 0 for v in counters):
            raise ValueError('negative cpu counters')
        return sum(counters), counters[3] + (counters[4] if len(counters) > 4 else 0)

    def _memory(self):
        info = {}
        for line in (self.proc / 'meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            info[key] = int(value.split()[0]) * 1024
        total, available = info['MemTotal'], info['MemAvailable']
        if total <= 0 or not 0 <= available <= total:
            raise ValueError('invalid memory counters')
        return {'total': total, 'used': total - available,
                'percent': round(100 * (total - available) / total, 1)}

    def _network(self):
        try:
            interface = self.interface
            if not interface:
                routes = []
                for line in (self.proc / 'net/route').read_text().splitlines()[1:]:
                    f = line.split()
                    if len(f) >= 8 and f[1] == '00000000' and f[7] == '00000000' and int(f[3], 16) & 1 and f[0] != 'lo':
                        routes.append((int(f[6]), f[0]))
                interface = min(routes)[1] if routes else None
            for line in (self.proc / 'net/dev').read_text().splitlines()[2:]:
                name, counters = line.split(':', 1)
                if name.strip() == interface:
                    values = counters.split()
                    return interface, int(values[0]), int(values[8])
        except (OSError, ValueError, IndexError):
            pass
        return None, None, None

    def sample(self):
        now = self.clock()
        try:
            total, idle = self._cpu()
            memory = self._memory()
            uptime = float((self.proc / 'uptime').read_text().split()[0])
            load = [round(float(x), 2) for x in (self.proc / 'loadavg').read_text().split()[:3]]
            if uptime < 0 or len(load) != 3:
                raise ValueError('invalid host counters')
            disk = self.disk('/')
            disk_info = {'total': disk.total, 'used': disk.used,
                         'percent': round(100 * disk.used / disk.total, 1) if disk.total else None}
            interface, rx, tx = self._network()
            cpu = rx_rate = tx_rate = None
            previous = self.previous
            if previous and uptime >= previous['uptime'] and now > previous['time']:
                delta, idle_delta = total - previous['total'], idle - previous['idle']
                if delta > 0 and 0 <= idle_delta <= delta:
                    cpu = round(100 * (delta - idle_delta) / delta, 1)
                if interface and interface == previous['interface'] and previous['rx'] is not None:
                    elapsed = now - previous['time']
                    if rx >= previous['rx'] and tx >= previous['tx']:
                        rx_rate, tx_rate = round((rx - previous['rx']) / elapsed), round((tx - previous['tx']) / elapsed)
            self.previous = dict(time=now, total=total, idle=idle, uptime=uptime,
                                 interface=interface, rx=rx, tx=tx)
            value = {'available': True, 'observed_at': int(self.wall()), 'detail': '',
                     'cpu_percent': cpu, 'cpu_count': os.cpu_count() or 1,
                     'memory': memory, 'disk': disk_info, 'load': load,
                     'uptime_seconds': int(uptime),
                     'network': {'available': interface is not None, 'interface': interface,
                                 'rx_bps': rx_rate, 'tx_bps': tx_rate}}
            with self.lock:
                # A reboot or interface switch starts a new trend, never connects unrelated rates.
                if previous and (uptime < previous['uptime'] or interface != previous['interface']):
                    self.history.clear()
                self.current = value
                self.history.append([value['observed_at'], cpu, memory['percent'], rx_rate, tx_rate])
        except (OSError, ValueError, KeyError, IndexError):
            # Preserve last known values/time, marking failure explicitly instead of inventing zeros.
            self.previous = None
            with self.lock:
                self.current = dict(self.current, available=False, detail='主机采样不可用')

    def snapshot(self):
        with self.lock:
            return dict(copy.deepcopy(self.current), sample_interval=2,
                        history=copy.deepcopy(list(self.history)))
