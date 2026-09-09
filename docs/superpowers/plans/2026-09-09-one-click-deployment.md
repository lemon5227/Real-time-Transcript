# Cross-Platform One-Click Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add idempotent cross-platform native launchers, reproducible Docker startup, and a Render Blueprint while keeping Mac MLX, NVIDIA CUDA, CPU, and cloud transcription paths separate.

**Architecture:** A standard-library-only `quickstart.py` owns platform detection, virtual-environment preparation, `.env` bootstrapping, and application launch. Thin `quickstart.sh` and `quickstart.ps1` wrappers provide native entry points. Docker and Render use the existing Flask/Socket.IO application unchanged: the default image is lightweight cloud mode, while an explicit local image installs the standard CPU runtime. All secrets remain runtime environment variables.

**Tech Stack:** Python 3 standard library, Bash, PowerShell, Docker, Docker Compose, Render Blueprint YAML, Flask-SocketIO, Gunicorn for Linux containers, pytest, Ruff.

## Global Constraints

- Apple Silicon macOS uses `requirements-mac.txt` and MLX Parakeet through native execution.
- NVIDIA Windows/Linux uses `requirements-local.txt`; the launcher may detect `nvidia-smi` but must not install NVIDIA drivers.
- Machines without Apple Silicon or a detected NVIDIA driver default to `requirements-cloud.txt` and cloud mode; `--local` explicitly selects the CPU local runtime.
- `quickstart` must be idempotent and must not overwrite `.env`, model caches, or classroom data.
- Docker must not claim Apple Metal support; Mac MLX remains a native path.
- Render uses a cloud-only image with `TRANSCRIPTION_MODE=cloud` and `/api/health` health checks.
- API keys are never committed, copied into an image layer, printed, or requested interactively by a launcher.
- The cloud provider remains OpenAI-compatible and uses `CLOUD_BASE_URL`, `CLOUD_API_KEY`, and `CLOUD_TRANSCRIPTION_MODEL`.
- The existing Flask routes, Socket.IO handlers, provider factory, transcription behavior, translation behavior, and review behavior are not duplicated or redesigned.
- The Docker/Render process uses one worker because the current session manager is process-local; long-polling remains supported.

---

## File Map

- Create: `quickstart.py` — dependency-free platform detection, environment preparation, and launch orchestration.
- Create: `quickstart.sh` — macOS/Linux shell entry point that invokes `quickstart.py`.
- Create: `quickstart.ps1` — Windows PowerShell entry point that invokes `quickstart.py`.
- Modify: `start.sh` — fix project-root resolution and preserve explicit mode handling for prepared environments.
- Create: `Dockerfile` — lightweight cloud image with production Gunicorn command.
- Create: `Dockerfile.local` — standard local CPU image for explicit Docker local mode.
- Create: `.dockerignore` — exclude credentials, virtual environments, model caches, and local data from build context.
- Create: `docker-compose.yml` — default cloud container with a single `docker compose up --build` command and an explicit Dockerfile override for local mode.
- Create: `render.yaml` — Render Blueprint for cloud-only deployment.
- Modify: `README.zh-CN.md` — one-click native, Docker, and Render instructions.
- Modify: `README.md` — matching English deployment entry points and runtime boundaries.
- Create: `docs/DEPLOYMENT.md` — detailed platform matrix, environment variables, and troubleshooting.
- Modify: `requirements-cloud.txt` — only if a cloud runtime dependency required by Docker is missing; keep Windows compatibility by installing Gunicorn in the Dockerfile rather than this cross-platform file.
- Create: `tests/test_quickstart.py` — pure platform/profile selection and non-destructive bootstrap contract tests.
- Create: `tests/test_deployment_files.py` — static contract tests for Docker, Compose, Render, and launcher files.

---

### Task 1: Build the cross-platform native quickstart

**Files:**
- Create: `quickstart.py`
- Create: `quickstart.sh`
- Create: `quickstart.ps1`
- Modify: `start.sh`
- Create: `tests/test_quickstart.py`

