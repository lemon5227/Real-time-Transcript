import base64
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


def test_local_provider_drops_windows_the_model_calls_no_speech(monkeypatch):
    """Whisper invents fluent sentences on silence; those are not captions."""

    class FakeSegment:
        def __init__(self, text, no_speech_prob):
            self.text = text
            self.no_speech_prob = no_speech_prob
            self.start = 0.0
            self.end = 1.0

    class FakeWhisperModel:
        def __init__(self, _name, device, compute_type):
            pass

        def transcribe(self, _audio, **_kwargs):
            return [
                FakeSegment("Thank you for watching", 0.97),
                FakeSegment("today we cover gradient descent", 0.02),
            ], None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel)
    )
    from backend.providers.local_whisper import LocalWhisperProvider

    provider = LocalWhisperProvider(model_name="small", device="cpu")
    provider.start(SessionConfig("local", "small", "en", 16000))
    segments = provider.push(np.full(16000, 0.2, dtype=np.float32))

    assert [segment.text for segment in segments] == ["today we cover gradient descent"]


def test_cloud_provider_sends_the_course_vocabulary(monkeypatch):
    """Cloud mode has to hint the endpoint, not only correct spelling later."""
    from backend.providers.cloud_transcription import CloudTranscriptionProvider

    sent = {}

    def fake_post(url, headers, files, data, timeout):
        sent.update(data)
        return FakeResponse({"text": "Cloud result"})

    monkeypatch.setattr("requests.post", fake_post)
    provider = CloudTranscriptionProvider(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="transcribe-test",
        timeout_seconds=5,
    )
    provider.start(SessionConfig("cloud", None, "en", 16000, glossary=("Backpropagation",)))
    provider.push(np.full(16000, 0.2, dtype=np.float32))

    assert "Backpropagation" in sent.get("prompt", "")


def test_cloud_provider_omits_the_prompt_when_no_vocabulary_is_set(monkeypatch):
    from backend.providers.cloud_transcription import CloudTranscriptionProvider

    sent = {}

    def fake_post(url, headers, files, data, timeout):
        sent.update(data)
        return FakeResponse({"text": "Cloud result"})

    monkeypatch.setattr("requests.post", fake_post)
    provider = CloudTranscriptionProvider(
        base_url="https://example.test/v1",
        api_key="secret-value",
        model="transcribe-test",
        timeout_seconds=5,
    )
    provider.start(SessionConfig("cloud", None, "en", 16000))
    provider.push(np.full(16000, 0.2, dtype=np.float32))

    assert "prompt" not in sent


def test_cloud_provider_is_not_called_for_silent_windows(monkeypatch):
    """A quiet room must not cost a request.

    The windowed provider is skipped before inference, so a lecture full of
    pauses only pays for the parts that carry speech.
    """
    from backend.providers.cloud_transcription import CloudTranscriptionProvider
    from backend.session_manager import SessionManager

    calls = []

    def fake_post(url, headers, files, data, timeout):
        calls.append(url)
        return FakeResponse({"text": "Cloud result"})

    monkeypatch.setattr("requests.post", fake_post)

    def build(_config):
        return CloudTranscriptionProvider(
            base_url="https://example.test/v1",
            api_key="secret-value",
            model="transcribe-test",
            timeout_seconds=5,
        )

    manager = SessionManager(provider_factory=build)
    config = SessionConfig(
        "cloud", None, "en", 16000, window_seconds=1.0, overlap_seconds=0.0, enable_vad=True
    )
    manager.start("sid-1", config)
    # One second of room tone (8/32768, about -72 dBFS) then one second of speech.
    manager.push_audio(
        "sid-1", base64.b64encode(b"\x08\x00" * 16000).decode(), 16000, sequence=1, offset_ms=0
    )
    manager.push_audio(
        "sid-1", base64.b64encode(b"\x40\x1f" * 16000).decode(), 16000, sequence=2, offset_ms=1000
    )
    manager.stop("sid-1")

    assert len(calls) == 1


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


def test_mlx_provider_sends_the_configured_right_context_to_the_stream():
    """The right context is the whole confirmation delay: right * 0.08s."""
    from backend.providers.mlx_parakeet import (
        PARAKEET_LEFT_CONTEXT,
        MlxParakeetProvider,
    )

    captured = {}

    class Stream:
        finalized_tokens = []
        draft_tokens = []
        result = type("Result", (), {"text": "", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            return None

    class Model:
        def transcribe_stream(self, **kwargs):
            captured.update(kwargs)
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3",
        loader=lambda **_: Model(),
        right_context=8,
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))

    assert captured["context_size"] == (PARAKEET_LEFT_CONTEXT, 8)
    assert provider.confirmation_lag_seconds() == pytest.approx(0.64)


def test_mlx_provider_defaults_to_a_usable_right_context():
    from backend.providers.mlx_parakeet import (
        PARAKEET_RIGHT_CONTEXT_DEFAULT,
        MlxParakeetProvider,
    )

    provider = MlxParakeetProvider("mlx-community/parakeet-tdt-0.6b-v3")

    assert PARAKEET_RIGHT_CONTEXT_DEFAULT == 32
    assert provider.confirmation_lag_seconds() == pytest.approx(2.56)


