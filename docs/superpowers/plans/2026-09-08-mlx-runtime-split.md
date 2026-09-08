# Mac MLX / Standard Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended) to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Use MLX Parakeet streaming on Apple Silicon Macs and keep faster-whisper/Whisper for Windows, Linux, and Intel Macs, with cloud fallback independent.

**Architecture:** Add MlxParakeetProvider beside LocalWhisperProvider under the existing provider protocol. Device profiles expose the actual execution runtime (`mlx`, `cuda`, or `cpu`); the factory selects a compatible model/provider, and the session manager avoids overlapping windows for the stateful MLX stream. The model UI and install docs expose the two local versions.

**Tech Stack:** Python 3.9+, Flask/Flask-SocketIO, NumPy, pytest, optional parakeet-mlx/MLX, existing faster-whisper/Whisper.

## Global Constraints

- Apple Silicon local transcription uses MLX and never silently falls back to the current CPU-only Distil Whisper path.
- Windows, Linux, and Intel Macs keep the standard faster-whisper/Whisper path; CUDA is used automatically when an NVIDIA GPU is available, otherwise CPU is used.
- Cloud is a separate provider and remains available to auto mode.
- Heavy MLX/Whisper packages are lazy imports.
- Parakeet v3 is for English and European-language lectures; on Mac, Chinese or unsupported languages must explicitly use cloud until a second MLX multilingual model is added. Standard Whisper remains the independent CUDA/CPU version for non-Mac platforms.
- MLX input is contiguous mono 16 kHz audio and output uses TranscriptSegment.

---

### Task 1: Runtime-aware device and model catalog

**Files:** Modify backend/device.py, backend/routes.py, backend/model_manager.py, tests/test_device.py, tests/test_routes.py, tests/test_model_manager.py.

**Interfaces:** device_public_dict(profile)["runtime"] returns "mlx", "cuda", or "cpu". Catalog entries expose model-family runtime (`mlx` or `standard`) and model_ref; fixtures that omit runtime default to "standard".

- [x] Write failing tests:

~~~python
def test_mps_device_reports_mlx_runtime():
    profile = DeviceProfile("mps", "apple", None, "balanced")
    assert device_public_dict(profile)["runtime"] == "mlx"


def test_catalog_marks_runtime_families():
    models = create_app({}).test_client().get("/api/models").get_json()["models"]
    by_id = {model["id"]: model for model in models}
    assert by_id["parakeet-tdt-0.6b-v3"]["runtime"] == "mlx"
    assert by_id["small"]["runtime"] == "standard"
~~~

- [x] Run red: python3 -m pytest -q tests/test_device.py::test_mps_device_reports_mlx_runtime tests/test_routes.py::test_catalog_marks_runtime_families
- [x] Implement runtime_for_profile and add the MLX catalog entry with model_ref "mlx-community/parakeet-tdt-0.6b-v3", runtime "mlx", size "~1.2GB", speed "最快", quality "很好", resource "中", languages "英语 / 24 种欧洲语言", best_for "Apple Silicon · 英语课堂". Mark current Whisper entries runtime "standard".
- [x] Run green: python3 -m pytest -q tests/test_device.py tests/test_routes.py tests/test_model_manager.py
- [x] Commit with message "feat: expose platform-specific transcription runtimes".

### Task 2: Apple Silicon MLX provider

**Files:** Create backend/providers/mlx_parakeet.py; modify backend/providers/__init__.py and tests/test_providers.py.

**Interfaces:** MlxParakeetProvider(model_ref, loader=None) implements start, push, flush, close; exposes name "mlx", model model_ref, requires_contiguous_audio True.

- [x] Write failing tests using a fake model whose transcribe_stream returns a context manager with result.sentences containing text/start/end. Assert push returns a final TranscriptSegment with the sentence text and millisecond timestamps. Add a test where loader returns None and assert ProviderError code MLX_RUNTIME_UNAVAILABLE.
- [x] Run red: python3 -m pytest -q tests/test_providers.py -k mlx
- [x] Implement lazy import of parakeet_mlx.from_pretrained, model.transcribe_stream(context_size=(256, 256), keep_original_attention=False), float32 audio input, unseen sentence de-duplication, and a provisional is_final=False segment from changed result.text. Convert stream-absolute timestamps to input-window-relative timestamps before SessionManager adds the window origin. Map import/load failures to an actionable MLX_RUNTIME_UNAVAILABLE error mentioning requirements-mac.txt.
- [x] Run green: python3 -m pytest -q tests/test_providers.py -k mlx
- [x] Commit with message "feat: add streaming Parakeet MLX provider".

