# Quickstart

## Fast path

macOS/Linux:

```bash
./quickstart.sh
```

Windows PowerShell:

```powershell
.\quickstart.ps1
```

The launcher creates or reuses `.venv`, preserves an existing `.env`, and
selects native MLX, local/CUDA, or cloud dependencies for the current device.
Use `--mode local` or `--mode cloud` (PowerShell: `-Mode Local` or
`-Mode Cloud`) to override automatic selection. See
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for Docker and Render one-click
deployment.

## macOS DMG

For the packaged desktop app, a release maintainer can build
`dist/Real-time-Transcript-macOS.dmg` on macOS after installing Homebrew
`librsvg`:

```bash
brew install librsvg
./scripts/build-macos-dmg.sh
```

Users drag the app from the DMG to Applications and open it. If macOS blocks
an unsigned local build, use **Control-click → Open** for the first launch.
The app keeps the writable environment, `.env`, logs, caches, and model weights
outside the bundle in `~/Library/Application Support/Real-time Transcript`.
Model weights download through the model manager on first use; they are not
embedded in the DMG. The repository commands above continue to use the local
`.venv` and `.env` exactly as before.

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

For English lectures on a Mac/MPS laptop, choose `Distil Small EN` in the classroom page. It is a lightweight faster-whisper model downloaded on first use. Other languages should use the multilingual `Base` or `Small` model.

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

Optional fast translation:

```dotenv
TRANSLATION_GOOGLE_PROJECT_ID=your-google-project
TRANSLATION_GOOGLE_API_KEY=your-google-key
TRANSLATION_MICROSOFT_API_KEY=your-microsoft-key
TRANSLATION_MICROSOFT_REGION=your-resource-region
```

Use the Google or Microsoft selector in the classroom page for quick translation. Google has a best-effort public path that works without a key; configure the official Google or Microsoft variables when you need a more stable service. For precise local translation, run an OpenAI-compatible local server such as Ollama or LM Studio and set `TRANSLATION_LOCAL_BASE_URL` and `TRANSLATION_LOCAL_MODEL`; an API key is optional for local servers. For cloud precise translation, configure `TRANSLATION_CLOUD_BASE_URL`, `TRANSLATION_CLOUD_API_KEY` and `TRANSLATION_CLOUD_MODEL`. Keys remain in the backend `.env` and translation sends text only, never the saved original audio.

Google Cloud Translation Basic currently has a monthly free allowance listed by Google, and Azure Translator F0 currently lists 2 million characters per month free. Both require provider accounts and may require billing setup. Follow the detailed [translation API setup in the Chinese README](README.zh-CN.md#翻译-api-可选) before class; the Google API-key path uses Basic v2, not Advanced v3.

## 3. Use it in class

1. Open `http://127.0.0.1:5001/`.
2. In **上课前检查**, confirm the selected model says **已就绪**. If not, download Tiny or Small there; the page shows progress and allows cancellation.
3. Select an input device, click **测试麦克风**, and speak until the level says **可以开始**.
4. Add a course label and choose language/model/path. Auto mode asks you to explicitly switch to cloud before sending audio when local weights are not ready.
5. Click **开始听课**, allow the microphone and keep the current sentence in view.
6. Leave **保存原声** enabled if you want to replay the lecturer later; the original is saved locally in browser storage by default.
7. If needed, choose **实时翻译** before class. It is off by default; after class, `/review` can translate the whole class, selected segments or one sentence and cache the result locally.
8. Stop after class. Open `/review` to search, edit, star, add notes, translate or export.

## 4. Verify the installation

```bash
python3 -c "from backend import create_app; app = create_app({}); print(app.test_client().get('/api/health').get_json())"
python3 -m pytest -q
```

Browser storage is not a permanent backup. Use **导出原声** after class if the recording must survive clearing website data or storage eviction. See [`docs/PRIVACY.md`](docs/PRIVACY.md) for local audio and translation data flow.
