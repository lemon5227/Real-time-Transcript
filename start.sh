#!/usr/bin/env bash
set -euo pipefail

MODE="auto"
if [[ "${1:-}" == "--mode" && -n "${2:-}" ]]; then
  MODE="$2"
fi

case "$MODE" in
  auto|local|cloud) export TRANSCRIPTION_MODE="$MODE" ;;
  *) echo "Usage: ./start.sh --mode auto|local|cloud" >&2; exit 2 ;;
esac

exec python3 app.py