@pytest.mark.parametrize(
    "given,expected",
    [(0, 1), (-5, 1), (9999, 256), ("not-an-int", 32), (None, 32)],
)
def test_mlx_provider_clamps_right_context(given, expected):
    """A bad env var must degrade to something usable, not crash the session."""
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    provider = MlxParakeetProvider("mlx-community/parakeet-tdt-0.6b-v3", right_context=given)

    assert provider._right_context == expected


def test_factory_passes_the_configured_right_context_to_the_mlx_provider():
    """The env var only helps if it survives the whole construction chain."""
    from backend.device import DeviceProfile
    from backend.providers.mlx_parakeet import PARAKEET_MODEL_ID

    config = load_config({"MLX_STREAM_RIGHT_CONTEXT": "8"})
    factory = ProviderFactory(
        config,
        local_available=lambda _model: True,
        device_profile=DeviceProfile("mps", "apple", None, "balanced"),
    )

    provider = factory.create(SessionConfig("local", PARAKEET_MODEL_ID, "en", 16000))

    assert provider._right_context == 8


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


def test_mlx_provider_maps_rolling_token_times_to_recording_time():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end
            self.duration = end - start

    class Stream:
        def __init__(self):
            self.calls = 0
            self.finalized_tokens = []
            self.draft_tokens = []
            self.result = type("Result", (), {"text": "", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            self.calls += 1
            frames = 101 if self.calls == 1 else 130
            self.mel_buffer = SimpleNamespace(shape=(1, frames))
            if self.calls == 2:
                self.finalized_tokens = [Token(" first.", 0.16, 0.32)]

    stream = Stream()

    class Model:
        preprocessor_config = SimpleNamespace(sample_rate=16000, hop_length=160)

        def transcribe_stream(self, **_kwargs):
            return stream

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    provider.push(np.zeros(16000, dtype=np.float32))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [(item.start_ms, item.end_ms) for item in result] == [(860, 1020)]


def test_mlx_provider_preserves_gap_between_absolute_token_groups():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end
            self.duration = end - start

    class Stream:
        def __init__(self):
            self.calls = 0
            self.finalized_tokens = []
            self.draft_tokens = []
            self.result = type("Result", (), {"text": "", "sentences": []})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def add_audio(self, _audio):
            self.calls += 1
            frames = {1: 101, 2: 130, 3: 140}[self.calls]
            self.mel_buffer = SimpleNamespace(shape=(1, frames))
            if self.calls == 2:
                self.finalized_tokens = [Token(" first.", 0.16, 0.32)]
            elif self.calls == 3:
                self.finalized_tokens = [
                    Token(" first.", 0.16, 0.32),
                    Token(" second.", 0.8, 0.96),
                ]

    stream = Stream()

    class Model:
        preprocessor_config = SimpleNamespace(sample_rate=16000, hop_length=160)

        def transcribe_stream(self, **_kwargs):
            return stream

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))
    provider.push(np.zeros(16000, dtype=np.float32))
    provider.push(np.zeros(16000, dtype=np.float32))
    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [(item.start_ms, item.end_ms) for item in result] == [(2400, 2560)]


def test_mlx_stable_stream_does_not_build_unused_full_result():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        text = " hello."
        start = 0.2
        end = 0.4
        duration = 0.2

    class Stream:
        finalized_tokens = [Token()]
        draft_tokens = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @property
        def result(self):
            raise AssertionError("stable stream should not build result")

        def add_audio(self, _audio):
            self.mel_buffer = SimpleNamespace(shape=(1, 130))

    class Model:
        preprocessor_config = SimpleNamespace(sample_rate=16000, hop_length=160)

        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))

    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert result[0].text == "hello."


def test_mlx_provider_splits_unpunctuated_run_on_a_natural_pause():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end
            self.duration = end - start

    class Stream:
        finalized_tokens = [
            Token(" first thought", 0.2, 0.4),
            Token(" second thought", 1.0, 1.2),
        ]
        draft_tokens = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @property
        def result(self):
            return type("Result", (), {"text": "", "sentences": []})()

        def add_audio(self, _audio):
            self.mel_buffer = SimpleNamespace(shape=(1, 130))

    class Model:
        preprocessor_config = SimpleNamespace(sample_rate=16000, hop_length=160)

        def transcribe_stream(self, **_kwargs):
            return Stream()

    provider = MlxParakeetProvider(
        "mlx-community/parakeet-tdt-0.6b-v3", loader=lambda **_: Model()
    )
    provider.start(SessionConfig("local", "parakeet-tdt-0.6b-v3", "en", 16000))

    result = provider.push(np.zeros(16000, dtype=np.float32))

    assert [(item.text, item.is_final) for item in result] == [
        ("first thought", True),
        ("second thought", False),
    ]


def test_mlx_provider_breaks_a_very_long_unpunctuated_run():
    from backend.providers.mlx_parakeet import MlxParakeetProvider

    class Token:
        def __init__(self, text, start):
            self.text = text
            self.start = start
            self.end = start + 0.2

    class Stream:
        def __init__(self):
            self.finalized_tokens = [
                Token(" word%d" % index, (index - 1) * 0.45)
                for index in range(1, 26)
            ]
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
    assert result[0].text == " ".join("word%d" % index for index in range(1, 21))
    assert result[1].is_final is False
    assert result[1].text == " ".join(
        "word%d" % index for index in range(21, 26)
    )


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
