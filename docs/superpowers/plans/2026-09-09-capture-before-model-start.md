# 先录音后转录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户点击“开始听课”后立即开始收音，并在 Parakeet/MLX 模型启动完成后按序补处理开头音频。

**Architecture:** 后端 `SessionManager.start()` 只负责创建会话、启动 worker 并立即返回 session metadata；provider 仍在同一 worker 线程中加载，加载完成后通过 `transcription_ready` 事件通知前端。前端在会话创建回调后立刻启动采集和原声保存，后端有界队列接收模型启动期间的 PCM 分片并在 ready 后消费。

**Tech Stack:** Python 3.12、Flask-SocketIO、threading/queue、NumPy、原生 JavaScript、AudioWorklet、IndexedDB/OPFS、pytest、Node syntax check、ruff。

## Global Constraints

- 模型启动期间继续录音，待转录队列最多保留 30 秒。
- Parakeet TDT v3 + MLX 继续使用连续 `transcribe_stream()`，不更换模型或破坏 MLX 线程亲和性。
- 原声保存从会话开始持续进行，不受待转录队列上限影响。
- 模型未 ready 时显示“正在收音 · 模型准备中”，不得伪造字幕。
- 模型启动失败时仍允许结束课堂并保存本地原声和已有字幕。
- 保持当前字幕历史、临时字幕、翻译、自动跟随和左右布局不变。
- 所有任务完成后运行完整 Python 测试、Node 语法检查、ruff 和 `git diff --check`。

---

### Task 1: Make the session manager accept audio before provider readiness

**Files:**
- Modify: `backend/session_manager.py:35-106, 146-205`
- Test: `tests/test_session_manager.py`

**Interfaces:**
- Consumes: existing `TranscriptionProvider.start/push/flush/close`, `SessionConfig.max_queue`, and `EmitCallback`.
- Produces: `SessionManager.start(sid, config)` returns `{status: "starting", session_id, provider, model, ready: false}` immediately; worker emits `transcription_ready` with `{status: "ready", session_id, provider, model}` after provider startup.

- [ ] **Step 1: Write the failing tests**

Add a delayed provider and assert that `start()` returns before `provider.start()` is released, that audio can be queued during the delay, and that the worker consumes it after readiness:

```python
def test_start_returns_before_provider_is_ready_and_replays_queued_audio():
    started = threading.Event()
    release = threading.Event()
    received = []

    class DelayedProvider(FakeProvider):
        def start(self, _config):
            started.set()
            assert release.wait(timeout=1)

        def push(self, audio):
            received.append(audio.copy())
            return []

    events = []
    manager = SessionManager(
        provider_factory=lambda _config: DelayedProvider(),
        emit=lambda _sid, event, payload: events.append((event, payload)),
    )
    config = SessionConfig("local", "fake", "en", 16000, window_seconds=0.2, overlap_seconds=0.0)

    result = manager.start("sid-1", config)
    assert result["status"] == "starting"
    assert result["ready"] is False
    assert started.wait(timeout=1)
    manager.push_audio("sid-1", base64.b64encode(b"\x00\x00" * 3200).decode(), 16000, 0)
    release.set()

    manager.stop("sid-1")
    assert received
    ready = [payload for event, payload in events if event == "transcription_ready"]
    assert ready and ready[0]["session_id"] == result["session_id"]
```

Add a startup-failure test proving the error is emitted asynchronously and `stop()` can still close the session:

