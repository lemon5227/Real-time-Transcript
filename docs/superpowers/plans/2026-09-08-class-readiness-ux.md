# Class Readiness UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the lecture workbench explain and verify model readiness, microphone readiness, and transcription startup before the student begins class.

**Architecture:** Add a small backend model manager that reports local dependency, cached-weight, and download state without loading a model at page load. Expose model status and an asynchronous download action over HTTP. Extend the existing no-framework browser client with a readiness checklist, microphone device/level probe, explicit startup states, and actionable recovery copy while preserving the existing Socket.IO and IndexedDB contracts.

**Tech Stack:** Flask, Python standard library threads/urllib/pathlib, browser MediaDevices/Web Audio APIs, vanilla JavaScript, CSS, pytest, Node-based frontend contract tests.

## Global Constraints

- Keep the two repositories decoupled; only change `RealTimeTranscript`.
- Preserve the existing `audio_chunk`, `start_transcription`, `stop_transcription`, and IndexedDB session contracts.
- Never expose cloud API keys or raw audio in HTTP responses or logs.
- Do not load a Whisper model while serving `/`, `/api/capabilities`, or `/api/models`.
- Model downloads must be asynchronous, cancellable, written through a temporary file, and promoted atomically.
- The default first-run path must favor a small local model and must never make the user wait silently for a large model download.
- Respect `prefers-reduced-motion`, keyboard focus, mobile safe areas, and accessible status announcements.

---

### Task 1: Add a testable local model readiness manager

**Files:**
- Create: `backend/model_manager.py`
- Modify: `backend/routes.py:12-46`
- Modify: `backend/__init__.py:20-44`
- Test: `tests/test_model_manager.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Produces `ModelManager(catalog, dependency_checker=None, cache_root=None)`.
- Produces `ModelManager.list_models() -> list[dict]` with `status` values `ready`, `not_downloaded`, `runtime_download`, `downloading`, `failed`, or `dependency_missing`, plus `downloaded_bytes`, `total_bytes`, `progress`, `dependency_available`, `download_supported`, and `weights_available`.
- Produces `ModelManager.start_download(model_id) -> dict` and `ModelManager.cancel_download(model_id) -> dict`.
- Routes expose `GET /api/models`, `POST /api/models/<model_id>/download`, and `POST /api/models/<model_id>/cancel`.

- [ ] **Step 1: Write the failing manager tests**

```python
def test_model_manager_reports_cached_weights(tmp_path):
    catalog = ({"id": "tiny", "label": "Tiny", "size": "~75MB", "best_for": "低配 CPU"},)
    (tmp_path / "tiny.pt").write_bytes(b"ready")
    manager = ModelManager(catalog, dependency_checker=lambda _model: True, cache_root=tmp_path)
    model = manager.list_models()[0]
    assert model["status"] == "ready"
    assert model["weights_available"] is True


def test_model_manager_starts_one_async_download(tmp_path, monkeypatch):
    catalog = ({"id": "tiny", "label": "Tiny", "size": "~75MB", "best_for": "低配 CPU", "url": "https://example.test/tiny.pt"},)
    class Response:
        def info(self): return {"Content-Length": "4"}
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self, _size): return b"data"
    monkeypatch.setattr("backend.model_manager.urllib.request.urlopen", lambda _url: Response())
    manager = ModelManager(catalog, dependency_checker=lambda _model: True, cache_root=tmp_path)
    result = manager.start_download("tiny")
    assert result["status"] in {"downloading", "ready"}
    assert manager.wait_for("tiny", timeout=1)["status"] == "ready"
    assert (tmp_path / "tiny.pt").read_bytes() == b"data"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python3 -m pytest tests/test_model_manager.py -q`

Expected: FAIL because `backend.model_manager` and `ModelManager` do not exist.

- [ ] **Step 3: Implement the minimal manager**

Use `whisper._MODELS` lazily to resolve URLs when the catalog does not include one. Resolve the default cache as `WHISPER_CACHE_DIR`, then `XDG_CACHE_HOME/whisper`, then `~/.cache/whisper`. Treat a model as ready only when its target file exists and its SHA-256 matches the checksum embedded in the Whisper URL; use a `.part` file for downloads. Keep per-model state under a lock, report byte progress from `Content-Length`, use a `threading.Event` for cancellation, and use `os.replace` after a successful checksum check.

- [ ] **Step 4: Add HTTP routes and app wiring**

Store one `ModelManager` in `app.extensions["model_manager"]`. Keep the legacy `available` field as dependency availability while adding the richer readiness fields. Return `404` for an unknown model, `409` for a duplicate/ineligible download, and `202` when a download begins.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run: `python3 -m pytest tests/test_model_manager.py tests/test_routes.py -q`

Expected: PASS.

### Task 2: Add a readiness checklist and model controls to the live page

**Files:**
- Modify: `templates/live-transcript.html:79-108`
- Modify: `static/app.js:1-105,263-303`
- Modify: `static/styles.css:100-140`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes `GET /api/models`, `GET /api/capabilities`, and the model download endpoints.
- Produces a visible `#readiness-panel` with model, microphone, and connection rows; a `#model-download-button`; and an accessible status flow for `idle`, `preparing`, `recording`, `stopping`, `saved`, and `error`.

