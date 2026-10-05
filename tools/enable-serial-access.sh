#!/usr/bin/env bash
set -euo pipefail
monitor_user="$(id -un)"
port="${AI_MONITOR_PORT:-/dev/ttyUSB0}"
echo "为当前用户 $monitor_user 启用串口权限；密码仅在本地终端输入。"
sudo usermod -aG dialout "$monitor_user"
sudo setfacl -m "u:$monitor_user:rw" "$port"
echo '当前设备立即可访问；dialout 组权限在重新登录后生效。'
