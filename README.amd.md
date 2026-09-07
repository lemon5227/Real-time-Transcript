# AMD / Linux guide

For AMD CPU or GPU laptops, begin with the core stack and let the capability panel determine whether local inference is practical. If model loading is slow or memory-constrained, use cloud mode without changing the browser workflow.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
pip install -r requirements-local.txt   # optional local path
cp .env.example .env
./start.sh --mode auto
```

The runtime does not assume CUDA. Use `./start.sh --mode cloud` for a thin laptop and configure the cloud variables in `.env`. See [QUICKSTART](QUICKSTART.md).