- [ ] **Step 1: Write failing frontend contract assertions**

```python
def test_live_page_exposes_class_readiness_controls():
    app = create_app({})
    html = app.test_client().get("/").get_data(as_text=True)
    for hook in ["readiness-panel", "model-download-button", "microphone-select", "mic-level"]:
        assert hook in html
    javascript = app.test_client().get("/static/app.js").get_data(as_text=True)
    assert "/api/models" in javascript
    assert "enumerateDevices" in javascript
    assert "preparing" in javascript
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest tests/test_frontend_contract.py::test_live_page_exposes_class_readiness_controls -q`

Expected: FAIL because the new hooks and behavior are absent.

- [ ] **Step 3: Add the readiness panel markup**

Add a compact checklist below the model/language fields. Each row must expose a stable label, a status text, and an optional action. Add a model download button that changes to cancel while downloading. Add a microphone selector, “测试麦克风” button, live level meter with `role="progressbar"`, and a short privacy explanation. Do not hide the primary start button; make its disabled/preparing state explain why it is unavailable.

- [ ] **Step 4: Implement readiness polling and persistence**

Fetch `/api/models` and `/api/capabilities` on load, poll model state every 750ms only while a download is active, render cached/download/error states, and persist the last mode/model/language/course title in `localStorage`. Do not overwrite a user-selected model when capabilities finish loading. Use `aria-live="polite"` for status changes and `aria-busy` on the panel while checking.

- [ ] **Step 5: Run the focused frontend tests and verify they pass**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: PASS.

### Task 3: Implement microphone selection and signal preflight

**Files:**
- Modify: `templates/live-transcript.html:116-120`
- Modify: `static/app.js:190-221,263-303`
- Modify: `static/styles.css:140-180`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces `navigator.mediaDevices.enumerateDevices()` based input selection.
- Produces `testMicrophone()` using an `AnalyserNode` and `requestAnimationFrame`, with cleanup on stop/error.
- `startListening()` requests the selected `deviceId`, rejects an obviously silent input with a recoverable message, and shows the selected device label when available.

- [ ] **Step 1: Add failing contract checks**

```python
def test_frontend_requests_selected_microphone_and_has_signal_health():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "deviceId" in javascript
    assert "AnalyserNode" in javascript or "createAnalyser" in javascript
    assert "没有检测到麦克风声音" in javascript
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest tests/test_frontend_contract.py::test_frontend_requests_selected_microphone_and_has_signal_health -q`

Expected: FAIL because selection and level analysis are absent.

- [ ] **Step 3: Implement the microphone probe**

Enumerate audio inputs after permission is granted, retain the selected device ID, update the selector when devices change, and use an analyser attached to the capture stream to render a normalized level. Keep the probe in the browser only; never send test audio to the server.

- [ ] **Step 4: Wire the selected device into recording**

Pass `deviceId: { exact: selectedDeviceId }` only when the user selected a concrete device; otherwise preserve browser default selection. Show “麦克风已连接 · <device>” when capture starts and “没有检测到麦克风声音” when the level stays below threshold during the test. Allow retry without reloading.

- [ ] **Step 5: Run focused tests and verify**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: PASS.

### Task 4: Make startup and failure states explicit

**Files:**
- Modify: `static/app.js:45-75,223-335`
- Modify: `templates/live-transcript.html:40-75,116-120`
- Modify: `static/styles.css:60-100,180-230`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces `setAppPhase(phase, message, detail)` and `showActionableError(message, detail, action)`, with action values such as `download-model`, `retry-mic`, and `switch-cloud`.
- Preserves `window.startLecture` and `window.stopLecture`.

