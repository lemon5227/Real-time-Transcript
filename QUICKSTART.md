# Quickstart

## 1. Prepare the core app

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
```

The core stack includes Flask, Socket.IO, CORS, NumPy and dotenv, but does not import Torch or Whisper. This keeps startup reliable on thin laptops.

## 2. Choose transcription

For local inference:

```bash
pip install -r requirements-local.txt
./start.sh --mode local
```

For a cloud endpoint:

```bash
pip install -r requirements-cloud.txt
./start.sh --mode cloud
```

For the recommended adaptive path:

```bash
./start.sh --mode auto
```

Set `CLOUD_BASE_URL`, `CLOUD_API_KEY` and `CLOUD_TRANSCRIPTION_MODEL` in `.env` before using cloud or auto fallback. Auto tries local startup first and falls back when the local model cannot load.

## 3. Use it in class

1. Open `http://127.0.0.1:5001/`.
2. Add a course label and choose language/model/path.
3. Click **开始听课**, allow the microphone and keep the current sentence in view.
4. Stop after class. Open `/review` to search, edit, star, add notes or export.

## 4. Verify the installation

```bash
python3 -c "from backend import create_app; app = create_app({}); print(app.test_client().get('/api/health').get_json())"
python3 -m pytest -q
```
