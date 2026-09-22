# 拾句 · Real-time Transcript

面向留学生听课和课后复习的实时转录工作台。它把“听不清、跟不上、课后找不到重点”拆成一条简单的学习流：课上实时字幕，课后可搜索、编辑、标记和导出。

> The product is designed for international students: a low-distraction live caption view during class, plus a local post-class review workspace.

Current implementation, validation, and deferred work are tracked in the [project roadmap](docs/ROADMAP.md).

## What it does

- Realtime microphone transcription for English lectures and other supported languages; the active local runtime is selected by device.
- Browser audio is normalized to mono 16 kHz; common 44.1/48 kHz microphones are accepted.
- Rooms that go quiet stop costing inference: audio below `AUDIO_VAD_THRESHOLD` is handed to the model as digital silence, so battery and latency are spent on speech while the recording timeline stays exact.
- Apple Silicon Mac local mode uses the MLX Parakeet TDT v3 path for English and European-language lectures.
- Windows, Linux and Intel Mac use the standard Whisper path; NVIDIA devices use CUDA automatically, while CPU laptops use CPU inference.
- Cloud mode sends short audio windows to the endpoint configured by the user, which makes the app practical on thin laptops.
- Auto mode follows the device recommendation and uses the configured cloud provider when the local path is unavailable or unsuitable.
- Post-class review stores sessions in browser IndexedDB, with search, inline edits, notes, starred segments and TXT/Markdown/VTT/SRT export.

## Install

Python 3.10+ is recommended for the MLX Parakeet path. Cloud and standard local
profiles can still run on Python 3.9+. `ffmpeg` is not required for microphone
transcription, but is required for post-class fine transcription.

```bash
git clone https://github.com/lemon5227/Real-time-Transcript.git
cd Real-time-Transcript
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
```

For local mode, install the optional runtime (model weights download on first use):

```bash
pip install -r requirements-local.txt
```

For Apple Silicon Mac local mode, install the MLX runtime instead:

```bash
pip install -r requirements-mac.txt
```

For cloud mode, install the HTTP client:

```bash
pip install -r requirements-cloud.txt
```

For development and tests:

```bash
pip install -r requirements-dev.txt
pytest -q          # backend and browser-contract tests; needs node on PATH
ruff check .
```

## One-click startup and deployment

For native cross-platform setup, run the launcher from the repository root:

```bash
./quickstart.sh
```

It selects Apple Silicon MLX, a standard local runtime for machines where
`nvidia-smi` succeeds, or cloud mode for other machines. Override the choice
with `./quickstart.sh --mode local` or `./quickstart.sh --mode cloud`; on
Windows use `.\quickstart.ps1 -Mode Local` or `.\quickstart.ps1 -Mode Cloud`.
The launcher reuses `.venv` and `.env` and never prints or asks for API keys.

For a reproducible Docker cloud container:

```bash
cp .env.example .env
docker compose up --build
```

For the explicit standard CPU local image:

```bash
TRANSCRIPT_DOCKERFILE=Dockerfile.local DOCKER_TRANSCRIPTION_MODE=local docker compose up --build
```

Docker on macOS cannot use the host’s Apple Metal; use native `quickstart.sh`
for MLX. A cloud container requires the OpenAI-compatible
`CLOUD_BASE_URL`, `CLOUD_API_KEY`, and `CLOUD_TRANSCRIPTION_MODEL` variables.

Deploy a cloud instance from GitHub with Render:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript)

The Render service uses the lightweight cloud image and prompts for the three
private cloud variables. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for
the platform matrix, port overrides, and troubleshooting.

### macOS DMG desktop app

The release builder creates `dist/拾句-macOS.dmg` for Apple
Silicon Macs. Release maintainers need macOS, Homebrew `librsvg`, and the
native `iconutil`, `hdiutil`, and `rsync` tools:

```bash
brew install librsvg
./scripts/build-macos-dmg.sh
```

Open the DMG and drag **拾句** to Applications. On the first
launch, macOS may show a security warning because local builds are unsigned;
use **Control-click → Open** once. The app bootstraps Python dependencies and
stores its writable environment, `.env`, logs, and PID files under
`~/Library/Application Support/拾句`, never in the app bundle. Hugging Face
models such as Parakeet reuse `HF_HUB_CACHE` or `HF_HOME` when configured, and
otherwise use `~/.cache/huggingface/hub`; this also reuses models already
downloaded by other local tools. Whisper weights use the app's private runtime
cache. Models are downloaded from the in-app model manager when they are not
already cached.

To reset the packaged runtime, stop the local server and remove that
拾句 Application Support directory; Hugging Face model weights in the shared
cache are not removed by this. Source checkout startup is unchanged:
`./quickstart.sh` continues to use the repository-local `.venv` and `.env`.

