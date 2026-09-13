# Post-Class Refinement Implementation Plan

> 当前状态（2026-09-13）：精细转录服务、API、浏览器保存和实时稿/精细稿切换均已实现并有自动化测试；真实 MLX 录音端到端验收尚待完成。当前状态以[项目路线图](../../ROADMAP.md)为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reliable post-class fine-transcription workflow that uses the saved local recording and Parakeet MLX batch context, while preserving the real-time transcript as an independent source.

**Architecture:** The browser keeps `segments` as the real-time transcript and stores a second `refinedSegments` array plus refinement metadata in the same IndexedDB session record. A local Flask background job accepts the saved recording, temporarily materializes it, reuses the shared MLX model cache, and returns timestamped refined segments; the browser polls the job and persists the result. The review page exposes an explicit real-time/refined source switch, status, retry behavior, audio seek, editing, notes, translation, and export without replacing the existing layout.

**Tech Stack:** Flask, Flask-SocketIO, Python threading, Parakeet MLX, IndexedDB, vanilla JavaScript, pytest, Node contract tests.

## Global Constraints

- Refined transcription must never overwrite or mutate real-time `segments`.
- Original audio remains browser-local; the backend may use only a temporary file for the active job and must delete it afterward.
- The first implementation is local MLX/Apple Silicon only; Windows, CUDA, and cloud transcription remain out of scope.
- The shared `MlxModelCache` must serialize model use so refinement cannot race with a live stream.
- Every implementation change needs a failing test first, then a focused test run, then the broader regression suite.

### Task 1: Define and test the refinement job service

**Files:**
- Create: `backend/fine_transcription.py`
- Test: `tests/test_fine_transcription.py`

**Interfaces:**
- Consumes: `MlxModelCache.acquire(model_ref)`, `release(model_ref)`, and a Parakeet MLX model exposing `transcribe(path, chunk_duration=..., overlap_duration=..., chunk_callback=...)`.
- Produces: `FineTranscriptionManager.start(audio_bytes, suffix, language, model_ref) -> dict`, `get(job_id) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fine_transcription_job_returns_timestamped_segments():
    manager = FineTranscriptionManager(model_cache=FakeCache(FakeBatchModel()))
    created = manager.start(b"audio", ".webm", "en", "mlx-community/parakeet-tdt-0.6b-v3")
    result = wait_until(lambda: manager.get(created["job_id"]))
    assert result["status"] == "ready"
    assert result["segments"] == [
        {"id": "refined-0", "text": "Hello class.", "startMs": 1200, "endMs": 2800, "isFinal": True}
    ]
    assert result["model"] == "mlx-community/parakeet-tdt-0.6b-v3"


def test_fine_transcription_job_records_actionable_failure():
    manager = FineTranscriptionManager(model_cache=FakeCache(RuntimeError("missing mlx")))
    created = manager.start(b"audio", ".webm", "en", "mlx-community/parakeet-tdt-0.6b-v3")
    result = wait_until(lambda: manager.get(created["job_id"]))
    assert result["status"] == "failed"
    assert result["error"]["code"] == "FINE_TRANSCRIPTION_FAILED"
    assert result["error"]["action"]
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_fine_transcription.py -q`

Expected: FAIL because `backend.fine_transcription` and `FineTranscriptionManager` do not exist.

- [ ] **Step 3: Implement the minimal service**

Implement a locked in-memory job registry with `queued`, `processing`, `ready`, and `failed` states. Write each upload to a `NamedTemporaryFile`, acquire the requested model from the shared cache, call batch `transcribe`, map `sentences` to `{id,text,startMs,endMs,isFinal}`, update progress through `chunk_callback`, release the cache in `finally`, and unlink the temporary file in `finally`. Limit accepted bytes to 512 MiB and reject empty audio.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_fine_transcription.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/fine_transcription.py tests/test_fine_transcription.py
git commit -m "feat: add local fine transcription jobs"
```

### Task 2: Expose the local refinement API and shared cache

**Files:**
- Modify: `backend/providers/factory.py`
- Modify: `backend/__init__.py`
- Modify: `backend/routes.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `FineTranscriptionManager` from Task 1.
- Produces: `POST /api/refine-transcription`, `GET /api/refine-transcription/<job_id>`, and `ProviderFactory.mlx_model_cache`.

- [ ] **Step 1: Write the failing route tests**

```python
def test_refine_transcription_requires_audio():
    app = create_app({})
    response = app.test_client().post("/api/refine-transcription")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "FINE_TRANSCRIPTION_INVALID_AUDIO"


def test_refine_transcription_returns_pollable_job(monkeypatch):
    app = create_app({})
    fake = ImmediateFineManager()
    app.extensions["fine_transcription_manager"] = fake
    response = app.test_client().post(
        "/api/refine-transcription",
        data={"audio": (io.BytesIO(b"wav"), "lecture.webm"), "language": "en"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]
    assert app.test_client().get(f"/api/refine-transcription/{job_id}").get_json()["status"] == "ready"
```

- [ ] **Step 2: Run route tests to verify they fail**

Run: `pytest tests/test_routes.py -q`

Expected: FAIL because the refinement routes and extension are absent.

- [ ] **Step 3: Implement the routes and wiring**

