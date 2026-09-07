# 留学生听课实时转录工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `Real-time-Transcript` 改造成面向留学生听课与课后复习的本地优先、云端可选实时转录工作台，并彻底移除主仓库中的离线字幕生成器副本。

**Architecture:** 保留 Flask、Flask-SocketIO 和原生浏览器前端，使用应用工厂、领域数据模型、音频管线、会话管理器和可替换的转录 provider 解开现有单文件耦合。浏览器通过 Socket.IO 发送 PCM 音频，通过 IndexedDB 保存会话；后端只在进程内保存活动会话，不引入数据库和账户系统。

**Tech Stack:** Python 3.9+, Flask, Flask-SocketIO, NumPy, requests, faster-whisper（可选本地依赖）, 原生 JavaScript, IndexedDB, AudioWorklet, pytest, Socket.IO test client。

## Global Constraints

- 本阶段不把两个 Git 仓库合并，也不通过 Git submodule 共享运行时代码。
- 本阶段不实现说话人分离、多人声纹识别或课堂录音云端存储。
- 本阶段不强制引入 Electron/Tauri 桌面壳。
- 本阶段不要求云端 provider 使用某一家厂商的私有 SDK；云端接入通过可配置 provider 边界完成。
- 页面不再调用不存在的 API；每个前端请求都有可测试的成功和错误路径。
- 默认绑定 `127.0.0.1`；若用户显式绑定局域网，启动日志提示风险。
- 默认关闭 Flask debug 模式和全开放 CORS。
- 不把音频、API key 或完整字幕写入普通日志。
- provider 不直接操作 Flask、Socket.IO 或 DOM。
- 每个任务都先写失败测试，再写最小实现，再执行定向测试和完整测试。

---

## 文件结构与职责映射

实现时使用 Flask 的标准目录布局，设计文档中的 `frontend/` 概念映射为 `templates/` 和 `static/`：

```text
backend/
  __init__.py              # 后端包入口
  config.py                # 环境变量和公开配置
  models.py                # SessionConfig、TranscriptSegment 等领域类型
  device.py                # 设备探测和模型推荐
  audio_pipeline.py        # 音频校验、重采样、窗口和尾部 flush
  segment_merger.py        # 重叠窗口字幕去重和时间戳合并
  session_manager.py       # sid 对应的活动会话生命周期
  routes.py                # health、capabilities、models、config API
  providers/
    __init__.py
    base.py                # provider 协议、错误类型
    factory.py              # local/cloud/auto 选择
    local_whisper.py       # faster-whisper / openai-whisper 本地实现
    cloud_transcription.py # OpenAI-compatible HTTP 云端实现
tests/
  conftest.py
  test_models.py
  test_config.py
  test_audio_pipeline.py
  test_segment_merger.py
  test_device.py
  test_providers.py
  test_session_manager.py
  test_routes.py
  test_socket_events.py
templates/
  live-transcript.html    # 听课页面
  review.html              # 课后复习页面
static/
  app.js                   # 听课页面控制器
  review.js                # 复习页面控制器
  storage.js               # IndexedDB 会话存储
  export.js                # TXT/Markdown/VTT/SRT 生成
  audio-worklet.js         # PCM 音频采集处理器
  styles.css               # 统一视觉系统和响应式布局
requirements-core.txt
requirements-local.txt
requirements-cloud.txt
requirements-dev.txt
.env.example
pytest.ini
README.md
```

保留根目录 `app.py` 作为启动兼容入口，但它只负责调用 `backend.create_app()` 和 `socketio.run()`；模型、音频和事件逻辑不得回到 `app.py`。

## Task 1: 移除离线项目副本并建立测试/依赖边界

**Files:**
- Delete: `Auto-Subtitle-Generator-Standalone/`
- Delete: `app.html`, `back_index.html`, `index.html`, `index01.html`, `index02.html`
- Delete: `web.py`, `web02.py`, `web03.py`, `demo01.py`, `demo02.py`, `demo03.py`
- Delete: `start.py`, `start_server.py`, `start_smart.py`, `download_models.sh`, `install-amd.sh`, `install-linux.sh`, `install-wsl2.sh`
- Delete: `test_model_api.py`, `test_gpu_detection.py`, `test_model_selection.html`, `test_qwen.py`, `test_qwen_refinement.py`, `test_translation_speed.py`, `test_translation_speed2.py`, `test_turbo.py`
- Delete: `README.qwen.md`, `README.qwen3.md`, `MODEL_MANAGEMENT.md`, `MODEL_MANAGEMENT_UPDATE.md`, `QWEN3_UPDATE.md`, `QWEN_IMPLEMENTATION.md`, `QWEN_QUICKSTART.md`, `TRANSLATION_FIX.md`, `TRANSLATION_GUIDE.md`, `TRANSLATION_UPDATE.md`
- Create: `requirements-core.txt`
- Create: `requirements-local.txt`
- Create: `requirements-cloud.txt`
- Create: `requirements-dev.txt`
- Create: `.env.example`
- Create: `pytest.ini`
- Create: `start.sh`
- Create: `tests/conftest.py`
- Create: `tests/test_repository_surface.py`
- Modify: `requirements.txt`
- Modify: `README.md`
- Modify: `start_realtime.sh`

**Interfaces:**
- Produces `requirements-core.txt` with Flask、Flask-SocketIO、NumPy、python-dotenv。
- Produces `requirements-local.txt` with faster-whisper 和可选 openai-whisper；不把本地模型依赖放入 core。
- Produces `requirements-cloud.txt` with requests；云端模式不导入 torch。
- Produces `pytest.ini` with `testpaths = tests` and `addopts = -q`。

