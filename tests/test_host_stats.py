import json
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from host_stats import HostStats


class HostStatsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'net').mkdir()
        self.tick = 10
        disk = namedtuple('Disk', 'total used free')(1000, 400, 600)
        self.stats = HostStats(self.root, clock=lambda: self.tick,
                              wall=lambda: 1000 + self.tick, disk=lambda _: disk)
        self.write()

    def tearDown(self):
        self.directory.cleanup()

    def write(self, cpu='100 0 100 800 50 0 0 0 99 0', rx=1000, tx=200, uptime=100):
        (self.root / 'stat').write_text('cpu ' + cpu + '\n')
        (self.root / 'meminfo').write_text('MemTotal: 10000 kB\nMemAvailable: 7000 kB\n')
        (self.root / 'uptime').write_text(f'{uptime} 50\n')
        (self.root / 'loadavg').write_text('1.2 2.3 3.4 2/100 99\n')
        (self.root / 'net/route').write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
            'eth1 00000000 01000000 0003 0 0 600 00000000\n'
            'eth0 00000000 01000000 0003 0 0 100 00000000\n')
        (self.root / 'net/dev').write_text('header\nheader\n'
            f'eth0: {rx} 0 0 0 0 0 0 0 {tx} 0 0 0 0 0 0 0\n'
            'lo: 99999999 0 0 0 0 0 0 0 99999999 0 0 0 0 0 0 0\n'
            'eth1: 99999999 0 0 0 0 0 0 0 99999999 0 0 0 0 0 0 0\n')

    def test_real_deltas_exclude_guest_and_loopback(self):
        self.stats.sample()
        first = self.stats.snapshot()
        self.assertIsNone(first['cpu_percent'])
        self.assertIsNone(first['network']['rx_bps'])
        self.tick = 12
        self.write(cpu='200 0 200 850 100 0 0 0 999999 0', rx=5000, tx=1200)
        self.stats.sample()
        value = self.stats.snapshot()
        self.assertEqual(value['cpu_percent'], 66.7)
        self.assertEqual(value['memory']['percent'], 30)
        self.assertEqual(value['memory']['used'], 3000 * 1024)
        self.assertEqual(value['disk']['percent'], 40)
        self.assertEqual(value['network']['interface'], 'eth0')
        self.assertEqual(value['network']['rx_bps'], 2000)
        self.assertEqual(value['network']['tx_bps'], 500)

    def test_counter_reset_and_interface_change_never_spike(self):
        self.stats.sample()
        self.tick += 2
        self.write(cpu='1 0 1 1 1 0 0 0', rx=0, tx=0)
        self.stats.sample()
        value = self.stats.snapshot()
        self.assertIsNone(value['cpu_percent'])
        self.assertIsNone(value['network']['rx_bps'])
        self.tick += 2
        self.stats.interface = 'eth1'
        self.stats.sample()
        self.assertIsNone(self.stats.snapshot()['network']['rx_bps'])
        self.assertEqual(len(self.stats.snapshot()['history']), 1)

    def test_failure_retains_values_and_time_then_recovers(self):
        self.stats.sample()
        before = self.stats.snapshot()
        (self.root / 'stat').unlink()
        self.tick += 2
        self.stats.sample()
        failed = self.stats.snapshot()
        self.assertFalse(failed['available'])
        self.assertEqual(failed['observed_at'], before['observed_at'])
        self.assertEqual(failed['memory'], before['memory'])
        self.write()
        self.tick += 2
        self.stats.sample()
        self.assertTrue(self.stats.snapshot()['available'])
        self.assertIsNone(self.stats.snapshot()['cpu_percent'])

    def test_history_bounded_and_unknown_network_is_not_zero(self):
        (self.root / 'net/route').unlink()
        for i in range(100):
            self.tick += 2
            self.stats.sample()
        value = self.stats.snapshot()
        self.assertEqual(len(value['history']), 60)
        self.assertFalse(value['network']['available'])
        self.assertIsNone(value['network']['rx_bps'])
        self.assertLess(len(json.dumps(value).encode()), 6000)
        value['history'].clear()
        self.assertEqual(len(self.stats.snapshot()['history']), 60)

    def test_host_reboot_clears_old_history(self):
        self.stats.sample()
        self.write(uptime=1)
        self.tick += 2
        self.stats.sample()
        value = self.stats.snapshot()
        self.assertEqual(len(value['history']), 1)
        self.assertIsNone(value['cpu_percent'])
