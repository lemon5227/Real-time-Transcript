#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/dist}"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-$(uname -m)}"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-}"

die() {
  printf 'macOS build failed: %s\n' "$1" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  die "this builder must run on macOS (Darwin)"
fi

for tool in iconutil hdiutil rsvg-convert rsync; do
  command -v "$tool" >/dev/null 2>&1 || die "missing $tool; install it before building (rsvg-convert is provided by Homebrew librsvg)"
done

if [[ -n "$CODESIGN_IDENTITY" ]] && ! command -v codesign >/dev/null 2>&1; then
  die "CODESIGN_IDENTITY was set but codesign is unavailable"
fi

SAFE_VERSION="${VERSION//[^0-9A-Za-z.-]/-}"
[[ -n "$SAFE_VERSION" ]] || SAFE_VERSION="0.1.0"

BUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/real-time-transcript-build.XXXXXX")"
trap 'rm -rf "$BUILD_ROOT"' EXIT

ICONSET_DIR="$BUILD_ROOT/Real-time Transcript.iconset"
ICNS_PATH="$BUILD_ROOT/Real-time Transcript.icns"
APP_BUNDLE="$BUILD_ROOT/Real-time Transcript.app"
CONTENTS_ROOT="$APP_BUNDLE/Contents"
APP_ROOT="$CONTENTS_ROOT/Resources/app"
mkdir -p "$ICONSET_DIR" "$CONTENTS_ROOT/MacOS" "$CONTENTS_ROOT/Resources"

render_icon() {
  local filename="$1"
  local pixels="$2"
  rsvg-convert -w "$pixels" -h "$pixels" "$PROJECT_ROOT/static/app-icon.svg" -o "$ICONSET_DIR/$filename"
}

render_icon "icon_16x16.png" 16
render_icon "icon_16x16@2x.png" 32
render_icon "icon_32x32.png" 32
render_icon "icon_32x32@2x.png" 64
render_icon "icon_128x128.png" 128
render_icon "icon_128x128@2x.png" 256
render_icon "icon_256x256.png" 256
render_icon "icon_256x256@2x.png" 512
render_icon "icon_512x512.png" 512
render_icon "icon_512x512@2x.png" 1024
iconutil --convert icns --output "$ICNS_PATH" "$ICONSET_DIR"
[[ -s "$ICNS_PATH" ]] || die "iconutil did not create an icon file"

# This is an allowlist: .git, .worktrees, .venv, .env, .cache, models, recordings,
# logs, tests, screenshots, and all other development-only files stay out of the bundle.
rsync -a \
  --exclude='__pycache__/' \
  --exclude='*.py[cod]' \
  "$PROJECT_ROOT/app.py" \
  "$PROJECT_ROOT/quickstart.py" \
  "$PROJECT_ROOT/.env.example" \
  "$PROJECT_ROOT"/requirements-*.txt \
  "$PROJECT_ROOT/backend" \
  "$PROJECT_ROOT/static" \
  "$PROJECT_ROOT/templates" \
  "$APP_ROOT/"

cp "$PROJECT_ROOT/packaging/macos/launcher.sh" "$CONTENTS_ROOT/MacOS/RealTimeTranscript"
chmod 755 "$CONTENTS_ROOT/MacOS/RealTimeTranscript"
cp "$ICNS_PATH" "$CONTENTS_ROOT/Resources/Real-time Transcript.icns"
sed "s/__VERSION__/$SAFE_VERSION/g" "$PROJECT_ROOT/packaging/macos/Info.plist" >"$CONTENTS_ROOT/Info.plist"

if [[ -n "$CODESIGN_IDENTITY" ]]; then
  codesign --force --deep --options runtime --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE"
else
  printf 'Warning: building an unsigned app; first launch may require Control-click → Open.\n'
fi

DMG_STAGE="$BUILD_ROOT/dmg"
mkdir -p "$DMG_STAGE"
cp -R "$APP_BUNDLE" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

mkdir -p "$OUTPUT_DIR"
DMG_PATH="$OUTPUT_DIR/Real-time-Transcript-macOS.dmg"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "Real-time Transcript" \
  -srcfolder "$DMG_STAGE" \
  -ov \
  -format UDZO \
  "$DMG_PATH" >/dev/null

printf 'Built %s for %s\n' "$DMG_PATH" "$ARCH"
printf 'First launch bootstraps dependencies and stores runtime data under ~/Library/Application Support/Real-time Transcript.\n'
printf 'Model weights remain outside the app bundle and are downloaded by the in-app model manager.\n'
