import importlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend.config import load_config
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
