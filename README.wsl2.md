# WSL2 guide

Run the backend inside WSL2 and open the forwarded localhost URL in your Windows browser.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
./start.sh --mode auto
```

If the WSL2 environment cannot run a local model comfortably, install `requirements-cloud.txt`, configure the cloud endpoint in `.env`, and run `./start.sh --mode cloud`. When prompted, allow microphone access to the localhost page.