- [ ] **Step 1: Write the failing repository-boundary test**

```python
# tests/test_repository_surface.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_standalone_generator_is_not_part_of_realtime_repository():
    assert not (ROOT / "Auto-Subtitle-Generator-Standalone").exists()


def test_runtime_requirements_are_split_by_execution_mode():
    assert (ROOT / "requirements-core.txt").exists()
    assert (ROOT / "requirements-local.txt").exists()
    assert (ROOT / "requirements-cloud.txt").exists()


def test_pytest_only_collects_isolated_tests():
    pytest_config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in pytest_config
```

- [ ] **Step 2: Run the boundary test and verify it fails**

Run: `python3 -m pytest tests/test_repository_surface.py -v`
Expected: FAIL because the copied standalone directory exists and the split requirement files do not exist.

- [ ] **Step 3: Remove the copied project and add mode-specific dependencies**

```bash
git rm -r Auto-Subtitle-Generator-Standalone
```

```text
# requirements-core.txt
Flask>=2.3,<4
Flask-Cors>=4,<6
Flask-SocketIO>=5.3,<6
python-engineio>=4.8,<5
python-socketio>=5.10,<6
numpy>=1.24,<3
python-dotenv>=1.0,<2
```

```text
# requirements-local.txt
-r requirements-core.txt
faster-whisper>=1.0,<2
openai-whisper>=20231117
```

```text
# requirements-cloud.txt
-r requirements-core.txt
requests>=2.31,<3
```

```text
# requirements-dev.txt
-r requirements-core.txt
pytest>=8,<9
pytest-cov>=5,<7
ruff>=0.6,<1
```

`requirements.txt` 改为只包含 `-r requirements-core.txt`，并在 README 中明确说明：云端安装使用 `pip install -r requirements-cloud.txt`，本地安装使用 `pip install -r requirements-local.txt`，开发环境使用 `pip install -r requirements-dev.txt`。

`.env.example` 固定包含以下变量及默认值：

```dotenv
HOST=127.0.0.1
PORT=5001
DEBUG=false
CORS_ORIGINS=http://127.0.0.1:5001,http://localhost:5001
TRANSCRIPTION_MODE=auto
LOCAL_MODEL=small
CLOUD_BASE_URL=
CLOUD_API_KEY=
CLOUD_TRANSCRIPTION_MODEL=
CLOUD_TIMEOUT_SECONDS=30
AUDIO_MAX_QUEUE=32
AUDIO_WINDOW_SECONDS=3.0
AUDIO_OVERLAP_SECONDS=0.5
```

- [ ] **Step 4: Configure the test runner and local-only test fixtures**

```ini
# pytest.ini
[pytest]
testpaths = tests
addopts = -q
```

```python
# tests/conftest.py
import pytest


@pytest.fixture
def fake_transcript_text():
    return "Today we will discuss supervised learning."
```

- [ ] **Step 5: Run the test and verify the repository boundary passes**

Run: `python3 -m pytest tests/test_repository_surface.py -v`
Expected: PASS without importing torch, whisper, faster-whisper, transformers or FunASR.

- [ ] **Step 6: Update the launch script and README references**

`start.sh` must accept `--mode auto|local|cloud`, export only `TRANSCRIPTION_MODE`, and execute `python3 app.py`:

```bash
#!/usr/bin/env bash
set -euo pipefail

MODE="auto"
if [[ "${1:-}" == "--mode" && -n "${2:-}" ]]; then
  MODE="$2"
fi

case "$MODE" in
  auto|local|cloud) export TRANSCRIPTION_MODE="$MODE" ;;
  *) echo "Usage: ./start.sh --mode auto|local|cloud" >&2; exit 2 ;;
esac

exec python3 app.py
```

`start_realtime.sh` becomes a compatibility wrapper that executes `./start.sh "$@"`. Neither script may reference `Auto-Subtitle-Generator-Standalone`, `app.html` or an offline subtitle workflow. README must describe the real-time product, the three install modes, local/cloud privacy behavior and the `/review` page.

- [ ] **Step 7: Commit the decoupling boundary**

```bash
git add -A
git commit -m "refactor: decouple realtime transcript runtime"
```

## Task 2: 建立领域类型、配置和设备能力模型

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/models.py`
- Create: `backend/config.py`
- Create: `backend/device.py`
- Delete: `gpu_detector.py`
- Create: `tests/test_models.py`
- Create: `tests/test_config.py`
- Create: `tests/test_device.py`

**Interfaces:**
- Produces `SessionConfig` with `mode`, `model`, `language`, `sample_rate`, `enable_vad`, `window_seconds`, `overlap_seconds`。
- Produces `TranscriptSegment` with `id`, `text`, `start_ms`, `end_ms`, `is_final`, `confidence`。
- Produces `DeviceProfile` with `device`, `kind`, `memory_gb`, `performance`。
- Produces `load_config(environ: Mapping[str, str] | None = None) -> AppConfig`。

- [ ] **Step 1: Write failing tests for stable domain types**

```python
# tests/test_models.py
from backend.models import SessionConfig, TranscriptSegment


def test_session_config_rejects_unsupported_sample_rate():
    try:
        SessionConfig(mode="local", model="small", language="en", sample_rate=123)
    except ValueError as exc:
        assert "sample_rate" in str(exc)
    else:
        raise AssertionError("invalid sample rate was accepted")