```python
def test_provider_start_failure_is_emitted_and_session_can_be_stopped():
    emitted = []

    class FailingProvider(FakeProvider):
        def start(self, _config):
            raise ProviderError("MODEL_START_FAILED", "模型启动失败", "切换模型")

    manager = SessionManager(
        provider_factory=lambda _config: FailingProvider(),
        emit=lambda _sid, event, payload: emitted.append((event, payload)),
    )
    result = manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))
    manager._sessions["sid-1"].worker.join(timeout=1)
    stopped = manager.stop("sid-1")

    assert result["status"] == "starting"
    assert any(event == "transcription_error" and payload["code"] == "MODEL_START_FAILED" for event, payload in emitted)
    assert stopped["status"] == "error"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `PYTHONPATH=. pytest -q tests/test_session_manager.py -k 'returns_before_provider or provider_start_failure'`

Expected: FAIL because `start()` currently waits for `startup_event` and does not emit readiness or asynchronous startup errors.

- [ ] **Step 3: Implement the minimal asynchronous lifecycle**

In `SessionManager.start()` remove the synchronous `startup_event.wait()` timeout branch. Return immediately after registering and starting the worker:

```python
return {
    "status": "starting",
    "ready": False,
    "session_id": state.session_id,
    "provider": getattr(provider, "name", "unknown"),
    "model": getattr(provider, "model", None),
}
```

In `_run_worker()` set `startup_event`, then emit `transcription_ready` before entering the queue loop. In both startup exception branches, normalize the exception to `ProviderError`, set `state.startup_error` and `state.error`, set `startup_event`, and emit `transcription_error`. Do not discard the session from the worker; `stop()` remains responsible for cleanup.

Keep `audio_queue` creation before worker startup so `push_audio()` can enqueue PCM while provider loading. Keep provider startup and all MLX inference in the same worker thread.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `PYTHONPATH=. pytest -q tests/test_session_manager.py -k 'returns_before_provider or provider_start_failure'`

Expected: PASS.

- [ ] **Step 5: Run the existing session manager regression tests**

Run: `PYTHONPATH=. pytest -q tests/test_session_manager.py`

Expected: PASS. If tests that assumed synchronous startup race with the worker, wait on their test provider’s event before stopping; do not reintroduce a production startup wait.

### Task 2: Expose the two-phase lifecycle through Socket.IO

**Files:**
- Modify: `backend/routes.py:188-252`
- Test: `tests/test_socket_events.py`

**Interfaces:**
- Consumes: Task 1’s `SessionManager.start()` and worker emit callback.
- Produces: `start_transcription` callback and `transcription_session_created` event return `status: "starting"`; worker-generated `transcription_ready` reaches the same Socket.IO client.

- [ ] **Step 1: Write the failing socket event test**

Add a delayed fake provider and verify session-created is available before provider readiness, audio is accepted during startup, and ready follows:

```python
def test_socket_accepts_audio_while_provider_is_starting():
    started = threading.Event()
    release = threading.Event()

    class DelayedProvider(FakeProvider):
        def start(self, _config):
            started.set()
            assert release.wait(timeout=1)

    app = create_app({}, provider_factory=lambda _config: DelayedProvider())
    client = socketio.test_client(app)
    result = client.emit("start_transcription", {"mode": "local", "model": "fake", "language": "en"}, callback=True)

    assert result["status"] == "starting"
    assert started.wait(timeout=1)
    accepted = client.emit("audio_chunk", {"audio": base64.b64encode(b"\x00\x00" * 160).decode(), "sample_rate": 16000, "sequence": 0}, callback=True)
    assert accepted["status"] == "accepted"
    release.set()

    ready = []
    for _ in range(10):
        ready.extend(client.get_received())
        if any(item["name"] == "transcription_ready" for item in ready):
            break
        time.sleep(0.01)
    assert any(item["name"] == "transcription_ready" for item in ready)
    client.emit("stop_transcription", {}, callback=True)