- [ ] **Step 1: Add failing startup-state assertions**

```python
def test_frontend_has_actionable_startup_and_recovery_copy():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "正在准备麦克风" in javascript
    assert "正在加载模型" in javascript
    assert "重试麦克风" in javascript
    assert "切换到云端" in javascript
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest tests/test_frontend_contract.py::test_frontend_has_actionable_startup_and_recovery_copy -q`

Expected: FAIL because startup phases and recovery actions are not implemented.

- [ ] **Step 3: Implement the phase state machine**

Add a `starting`/`finishing` guard so double clicks cannot issue duplicate starts. Disable setup controls while preparing or recording, change the primary label to “准备麦克风…”, “正在加载模型…”, “结束听课”, or “正在保存…”, and set `aria-busy` on the workspace. Check the selected local model status before emitting `start_transcription`; if it is not ready, offer download. In auto mode with configured cloud and unavailable local weights, make the outgoing payload explicitly use cloud and show that choice.

- [ ] **Step 4: Add recovery actions and truthful privacy copy**

Render an inline recovery panel with a primary button tied to the current error. Update auto-mode copy to say that automatic fallback can send audio to the configured cloud endpoint, and require the user to choose cloud or disable fallback before starting when local is unavailable. Make Socket.IO disconnect and provider errors return the UI to a recoverable state instead of leaving a recording-looking button active.

- [ ] **Step 5: Run focused tests and verify**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: PASS.

### Task 5: Complete the visual and accessibility polish for the new flow

**Files:**
- Modify: `templates/live-transcript.html:1-128`
- Modify: `static/styles.css:1-220`
- Modify: `templates/review.html:1-60`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Adds skip links, visible focus states, readable readiness text, `env(safe-area-inset-bottom)`, and local Socket.IO fallback messaging without changing application APIs.

- [ ] **Step 1: Add failing markup/style assertions**

```python
def test_pages_include_keyboard_and_mobile_safety_hooks():
    app = create_app({})
    live = app.test_client().get("/").get_data(as_text=True)
    review = app.test_client().get("/review").get_data(as_text=True)
    stylesheet = app.test_client().get("/static/styles.css").get_data(as_text=True)
    assert 'class="skip-link"' in live
    assert 'class="skip-link"' in review
    assert "safe-area-inset-bottom" in stylesheet
    assert 'aria-label="搜索课堂笔记"' in review
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m pytest tests/test_frontend_contract.py::test_pages_include_keyboard_and_mobile_safety_hooks -q`

Expected: FAIL because these hooks are absent.

- [ ] **Step 3: Implement the polish**

Add skip links targeting main content, `:focus-visible` outlines for all buttons/links/selects/inputs, `aria-hidden` on decorative SVGs, safe-area padding for the mobile control dock, and an accessible search label on the review page. Keep the existing reduced-motion rule and avoid introducing a new frontend dependency.

- [ ] **Step 4: Run all tests and static checks**

Run: `python3 -m pytest -q && python3 -m compileall -q backend app.py && ruff check backend tests`

Expected: PASS with no new warnings.

### Task 6: Verify the real student flow

**Files:**
- No new production files.
- Test artifacts: keep outside the repository.

- [ ] **Step 1: Verify backend endpoints with a fresh app**

Run: `python3 -m pytest -q` and request `/api/models`, `/api/capabilities`, and `/api/health` from the running Miniconda-backed service.

- [ ] **Step 2: Verify model manager behavior in the browser**

Open the live page, confirm Tiny/Small/Medium show distinct readiness states, start and cancel a download on a test model state if available, and confirm the primary button never waits silently.

- [ ] **Step 3: Verify microphone preflight**

Use a real microphone or browser synthetic input, select it, run the test, confirm the level meter moves, then start recording and confirm the status changes through preparation to recording.

- [ ] **Step 4: Verify recovery**

Test denied microphone permission, unavailable model, disconnected Socket.IO, and cloud-unconfigured auto mode. Each case must show one clear next action and must leave the page startable again.

- [ ] **Step 5: Inspect the final diff and repository hygiene**

Run `git diff --check`, `git status --short`, and ensure no model weights, recordings, browser artifacts, credentials, or temporary download files are tracked.
