#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
if ! command -v ffmpeg >/dev/null; then echo '请先安装 FFmpeg（含 libx264、drawtext）'; exit 1; fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
