# Nemotron CoreML Diarization Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional local Nemotron CoreML speaker attribution to the macOS live transcription path without delaying Parakeet captions or breaking recording fallback.

**Architecture:** Python owns the session, audio fan-out, timestamp alignment, and Socket.IO events. A single-threaded Swift helper owns FluidAudio/Nemotron CoreML and communicates over JSON Lines. Speaker turns are merged asynchronously into existing transcript segment IDs; missing or failed helper processes degrade to ordinary ASR.

**Tech Stack:** Python 3.10+, Flask-SocketIO, NumPy, pytest, Swift 6 / Swift Package Manager, FluidAudio 0.17.1, CoreML/Apple Neural Engine on macOS 14+.

## Global Constraints

- Parakeet MLX remains the Mac ASR provider and must run on its existing worker thread.
- Recording starts before model/helper readiness and is never discarded because diarization is unavailable.
- No new Python runtime dependency is required for the CoreML integration.
- The helper protocol is JSON Lines over stdin/stdout and must not log non-JSON data to stdout.
- CI on Linux must test the Python protocol and fallback paths without importing CoreML or Swift.
- Existing `TranscriptSegment.to_dict()` fields remain compatible; speaker fields are optional.

---

### Task 1: Add speaker data contracts and pure timeline attribution

**Files:**
- Create: `backend/diarization.py`
- Modify: `backend/models.py`
- Create: `tests/test_diarization.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- `SpeakerTurn(speaker_id: str, start_ms: int, end_ms: int, confidence: float)` is immutable and serializable.
- `SpeakerTimeline.add_turns(turns)` stores sorted, non-overlapping speaker turns.
- `SpeakerTimeline.attribute(segment)` returns `(speaker_id, confidence)` only when one speaker has at least 50% of the segment overlap and beats the next speaker by 0.10 overlap ratio.
- `SpeakerTimeline.updates_for_segments(segments)` returns only segments whose speaker attribution changed.

- [ ] **Step 1: Write failing tests** for turn validation, overlap attribution, ambiguous overlap returning no speaker, late turn updates, and identical repeated updates being ignored.
- [ ] **Step 2: Run `pytest -q tests/test_diarization.py tests/test_models.py` and verify the new tests fail because the contract is absent.
- [ ] **Step 3: Add optional `speaker_id` and `speaker_confidence` fields to `TranscriptSegment`, preserving the six existing positional arguments and validating confidence.
- [ ] **Step 4: Implement `SpeakerTurn` and `SpeakerTimeline` with deterministic overlap math and no dependency on CoreML.
- [ ] **Step 5: Run the focused tests and then `pytest -q tests/test_models.py tests/test_diarization.py`.
- [ ] **Step 6: Commit `feat: add speaker attribution data contracts`.

### Task 2: Add a JSONL Nemotron helper client with a no-op fallback

**Files:**
- Create: `backend/providers/diarization.py`
- Create: `tests/test_diarization_provider.py`

**Interfaces:**
- `SpeakerDiarizer` protocol: `start(sample_rate, variant)`, `push(audio, start_ms)`, `flush()`, `close()`.
- `NullSpeakerDiarizer` implements the protocol and always returns no turns.
- `JsonlNemotronDiarizer` starts a configured executable, sends protocol messages, reads JSON responses, validates turn payloads, and raises `ProviderError` with stable `DIARIZATION_*` codes.
- `create_diarizer(command, enabled, variant)` returns the no-op implementation when disabled or command is empty.

- [ ] **Step 1: Write failing tests** using a real temporary Python helper executable that returns `ready`, `speaker_turns`, malformed JSON, and an early exit; assert valid turns and stable errors.
- [ ] **Step 2: Run `pytest -q tests/test_diarization_provider.py` and verify the tests fail before the client exists.
- [ ] **Step 3: Implement the protocol client with `subprocess.Popen`, line-buffered stdin/stdout, one lock around request/response, and stderr drained by a daemon thread.
- [ ] **Step 4: Add bounded payload validation and terminate/kill cleanup so a broken helper cannot remain orphaned.
- [ ] **Step 5: Run the focused provider tests and `ruff check backend/providers/diarization.py tests/test_diarization_provider.py`.
- [ ] **Step 6: Commit `feat: add JSONL speaker diarization provider`.

### Task 3: Fan out session audio and emit late speaker updates

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/config.py`
- Modify: `backend/session_manager.py`
- Modify: `backend/__init__.py`
- Modify: `backend/routes.py`
- Modify: `tests/test_session_manager.py`
- Modify: `tests/test_socket_events.py`

**Interfaces:**
- `SessionConfig.enable_diarization: bool` and `SessionConfig.diarization_variant: str` are optional session controls.
- `SessionManager(..., diarizer_factory=...)` accepts a factory for tests and creates one diarizer per session.
- Events: `diarization_ready`, `diarization_status`, `transcript_segment_updated`, and existing `transcription_error` codes remain stable.