def test_transcript_segment_serializes_without_provider_objects():
    segment = TranscriptSegment(
        id="segment-1",
        text="Hello",
        start_ms=100,
        end_ms=900,
        is_final=True,
        confidence=0.91,
    )
    assert segment.to_dict() == {
        "id": "segment-1",
        "text": "Hello",
        "start_ms": 100,
        "end_ms": 900,
        "is_final": True,
        "confidence": 0.91,
    }
```

- [ ] **Step 2: Run the model tests and verify they fail**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.models'`.

- [ ] **Step 3: Implement the validated dataclasses**

`backend/models.py` must validate `mode in {"auto", "local", "cloud"}`, `sample_rate in {8000, 16000, 22050, 32000, 44100, 48000}`, positive window values, non-negative timestamps, and `end_ms >= start_ms`. Use `dataclasses.dataclass(frozen=True)` and `typing.Optional` so the module works on Python 3.9.

- [ ] **Step 4: Add configuration tests for safe defaults and public redaction**

```python
# tests/test_config.py
from backend.config import load_config


def test_config_defaults_to_localhost_and_auto_mode():
    config = load_config({})
    assert config.host == "127.0.0.1"
    assert config.port == 5001
    assert config.debug is False
    assert config.transcription_mode == "auto"


def test_public_config_never_contains_api_key():
    config = load_config({"CLOUD_API_KEY": "secret-value"})
    public = config.public_dict()
    assert "secret-value" not in repr(public)
    assert public["cloud"]["configured"] is True
```

- [ ] **Step 5: Implement `AppConfig` and environment parsing**

`load_config()` must parse booleans, integers and floats explicitly; reject an invalid port, negative queue size, invalid mode or empty cloud key paired with a non-empty cloud base URL. `public_dict()` may expose provider name, configured boolean, model names and timeout, but never the raw API key.

- [ ] **Step 6: Add deterministic device tests**

```python
# tests/test_device.py
from backend.device import DeviceProfile, recommend_local_model


def test_cpu_low_memory_recommends_tiny_or_base():
    profile = DeviceProfile(device="cpu", kind="cpu", memory_gb=4.0, performance="limited")
    assert recommend_local_model(profile) in {"tiny", "base"}


def test_fast_cuda_recommends_large_family():
    profile = DeviceProfile(device="cuda", kind="nvidia", memory_gb=12.0, performance="fast")
    assert recommend_local_model(profile) in {"medium", "large-v3-turbo"}
```

- [ ] **Step 7: Implement one-time device probing**

`get_device_profile()` must import torch lazily, return CPU when torch is unavailable, test CUDA/MPS only when the corresponding backend reports available, never write process-wide GPU environment overrides, and keep the result cached with `functools.lru_cache(maxsize=1)`. GPU names and memory must come from actual runtime values; no model-name-based hardcoded VRAM values.

- [ ] **Step 8: Run the domain/config/device tests**

Run: `python3 -m pytest tests/test_models.py tests/test_config.py tests/test_device.py -v`
Expected: PASS on a machine without torch; CPU fallback tests must still run.

- [ ] **Step 9: Commit the domain layer**

```bash
git add backend tests/test_models.py tests/test_config.py tests/test_device.py
git commit -m "feat: add runtime config and device capabilities"
```

## Task 3: 实现音频校验、重采样、窗口和字幕合并

**Files:**
- Create: `backend/audio_pipeline.py`
- Create: `backend/segment_merger.py`
- Create: `tests/test_audio_pipeline.py`
- Create: `tests/test_segment_merger.py`

**Interfaces:**
- Produces `decode_pcm16_base64(encoded: str, sample_rate: int, max_bytes: int) -> np.ndarray`。
- Produces `resample_audio(audio: np.ndarray, src_rate: int, target_rate: int = 16000) -> np.ndarray`。
- Produces `AudioWindow(start_ms: int, audio: np.ndarray, sequence: int)`。
- Produces `AudioWindowBuffer.append(audio, sample_rate) -> list[AudioWindow]` and `.flush() -> list[AudioWindow]`。
- Produces `SegmentMerger.add(segments, window_start_ms) -> list[TranscriptSegment]`。

- [ ] **Step 1: Write failing tests for browser sample rates and invalid payloads**

```python
# tests/test_audio_pipeline.py
import base64
import numpy as np
import pytest

from backend.audio_pipeline import decode_pcm16_base64, resample_audio


def test_48khz_pcm_is_resampled_to_16khz():
    source = np.zeros(4800, dtype=np.int16).tobytes()
    decoded = decode_pcm16_base64(base64.b64encode(source).decode(), 48000, 20000)
    assert decoded.dtype == np.float32
    assert decoded.shape == (1600,)


def test_invalid_sample_rate_is_rejected():
    with pytest.raises(ValueError, match="sample_rate"):
        decode_pcm16_base64("AA==", 123, 20000)
```

- [ ] **Step 2: Run audio tests and verify they fail**

Run: `python3 -m pytest tests/test_audio_pipeline.py -v`
Expected: FAIL because `backend.audio_pipeline` does not exist.

- [ ] **Step 3: Implement safe PCM decoding and linear resampling**

Validate base64 with strict decoding, reject payloads over `max_bytes`, reject odd PCM byte lengths, accept only sample rates from `SessionConfig`, decode little-endian PCM16 to `[-1, 1]` float32, convert to mono, and resample to exactly 16kHz. An empty payload returns an empty float32 array only when the caller explicitly passes an empty frame; malformed base64 always raises `ValueError`.