**Interfaces:**
- `quickstart.py` exposes `select_profile(mode: str, system: str, machine: str, nvidia_available: bool) -> BootstrapProfile` for tests and launcher decisions.
- `BootstrapProfile` contains `mode: str`, `requirements_file: str`, and `runtime_label: str`.
- `quickstart.py` accepts `--mode auto|local|cloud`; `auto` is the default.
- The wrappers invoke `quickstart.py` using the repository root, so launching from another current directory still works.

- [ ] **Step 1: Write failing profile-selection tests**

```python
from quickstart import select_profile


def test_auto_selects_mlx_on_apple_silicon():
    profile = select_profile("auto", "Darwin", "arm64", False)
    assert profile.mode == "auto"
    assert profile.requirements_file == "requirements-mac.txt"
    assert profile.runtime_label == "Mac MLX"


def test_auto_selects_cuda_capable_local_runtime_when_nvidia_smi_works():
    profile = select_profile("auto", "Linux", "x86_64", True)
    assert profile.mode == "auto"
    assert profile.requirements_file == "requirements-local.txt"
    assert profile.runtime_label == "CUDA/local"


def test_auto_selects_cloud_for_thin_cpu_machine():
    profile = select_profile("auto", "Windows", "AMD64", False)
    assert profile.mode == "cloud"
    assert profile.requirements_file == "requirements-cloud.txt"
    assert profile.runtime_label == "cloud"


def test_explicit_local_overrides_hardware_detection():
    profile = select_profile("local", "Darwin", "arm64", False)
    assert profile.mode == "local"
    assert profile.requirements_file == "requirements-local.txt"


def test_explicit_cloud_is_platform_independent():
    profile = select_profile("cloud", "Linux", "x86_64", True)
    assert profile.mode == "cloud"
    assert profile.requirements_file == "requirements-cloud.txt"
```

- [ ] **Step 2: Run the focused tests and verify the import fails**

Run: `PYTHONPATH=. pytest -q tests/test_quickstart.py`

Expected: FAIL because `quickstart.py` and `select_profile` do not exist yet.

- [ ] **Step 3: Implement the dependency-free profile selector**

Implement the following behavior in `quickstart.py` without importing Flask or any project dependency:

```python
def select_profile(mode, system, machine, nvidia_available):
    if mode == "cloud":
        return BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")
    if mode == "local":
        return BootstrapProfile("local", "requirements-local.txt", "CPU/local")
    if system == "Darwin" and machine.lower() in {"arm64", "aarch64"}:
        return BootstrapProfile("auto", "requirements-mac.txt", "Mac MLX")
    if nvidia_available:
        return BootstrapProfile("auto", "requirements-local.txt", "CUDA/local")
    return BootstrapProfile("cloud", "requirements-cloud.txt", "cloud")
```

Validate `--mode` before doing any filesystem or subprocess work. Detect NVIDIA availability by running `nvidia-smi` with a short timeout and checking only its return code; never install or modify a driver.

- [ ] **Step 4: Implement idempotent environment preparation**

Add functions with these signatures:

```python
def ensure_env_file(root: Path) -> bool: ...
def ensure_venv(root: Path) -> Path: ...
def install_requirements(python: Path, requirements_file: Path) -> None: ...
def missing_cloud_settings(env_path: Path) -> list[str]: ...
```

`ensure_env_file` copies `.env.example` to `.env` only when `.env` is absent and returns whether it created the file. `ensure_venv` creates `.venv` with `python -m venv` only when the expected interpreter is absent. `install_requirements` runs the resolved virtual-environment interpreter with `-m pip install -r` against the selected requirements file and does not print environment values. `missing_cloud_settings` reports only missing variable names among `CLOUD_BASE_URL`, `CLOUD_API_KEY`, and `CLOUD_TRANSCRIPTION_MODEL`.

- [ ] **Step 5: Implement native launch orchestration**

