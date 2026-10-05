#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
port="${AI_MONITOR_PORT:-/dev/ttyUSB0}"
file="backups/core-$(date +%Y%m%d-%H%M%S)-4MB.bin"
esptool --port "$port" --baud 115200 read-flash 0 0x400000 "$file"
test "$(stat -c %s "$file")" = 4194304
sha256sum "$file" > "$file.sha256"
chmod 600 "$file" "$file.sha256"
