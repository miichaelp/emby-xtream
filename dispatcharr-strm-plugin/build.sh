#!/usr/bin/env bash
# Builds dist/strm_sync.zip: the plugin folder only, without tests or caches.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p dist
rm -f dist/strm_sync.zip
find strm_sync -name __pycache__ -type d -prune -exec rm -rf {} +
zip -qr dist/strm_sync.zip strm_sync -x '*.pyc'
unzip -l dist/strm_sync.zip
