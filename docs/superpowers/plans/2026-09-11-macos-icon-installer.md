# macOS Icon and Installer Implementation Plan

> 当前状态（2026-09-13）：图标、DMG 构建、启动器、GitHub Actions 工作流及启动超时回归测试已实现；修复版 DMG 构建与校验通过。实际从 Finder 安装并完整启动仍待验证，见[项目路线图](../../ROADMAP.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a polished macOS application icon and a one-click DMG installer for Real-time Transcript, while keeping source files, user settings, logs, virtual environments, and model caches outside the installed app bundle.

**Architecture:** Keep the existing Flask/Socket.IO application and `quickstart.py` as the runtime entry points. Add a small macOS launcher inside the app bundle that points the source tree at a per-user Application Support runtime directory. Add a deterministic SVG source for the icon, generate `.icns` and `.dmg` only on macOS, and preserve the existing command-line quickstart behavior for developers and non-macOS users.

**Tech Stack:** Python 3.9+, Flask, Flask-SocketIO, shell scripts, macOS `iconutil`, macOS `hdiutil`, `rsvg-convert`, pytest, and the existing project tooling.

## Global Constraints

- Preserve the current repository layout and existing quickstart behavior unless the user explicitly launches the packaged macOS app.
- Never put `.env`, API keys, model weights, `.venv`, logs, PID files, recordings, or generated user data inside the `.app` bundle or DMG.
- The icon must be a flat vector composition: deep navy classroom background, warm-white subtitle page, mint live cursor/accent, and no gradients, shadows, textures, microphone, sound wave, `A/文`, or third-party logos.
- The same icon geometry and palette must be used by `static/favicon.svg` and the macOS icon source so the web UI and desktop app are visually coherent.
- A missing optional macOS build dependency must produce a clear actionable error; it must not silently create a broken installer.
- Tests must not require network access, model downloads, macOS-only tools, or a real microphone.

---

## Task 1: Separate source files from the packaged runtime directory

**Files:** `quickstart.py`, `backend/config.py`, `tests/test_quickstart.py`, `tests/test_config.py`.

- [x] Add a runtime-root resolver in `quickstart.py` with this precedence:
  1. explicit `TRANSCRIPT_RUNTIME_DIR`;
  2. the directory containing `TRANSCRIPT_ENV_FILE`;
  3. the existing repository root for normal CLI usage.
- [x] Keep the current default `.venv` and `.env` locations unchanged when no runtime variables are set.
- [x] Extend `ensure_env_file` and `ensure_venv` with an optional `runtime_root` argument so the packaged launcher can create its environment under Application Support without duplicating bootstrap logic.
- [x] Make the generated `.env` path explicit through `TRANSCRIPT_ENV_FILE`, and make `backend.config.load_config()` load that path when set while retaining normal `load_dotenv()` behavior otherwise.
- [x] Keep model cache configuration in the runtime directory, but do not move application source files or requirements there.
- [x] Add unit tests covering default repository behavior, an explicit runtime directory, an explicit env-file path, and config loading from the external env file.
- [x] Run the focused tests and `python -m py_compile quickstart.py backend/config.py`.

~~~python
def resolve_runtime_root(source_root: Path) -> Path:
    configured = os.environ.get("TRANSCRIPT_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser()

    env_file = os.environ.get("TRANSCRIPT_ENV_FILE")
    if env_file:
        return Path(env_file).expanduser().parent

    return source_root
~~~

The launcher will export both variables, so the source checkout can remain read-only after installation and upgrades.

## Task 2: Replace the favicon with the approved Live Page mark

**Files:** `static/app-icon.svg`, `static/favicon.svg`, `tests/test_macos_packaging.py`.

- [x] Create a canonical, viewBox-based SVG with a 1024×1024 coordinate system and only flat fills/strokes.
- [x] Draw a deep navy background, a warm-white subtitle page with a small folded corner, three dark transcript rules, one mint live rule, and a short mint cursor/marker.
- [x] Use stable design tokens in the SVG: `#0B1020`, `#F5F3EE`, `#70E0C0`, and a readable dark text color.
- [x] Copy the same geometry into `static/favicon.svg` with a compact `viewBox="0 0 32 32"` rendering suitable for 16px browser tabs.
- [x] Keep the SVG self-contained: no external fonts, images, CSS imports, gradients, filters, masks, or embedded raster data.
- [x] Add XML-level tests that verify the viewBox, required palette, absence of forbidden effects/logos, and that the favicon is a valid standalone SVG.
- [x] When available on macOS, render the SVG at 16px, 32px, 128px, 256px, and 512px with `rsvg-convert` and inspect the generated PNGs for a legible central page/cursor mark.

**Acceptance criteria:**

- At 16px the icon still reads as a page with a live/current mark, not as a generic circle or microphone.
- The app icon remains recognizable on both light and dark Finder backgrounds.
- No visual asset uses a different palette or a different metaphor from the approved design.

## Task 3: Add a reliable macOS app launcher

**Files:** `packaging/macos/Info.plist`, `packaging/macos/launcher.sh`, `tests/test_macos_packaging.py`.

- [x] Add an executable launcher at `Contents/MacOS/RealTimeTranscript` in the final bundle, sourced from `packaging/macos/launcher.sh`.
- [x] Resolve the installed app’s `Contents/Resources/app` directory without relying on the caller’s working directory.
- [x] Create `~/Library/Application Support/Real-time Transcript/{logs,pids}` with restrictive permissions and export:
  - `TRANSCRIPT_RUNTIME_DIR` for the environment, venv, cache, and runtime metadata;
  - `TRANSCRIPT_ENV_FILE` for the user’s settings file;
  - `PYTHONUNBUFFERED=1` and a configurable `PORT` defaulting to `8765`.
- [x] Detect an already-running local service through `/api/health` and open the existing browser session instead of starting a duplicate server.
- [x] Start `quickstart.py --mode auto` in the background, save a PID, write timestamped logs, poll health with a bounded timeout, then open `http://127.0.0.1:$PORT/`.
- [x] On startup failure, keep the log file and show a concise `osascript` error dialog containing the log path and a suggested next action.
- [x] Trap `TERM`, `INT`, and `EXIT`; clean the PID file and terminate only the child process started by this launcher.
- [x] Add a minimal `Info.plist` with a stable bundle identifier, display name, a build version supplied by the build script, `CFBundlePackageType=APPL`, and the generated `CFBundleIconFile`.
- [x] Add shell/static tests that verify path resolution, runtime variables, health polling, bounded failure handling, and no hard-coded developer paths.
- [x] Run `bash -n packaging/macos/launcher.sh` and the focused packaging tests.

## Task 4: Build a `.icns` file and DMG installer

**Files:** `scripts/build-macos-dmg.sh`, `tests/test_macos_packaging.py`, `.gitignore` if a build scratch path needs an explicit ignore rule.

- [x] Add a macOS-only build script with strict shell options and clear prerequisite checks for `iconutil`, `hdiutil`, `rsvg-convert`, and `rsync`.
- [x] Render the canonical SVG into an iconset containing 16, 32, 128, 256, and 512 pixel variants at 1× and 2×, using the exact Apple filename convention.
- [x] Run `iconutil --convert icns` and fail if the output is missing or empty.
- [x] Assemble `dist/Real-time Transcript.app` with:
  - `Contents/MacOS/RealTimeTranscript` from the launcher;
  - `Contents/Resources/app` containing only the source/runtime files needed by `quickstart.py` and `app.py`;
  - `Contents/Resources/Real-time Transcript.icns`;
  - `Contents/Info.plist` with a build version.
- [x] Exclude `.git`, `.worktrees`, `.venv`, `.env`, `.cache`, model files, recordings, logs, tests, screenshots, and local build scratch files from the bundle.
- [x] Create a temporary DMG staging directory with an `Applications` symlink, then create `dist/Real-time-Transcript-macOS.dmg` with `hdiutil create -format UDZO`.
- [x] Support `VERSION`, `ARCH`, and optional `CODESIGN_IDENTITY` environment variables without requiring signing for local builds.
- [x] Print the exact first-run behavior: the user opens the app, the launcher bootstraps dependencies in Application Support, then the model manager downloads the selected model.
- [x] Add non-macOS tests that inspect the script text and a macOS integration test that runs the build in a temporary directory when all tools are available.

## Task 5: Document the install and first-run experience

**Files:** `README.md`, `QUICKSTART.md`, `docs/DEPLOYMENT.md`, `docs/superpowers/specs/2026-09-11-macos-installer-icon-design.md` only if implementation details need an addendum.

- [x] Add a “macOS DMG” section explaining prerequisites for developers who build releases, how users launch the app, where runtime files live, and how to remove/reset local runtime state.
- [x] Document that the DMG is currently unsigned if no `CODESIGN_IDENTITY` is supplied, and give the macOS Control-click → Open path for the first launch.
- [x] Document that model download is intentionally separate from the app bundle and may take time on the first run.
- [x] Explain that normal `./quickstart.sh` and `python quickstart.py` behavior is unchanged for source checkouts.
- [x] Add troubleshooting for blocked model downloads, port conflicts, missing microphone permission, and stale PID files.
- [x] Keep the two repositories decoupled: this repository owns transcription, local runtime, and packaging; the separated generative-AI subtitle project is not added as a dependency.

## Task 6: Verify the release artifact end to end

**Files:** no production files unless verification exposes a defect.

- [x] Run `bash -n` for every shell script in the repository.
- [x] Run `python -m py_compile` on changed Python files.
- [x] Run the full test suite with `pytest -q` and fix regressions before packaging.
- [x] On macOS, build the DMG in a clean temporary output directory and verify:
  - `hdiutil imageinfo` can read the DMG;
  - the app bundle has a valid `Info.plist`, launcher, and `.icns` file;
  - no `.env`, API key, `.venv`, model, recording, or log is present under the bundle;
  - the launcher can bootstrap into a temporary Application Support directory and reach `/api/health`.
- [x] If a full live launch is not possible in the current environment, state exactly which check was unavailable rather than claiming it passed.
- [x] Review `git diff --check`, inspect the final diff, and summarize the resulting artifact paths and first-run behavior.

## Suggested verification commands

~~~bash
python -m py_compile quickstart.py backend/config.py
pytest -q
bash -n packaging/macos/launcher.sh scripts/build-macos-dmg.sh
./scripts/build-macos-dmg.sh
hdiutil imageinfo dist/Real-time-Transcript-macOS.dmg
~~~
