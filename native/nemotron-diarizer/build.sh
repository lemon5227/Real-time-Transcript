#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="$ROOT/patches/disable-coreml-output-backings.patch"
CHECKOUT="$ROOT/.build/checkouts/FluidAudio"

cd "$ROOT"

swift package resolve

if [[ ! -d "$CHECKOUT" ]]; then
  printf '%s\n' "FluidAudio checkout was not created" >&2
  exit 1
fi

if grep -q 'options.outputBackings = backings' "$CHECKOUT/Sources/FluidAudio/Diarizer/Nemotron3/Nemotron3Models.swift"; then
  git -C "$CHECKOUT" apply --check "$PATCH_FILE"
  git -C "$CHECKOUT" apply "$PATCH_FILE"
fi

swift build -c release "$@"