Resolve `root` from `Path(__file__).resolve().parent`, load the selected profile, create `.env` if needed, prepare the virtual environment, install the profile requirements, print the selected runtime label, and launch `app.py` with the venv interpreter. Export `TRANSCRIPTION_MODE` only when the selected profile requires a concrete cloud mode; preserve `auto` for Apple Silicon and NVIDIA profiles so the existing `ProviderFactory` remains authoritative.

When the selected mode is cloud and settings are missing, print the variable names and continue to launch so the settings page remains available. Do not print values from `.env`.

- [ ] **Step 6: Fix `start.sh` path resolution**

Replace the escaped literal `"\${BASH_SOURCE[0]}"` with the actual Bash source expression so `start.sh` resolves `app.py` relative to the script instead of the caller’s current directory. Preserve `--mode auto|local|cloud` validation and the existing venv fallback.

- [ ] **Step 7: Add thin platform wrappers**

`quickstart.sh` must use `set -euo pipefail`, resolve its own directory, and execute `python3 quickstart.py "$@"`. `quickstart.ps1` must resolve `$PSScriptRoot`, prefer `.venv\Scripts\python.exe` when it exists, and invoke `quickstart.py` with `@args`; it must not embed secrets or duplicate detection logic.

- [ ] **Step 8: Run the focused test cycle**

Run: `PYTHONPATH=. pytest -q tests/test_quickstart.py`

Expected: PASS, with profile selection covering MLX, CUDA/local, cloud, and explicit overrides.

- [ ] **Step 9: Commit the native launcher unit**

```bash
git add quickstart.py quickstart.sh quickstart.ps1 start.sh tests/test_quickstart.py
git commit -m "feat: add cross-platform native quickstart"
```

### Task 2: Add reproducible Docker startup

**Files:**
- Create: `Dockerfile`
- Create: `Dockerfile.local`
- Create: `.dockerignore`
- Create: `docker-compose.yml`
- Create: `tests/test_deployment_files.py`

**Interfaces:**
- `docker compose up --build` runs the cloud image on host port `5001`.
- `TRANSCRIPT_DOCKERFILE=Dockerfile.local TRANSCRIPTION_MODE=local docker compose up --build` runs the explicit standard local CPU image.
- Both images expose the same Flask app and `/api/health` endpoint.

- [ ] **Step 1: Write Docker contract tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cloud_dockerfile_uses_cloud_requirements_and_gunicorn():
    content = (ROOT / "Dockerfile").read_text()
    assert "requirements-cloud.txt" in content
    assert "gunicorn" in content
    assert "workers 1" in content


def test_local_dockerfile_uses_local_requirements():
    assert "requirements-local.txt" in (ROOT / "Dockerfile.local").read_text()


def test_dockerignore_excludes_env_and_model_data():
    content = (ROOT / ".dockerignore").read_text()
    assert ".env" in content
    assert "models/" in content
    assert ".venv/" in content
```

- [ ] **Step 2: Run the focused tests and verify the files are absent**

Run: `PYTHONPATH=. pytest -q tests/test_deployment_files.py`

Expected: FAIL because the Docker files do not exist yet.

- [ ] **Step 3: Implement the cloud Dockerfile**

Use `python:3.12-slim`, set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`, install `requirements-cloud.txt` plus a Linux-only Gunicorn dependency, copy the application source, set `HOST=0.0.0.0`, and run:

```text
gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 0 --bind 0.0.0.0:${PORT:-5001} app:app
```

Use a shell form only for `${PORT:-5001}` expansion. Do not copy `.env`, `save/`, `models/`, or cache directories.

- [ ] **Step 4: Implement the explicit local CPU Dockerfile**

Use the same base image and production command, but install `requirements-local.txt`. Set no GPU claims or default mode in the image; Compose supplies `TRANSCRIPTION_MODE=local`. Document that this image is CPU-oriented and that native execution is recommended for MLX or CUDA.

- [ ] **Step 5: Implement the Docker build context exclusions**