- [ ] **Step 4: Add window-buffer tests**

```python
# tests/test_audio_pipeline.py
from backend.audio_pipeline import AudioWindowBuffer


def test_window_buffer_emits_overlap_windows_and_flushes_tail():
    buffer = AudioWindowBuffer(window_seconds=1.0, overlap_seconds=0.25)
    first = buffer.append(np.zeros(12000, dtype=np.float32), 16000)
    tail = buffer.flush()
    assert first[0].start_ms == 0
    assert first[0].audio.shape[0] == 16000
    assert tail
    assert tail[-1].sequence > first[0].sequence
```

- [ ] **Step 5: Implement bounded windows and tail flush**

Use a bounded deque of float32 samples. Emit a window once it reaches `window_seconds * 16000`; retain exactly `overlap_seconds * 16000` samples for the next window; never retain more than one window plus overlap; `flush()` emits all remaining speech samples above 200ms and then clears the buffer. Sequence numbers increase monotonically per session.

- [ ] **Step 6: Add duplicate/overlap merger tests**

```python
# tests/test_segment_merger.py
from backend.models import TranscriptSegment
from backend.segment_merger import SegmentMerger


def test_merger_deduplicates_repeated_overlap_text():
    merger = SegmentMerger()
    first = [TranscriptSegment("1", "supervised learning", 0, 1200, True, None)]
    second = [TranscriptSegment("2", "supervised learning", 900, 2100, True, None)]
    assert [s.text for s in merger.add(first, 0)] == ["supervised learning"]
    assert merger.add(second, 900) == []
```

- [ ] **Step 7: Implement text normalization and timestamp merge**

Normalize whitespace and punctuation only for comparison, preserve the provider’s user-facing text, deduplicate identical or near-identical overlap segments when their normalized text matches and the timestamp overlap is at least 30%, and return final segments sorted by `start_ms`.

- [ ] **Step 8: Run the audio and merger tests**

Run: `python3 -m pytest tests/test_audio_pipeline.py tests/test_segment_merger.py -v`
Expected: PASS with no external model or microphone.

- [ ] **Step 9: Commit the audio pipeline**

```bash
git add backend/audio_pipeline.py backend/segment_merger.py tests/test_audio_pipeline.py tests/test_segment_merger.py
git commit -m "feat: normalize streaming audio and merge segments"
```

## Task 4: 建立本地/云端转录 provider 和自动选择

**Files:**
- Create: `backend/providers/__init__.py`
- Create: `backend/providers/base.py`
- Create: `backend/providers/factory.py`
- Create: `backend/providers/local_whisper.py`
- Create: `backend/providers/cloud_transcription.py`
- Create: `tests/test_providers.py`

**Interfaces:**
- Produces `ProviderError(code: str, message: str, action: str | None)`。
- Produces `TranscriptionProvider.start(config) -> None`。
- Produces `TranscriptionProvider.push(audio: np.ndarray) -> List[TranscriptSegment]`。
- Produces `TranscriptionProvider.flush() -> List[TranscriptSegment]`。
- Produces `TranscriptionProvider.close() -> None`。
- Produces `ProviderFactory.create(config) -> TranscriptionProvider`。

- [ ] **Step 1: Write provider contract tests with fake dependencies**

```python
# tests/test_providers.py
import numpy as np
import pytest

from backend.config import load_config
from backend.models import SessionConfig
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderFactory


def test_auto_mode_uses_cloud_when_local_is_unavailable(monkeypatch):
    config = load_config({
        "CLOUD_BASE_URL": "https://example.test/v1",
        "CLOUD_API_KEY": "key",
        "CLOUD_TRANSCRIPTION_MODEL": "transcribe-test",
    })
    factory = ProviderFactory(config, local_available=lambda _: False)
    provider = factory.create(SessionConfig("auto", None, "en", 48000))
    assert provider.name == "cloud"


def test_cloud_mode_without_key_has_actionable_error():
    factory = ProviderFactory(load_config({}))
    with pytest.raises(ProviderError, match="CLOUD_NOT_CONFIGURED"):
        factory.create(SessionConfig("cloud", None, "en", 48000))
```

- [ ] **Step 2: Run provider tests and verify they fail**

Run: `python3 -m pytest tests/test_providers.py -v`
Expected: FAIL because the provider package and factory do not exist.

- [ ] **Step 3: Implement the provider protocol and stable errors**

`base.py` defines the protocol and error class. Provider errors expose `to_dict()` returning `code`, `message` and `action`; no traceback or API key is included. The protocol accepts normalized float32 16kHz mono audio only.

- [ ] **Step 4: Add local provider availability tests**

```python
def test_local_provider_does_not_import_heavy_packages_at_core_import_time():
    import sys
    import importlib
    was_loaded = "torch" in sys.modules
    importlib.import_module("backend.providers.local_whisper")
    if not was_loaded:
        assert "torch" not in sys.modules


def test_local_provider_maps_missing_dependency_to_stable_error(monkeypatch):
    from backend.providers.local_whisper import LocalWhisperProvider
    provider = LocalWhisperProvider(model_name="small", device="cpu", loader=lambda **_: None)
    with pytest.raises(ProviderError, match="LOCAL_MODEL_UNAVAILABLE"):
        provider.start(SessionConfig("local", "small", "en", 16000))
```

- [ ] **Step 5: Implement `LocalWhisperProvider` with lazy imports**

