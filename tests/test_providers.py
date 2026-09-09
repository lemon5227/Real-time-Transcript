import importlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend.config import load_config
from backend.device import DeviceProfile
from backend.models import SessionConfig
from backend.providers.base import ProviderError
from backend.providers.factory import AutoFallbackProvider, ProviderFactory


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def test_auto_mode_uses_cloud_when_local_is_unavailable():
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


def test_local_provider_does_not_import_heavy_packages_at_core_import_time():
    was_loaded = "torch" in __import__("sys").modules
    importlib.import_module("backend.providers.local_whisper")
    if not was_loaded:
        assert "torch" not in __import__("sys").modules


def test_local_provider_maps_missing_dependency_to_stable_error():
    from backend.providers.local_whisper import LocalWhisperProvider

    provider = LocalWhisperProvider(model_name="small", device="cpu", loader=lambda **_: None)
    with pytest.raises(ProviderError, match="LOCAL_MODEL_UNAVAILABLE"):
        provider.start(SessionConfig("local", "small", "en", 16000))


def test_local_provider_disables_fp16_on_mps_to_avoid_nan_logits(monkeypatch):
    captured = {}

    class FakeModel:
        def transcribe(self, _audio, **kwargs):
            captured.update(kwargs)
            return {"segments": []}

    fake_whisper = SimpleNamespace(
        load_model=lambda _name, device: FakeModel(),
    )
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    from backend.providers.local_whisper import LocalWhisperProvider

    provider = LocalWhisperProvider(model_name="small", device="mps")
    provider.start(SessionConfig("local", "small", "en", 16000))
    provider.push(np.zeros(16000, dtype=np.float32))

    assert captured["fp16"] is False


def test_distil_english_model_uses_faster_whisper_on_mps(monkeypatch):
    captured = {}

    class FakeModel:
        def transcribe(self, _audio, **_kwargs):
            return [], None

    class FakeWhisperModule:
        def __init__(self, model_name, device, compute_type):
            captured.update({"model_name": model_name, "device": device, "compute_type": compute_type})

        def transcribe(self, _audio, **_kwargs):
            return [], None

    fake_module = SimpleNamespace(WhisperModel=FakeWhisperModule)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    from backend.providers.local_whisper import LocalWhisperProvider

    provider = LocalWhisperProvider(model_name="distil-small.en", device="mps")
    provider.start(SessionConfig("local", "distil-small.en", "en", 16000))

    assert captured == {"model_name": "distil-small.en", "device": "cpu", "compute_type": "int8"}


def test_standard_local_provider_uses_cuda_when_available(monkeypatch):
    captured = {}

    class FakeWhisperModel:
        def __init__(self, model_name, device, compute_type):
            captured.update({"model_name": model_name, "device": device, "compute_type": compute_type})

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    from backend.providers.local_whisper import LocalWhisperProvider

    profile = DeviceProfile(device="cuda", kind="nvidia", memory_gb=8.0, performance="fast")
    provider = LocalWhisperProvider(model_name="small", device_profile=profile)
    provider.start(SessionConfig("local", "small", "en", 16000))

    assert captured == {"model_name": "small", "device": "cuda", "compute_type": "float16"}


def test_cloud_provider_posts_audio_without_logging_secret(monkeypatch):
    from backend.providers.cloud_transcription import CloudTranscriptionProvider

    captured = {}

    def fake_post(url, headers, files, data, timeout):
        captured.update({"url": url, "headers": headers, "data": data, "files": files})
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


def test_auto_provider_falls_back_when_local_model_cannot_start():
    class FailingLocal:
        name = "local"
        model = "small"

        def start(self, _config):
            raise ProviderError("LOCAL_MODEL_LOAD_FAILED", "本地模型加载失败")

        def push(self, _audio):
            raise AssertionError("local provider must not receive audio after fallback")

        def flush(self):
            return []

        def close(self):
            self.closed = True

    class WorkingCloud:
        name = "cloud"
        model = "cloud-model"

        def start(self, _config):
            self.started = True

        def push(self, _audio):
            return []

        def flush(self):
            return []

        def close(self):
            self.closed = True

    cloud = WorkingCloud()
    provider = AutoFallbackProvider(FailingLocal(), cloud)
    provider.start(object())
    assert provider.name == "cloud"
    assert provider.model == "cloud-model"
    assert cloud.started is True


def test_mlx_provider_maps_stream_sentences_to_segments():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Sentence:
        text, start, end = "Welcome to economics.", 0.2, 1.8

    class Stream:
        result = type("Result", (), {"text": "Welcome to economics.", "sentences": [Sentence()]})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, audio):
            self.audio = audio

    class Model:
        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert provider.name == "mlx"
    assert provider.requires_contiguous_audio is True
    assert result[0].text == "Welcome to economics."
    assert result[0].is_final is True
    assert result[0].start_ms == 200


def test_mlx_provider_keeps_stream_sentence_timestamps_absolute():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Sentence:
        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end

    class Stream:
        def __init__(self):
            self.calls = 0
            self.result = type(
                "Result",
                (),
                {"text": "First sentence.", "sentences": [Sentence("First sentence.", 0.2, 0.8)]},
            )()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            self.calls += 1
            if self.calls == 2:
                self.result = type(
                    "Result",
                    (),
                    {"text": "Second sentence.", "sentences": [Sentence("Second sentence.", 1.2, 1.8)]},
                )()

    stream = Stream()

    class Model:
        def transcribe_stream(self, **_kwargs):
            return stream

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    provider.push(np.zeros(16000, dtype=np.float32))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert result[0].text == "Second sentence."
    assert result[0].start_ms == 1200


