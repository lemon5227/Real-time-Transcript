# Real-time Transcript

面向留学生听课和课后复习的实时转录工作台。它把“听不清、跟不上、课后找不到重点”拆成一条简单的学习流：课上实时字幕，课后可搜索、编辑、标记和导出。

> The product is designed for international students: a low-distraction live caption view during class, plus a local post-class review workspace.

## What it does

- Realtime microphone transcription for English, Chinese, Japanese, Korean and other Whisper-supported languages.
- Browser audio is normalized to mono 16 kHz; common 44.1/48 kHz microphones are accepted.
- Local mode uses `faster-whisper` when available and keeps audio on the computer.
- Cloud mode sends short audio windows to the endpoint configured by the user, which makes the app practical on thin laptops.
- Auto mode tries local startup first and falls back to the configured cloud provider when local model loading fails.
- Post-class review stores sessions in browser IndexedDB, with search, inline edits, notes, starred segments and TXT/Markdown/VTT/SRT export.

## Install

Python 3.9+ is supported. `ffmpeg` is not required for microphone transcription.

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

For cloud mode, install the HTTP client:

```bash
pip install -r requirements-cloud.txt
```

For development and tests:

```bash
pip install -r requirements-dev.txt
```

## Choose a runtime

| Runtime | When to choose it | Audio leaves the computer? |
| --- | --- | --- |
| Auto | Default; local first, cloud fallback | Only if local startup fails and cloud is configured |
| Local | Privacy, stable offline use, capable CPU/GPU | No |
| Cloud | Thin laptop, low memory, no local model runtime | Yes, to your configured endpoint |

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

Real-time translation is off by default. When enabled, Google Cloud Translation or Microsoft Translator can provide quick text translation; after class, the review page can translate the whole class, selected segments or one sentence with a precise local/cloud model. Translation receives caption text only, never the locally saved original audio, and all translation keys stay in the backend `.env`.

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

- The local model is too slow or fails to load: choose a smaller model, use `--mode cloud`, or configure cloud variables and keep `--mode auto`.
- The browser cannot hear audio: use a secure browser context or localhost, allow the microphone, and check the selected input device.
- Cloud requests fail: verify the base URL includes the provider API root, the key/model are valid, and the service accepts `POST /audio/transcriptions`.
- No review records appear: allow IndexedDB/local storage for `127.0.0.1`; live transcription itself does not depend on the review database.

See [`QUICKSTART.md`](QUICKSTART.md), [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md), [`docs/API.md`](docs/API.md) and [`docs/PRIVACY.md`](docs/PRIVACY.md).

Runtime discovery is available at `/api/capabilities`; the live page is `/` and the review page is `/review`.

## Project boundary

This repository is only the realtime lecture transcription product. The separate video/offline subtitle project is maintained at [Auto-Subtitle-on-Generative-AI](https://github.com/lemon5227/Auto-Subtitle-on-Generative-AI); this project does not import its runtime code, templates or dependencies.