Try `faster_whisper.WhisperModel` first when installed; use `openai_whisper` only when explicitly selected by configuration. Choose `cuda`, `mps` or `cpu` from `DeviceProfile`, use `float16` only on CUDA, `int8` on CPU when faster-whisper supports it, and map model loading/inference errors to `ProviderError`. `push()` must return segment timestamps relative to the current window; it must not emit through Socket.IO.

- [ ] **Step 6: Add mocked cloud request tests**

```python
def test_cloud_provider_posts_audio_without_logging_secret(monkeypatch):
    from backend.providers.cloud_transcription import CloudTranscriptionProvider

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            return None

    captured = {}

    def fake_post(url, headers, files, data, timeout):
        captured.update({"url": url, "headers": headers, "data": data})
        return FakeResponse({"text": "Cloud result"})

    monkeypatch.setattr("requests.post", fake_post)
    provider = CloudTranscriptionProvider(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="transcribe-test",
        timeout_seconds=5,
    )
    provider.start(SessionConfig("cloud", None, "en", 16000))
    segments = provider.push(np.zeros(16000, dtype=np.float32))
    assert segments[0].text == "Cloud result"
    assert captured["headers"]["Authorization"] == "Bearer secret-value"
```

`FakeResponse` 提供 `json()`、`raise_for_status()` 和 `status_code`，因此该测试不产生真实网络请求。

- [ ] **Step 7: Implement the cloud provider as chunked HTTP transcription**

Encode each normalized audio window into an in-memory WAV using `wave` and `io.BytesIO`, call `{base_url}/audio/transcriptions` with multipart field `file` and form fields `model`, `language` and `response_format=json`, use the Authorization header only in the request, and apply the configured timeout. Map 401/403 to `CLOUD_AUTH_FAILED`, 429 to `CLOUD_RATE_LIMITED`, timeouts to `CLOUD_TIMEOUT`, connection errors to `CLOUD_NETWORK_ERROR`, and non-JSON responses to `CLOUD_INVALID_RESPONSE`.

- [ ] **Step 8: Implement provider factory and auto policy**

`ProviderFactory.create()` must choose explicit local/cloud modes exactly; in auto mode choose local when the selected local model is available, otherwise choose cloud when configured, otherwise raise `NO_TRANSCRIPTION_PROVIDER` with an action that names both install/configuration options. Factory output exposes `name` and `model` for capability and session acknowledgements.

- [ ] **Step 9: Run provider tests without installing model packages**

Run: `python3 -m pytest tests/test_providers.py -v`
Expected: PASS with mocked local loader and mocked requests; importing `backend` in cloud mode must not import torch.

- [ ] **Step 10: Commit the provider layer**

```bash
git add backend/providers tests/test_providers.py
git commit -m "feat: add local cloud and auto transcription providers"
```

## Task 5: 接入会话管理、Socket.IO ack 和 REST capability API

**Files:**
- Modify: `app.py`
- Create: `backend/session_manager.py`
- Create: `backend/routes.py`
- Modify: `backend/__init__.py`
- Create: `tests/test_session_manager.py`
- Create: `tests/test_routes.py`
- Create: `tests/test_socket_events.py`

**Interfaces:**
- Produces `SessionManager.start(sid, config) -> dict`。
- Produces `SessionManager.push_audio(sid, encoded_audio, sample_rate, sequence) -> None`。
- Produces `SessionManager.stop(sid) -> dict`。
- Produces `SessionManager.cleanup(sid) -> None`。
- Produces `create_app(config=None, provider_factory=None) -> Flask`。
- Produces global `socketio = SocketIO()` initialized by `create_app()`。

- [ ] **Step 1: Write failing session lifecycle tests**

```python
# tests/test_session_manager.py
import numpy as np
import pytest

from backend.models import SessionConfig, TranscriptSegment
from backend.session_manager import SessionManager


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def start(self, config):
        self.started = True

    def push(self, audio):
        return [TranscriptSegment("1", "lecture text", 0, 1000, True, 0.9)]

    def flush(self):
        return []

    def close(self):
        self.closed = True


def test_second_start_for_same_sid_is_rejected():
    manager = SessionManager(provider_factory=lambda _: FakeProvider())
    config = SessionConfig("local", "fake", "en", 16000)
    manager.start("sid-1", config)
    with pytest.raises(ValueError, match="SESSION_ALREADY_ACTIVE"):
        manager.start("sid-1", config)


def test_stop_is_idempotent_and_closes_provider():
    provider = FakeProvider()
    manager = SessionManager(provider_factory=lambda _: provider)
    manager.start("sid-1", SessionConfig("local", "fake", "en", 16000))
    manager.stop("sid-1")
    manager.stop("sid-1")
    assert provider.closed is True
```

- [ ] **Step 2: Run session tests and verify they fail**

Run: `python3 -m pytest tests/test_session_manager.py -v`
Expected: FAIL because `backend.session_manager` does not exist.

- [ ] **Step 3: Implement session state and bounded worker lifecycle**

For each `sid`, store a lock, bounded `queue.Queue(maxsize=config.max_queue)`, provider, audio window buffer, merger, worker thread, stop event, last sequence and counters. `start()` rejects an active session, starts provider and worker, and returns `session_id`, provider and model. `push_audio()` rejects unknown sid, validates monotonically increasing sequence, decodes/resamples frames and drops the newest frame with a structured warning when the queue is full. `stop()` sets the event, enqueues a sentinel, joins for the configured timeout, flushes provider and merger, closes provider and removes state. All cleanup paths are idempotent.

