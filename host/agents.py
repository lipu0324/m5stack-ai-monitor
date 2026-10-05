"""Discover local agents without running them or retaining command arguments."""
import os
import shutil
import time
from pathlib import Path

# IDs and order are part of the device protocol; append new entries, never reorder.
AGENTS = (
    ('codex', 'Codex', ('codex', 'ChatGPT'), ('.codex',)),
    ('hermes', 'Hermes', ('hermes', 'hermes-desktop'), ('.hermes',)),
    ('opencode', 'OpenCode', ('opencode',), ('.opencode/bin/opencode', '.local/share/opencode/opencode.db')),
    ('claude', 'Claude Code', ('claude',), ('.claude',)),
    ('gemini', 'Gemini CLI', ('gemini',), ('.gemini',)),
    ('aider', 'Aider', ('aider',), ('.aider.conf.yml',)),
    ('goose', 'Goose', ('goose',), ('.config/goose',)),
    ('amp', 'Amp', ('amp',), ('.config/amp',)),
    ('cursor', 'Cursor Agent', ('cursor-agent',), ('.local/bin/cursor-agent',)),
)
IDS = tuple(a[0] for a in AGENTS)
LABELS = {a[0]: a[1] for a in AGENTS}


def agent_command(args):
    """Match executable/CLI entrypoint, never prompt text or arbitrary substrings."""
    if not args:
        return None
    first = Path(args[0]).name
    candidates = [first]
    if first in ('node', 'nodejs', 'bun', 'python', 'python3') and len(args) > 1:
        entry = args[1].replace('\\', '/')
        candidates.append(Path(entry).name)
        for agent, _, _, _ in AGENTS:
            package = {'claude': '@anthropic-ai/claude-code', 'gemini': '@google/gemini-cli',
                       'hermes': 'hermes_cli', 'aider': 'aider'}.get(agent)
            if package and (f'/{package}/' in entry or (args[1] == '-m' and len(args) > 2 and args[2] == package)):
                return agent
    for agent, _, commands, _ in AGENTS:
        if any(c in commands for c in candidates):
            return agent
    return None


class AgentDiscovery:
    def __init__(self, home=None, proc='/proc', path=None):
        self.home = Path(home) if home else Path.home()
        self.proc = Path(proc)
        self.path = path
        self.processes = {key: [] for key in IDS}
        self.detected = {}
        self.last_scan = 0

    def scan(self, force=False):
        if not force and time.monotonic() - self.last_scan < 5:
            return self.detected
        processes = {key: [] for key in IDS}
        for directory in self.proc.glob('[0-9]*'):
            try:
                if directory.stat().st_uid != os.getuid():
                    continue
                args = [x.decode(errors='replace') for x in (directory/'cmdline').read_bytes().split(b'\0') if x]
                key = agent_command(args)
                if key:
                    processes[key].append(int(directory.name))
            except (OSError, ValueError):
                continue
        found = {}
        for key, label, commands, footprints in AGENTS:
            executable = any(shutil.which(c, path=self.path) for c in commands)
            # Desktop and pip/npm installs may not be in a systemd user's PATH.
            local = any((self.home/p).exists() for p in footprints)
            installed = bool(executable or local)
            found[key] = {'id': key, 'label': label, 'detected': installed or bool(processes[key]),
                          'installed': installed, 'process_count': len(processes[key]),
                          'online': bool(processes[key]), 'capability': 'tasks' if key in IDS[:3] else 'presence',
                          'healthy': True, 'live': False, 'wait_supported': False,
                          'detail': '仅检测进程；任务状态与用量未知' if processes[key] else '已发现安装或本地数据' if installed else '未发现'}
        self.processes, self.detected = processes, found
        self.last_scan = time.monotonic()
        return found

    def opencode_urls(self):
        """Only inspect sockets owned by discovered OpenCode processes; no port scan."""
        inodes = set()
        for pid in self.processes['opencode']:
            try:
                for fd in (self.proc/str(pid)/'fd').iterdir():
                    link = fd.readlink().as_posix()
                    if link.startswith('socket:['):
                        inodes.add(link[8:-1])
            except OSError:
                continue
        urls = []
        try:
            for line in (self.proc/'net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] != '0A' or fields[9] not in inodes:
                    continue
                address, port = fields[1].split(':')
                if address in ('0100007F', '00000000'):
                    urls.append(f'http://127.0.0.1:{int(port,16)}')
        except (OSError, IndexError, ValueError):
            pass
        return sorted(set(urls))[:8]
