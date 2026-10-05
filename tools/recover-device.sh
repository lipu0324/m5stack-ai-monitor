#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
port="${AI_MONITOR_PORT:-/dev/ttyUSB0}"
monitor_user="$(id -un)"
echo '重新烧录当前项目固件（不会恢复原厂 Flash 备份）；保持 USB 连接。'
if [[ ! -r "$port" || ! -w "$port" ]]; then
  sudo setfacl -m "u:$monitor_user:rw" "$port"
fi
pio run -t upload --upload-port "$port" 2>&1 | tee tools/recovery-upload.log
echo '烧录完成，设备已重启。'