Exclude `.env`, `.git/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `models/`, `.cache/`, `transformers_cache/`, `save/`, and local logs. Keep source, templates, static assets, requirements, and backend files included.

- [ ] **Step 6: Implement the default Compose service**

Use `TRANSCRIPT_DOCKERFILE` with default `Dockerfile`, `TRANSCRIPT_PORT` with default `5001`, and `TRANSCRIPTION_MODE` with default `cloud`. Inject `.env` at runtime with optional-file semantics, set `HOST=0.0.0.0` and container `PORT=5001`, map `${TRANSCRIPT_PORT:-5001}:5001`, and use `restart: unless-stopped`. Do not mount model or save directories by default.

- [ ] **Step 7: Add local Docker instructions to contract tests**

Assert that `docker-compose.yml` contains `TRANSCRIPT_DOCKERFILE`, `TRANSCRIPT_PORT`, `HOST: 0.0.0.0`, and the cloud default. Add a test that the README command uses `Dockerfile.local` and `TRANSCRIPTION_MODE=local` for the explicit local path.

- [ ] **Step 8: Run static and Docker validation**

Run: `PYTHONPATH=. pytest -q tests/test_deployment_files.py`

Then run: `docker compose config`

Expected: both commands PASS. If Docker is unavailable, retain the static test result and report the unavailable runtime check rather than claiming a build succeeded.

- [ ] **Step 9: Commit the Docker unit**

```bash
git add Dockerfile Dockerfile.local .dockerignore docker-compose.yml tests/test_deployment_files.py
git commit -m "feat: add reproducible docker deployment"
```

### Task 3: Add Render one-click deployment and user documentation

**Files:**
- Create: `render.yaml`
- Create: `docs/DEPLOYMENT.md`
- Modify: `README.zh-CN.md`
- Modify: `README.md`
- Modify: `tests/test_deployment_files.py`

**Interfaces:**
- Render reads `render.yaml`, builds the cloud `Dockerfile`, and checks `/api/health`.
- The README button points to `https://render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript`.
- Deployment documentation lists the three required cloud variables without embedding values.

- [ ] **Step 1: Extend the deployment contract tests**

```python
def test_render_blueprint_is_cloud_only_and_has_health_check():
    content = (ROOT / "render.yaml").read_text()
    assert "runtime: docker" in content
    assert "healthCheckPath: /api/health" in content
    assert "TRANSCRIPTION_MODE" in content
    assert "CLOUD_BASE_URL" in content
    assert "CLOUD_API_KEY" in content
    assert "CLOUD_TRANSCRIPTION_MODEL" in content


def test_readme_contains_one_click_deploy_link():
    content = (ROOT / "README.zh-CN.md").read_text()
    assert "render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript" in content
```

- [ ] **Step 2: Implement `render.yaml`**

Define one web service with Docker runtime, `dockerfilePath: ./Dockerfile`, `plan: free`, `autoDeploy: false`, `healthCheckPath: /api/health`, `HOST=0.0.0.0`, `TRANSCRIPTION_MODE=cloud`, a generated `SECRET_KEY`, and three `sync: false` cloud variables. Let the Dockerfile `CMD` provide the single worker-compatible start path. Do not include model download commands, GPU settings, or translation secrets.

- [ ] **Step 3: Write the Chinese deployment guide**

Document this exact matrix:

| Device | Entry point | Runtime |
| --- | --- | --- |
| Apple Silicon Mac | `./quickstart.sh` | native MLX Parakeet |
| NVIDIA Windows/Linux | `./quickstart.sh` or `quickstart.ps1` | native standard model with CUDA when available |
| Thin CPU laptop | quickstart with cloud settings | cloud transcription |
| Any device with Docker | `docker compose up --build` | cloud container |
| Any browser | Render button | hosted cloud container |

Include the explicit local Docker command, Render variable names, the warning that Docker on macOS cannot use host Metal, and the warning that no launcher installs NVIDIA drivers or creates third-party accounts.

- [ ] **Step 4: Add concise README entry points**