On an Apple Silicon Mac, the classroom page automatically selects `Parakeet TDT v3 · Mac MLX` for English and European-language lectures. For Chinese or another unsupported language, switch to Cloud mode; this Mac path does not silently fall back to CPU Whisper. On Windows, Linux and Intel Mac, choose a standard Whisper model; an NVIDIA GPU uses CUDA automatically. Open the gear-shaped Settings button to inspect model readiness and pre-download local models before class; runtime-managed models remain available as a first-use fallback.

## Choose a runtime

| Runtime | When to choose it | Audio leaves the computer? |
| --- | --- | --- |
| Apple Silicon + MLX | English/European lectures on Mac | No |
| CUDA | Windows/Linux with NVIDIA GPU | No |
| CPU | Thin laptop with local runtime; use Auto for cloud fallback | No, unless Auto falls back to cloud |
| Cloud | Thin laptop, low memory, or Chinese on Mac | Yes, to your configured endpoint |
| Auto | Device recommendation plus cloud fallback | Only when cloud fallback is selected |

Cloud variables belong in the backend `.env` only:

```dotenv
TRANSCRIPTION_MODE=auto
CLOUD_BASE_URL=https://api.example.com/v1
CLOUD_API_KEY=your-key
CLOUD_TRANSCRIPTION_MODEL=your-transcription-model
CLOUD_TIMEOUT_SECONDS=30

# Optional fast translation / precise cloud model translation
TRANSLATION_GOOGLE_PROJECT_ID=your-google-project
TRANSLATION_GOOGLE_API_KEY=your-google-key
TRANSLATION_MICROSOFT_API_KEY=your-microsoft-key
TRANSLATION_MICROSOFT_REGION=your-resource-region
TRANSLATION_CLOUD_BASE_URL=https://api.example.com/v1
TRANSLATION_CLOUD_API_KEY=your-translation-model-key
TRANSLATION_CLOUD_MODEL=your-translation-model
```

The browser never receives `CLOUD_API_KEY`. The UI makes the current path visible and displays a privacy notice when cloud mode is selected. Read [`docs/PRIVACY.md`](docs/PRIVACY.md) before using a third-party endpoint.

Real-time translation is off by default. For users without a saved provider preference, quick translation defaults to Microsoft Translator; an existing Google choice is preserved. The optional keyless Google web endpoint is undocumented and unreliable, so configure official Google Cloud Translation or Microsoft credentials for dependable use. After class, the review page can translate the whole class, selected segments or one sentence with a precise local/cloud model. Precise local translation can use an OpenAI-compatible Ollama/LM Studio endpoint; cloud precise translation uses the configured OpenAI-compatible endpoint. Translation receives caption text only, never the locally saved original audio, and all translation keys stay in the backend `.env`.

### Caption latency on Apple Silicon (MLX)

The live decoder is a **sliding window decoded in batch** (`MLX_LIVE_MODE=windowed`, the default).
It keeps the last `MLX_WINDOW_SECONDS` of audio and re-decodes the whole window every
`MLX_HOP_SECONDS`, always ending at the live edge. The window is what gives the model the run-up to
the sentence it is deciding, and because it ends at the live edge its length costs no latency:
latency is `decode time + hop`, not window length.

```dotenv
MLX_LIVE_MODE=windowed        # windowed (default) | streaming
MLX_WINDOW_SECONDS=18.0       # left context; larger = more readable, slower to start
MLX_HOP_SECONDS=2.0           # how often a caption may update
```

Measured by replaying a real 127.3s lecture recording at realtime pace: a caption appears at the
live edge (median ≈0s, worst 3.3s), and decoding runs at 0.24× realtime. The window starts short
and grows into `MLX_WINDOW_SECONDS`, so a session's first caption arrives after ~8s rather than
waiting for a full window. Windows shorter than about 12s return empty output over quiet
stretches, which is why 18 is the default.

`MLX_LIVE_MODE=streaming` selects the older `transcribe_stream()` decoder. It is the only path that
can emit draft tokens, but on lecture audio it produced word salad and ran at ~1.5× realtime, so it
is not the default. On that path the decoder holds back the last `right context` encoder frames, one
encoder frame being `8 (subsampling) × 160 (hop) / 16000 = 0.08s`, so the confirmation lag is
`MLX_STREAM_RIGHT_CONTEXT × 0.08s`.

`STREAMING_CHUNK_SECONDS` only applies to streaming providers. Each inference step has a
fixed ~0.4s cost, so smaller chunks buy smoother live captions at a CPU price: 3.0s runs
at 0.19× realtime, 1.0s at 0.42×, and 0.5s at 0.82× — too close to the limit to be the
default.