### Task 3: Runtime routing and contiguous buffering

**Files:** Modify backend/providers/factory.py, backend/session_manager.py, tests/test_providers.py, tests/test_session_manager.py.

**Interfaces:** ProviderFactory.create returns MlxParakeetProvider for Apple Silicon plus the MLX model, LocalWhisperProvider for standard platforms/models, and LOCAL_RUNTIME_MISMATCH for incompatible pairs. SessionManager uses zero overlap for providers declaring requires_contiguous_audio.

- [x] Write failing tests:

~~~python
def test_mps_factory_selects_mlx_provider():
    profile = DeviceProfile("mps", "apple", None, "balanced")
    factory = ProviderFactory(load_config({}), local_available=lambda _model: True, device_profile=profile)
    assert factory.create(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000)).name == "mlx"


def test_cpu_factory_selects_standard_provider():
    profile = DeviceProfile("cpu", "cpu", 16, "fast")
    factory = ProviderFactory(load_config({}), local_available=lambda _model: True, device_profile=profile)
    assert factory.create(SessionConfig("local", "small", "en", 16000)).name == "local"
~~~

- [x] Run red: python3 -m pytest -q tests/test_providers.py -k "mps_factory or cpu_factory"
- [x] Add _create_local(model_id) to ProviderFactory, select by runtime_for_profile, reject MLX models on standard devices instead of selecting CPU, and reject unsupported Parakeet languages on Mac with an actionable MLX_LANGUAGE_UNSUPPORTED error so auto mode can use cloud. Preserve auto local-first/cloud-fallback. Configure AudioWindowBuffer overlap_seconds=0.0 for MLX providers and configured overlap for Whisper; test two adjacent windows do not overlap.
- [x] Run green: python3 -m pytest -q tests/test_providers.py tests/test_session_manager.py
- [x] Commit with message "feat: route Apple Silicon through MLX runtime".

### Task 4: Two installation versions and model UI

**Files:** Create requirements-mac.txt; modify templates/live-transcript.html, static/app.js, backend/routes.py, README.md, README.macOS.md, tests/test_frontend_contract.py.

**Interfaces:** requirements-mac.txt installs parakeet-mlx separately; capabilities expose local.runtime; the UI labels and disables incompatible runtime choices.

- [x] Write failing contract test:

~~~python
def test_mac_runtime_install_and_ui_contracts():
    assert "parakeet-mlx" in Path("requirements-mac.txt").read_text()
    html = create_app({}).test_client().get("/").get_data(as_text=True)
    javascript = create_app({}).test_client().get("/static/app.js").get_data(as_text=True)
    assert "parakeet-tdt-0.6b-v3" in html
    assert "local.runtime" in javascript
    assert "Mac MLX" in html
~~~

- [x] Run red: python3 -m pytest -q tests/test_frontend_contract.py::test_mac_runtime_install_and_ui_contracts
- [x] Create requirements-mac.txt from requirements-core.txt plus parakeet-mlx. Add the Parakeet option and runtime badge; after capabilities load select Parakeet on Apple Silicon and the device-recommended standard model elsewhere; disable mismatched options. Keep the existing speed/quality/resource summary and document Parakeet's supported languages plus cloud for Chinese on Mac, while standard Whisper remains available on non-Mac platforms.
- [x] Run green: ruff check backend tests && python3 -m pytest -q && node --check static/app.js && python3 -m compileall -q backend app.py && git diff --check
- [x] Commit with message "docs: split Mac MLX and standard installation paths".

## Verification Checklist

- Apple Silicon reports runtime "mlx" and recommends Parakeet.
- CUDA reports runtime "cuda" with acceleration; CPU/Intel reports runtime "cpu" with cloud available in auto mode.
- Missing parakeet_mlx is actionable and does not load MLX at app startup.
- MLX audio is contiguous and non-overlapping; standard Whisper keeps overlap.
- UI exposes runtime, readiness, speed, quality, resource pressure, supported-language limits, and cloud fallback.
