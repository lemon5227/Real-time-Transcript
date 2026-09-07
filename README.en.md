# Real-time Transcript — English guide

Real-time lecture captions and post-class review for international students. The live workspace keeps the current sentence prominent, while the review workspace makes every session searchable, editable, annotatable and exportable.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
./start.sh --mode auto
```

Install `requirements-local.txt` for local Whisper inference, or `requirements-cloud.txt` for a configured cloud transcription endpoint. Open `/` for class and `/review` afterwards. See the main [README](README.md), [API contract](docs/API.md) and [privacy notes](docs/PRIVACY.md).