- [ ] **Step 4: Write route and Socket.IO contract tests**

```python
# tests/test_routes.py
from backend import create_app


def test_capabilities_redact_cloud_api_key(monkeypatch):
    app = create_app({"CLOUD_API_KEY": "secret-value"})
    response = app.test_client().get("/api/capabilities")
    assert response.status_code == 200
    body = response.get_json()
    assert body["cloud"]["configured"] is True
    assert "secret-value" not in response.get_data(as_text=True)


def test_health_endpoint_is_available():
    app = create_app({})
    assert app.test_client().get("/api/health").get_json()["status"] == "ok"
```

```python
# tests/test_socket_events.py
from backend.models import TranscriptSegment


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def start(self, _config):
        return None

    def push(self, _audio):
        return [TranscriptSegment("1", "lecture text", 0, 1000, True, 0.9)]

    def flush(self):
        return []

    def close(self):
        return None


def test_socket_start_and_stop_return_ack():
    from backend import create_app, socketio
    from backend.session_manager import SessionManager

    fake_provider_factory = lambda _config: FakeProvider()
    app = create_app({}, provider_factory=fake_provider_factory)
    client = socketio.test_client(app)
    start = client.emit(
        "start_transcription",
        {"mode": "local", "model": "fake", "language": "en", "sample_rate": 48000},
        callback=True,
    )
    assert start["status"] == "success"
    stop = client.emit("stop_transcription", {}, callback=True)
    assert stop["status"] == "success"
```

- [ ] **Step 5: Run API/socket tests and verify they fail**

Run: `python3 -m pytest tests/test_routes.py tests/test_socket_events.py -v`
Expected: FAIL because the application factory and real event acknowledgements do not exist.

- [ ] **Step 6: Implement application factory and REST routes**

`create_app()` creates Flask with `templates/` and `static/`, loads `AppConfig`, configures `CORS` from `CORS_ORIGINS`, registers routes and binds Socket.IO handlers. Implement only `/api/health`, `/api/capabilities`, `/api/models`, `/api/config/public`, `/`, and `/review`. `/api/models` returns local model metadata and whether each model is available; it must not load a model.

- [ ] **Step 7: Implement Socket.IO event handlers with ack responses**

`connect` returns `{"status": "success"}`. `start_transcription` parses and validates `SessionConfig`, calls `SessionManager.start`, returns its dictionary as the Socket.IO ack, and emits `transcription_started`. `audio_chunk` calls `push_audio` and returns `{"status": "accepted"}`. `stop_transcription` calls `stop`, emits each final segment, emits `transcription_stopped`, and returns `{"status": "success", ...}`. `disconnect` calls `cleanup`; no handler uses bare `except` or emits directly from provider code.

- [ ] **Step 8: Replace root startup code with the application factory**

`app.py` must contain only:

```python
from backend import create_app, socketio


app = create_app()


if __name__ == "__main__":
    socketio.run(app, host=app.config["APP_HOST"], port=app.config["APP_PORT"], debug=False)
```

- [ ] **Step 9: Run the backend test suite**

Run: `python3 -m pytest tests/test_models.py tests/test_config.py tests/test_device.py tests/test_audio_pipeline.py tests/test_segment_merger.py tests/test_providers.py tests/test_session_manager.py tests/test_routes.py tests/test_socket_events.py -v`
Expected: PASS without a real model, microphone or cloud request.

- [ ] **Step 10: Commit the backend integration**

```bash
git add app.py backend tests
git commit -m "feat: wire realtime sessions and socket contracts"
```

## Task 6: 重做听课页面和浏览器音频采集

**Files:**
- Delete: `realtime.html`
- Create: `templates/live-transcript.html`
- Create: `static/app.js`
- Create: `static/audio-worklet.js`
- Create: `static/styles.css`
- Create: `tests/test_frontend_contract.py`

**Interfaces:**
- Browser sends `start_transcription`, `audio_chunk`, `stop_transcription` using the backend contract from Task 5。
- Browser consumes `capabilities`, `transcription_started`, `transcript_segment`, `transcription_error`, `transcription_stopped`。
- `static/app.js` exposes `window.startLecture()`, `window.stopLecture()` only for progressive enhancement; controls use event listeners。

- [ ] **Step 1: Write the failing static contract test**

```python
# tests/test_frontend_contract.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_live_template_has_primary_controls_and_review_link():
    html = (ROOT / "templates/live-transcript.html").read_text(encoding="utf-8")
    for marker in ["startButton", "languageSelect", "modeSelect", "subtitleStream", "review.html"]:
        assert marker in html


def test_frontend_does_not_reference_removed_endpoints():
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    for removed in ["/api/qwen_models", "/api/refine_subtitle", "/api/batch_refine_subtitles"]:
        assert removed not in source
```

- [ ] **Step 2: Run the frontend contract test and verify it fails**

Run: `python3 -m pytest tests/test_frontend_contract.py -v`
Expected: FAIL because the new template and controller do not exist.

- [ ] **Step 3: Build the accessible lecture layout**

Create a semantic template with a header, a compact state bar, a left control panel, a main live-caption region, a current-sentence region, a history list, a “回到最新” control, an inline notification region with `aria-live="polite"`, and a link to `/review`. Use local SVG symbols or inline SVG, no Emoji-only controls, no CDN Tailwind, no Google Fonts and no inline `onclick` handlers.

- [ ] **Step 4: Implement the visual system in local CSS**