def test_mlx_provider_keeps_parakeet_drafts_out_of_confirmed_history():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Sentence:
        text, start, end = "Welcome to economics.", 0.2, 1.8

    class Token:
        def __init__(self, text):
            self.text = text
            self.start = 0.0
            self.end = 0.5

    class Stream:
        finalized_tokens = []
        draft_tokens = [Token(" Welcome"), Token(" to economics.")]
        result = type("Result", (), {"text": "Welcome to economics.", "sentences": [Sentence()]})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            return None

    class Model:
        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [segment.text for segment in result] == ["Welcome to economics."]
    assert result[0].is_final is False


def test_mlx_provider_emits_a_newly_finalized_sentence_once():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start):
            self.text = text
            self.start = start
            self.end = start + 0.5

    class Stream:
        def __init__(self):
            self.finalized_tokens = []
            self.draft_tokens = [Token(" Welcome", 0.0)]
            self.result = type("Result", (), {"text": "Welcome", "sentences": []})()
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            self.calls += 1
            if self.calls == 2:
                self.finalized_tokens = [
                    Token(" Welcome", 0.0),
                    Token(" to", 0.5),
                    Token(" economics", 1.0),
                    Token(".", 1.5),
                ]
                self.draft_tokens = []
                self.result = type(
                    "Result", (), {"text": "Welcome to economics.", "sentences": []}
                )()

    stream = Stream()

    class Model:
        def transcribe_stream(self, **_kwargs):
            return stream

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    provider.push(np.zeros(16000, dtype=np.float32))
    result = provider.push(np.zeros(16000, dtype=np.float32))
    repeated = provider.push(np.zeros(16000, dtype=np.float32))

    assert [(segment.text, segment.is_final) for segment in result] == [
        ("Welcome to economics.", True)
    ]
    assert repeated == []


def test_mlx_provider_breaks_unpunctuated_final_text_into_readable_chunks():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start):
            self.text = text
            self.start = start
            self.end = start + 0.2

    class Stream:
        def __init__(self):
            self.finalized_tokens = [Token(" word%d" % index, index * 0.2) for index in range(1, 26)]
            self.draft_tokens = []
            self.result = type("Result", (), {"text": "", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            return None

    class Model:
        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert result[0].is_final is True
    assert result[0].text == " ".join("word%d" % index for index in range(1, 25))
    assert result[1].is_final is False
    assert result[1].text == "word25"


def test_mlx_provider_does_not_split_on_internal_domain_periods():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text):
            self.text = text
            self.start = 0.0
            self.end = 0.2

    class Stream:
        finalized_tokens = [
            Token(" .tensorflow"),
            Token(" dot autolog."),
        ]
        draft_tokens = []
        result = type("Result", (), {"text": ".tensorflow dot autolog.", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            return None

    class Model:
        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [segment.text for segment in result] == [".tensorflow dot autolog."]
    assert result[0].is_final is True


def test_mlx_provider_removes_a_stray_period_at_history_boundary():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text):
            self.text = text
            self.start = 0.0
            self.end = 0.2

    class Stream:
        finalized_tokens = [Token(" First."), Token(" .second sentence.")]
        draft_tokens = []
        result = type("Result", (), {"text": "First. .second sentence.", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            return None

    class Model:
        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [segment.text for segment in result] == ["First.", "second sentence."]


def test_mlx_provider_reports_missing_runtime():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: None
    )
    with pytest.raises(ProviderError, match="MLX_RUNTIME_UNAVAILABLE"):
        provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))


def test_mps_factory_selects_mlx_provider():
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    factory = ProviderFactory(
        load_config({}),
        local_available=lambda _model: True,
        device_profile=profile,
    )
    provider = factory.create(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))

    assert provider.name == "mlx"
    assert provider.model == "mlx-community/parakeet-tdt-0.6b-v3"


def test_mps_factory_rejects_standard_whisper_model_instead_of_using_cpu():
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    factory = ProviderFactory(
        load_config({}),
        local_available=lambda _model: True,
        device_profile=profile,
    )

    with pytest.raises(ProviderError, match="LOCAL_RUNTIME_MISMATCH"):
        factory.create(SessionConfig("local", "small", "en", 16000))


def test_mps_auto_mode_replaces_legacy_model_with_mlx_recommendation():
    calls = []
    config = load_config({
        "CLOUD_BASE_URL": "https://example.test/v1",
        "CLOUD_API_KEY": "key",
        "CLOUD_TRANSCRIPTION_MODEL": "transcribe-test",
    })
    profile = DeviceProfile(device="mps", kind="apple", memory_gb=None, performance="balanced")
    factory = ProviderFactory(
        config,
        local_available=lambda model: calls.append(model) or False,
        device_profile=profile,
    )

    provider = factory.create(SessionConfig("auto", "small", "en", 16000))

    assert provider.name == "cloud"
    assert calls == ["parakeet-tdt-0.6b-v3"]


def test_cpu_factory_selects_standard_provider():
    profile = DeviceProfile(device="cpu", kind="cpu", memory_gb=16.0, performance="fast")
    factory = ProviderFactory(
        load_config({}),
        local_available=lambda _model: True,
        device_profile=profile,
    )
    provider = factory.create(SessionConfig("local", "small", "en", 16000))

    assert provider.name == "local"