Expose the factory cache through a read-only property, create one `FineTranscriptionManager` in `create_app`, and register it in `app.extensions`. The POST route reads at most 512 MiB plus one byte, derives a safe audio suffix, returns `202` with a job id, and maps missing/empty audio to a structured 400 response. The GET route returns 404 for an unknown job and otherwise returns the manager snapshot.

- [ ] **Step 4: Run focused and existing backend tests**

Run: `pytest tests/test_routes.py tests/test_fine_transcription.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/providers/factory.py backend/__init__.py backend/routes.py tests/test_routes.py
git commit -m "feat: expose post-class refinement endpoint"
```

### Task 3: Preserve refined transcripts in browser storage

**Files:**
- Modify: `static/storage.js`
- Test: `tests/test_frontend_audio_storage.py`

**Interfaces:**
- Consumes: API result fields `segments`, `model`, `language`, `status`, and `completedAt`.
- Produces: normalized session fields `refinedSegments` and `refinement`.

- [ ] **Step 1: Write the failing Node test**

```javascript
const record = context.window.EchoStore.normalizeSession({
  id: 'refined', segments: [{id: 'live-1', text: 'draft'}],
  refinedSegments: [{id: 'refined-0', text: 'polished', startMs: 1000, endMs: 2400}],
  refinement: {status: 'ready', provider: 'mlx', model: 'parakeet', completedAt: '2026-09-11T10:00:00Z'}
});
if (record.refinedSegments[0].text !== 'polished') process.exit(1);
if (record.refinement.status !== 'ready' || record.refinement.provider !== 'mlx') process.exit(2);
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_frontend_audio_storage.py::test_session_normalization_preserves_refined_transcript -q`

Expected: FAIL because normalized records discard the new fields.

- [ ] **Step 3: Implement normalization**

Normalize both camelCase and snake_case input, reuse `normalizeSegment`, and default refinement metadata to `not_started` without changing legacy session shape or audio behavior.

- [ ] **Step 4: Run the focused test**

Run: `pytest tests/test_frontend_audio_storage.py -q`

- [ ] **Step 5: Commit**

```bash
git add static/storage.js tests/test_frontend_audio_storage.py
git commit -m "feat: persist refined transcript metadata"
```

### Task 4: Add review-page source switching and refinement controls

**Files:**
- Modify: `templates/review.html`
- Modify: `static/review.js`
- Modify: `static/styles.css`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: local audio Blob from `EchoAudioRepository`, Task 2 API, and Task 3 session shape.
- Produces: `#refine-transcription`, `#refine-transcription-status`, `#transcript-mode-realtime`, `#transcript-mode-refined`, and an editable active transcript that still supports notes, translation, seek, and export.

- [ ] **Step 1: Write failing HTML/JS contract tests**

```python
def test_review_page_exposes_fine_transcription_controls():
    app = create_app({})
    html = app.test_client().get("/review").get_data(as_text=True)
    javascript = app.test_client().get("/static/review.js").get_data(as_text=True)
    for hook in ["refine-transcription", "refine-transcription-status", "transcript-mode-realtime", "transcript-mode-refined"]:
        assert hook in html
    for hook in ["refinedSegments", "/api/refine-transcription", "transcriptionMode"]:
        assert hook in javascript
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `pytest tests/test_frontend_contract.py::test_review_page_exposes_fine_transcription_controls -q`

Expected: FAIL because the controls and API hooks do not exist.

- [ ] **Step 3: Implement the review workflow**

Add a compact refinement toolbar below original audio with status/progress and two source buttons. Store the loaded Blob in review state, avoid reloading it on every render, upload it with `FormData`, poll until ready/failed, persist the returned refined transcript, and default to refined mode only after success. Add `segmentsOf` helpers for realtime/refined sources; route `renderDetail`, selection, translation, current-audio highlighting, library search, and export through the active source. Set each rendered card’s `data-segment-id` so translations update the right card. Keep real-time notes and refined notes isolated by transcript source.

- [ ] **Step 4: Run frontend contract and JavaScript tests**

Run: `pytest tests/test_frontend_contract.py tests/test_frontend_translation_review.py tests/test_frontend_audio_storage.py -q` and `node --check static/review.js`.

- [ ] **Step 5: Commit**

```bash
git add templates/review.html static/review.js static/styles.css tests/test_frontend_contract.py
git commit -m "feat: add post-class refinement to review page"
```

### Task 5: Verify end-to-end behavior and document limits

**Files:**
- Modify: `docs/MAINTENANCE.md`
- Modify: `docs/LATENCY.md`
- Test: existing backend/frontend suites

- [ ] **Step 1: Run the complete automated suite**

Run: `pytest -q --basetemp=/tmp/rtt-pytest-review`.

- [ ] **Step 2: Run static checks**

Run: `ruff check .`, `python -m compileall -q backend`, and `node --check static/review.js`.

- [ ] **Step 3: Exercise the API without a real MLX model**

Use the fake model tests to verify queued/processing/ready/failed transitions, timestamp conversion, cache release, invalid upload handling, and temporary-file cleanup.

- [ ] **Step 4: Update maintenance documentation**

Document that fine transcription is an explicit post-class batch pass, uses the saved browser-local recording, shares the exclusive MLX cache, preserves the real-time draft, and is currently local Apple Silicon only.

- [ ] **Step 5: Commit**

```bash
git add docs/MAINTENANCE.md docs/LATENCY.md
git commit -m "docs: document post-class refinement workflow"
```