Add a “一键启动 / One-click deployment” section near quickstart instructions. Include the Render button, native commands, Docker cloud command, Docker local override command, the cloud API requirement, and a link to `docs/DEPLOYMENT.md`. State that the separated subtitle-generator repositories remain decoupled.

- [ ] **Step 5: Add config and port troubleshooting**

Document `TRANSCRIPT_PORT=5002 docker compose up --build` and explain that the browser uses `http://localhost:5002`. Explain that a cloud container needs `CLOUD_BASE_URL`, `CLOUD_API_KEY`, and `CLOUD_TRANSCRIPTION_MODEL` before transcription can start; the page may still open without them.

- [ ] **Step 6: Validate docs and Blueprint statically**

Run: `PYTHONPATH=. pytest -q tests/test_deployment_files.py`

Run a YAML parse check using the installed Python YAML parser when available; otherwise inspect the Render file with a small structure-aware check and report the limitation. Do not call the Render API or create an external service during repository tests.

- [ ] **Step 7: Commit the Render/documentation unit**

```bash
git add render.yaml docs/DEPLOYMENT.md README.zh-CN.md README.md tests/test_deployment_files.py
git commit -m "docs: add render one-click deployment"
```

### Task 4: Run the complete integration verification

**Files:**
- Modify: only files needed to correct verification failures from Tasks 1–3.

**Interfaces:**
- Existing application tests remain green.
- Shell, PowerShell, Docker, Compose, Render, and documentation contracts are all checked before reporting completion.

- [ ] **Step 1: Run launcher syntax checks**

Run: `bash -n quickstart.sh start.sh`

Expected: PASS. On a system with PowerShell available, run `pwsh -NoProfile -Command "[System.Management.Automation.Language.Parser]::ParseFile('quickstart.ps1',[ref]$null,[ref]$null) | Out-Null"` and expect no parser error.

- [ ] **Step 2: Run the full Python and frontend checks**

Run:

```bash
PYTHONPATH=. pytest -q
ruff check backend tests
node --check static/app.js
git diff --check
```

Expected: all commands PASS.

- [ ] **Step 3: Validate the local health endpoint**

Start the already prepared app with a non-destructive test environment, request `GET /api/health`, and assert the JSON status is `ok`. Do not put real cloud credentials into test output.

- [ ] **Step 4: Validate Docker when the runtime is available**

Run `docker compose config` and `docker build --tag real-time-transcript-cloud-test .`. If the host has Docker but no network or registry access, report the exact failed build stage; do not claim an image build succeeded.

- [ ] **Step 5: Review the final diff for secrets and accidental coupling**

Run `git status --short`, inspect all deployment files, and verify no `.env`, API key, model cache, Auto-Subtitle repository reference, or unrelated generated file is included.

- [ ] **Step 6: Commit any verification-only correction**

```bash
git add quickstart.py quickstart.sh quickstart.ps1 start.sh Dockerfile Dockerfile.local .dockerignore docker-compose.yml render.yaml docs/DEPLOYMENT.md README.zh-CN.md README.md tests/test_quickstart.py tests/test_deployment_files.py
git commit -m "fix: address deployment verification findings"
```

- [ ] **Step 7: Push the completed deployment commits**

```bash
git push origin main
```

Report the final commit hash, the GitHub repository URL, the native quickstart command, the Docker command, and the Render deployment link. Explicitly state which runtime was verified locally and which external cloud configuration remains user-provided.

---

## Self-review checklist

- [ ] Every requirement in the approved design has a task: native platform detection, explicit overrides, idempotent `.env`/venv handling, Docker cloud/local paths, Render health check, docs, secrets, and verification.
- [ ] No task uses unfinished markers or vague “handle edge cases” language.
- [ ] `select_profile`, file names, environment variables, and commands are consistent across tasks.
- [ ] The plan does not install Gunicorn into the Windows-compatible `requirements-cloud.txt`; only Linux Docker images install it.
- [ ] The plan keeps MLX native on Apple Silicon and does not promise Metal through Docker.
