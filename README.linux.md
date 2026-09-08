# Linux guide

Use the core requirements for the web server, plus the optional local or cloud requirements for transcription.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
./start.sh --mode auto
```

For a capable machine, add `requirements-local.txt`; an NVIDIA GPU is detected and used through CUDA automatically. For a thin CPU laptop, add `requirements-cloud.txt` and set `CLOUD_BASE_URL`, `CLOUD_API_KEY` and `CLOUD_TRANSCRIPTION_MODEL`, then use Auto or Cloud mode. The browser pages are `/` and `/review`.
