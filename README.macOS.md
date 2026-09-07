# macOS guide

The core server is CPU-friendly and the device panel detects Apple Silicon/CPU capabilities lazily. Install the local runtime only when you want on-device Whisper inference; otherwise use the cloud path for a quieter, cooler laptop.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
pip install -r requirements-local.txt   # optional
cp .env.example .env
./start.sh --mode auto
```

Allow microphone access for the browser at `http://127.0.0.1:5001`. See [privacy](docs/PRIVACY.md) before enabling cloud mode.
