#!/usr/bin/env python3
"""Install the bridge and Hermes plugin; never restart an active AI task."""
import json
import importlib.util
import os
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
CONFIG = HOME / '.config/ai-monitor'
STATE = HOME / '.local/state/ai-monitor'
os.umask(0o077)
CONFIG.mkdir(parents=True, exist_ok=True)
STATE.mkdir(parents=True, exist_ok=True)
(STATE / 'events').mkdir(exist_ok=True)
config_path = CONFIG / 'config.json'
config = json.loads(config_path.read_text()) if config_path.exists() else {
    'port': 8766, 'bind': '0.0.0.0', 'token': secrets.token_urlsafe(32),
    'mdns_host': 'ai-monitor-' + secrets.token_hex(3),
    'codex_home': str(HOME / '.codex'), 'hermes_home': str(HOME / '.hermes'),
    'events_dir': str(STATE / 'events'), 'state_dir': str(STATE)}
config_path.write_text(json.dumps(config, indent=2))
config_path.chmod(0o600)
if shutil.which('hermes'):
    plugin = HOME / '.hermes/plugins/m5stack-ai-monitor'
    plugin.mkdir(parents=True, exist_ok=True)
    for name in ('__init__.py', 'plugin.yaml'):
        shutil.copy2(ROOT / 'hermes_plugin' / name, plugin / name)
    subprocess.run(['hermes','plugins','enable','m5stack-ai-monitor'], check=True, stdout=subprocess.DEVNULL)
    # An already-enabled plugin is not reloaded by the CLI; explicitly refresh its code.
    control_path = HOME / '.hermes/hermes-agent/gateway/control_socket.py'
    if control_path.exists():
        spec = importlib.util.spec_from_file_location('hermes_monitor_control', control_path)
        control = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(control)
        answer = control.reload_gateway_plugins(HOME / '.hermes')
        print('Gateway hot reload:', bool(answer and answer.get('reloaded')))
else:
    print('Hermes CLI not found; continuing with Codex monitoring.')
unit_dir = HOME / '.config/systemd/user'
unit_dir.mkdir(parents=True, exist_ok=True)
(unit_dir / 'ai-monitor.service').write_text(f'''[Unit]
Description=M5Stack AI status bridge
After=network.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={ROOT}/.venv/bin/python {ROOT}/host/server.py --config {config_path}
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
''')
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
subprocess.run(['systemctl', '--user', 'enable', '--now', 'ai-monitor.service'], check=True)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(('192.0.2.1', 80))
    address = s.getsockname()[0]
finally:
    s.close()
pairing = CONFIG / 'pairing.txt'
pairing.write_text(f'''M5Stack AI Monitor 配网信息（仅保存在本机）

服务地址：http://{address}:{config['port']}
mDNS 地址：http://{config['mdns_host']}.local:{config['port']}
监视 Token：{config['token']}

设备首次启动显示临时热点名称和密码。
手机连接该热点，打开 http://192.168.4.1 ，填入 2.4GHz Wi-Fi、上面的服务地址和 Token。
现有 Gateway 不会被安装脚本重启；重启或重新启动 Hermes 后加载监视插件。
不要把本文件或 Token 提交到代码仓库。
''')
pairing.chmod(0o600)
print('Installed ai-monitor.service on port', config['port'])
print('Private pairing details:', pairing)
if shutil.which('hermes'):
    print('Hermes observer installed; active Hermes processes are not restarted.')