```

- [ ] **Step 2: Run the focused socket test to verify it fails**

Run: `PYTHONPATH=. pytest -q tests/test_socket_events.py -k 'accepts_audio'`

Expected: FAIL because the start callback currently reports `success` only after provider startup and no `transcription_ready` event exists.

- [ ] **Step 3: Update the Socket.IO start handler**

After `manager.start()` succeeds, emit `transcription_session_created` to the requesting sid and return the starting result. Remove the synchronous `transcription_started` emission; the worker’s `transcription_ready` callback is the readiness event. Keep error handling for provider-factory/configuration failures unchanged.

- [ ] **Step 4: Run socket tests**

Run: `PYTHONPATH=. pytest -q tests/test_socket_events.py`

Expected: PASS.

- [ ] **Step 5: Commit the backend lifecycle changes**

```bash
git add backend/session_manager.py backend/routes.py tests/test_session_manager.py tests/test_socket_events.py
git commit -m "feat: accept audio while transcription model starts"
```

### Task 3: Start browser capture as soon as the session exists

**Files:**
- Modify: `static/app.js:90-165, 1040-1158, 1300-1418, 1555-1588`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: `transcription_session_created`/starting Socket.IO ack, `transcription_ready`, existing AudioWorklet PCM callbacks and `EchoAudioRecorder`.
- Produces: capture state that can be active while transcription is not ready; ordered backend audio delivery and local recording from the beginning of the session.

- [ ] **Step 1: Write the failing frontend contract assertions**

Extend the live workbench contract test with the required state and behavior markers:

```python
def test_live_workbench_captures_while_model_is_starting():
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    for hook in [
        "transcription_session_created",
        "transcription_ready",
        "正在收音 · 模型准备中",
        "pendingAudio",
        "MAX_PENDING_AUDIO_SECONDS",
        "flushPendingAudio",
    ]:
        assert hook in javascript
    assert "if (!state.recording" not in javascript.split("function sendAudioBuffer", 1)[1].split("function flushPendingAudio", 1)[0]
```

- [ ] **Step 2: Run the focused contract test to verify it fails**

Run: `PYTHONPATH=. pytest -q tests/test_frontend_contract.py -k 'captures_while_model'`

Expected: FAIL because the current app has no two-phase session events or startup audio buffer.

- [ ] **Step 3: Add bounded startup capture state and delivery**

Add `MAX_PENDING_AUDIO_SECONDS = 30`, `state.transcriptionReady`, `state.transcriptionFailed`, `state.pendingAudio`, and `state.pendingAudioSeconds`. Change `sendAudioBuffer()` so it accepts audio while `state.recording` or `state.starting` is active. While `state.sessionId` is absent, store PCM buffers in `pendingAudio`; once session metadata exists, flush them in sequence through `emitAudioBuffer()`.

Limit the queue by audio duration using `buffer.byteLength / 2 / sampleRate`. Remove the oldest pending buffers first and update `feed-hint` with the startup-overflow notice. After the session id is known, emit each queued buffer in order; use the existing `state.sequence` counter for all audio chunks.

The backend session is created before `beginCapture()` in the start callback, so local audio has a real session id. Start `beginCapture()` and `EchoAudioRecorder.start()` immediately after the starting acknowledgement; set capture UI before or during capture setup so the first PCM callback is not rejected by a `state.recording` guard. On setup failure, stop the backend session and finalize any partial audio as today.

- [ ] **Step 4: Add separate capture/ready UI phases**

Update `setAppPhase()` and `setRecordingUi()` so:

```javascript
// model not ready, but microphone and local recording are active
setAppPhase("capturing", "正在收音 · 模型准备中", "模型启动后会补处理已录音频")