Define CSS variables for navy background, panel, border, primary accent, warning, error, success, text levels, spacing, radius, shadow and focus ring. Use a desktop two-column layout with a fixed readable caption width, a one-column layout below 840px, minimum 44px controls, `:focus-visible` styles, `@media (prefers-reduced-motion: reduce)`, and explicit empty/loading/error states. Keep current captions larger and brighter than history captions; do not animate text itself.

- [ ] **Step 5: Implement capability-aware controls**

On load, fetch `/api/capabilities`, render the actual device profile, local model availability and cloud configured state, select `auto` by default, and show a privacy note when cloud mode is selected. Disable only impossible choices; if neither provider is ready, show an actionable setup panel without blocking review-page navigation.

- [ ] **Step 6: Implement AudioWorklet with fallback and sample-rate declaration**

`audio-worklet.js` must copy input samples into transferable Float32 buffers. `app.js` creates `AudioContext`, reads `audioContext.sampleRate`, sends `sample_rate` on start and every `audio_chunk`, converts samples to PCM16 base64, increments `sequence`, and uses a small bounded client queue. If `AudioWorklet` is unavailable, use `ScriptProcessorNode` with the same payload contract and visibly show the fallback only in the diagnostics state.

- [ ] **Step 7: Implement Socket.IO state management and caption rendering**

Use one socket instance, one active session flag and one cleanup path. Render a provisional segment in the current-sentence area, move final segments into the history list, update latency and session duration, pause auto-follow when the user scrolls upward, and show the “回到最新” button until the user returns to the bottom. Use `textContent` for caption text and a dedicated `showNotice(kind, message, action)` for feedback.

- [ ] **Step 8: Run static and backend tests**

Run: `python3 -m pytest tests/test_frontend_contract.py tests/test_routes.py tests/test_socket_events.py -v`
Expected: PASS.

- [ ] **Step 9: Manually verify desktop and mobile states**

Run: `python3 app.py`, open `http://127.0.0.1:5001/`, and verify: fresh empty state, capability loading, no cloud configuration, local unavailable, microphone permission denial, start, duplicate start click, stop, connection error, pause-follow, return-to-latest, keyboard navigation and viewport widths 390px, 840px and 1440px. Record no screenshots or audio in the repository.

- [ ] **Step 10: Commit the lecture UI**

```bash
git add templates static tests/test_frontend_contract.py
git rm realtime.html app.html
git commit -m "feat: redesign lecture transcription workspace"
```

## Task 7: 实现课后复习、IndexedDB、编辑和导出

**Files:**
- Create: `templates/review.html`
- Create: `static/storage.js`
- Create: `static/review.js`
- Create: `static/export.js`
- Create: `tests/test_export_contract.py`

**Interfaces:**
- `storage.js` provides `openSessionStore()`, `saveSession(session)`, `listSessions()`, `getSession(id)`, `deleteSession(id)`。
- `export.js` provides `formatVtt(segments)`, `formatSrt(segments)`, `formatMarkdown(session)`, `formatPlainText(session)`。
- Review records use `id`, `title`, `createdAt`, `durationMs`, `language`, `provider` and `segments[{id,text,startMs,endMs,note,starred}]`。

- [ ] **Step 1: Write failing export tests**

```python
# tests/test_export_contract.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_export_module_contains_real_timestamp_formatters():
    source = (ROOT / "static/export.js").read_text(encoding="utf-8")
    for name in ["formatVtt", "formatSrt", "formatMarkdown", "formatPlainText"]:
        assert f"function {name}" in source
    assert "toISOString" not in source


def test_review_page_has_search_notes_and_star_controls():
    html = (ROOT / "templates/review.html").read_text(encoding="utf-8")
    for marker in ["sessionList", "searchInput", "noteInput", "starred"]:
        assert marker in html
```

- [ ] **Step 2: Run the review contract test and verify it fails**

Run: `python3 -m pytest tests/test_export_contract.py -v`
Expected: FAIL because the review page and export module do not exist.

- [ ] **Step 3: Implement IndexedDB storage with a versioned schema**

Use database name `realtime-transcript`, object store `sessions`, key path `id`, and schema version `1`. Resolve open errors into an inline notice; never throw an unhandled promise that stops live transcription. Save after every final segment with a debounce of 500ms, save edits immediately, and keep sessions available after page reload.

- [ ] **Step 4: Implement exact subtitle timestamp formatters**

Format milliseconds directly into `HH:MM:SS.mmm`; use `.` for VTT and `,` for SRT; preserve segment `startMs` and `endMs`; never use current wall-clock time or a fixed five-second duration. Markdown output must include title, date, provider, language, timestamps, text, starred marker and notes.

- [ ] **Step 5: Build the review interaction**

Render session list ordered by `createdAt` descending, search text and notes case-insensitively, show a session summary, allow inline editing, add/remove notes, toggle starred state, filter all/starred/notes, and offer download buttons for TXT/Markdown/VTT/SRT. Empty sessions and deleted sessions must have dedicated empty states.

- [ ] **Step 6: Connect live completion to review storage**

When `transcription_stopped` arrives, update duration and provider metadata, save the final session, show a “已保存，可进入课后复习” action and keep the live page usable for a new session. Starting a new session creates a new id and never overwrites the previous session.

- [ ] **Step 7: Run the review contract and backend tests**

Run: `python3 -m pytest tests/test_export_contract.py tests/test_routes.py tests/test_socket_events.py -v`
Expected: PASS.

