# Deployment

拾句（Real-time Transcript）has three deployment paths. Choose the path that matches
the computer doing the transcription rather than the computer opening the web
page.

| Device | Entry point | Runtime |
| --- | --- | --- |
| Apple Silicon Mac | `./quickstart.sh` | native MLX Parakeet |
| NVIDIA Windows/Linux | `./quickstart.sh` or `quickstart.ps1` | native standard model with CUDA when available |
| Thin CPU laptop | quickstart with cloud settings | cloud transcription |
| Any device with Docker | `docker compose up --build` | cloud container |
| Any browser | Render button | hosted cloud container |

## Native one-click startup

The MLX Parakeet package requires Python 3.10 or newer. The launcher checks the
interpreter before installing dependencies and automatically uses an installed
`python3.10`–`python3.13` when the shell's default `python3` is older. On a Mac
with only Apple's Python 3.9, install a current interpreter once:

```bash
brew install python@3.12
```

Then rerun `./quickstart.sh`; if an earlier failed attempt created `.venv` with
Python 3.9, the launcher rebuilds that managed environment automatically.

### macOS/Linux

From the repository root:

```bash
./quickstart.sh
```

The launcher creates `.venv` and copies `.env.example` to `.env` only when
those files do not already exist. Its automatic selection is:

- Apple Silicon macOS: installs `requirements-mac.txt` and keeps `auto` so the
  existing provider factory selects MLX Parakeet.
- A machine where `nvidia-smi` succeeds: installs `requirements-local.txt` and
  keeps `auto` so the standard local provider can use CUDA.
- Other machines: installs `requirements-cloud.txt` and uses cloud mode.

Choose a mode explicitly when needed:

```bash
./quickstart.sh --mode local
./quickstart.sh --mode cloud
```

The launcher never installs an NVIDIA driver and never asks for an API key.
If cloud mode is selected without configuration, it prints the names of the
missing variables and still opens the application so you can finish setup.

### macOS DMG desktop app

Build the desktop installer on an Apple Silicon Mac with the native macOS
tools and Homebrew `librsvg`:

```bash
brew install librsvg
./scripts/build-macos-dmg.sh
```

The result is `dist/拾句-macOS.dmg`. Drag **拾句** to
Applications. An unsigned local build may be blocked on first launch; use
**Control-click → Open** once to approve it. The launcher resolves its bundled
source files independently of the current working directory, creates
`~/Library/Application Support/拾句/{runtime,logs,pids}`, and
exports `TRANSCRIPT_RUNTIME_DIR` and `TRANSCRIPT_ENV_FILE`. This keeps the
`.env`, Python environment, caches, logs, recordings, and model weights out of
the app bundle. Model weights are downloaded only when the user chooses a
model in the app, so installing the DMG does not silently download gigabytes.

To reset a packaged installation, quit the browser/server and remove the
`~/Library/Application Support/拾句` directory. Source
checkout startup remains unchanged and continues to use `.venv` and `.env` in
the repository.

### Windows PowerShell

```powershell
.\quickstart.ps1
.\quickstart.ps1 -Mode Local
.\quickstart.ps1 -Mode Cloud
```

The same detection rules apply. A Windows machine with an NVIDIA driver can
use the native local runtime; a machine without one defaults to cloud mode.

## Docker

Docker uses the same application but intentionally does not claim Apple Metal
support. On macOS, Docker runs inside a Linux virtual machine, so use native
`quickstart.sh` for MLX performance.

### Cloud container

Copy the example environment file and set the cloud transcription variables in
`.env`:

```bash
cp .env.example .env
docker compose up --build
```

The page is available at <http://localhost:5001/>. The container sends only
the short processing window required by the configured cloud transcription
provider; it does not store an audio archive in the image.

The cloud provider must expose an OpenAI-compatible transcription endpoint:

```dotenv
CLOUD_BASE_URL=https://your-provider.example/v1
CLOUD_API_KEY=your-key
CLOUD_TRANSCRIPTION_MODEL=your-transcription-model
```

The backend calls `POST {CLOUD_BASE_URL}/audio/transcriptions` with a WAV file,
the model name, and the selected language. Provider-specific endpoints that do
not implement this contract need an adapter before they can be used.

### Local CPU container

Use the explicit local image when you want standard local inference inside a
container:

```bash
TRANSCRIPT_DOCKERFILE=Dockerfile.local \
DOCKER_TRANSCRIPTION_MODE=local \
docker compose up --build
```

This image is CPU-oriented. Native execution is recommended for Apple MLX or
NVIDIA CUDA; using a GPU inside Docker additionally requires a compatible
host driver and NVIDIA Container Toolkit.

### Port override

If port `5001` is already used:

```bash
TRANSCRIPT_PORT=5002 docker compose up --build
```

Open <http://localhost:5002/>. `TRANSCRIPT_PORT` changes the host port only;
the container continues to listen on `5001`.

## Allowed browser origins

Both the HTTP API and the live transcription socket follow `CORS_ORIGINS`.
An origin that is not listed is refused, which is what keeps an unrelated web
page from driving a local server: starting transcription, downloading models and
spending the configured translation quota.

The default covers `http://127.0.0.1:5001` and `http://localhost:5001`. Set the
variable whenever the page is opened from another address, for example a LAN
address, another port, or a public hostname:

```dotenv
CORS_ORIGINS=https://transcript.example.com
```

When running the native entry point with a local `PORT`, the matching
`localhost` and `127.0.0.1` origins are added automatically. For example:

```bash
PORT=5002 .venv/bin/python app.py
```

can be opened at <http://127.0.0.1:5002/> without a separate CORS setting.

Compose derives the entry from `TRANSCRIPT_PORT`, so a port override needs no
extra work. On Render the service URL is added automatically from
`RENDER_EXTERNAL_URL`, which the platform sets while the container runs; to
serve a custom domain instead, set `CORS_ORIGINS` to that domain.

If transcription never starts but the page loads, the browser origin is the
first thing to check: the socket handshake is refused with HTTP 400 before any
audio is sent.

## Render one-click deployment

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript)

1. Click the button and sign in to Render.
2. Review the `real-time-transcript` web service.
3. Enter `CLOUD_BASE_URL`, `CLOUD_API_KEY`, and
   `CLOUD_TRANSCRIPTION_MODEL` when Render prompts for the private values.
4. Approve the deployment and wait for `/api/health` to pass.
5. Open the generated Render URL from any browser.

The Blueprint uses the cloud-only Dockerfile, generates `SECRET_KEY`, and
does not install MLX/CUDA or download local model weights. Auto-deploy is off
for button-created personal instances; deploy a new commit manually from the
Render dashboard when you want to update one.

## Troubleshooting

- **The app opens but transcription cannot start:** check the three `CLOUD_*`
  variables and confirm the endpoint accepts `POST /audio/transcriptions`.
- **Mac is not using MLX:** use native `./quickstart.sh`, not Docker, and
  confirm the machine is Apple Silicon rather than Intel.
- **CUDA is not shown:** confirm `nvidia-smi` works in the host terminal. The
  launcher cannot repair or install the NVIDIA driver.
- **Docker cannot connect:** start Docker Desktop/OrbStack and rerun
  `docker compose up --build`; the repository cannot start the daemon for you.
- **Render health check fails:** inspect the service logs and confirm the
  container is binding to the platform-provided `PORT` on `0.0.0.0`.

The separate offline/video subtitle project remains at
[Auto-Subtitle-on-Generative-AI](https://github.com/lemon5227/Auto-Subtitle-on-Generative-AI)
and is not a dependency of this deployment.