// provider ready event
setAppPhase("recording", "正在实时转录", actualProvider + " · " + actualModel)
```

Do not disable the stop button during `capturing`; do not show “正在实时转录” until the ready event. Handle `transcription_ready` by setting `state.transcriptionReady = true`, updating provider/model labels, and switching from `capturing` to `recording` without restarting capture or the audio recorder.

Handle startup `transcription_error` by setting `state.transcriptionFailed = true`, retaining capture/local recording, and showing “转录暂不可用 · 原声仍在保存”; prevent further backend audio sends while allowing the user to stop and save.

Remove the old 45-second timeout branch that releases the microphone and discards the session during model startup. If a wait notice is needed, update only the feed hint and keep capture active.

- [ ] **Step 5: Run the focused frontend contract test**

Run: `PYTHONPATH=. pytest -q tests/test_frontend_contract.py -k 'captures_while_model'`

Expected: PASS.

- [ ] **Step 6: Run JavaScript syntax and frontend regressions**

Run: `node --check static/app.js && PYTHONPATH=. pytest -q tests/test_frontend_contract.py tests/test_frontend_audio_buffer.py tests/test_frontend_audio_recorder.py`

Expected: PASS with no syntax errors.

### Task 4: Make the existing queue represent the approved 30-second startup window

**Files:**
- Modify: `backend/config.py:220-226`, `backend/models.py:20-34`
- Modify: `docs/API.md` audio examples and relevant README configuration text
- Test: `tests/test_config.py` if present, otherwise add to `tests/test_session_manager.py`

**Interfaces:**
- Consumes: existing `AUDIO_MAX_QUEUE` and 0.5-second frontend PCM batching.
- Produces: default queue capacity of 64 PCM chunks (about 32 seconds at the current 0.5-second batching), while honoring explicit user configuration.

- [ ] **Step 1: Write the failing configuration assertion**

Add a test that default configuration exposes the larger startup queue:

```python
def test_default_audio_queue_covers_model_startup_buffer():
    config = load_config({})
    assert config.audio_max_queue == 64
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=. pytest -q tests/test_config.py -k 'startup_buffer'` (or the file containing the new test)

Expected: FAIL because the current default is 32.

- [ ] **Step 3: Update the default and documentation**

Change only the default fallback from `"32"` to `"64"`; keep environment overrides and minimum validation unchanged. Update API examples and README configuration notes to describe the approximately 30-second model-start buffer and the existing backpressure behavior.

- [ ] **Step 4: Run configuration and documentation contracts**

Run: `PYTHONPATH=. pytest -q tests/test_config.py tests/test_documentation_contract.py`

Expected: PASS.

### Task 5: Integrate stop/error cleanup and verify end-to-end behavior

**Files:**
- Modify: `static/app.js:1390-1420, 1570-1585`
- Modify: `backend/session_manager.py:104-145`
- Test: `tests/test_session_manager.py`, `tests/test_socket_events.py`, `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Tasks 1–4’s two-phase state and bounded queues.
- Produces: stopping a not-yet-ready or failed session closes capture, flushes local audio, returns already received final segments, and never leaves a worker/session orphaned.

- [ ] **Step 1: Write failing stop-before-ready regression coverage**

Add a test where provider startup is delayed, audio is queued, stop is called before release, and assert the worker closes and `manager._sessions` no longer contains the sid after cleanup. Add a frontend contract assertion for the startup error copy and stop path:

```python
def test_stop_before_provider_ready_closes_session_without_losing_cleanup():
    release = threading.Event()
    closed = threading.Event()

    class DelayedProvider(FakeProvider):
        def start(self, _config):
            assert release.wait(timeout=1)

        def close(self):
            closed.set()

    manager = SessionManager(provider_factory=lambda _config: DelayedProvider())
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))
    stop_thread = threading.Thread(target=lambda: manager.stop("sid-1"))
    stop_thread.start()
    release.set()
    stop_thread.join(timeout=1)
    assert closed.is_set()
    assert "sid-1" not in manager._sessions
```

- [ ] **Step 2: Run focused cleanup tests to verify they fail**

Run: `PYTHONPATH=. pytest -q tests/test_session_manager.py -k 'stop_before_provider_ready or cleanup'`

Expected: FAIL if the current stop path leaves the delayed worker or reports success after startup failure.

- [ ] **Step 3: Implement cleanup and state guards**

Ensure `stop()` always enqueues the sentinel after setting `stop_event`, joins the worker, returns `state.error` when startup or inference failed, and removes the session. In the frontend, `stopListening()` must work for `capturing` as well as `recording`, flush pending PCM only when a live backend session exists, then await both audio finalization and the backend stop callback. Reset `pendingAudio`, readiness flags, and timers in `completeSession()` and all capture-failure paths.

- [ ] **Step 4: Run focused cleanup tests**

Run: `PYTHONPATH=. pytest -q tests/test_session_manager.py tests/test_socket_events.py tests/test_frontend_contract.py -k 'stop_before_provider_ready or cleanup or captures_while_model'`

Expected: PASS.

- [ ] **Step 5: Run the complete verification suite**

Run:

```bash
PYTHONPATH=. pytest -q
ruff check backend tests
node --check static/app.js
git diff --check
curl -fsS http://127.0.0.1:5001/api/health
```

Expected: all tests and checks pass; health returns `{"service":"real-time-transcript","status":"ok"}`.

- [ ] **Step 6: Commit the completed implementation**

```bash
git add backend static tests docs/API.md README.md README.zh-CN.md
git commit -m "feat: start recording before model readiness"
```