- [ ] **Step 8: Manually verify review behavior**

Create a session with fake captions, reload the browser, search a keyword, edit text, add a note, star a segment, filter starred and notes, delete a session, and download all four formats. Open the downloaded files and verify timestamp order and millisecond separators.

- [ ] **Step 9: Commit the review workflow**

```bash
git add templates/review.html static/storage.js static/review.js static/export.js tests/test_export_contract.py
git commit -m "feat: add local transcript review workspace"
```

## Task 8: 完成文档、安装路径和最终验证

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `README.en.md`
- Modify: `README.amd.md`, `README.macOS.md`, `README.linux.md`, `README.wsl2.md`
- Modify: `QUICKSTART.md`
- Modify: `TROUBLESHOOTING.md`
- Modify: `start_realtime.sh`
- Create: `docs/API.md`
- Create: `docs/PRIVACY.md`
- Create: `tests/test_documentation_contract.py`

**Interfaces:**
- README documents only the real-time project and links to the separate offline repository without importing it。
- `docs/API.md` documents exact REST and Socket.IO payloads from Task 5。
- `docs/PRIVACY.md` explains local/cloud audio flow, API key storage and default binding。

- [ ] **Step 1: Write failing documentation contract tests**

```python
# tests/test_documentation_contract.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_local_cloud_and_review_workflows():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in ["local mode", "cloud mode", "post-class review", "/api/capabilities", "/review"]:
        assert phrase in readme


def test_api_docs_match_the_runtime_endpoint_names():
    docs = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for endpoint in ["/api/health", "/api/capabilities", "/api/models", "start_transcription", "audio_chunk"]:
        assert endpoint in docs
```

- [ ] **Step 2: Run documentation tests and verify they fail**

Run: `python3 -m pytest tests/test_documentation_contract.py -v`
Expected: FAIL because the new docs do not exist and the README does not describe the new workflows.

- [ ] **Step 3: Rewrite the active documentation**

README must show the product promise, 3 install commands, local/cloud selection table, privacy notice, supported languages, start commands, review workflow, troubleshooting links and exact cloud environment variables. Remove claims for Qwen, Helsinki-NLP, offline video processing and model management unless they exist in the runtime after this plan.

- [ ] **Step 4: Document API and privacy behavior**

`docs/API.md` must include request/response JSON for health, capabilities, models, start, audio and stop. `docs/PRIVACY.md` must state: local mode keeps audio on the machine; cloud mode sends audio windows to the configured endpoint; API keys remain in backend environment variables; logs exclude audio, keys and full captions; default host is localhost.

- [ ] **Step 5: Run formatting and full verification**

Run:

```bash
python3 -m pytest -q
python3 -m compileall app.py backend
ruff check backend tests
git diff --check
git grep -n "Auto-Subtitle-Generator-Standalone" -- ':!docs/superpowers/specs/**' ':!docs/superpowers/plans/**'
```

Expected: pytest, compileall, ruff and diff checks pass; the final `git grep` returns only the documented link to the separate repository, if such a link is retained.

- [ ] **Step 6: Run the no-heavy-dependency smoke check**

In a clean Python environment with only `requirements-core.txt` installed, run:

```bash
python3 -c "from backend import create_app; app = create_app({}); print(app.test_client().get('/api/health').get_json())"
```

Expected: `{'status': 'ok', ...}` and no import error for torch, whisper, transformers, FunASR or faster-whisper.

- [ ] **Step 7: Execute browser acceptance**

With the local environment configured, verify desktop and mobile flows from Tasks 6 and 7, plus local provider with a fake provider, cloud provider with mocked HTTP, and actual local transcription only when a model is intentionally installed. Do not put credentials, model caches or recordings into Git.

- [ ] **Step 8: Commit the final documentation and verification changes**

```bash
git add README.md README.zh-CN.md README.en.md TROUBLESHOOTING.md start_realtime.sh docs tests
git commit -m "docs: document hybrid lecture transcription workflows"
```

## Plan Self-Review

### Spec coverage

- 解耦和删除副本：Task 1。
- 本地/云端/自动 provider：Task 4。
- 设备探测和低配推荐：Task 2。
- 采样率、重采样、窗口、VAD 入口、队列和尾部 flush：Task 3 与 Task 5。
- Socket.IO ack、错误映射和会话清理：Task 5。
- 易用性、视觉质量、无障碍和响应式：Task 6。
- IndexedDB、搜索、编辑、重点、笔记和导出：Task 7。
- 依赖分层、隐私、API 和最终验证：Task 1 与 Task 8。
- 说话人分离、桌面壳、数据库和云端录音存储明确保持在范围之外。

### Consistency checks

- `SessionConfig`、`TranscriptSegment`、`TranscriptionProvider` 和 `ProviderFactory` 在前置任务中定义，后续任务使用相同字段和方法名。
- 音频统一在 `audio_pipeline.py` 转为 16kHz float32 mono，provider 不处理浏览器采样率。
- provider 不接触 Socket.IO；`SessionManager` 负责 provider、merger 和事件之间的连接。
- capability 只返回配置状态，API key 只在云端 provider 内部使用。
- 前端只使用 Task 5 定义的 `/api/*` API 和 Socket.IO 事件，不再调用旧 Qwen/翻译接口。
- 导出使用字幕 segment 的真实时间戳，不使用墙上时钟或固定时长。

### Completeness scan

Every task has concrete files, interfaces, executable test commands and commit commands；没有未定义的 endpoint、函数名或前置任务依赖。