Read [`docs/LATENCY.md`](docs/LATENCY.md) for the full measurements, the window-versus-stream
comparison, and the scripts to re-measure on your own machine.

### Optional Google and Microsoft translation keys

Google Cloud Translation Basic currently lists the first 500,000 characters per month as free, while Azure Translator’s F0 tier currently lists 2,000,000 characters per month free. Both limits, billing requirements and regional availability are controlled by the providers; check their official pricing pages before relying on them for a course. Google’s no-key public path is best-effort only and can be rate-limited.

Create a Google Cloud project, enable Cloud Translation API, create an API key under **APIs & Services → Credentials**, and restrict the key to Cloud Translation. The app uses the Basic v2 API-key flow; the project ID is optional in the app but useful for identifying the billing project:

```dotenv
TRANSLATION_GOOGLE_PROJECT_ID=your-project-id
TRANSLATION_GOOGLE_API_KEY=your-google-key
TRANSLATION_GOOGLE_LOCATION=global
```

See [Google Cloud Translation setup](https://docs.cloud.google.com/translate/docs/setup), [authentication](https://docs.cloud.google.com/translate/docs/authentication) and [pricing](https://cloud.google.com/products/translate/pricing).

For Microsoft, create a Translator resource in the [Azure Portal](https://portal.azure.com/), choose the F0 free tier when available, then open **Keys and Endpoint** after deployment:

```dotenv
TRANSLATION_MICROSOFT_ENDPOINT=https://api.cognitive.microsofttranslator.com
TRANSLATION_MICROSOFT_API_KEY=your-microsoft-key
TRANSLATION_MICROSOFT_REGION=
```

Use a region only when your resource uses a regional endpoint. See [Microsoft resource setup](https://learn.microsoft.com/azure/ai-services/translator/how-to/create-translator-resource), [pricing](https://azure.microsoft.com/pricing/details/cognitive-services/translator/) and [service limits](https://learn.microsoft.com/azure/ai-services/translator/service-limits). Keep both providers’ keys in the backend `.env`; never put them in browser code or commit them.

## Start

```bash
./start.sh --mode auto
./start.sh --mode local
./start.sh --mode cloud
```

Open [http://127.0.0.1:5001/](http://127.0.0.1:5001/) for the live lecture workspace. Use [http://127.0.0.1:5001/review](http://127.0.0.1:5001/review) for post-class review.

Recommended classroom flow:

1. Open the live page before class and enter a course label.
2. Select `auto`, `local`, or `cloud`; check the detected device/provider message.
3. Click **开始听课** and allow microphone access. The current sentence is large and bright; confirmed history remains scrollable.
4. Leave **保存原声** enabled if you want synchronized replay during review; the default recording is local browser storage.
5. Stop after class. The final session is saved locally and can be searched, edited, starred, translated, annotated or exported from **课后复习**.

## Troubleshooting

- The local model is too slow or fails to load: on Mac use the MLX requirements, on an NVIDIA machine check CUDA, or configure cloud variables and keep `--mode auto`.
- The browser cannot hear audio: use a secure browser context or localhost, allow the microphone, and check the selected input device.
- Cloud requests fail: verify the base URL includes the provider API root, the key/model are valid, and the service accepts `POST /audio/transcriptions`.
- No review records appear: allow IndexedDB/local storage for `127.0.0.1`; live transcription itself does not depend on the review database.

- Captions arrive late: a session's first caption lands after ~8s, which is the head guard plus the audio the model needs to form a sentence — it is not affected by `MLX_WINDOW_SECONDS`, because the window now grows into its full length instead of waiting for it. Raising `MLX_WINDOW_SECONDS` was measured **not** to improve the text either; see [`docs/LATENCY.md`](docs/LATENCY.md). Once running, the default windowed path already lands at the live edge.
- Real-time translation fails: the keyless Google endpoint may return verification or rate-limit errors; configure Microsoft Translator or Google Cloud credentials for dependable use.

See [`QUICKSTART.md`](QUICKSTART.md), [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md), [`docs/API.md`](docs/API.md) and [`docs/PRIVACY.md`](docs/PRIVACY.md).

Working on the code rather than using it? [`CHANGELOG.md`](CHANGELOG.md) records what changed and the current project state; [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) is the debugging handbook, including the traps in this project's development environment.

Runtime discovery is available at `/api/capabilities`; the live page is `/` and the review page is `/review`.

## Project boundary

This repository is only the realtime lecture transcription product. The separate video/offline subtitle project is maintained at [Auto-Subtitle-on-Generative-AI](https://github.com/lemon5227/Auto-Subtitle-on-Generative-AI); this project does not import its runtime code, templates or dependencies.
