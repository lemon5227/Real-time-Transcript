# 拾句 macOS guide

Apple Silicon Macs use the MLX Parakeet runtime for English and European-language lectures. Intel Macs stay on the standard CPU Whisper path. If the laptop is too warm or cannot keep up, use Auto/Cloud mode; the device panel shows the active runtime before class.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
pip install -r requirements-mac.txt     # Apple Silicon MLX, optional
pip install -r requirements-local.txt   # Intel Mac standard Whisper, optional
cp .env.example .env
./start.sh --mode auto
```

Allow microphone access for the browser at `http://127.0.0.1:5001`. See [privacy](docs/PRIVACY.md) before enabling cloud mode.

## macOS DMG from GitHub Actions

The `macOS DMG` workflow builds `拾句-macOS.dmg` on `workflow_dispatch` and uploads
it as the `shiju-macos-dmg` artifact. Pushing a version tag matching `v*` also
attaches the same DMG to a GitHub Release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

For a test build, open **Actions → macOS DMG → Run workflow** and optionally enter
a version. The current package is unsigned; on first launch use **Control-click →
Open**. The workflow does not download model weights.