- [ ] **Step 1: Write failing session tests** showing ASR receives audio while a slow diarizer is blocked, a final transcript is emitted without waiting, and a later turn emits an update for the same segment ID.
- [ ] **Step 2:** Run the focused tests and verify failure.
- [ ] **Step 3: Add a bounded diarization queue and worker to `_SessionState`; fan out already-normalized audio from `push_audio` without changing ASR queue semantics.
- [ ] **Step 4: Start/flush/close the diarizer on its own worker; log failures and switch the session to `NullSpeakerDiarizer` without stopping ASR.
- [ ] **Step 5: Add `SpeakerTimeline` attribution after ASR merger updates and emit `transcript_segment_updated` only for changed speaker fields.
- [ ] **Step 6: Add config parsing and public capability fields for `DIARIZATION_ENABLED`, `DIARIZATION_COMMAND`, `DIARIZATION_VARIANT`, and `DIARIZATION_QUEUE`.
- [ ] **Step 7: Run `pytest -q tests/test_session_manager.py tests/test_socket_events.py tests/test_diarization.py`.
- [ ] **Step 8: Commit `feat: merge asynchronous speaker turns into live sessions`.

### Task 4: Add the macOS Swift/CoreML helper

**Files:**
- Create: `native/nemotron-diarizer/Package.swift`
- Create: `native/nemotron-diarizer/Sources/NemotronDiarizer/main.swift`
- Create: `native/nemotron-diarizer/README.md`
- Create: `tests/test_native_diarizer_contract.py`

**Interfaces:**
- Executable name: `echonote-nemotron-diarizer`.
- Input/output follows the JSON Lines contract in the design spec.
- The service loads `Nemotron3Config.fast` for live mode by default, uses `Nemotron3Diarizer.appendAudio/processBufferedAudio/finishStream`, converts probabilities through `Nemotron3Diarizer.segments`, and writes only protocol JSON to stdout.
- Model directory is resolved from `NEMOTRON_MODEL_DIR`; absent directory uses FluidAudio’s Hugging Face loader and shared `HF_HOME/HF_HUB_CACHE`.

- [ ] **Step 1: Write a contract test** that checks the package manifest, executable name, protocol examples, and source contains no stdout logging outside JSON responses.
- [ ] **Step 2: Run the contract test and verify it fails because the native package is absent.
- [ ] **Step 3: Create the Swift package pinned to FluidAudio `0.17.1` and implement JSON decoding/encoding, model loading, streaming audio conversion, and graceful stop.
- [ ] **Step 4: Build with `swift build -c release` on macOS and run a synthetic two-speaker smoke test against the built executable.
- [ ] **Step 5: Run Python contract tests on all platforms; skip the CoreML smoke test unless the host is macOS with Swift available.
- [ ] **Step 6: Commit `feat: add macOS Nemotron diarization helper`.

### Task 5: Expose status in the live page without changing the caption layout

**Files:**
- Modify: `templates/live-transcript.html`
- Modify: `static/app.js`
- Modify: `static/transcript-current.js`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Live page renders a compact, non-blocking speaker status indicator near existing model status.
- `transcript_segment_updated` updates an existing article by segment ID; it never appends a second article.
- If no speaker ID exists, the label is hidden rather than showing an error state.

- [ ] **Step 1:** Add failing frontend contract assertions for the new socket event, speaker label hook, and hidden fallback state.
- [ ] **Step 2:** Run `pytest -q tests/test_frontend_contract.py` and verify failure.
- [ ] **Step 3: Implement DOM update-by-ID and subtle speaker metadata styling; preserve current auto-follow behavior and historical subtitle layout.
- [ ] **Step 4: Run `node --check static/app.js static/transcript-current.js` and the focused frontend tests.
- [ ] **Step 5: Commit `feat: show asynchronous speaker labels in live captions`.

### Task 6: Add model management, packaging, documentation, and verification

**Files:**
- Modify: `backend/model_manager.py`
- Modify: `backend/routes.py`
- Modify: `scripts/build-macos-dmg.sh`
- Modify: `packaging/macos/launcher.sh`
- Modify: `.github/workflows/macos-dmg.yml`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `README.macOS.md`
- Modify: `tests/test_model_manager.py`
- Modify: `tests/test_macos_packaging.py`
- Modify: `tests/test_documentation_contract.py`

**Interfaces:**
- Model catalog reports Nemotron variant, approximate size, local cache state, and whether the native helper is installed.
- DMG includes a prebuilt helper when available; local development continues to work without it.
- GitHub Actions macOS build compiles the Swift helper before assembling the app and fails clearly if the helper build fails.

- [ ] **Step 1: Add failing tests** for model catalog status, shared-cache reuse, helper bundling, and documentation claims.
- [ ] **Step 2:** Run focused tests and verify failure.
- [ ] **Step 3: Add helper/model status to the existing settings APIs without making ASR readiness depend on Nemotron.
- [ ] **Step 4: Update DMG build allowlist and launcher environment so the bundled helper is found and logs remain in the app support directory.
- [ ] **Step 5: Update CI to resolve/build the native helper on `macos-14`, then package it into the DMG.
- [ ] **Step 6: Update the roadmap with verified versus pending CoreML checks.
- [ ] **Step 7: Run the full verification suite: `pytest -q`, `ruff check .`, `for file in static/*.js; do node --check "$file"; done`, and macOS `swift build -c release` where available.
- [ ] **Step 8: Inspect `git diff --check`, `git status --short`, and test output before claiming completion.
- [ ] **Step 9: Commit `feat: package Nemotron diarization for macOS`.
