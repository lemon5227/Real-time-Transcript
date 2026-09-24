#!/usr/bin/env bash
set -euo pipefail

CONTENTS_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="$CONTENTS_ROOT/Resources/app"
PORT="${PORT:-8765}"
export PORT
BASE_URL="http://127.0.0.1:${PORT}"
HEALTH_URL="$BASE_URL/api/health"
STARTUP_TIMEOUT_SECONDS="${TRANSCRIPT_STARTUP_TIMEOUT_SECONDS:-90}"
for common_bin in "/opt/homebrew/bin" "/usr/local/bin" "$HOME/.local/bin"; do
  if [[ -d "$common_bin" ]]; then
    PATH="$common_bin:${PATH:-}"
  fi
done
export PATH

if [[ -z "${PYTHON_BIN:-}" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_SUPPORT_ROOT="${TRANSCRIPT_APP_SUPPORT_DIR:-$HOME/Library/Application Support/拾句}"
RUNTIME_ROOT="${TRANSCRIPT_RUNTIME_DIR:-$APP_SUPPORT_ROOT/runtime}"
ENV_FILE="${TRANSCRIPT_ENV_FILE:-$RUNTIME_ROOT/.env}"
LOG_ROOT="$APP_SUPPORT_ROOT/logs"
PID_ROOT="$APP_SUPPORT_ROOT/pids"
PID_FILE="$PID_ROOT/server.pid"
LOG_FILE="$LOG_ROOT/server-$(date +%Y%m%d-%H%M%S).log"

mkdir -p "$RUNTIME_ROOT" "$LOG_ROOT" "$PID_ROOT"
chmod 700 "$RUNTIME_ROOT" "$LOG_ROOT" "$PID_ROOT"

export TRANSCRIPT_RUNTIME_DIR="$RUNTIME_ROOT"
export TRANSCRIPT_ENV_FILE="$ENV_FILE"
export PYTHONUNBUFFERED=1
default_xdg_cache_home="${XDG_CACHE_HOME:-$HOME/.cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$RUNTIME_ROOT/.cache}"
export HF_HOME="${HF_HOME:-$default_xdg_cache_home/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export WHISPER_CACHE_DIR="${WHISPER_CACHE_DIR:-$RUNTIME_ROOT/.cache/whisper}"
export NEMOTRON_MODEL_DIR="${NEMOTRON_MODEL_DIR:-$HOME/Library/Application Support/FluidAudio/Models}"
NEMOTRON_HELPER="$SOURCE_ROOT/native/echonote-nemotron-diarizer"
if [[ -z "${DIARIZATION_COMMAND:-}" && -x "$NEMOTRON_HELPER" ]]; then
  export DIARIZATION_COMMAND="$NEMOTRON_HELPER"
fi

child_pid=""
keep_child=0

cleanup() {
  if [[ "$keep_child" != "1" && -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  if [[ -f "$PID_FILE" ]]; then
    stored_pid="$(<"$PID_FILE")"
    if [[ -z "$child_pid" || "$stored_pid" == "$child_pid" ]]; then
      rm -f "$PID_FILE"
    fi
  fi
}
trap cleanup EXIT INT TERM

open_browser() {
  open "$BASE_URL/" >/dev/null 2>&1 || true
}

health_check() {
  curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1
}

wait_for_health() {
  local timeout_seconds="$1"
  local watched_pid="${2:-$child_pid}"
  # The budget starts here, not at shell start: resolving the interpreter and
  # preparing the runtime directories can take a second or two, and counting
  # that against the startup timeout left short timeouts with no real chance.
  # Polling every second was the second half of the problem -- the first check
  # fires the instant the child is spawned, before it can possibly have bound
  # its port, so a two second budget bought a single useful retry. Sub-second
  # polling gives the whole budget to the server. (Bash 3.2 on macOS has no
  # EPOCHREALTIME, so this counts attempts rather than seconds.)
  local interval="0.2"
  local attempts=$((timeout_seconds * 5))
  (( attempts < 1 )) && attempts=1
  local attempt
  for ((attempt = 0; attempt < attempts; attempt++)); do
    if health_check; then
      return 0
    fi
    if [[ -n "$watched_pid" ]] && ! kill -0 "$watched_pid" 2>/dev/null; then
      return 1
    fi
    sleep "$interval"
  done
  return 1
}

show_failure() {
  local message="$1"
  if [[ -f "$LOG_FILE" ]] && grep -q "requires Python 3.10" "$LOG_FILE"; then
    message="拾句需要 Python 3.10+。请先运行 brew install python@3.12，再重新打开应用。详细日志：$LOG_FILE"
  fi
  printf '%s\n' "$message" >&2
  if command -v osascript >/dev/null 2>&1; then
    osascript -e "display alert \"拾句启动失败\" message \"$message\"" >/dev/null 2>&1 || true
  fi
}

if health_check; then
  open_browser
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(<"$PID_FILE")"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
    if wait_for_health "$STARTUP_TIMEOUT_SECONDS" "$existing_pid"; then
      open_browser
      exit 0
    fi
    show_failure "已有启动进程但服务未就绪。请查看日志目录：$LOG_ROOT"
    exit 1
  fi
  rm -f "$PID_FILE"
fi

if [[ ! -f "$SOURCE_ROOT/quickstart.py" ]]; then
  show_failure "应用文件不完整：找不到 $SOURCE_ROOT/quickstart.py"
  exit 1
fi

(
  cd "$SOURCE_ROOT"
  exec "$PYTHON_BIN" quickstart.py --mode auto
) >>"$LOG_FILE" 2>&1 &
child_pid=$!
printf '%s\n' "$child_pid" >"$PID_FILE"
chmod 600 "$PID_FILE"

if wait_for_health "$STARTUP_TIMEOUT_SECONDS"; then
  keep_child=1
  open_browser
  exit 0
fi

show_failure "服务启动超时或失败。请查看日志：$LOG_FILE"
exit 1
