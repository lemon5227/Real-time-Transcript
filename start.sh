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

ECHONOTE_ROOT="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
ECHONOTE_PYTHON="$ECHONOTE_ROOT/.venv/bin/python"
if [[ ! -x "$ECHONOTE_PYTHON" ]]; then
  ECHONOTE_PYTHON="python3"
fi

exec "$ECHONOTE_PYTHON" "$ECHONOTE_ROOT/app.py"
