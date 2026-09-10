import pytest

from backend import create_app, socketio
from backend.config import load_config
from backend.models import TranscriptSegment
from backend.providers.base import ProviderError
from backend.translation import (
    GoogleTranslationProvider,
    MicrosoftTranslationProvider,
    ModelTranslationProvider,
    TranslationRouter,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_google_provider_posts_ordered_contents_without_leaking_key(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, params, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "params": params, "timeout": timeout})
        return FakeResponse(200, {"data": {"translations": [{"translatedText": "你好"}, {"translatedText": "世界"}]}})

    monkeypatch.setattr("requests.post", fake_post)
    provider = GoogleTranslationProvider(
        project_id="",
        api_key="google-secret",
        timeout_seconds=7,
    )

    result = provider.translate_batch(["Hello", "world"], "en", "zh-CN")

    assert result == ["你好", "世界"]
    assert captured["url"] == "https://translation.googleapis.com/language/translate/v2"
    assert captured["params"] == {"key": "google-secret"}
    assert captured["json"] == {
        "q": ["Hello", "world"],
        "source": "en",
        "target": "zh-CN",
        "format": "text",
    }
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert "google-secret" not in str(provider.last_error if hasattr(provider, "last_error") else "")


def test_google_configured_with_api_key_only():
    config = load_config({"TRANSLATION_GOOGLE_API_KEY": "google-secret"})

    assert config.translation_google_configured is True


def test_google_provider_has_best_effort_public_fallback_without_key(monkeypatch):
    captured = []

    def fake_get(url, params, timeout):
        captured.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(200, [[["你好", "Hello", None, None, 1]]])

    monkeypatch.setattr("requests.get", fake_get)
    provider = GoogleTranslationProvider("", "")

    assert provider.translate_batch(["Hello"], "en", "zh") == ["你好"]
    assert captured[0]["url"] == "https://translate.googleapis.com/translate_a/single"
    assert captured[0]["params"] == {"client": "gtx", "sl": "en", "tl": "zh", "dt": "t", "q": "Hello"}


def test_google_public_fallback_uses_http2_curl_after_rate_limit(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(429, {})

    calls = []

    class Completed:
        returncode = 0
        stdout = '[[["你好", "Hello", null, null, 1]]]'
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else None)
    monkeypatch.setattr("subprocess.run", fake_run)

    provider = GoogleTranslationProvider("", "")

    assert provider.translate_batch(["Hello"], "en", "zh") == ["你好"]
    assert calls
    assert "--http2" in calls[0][0]
    assert calls[0][1]["shell"] is False


def test_microsoft_provider_posts_translator_body_in_order(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, params, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "params": params, "timeout": timeout})
        return FakeResponse(200, [{"translations": [{"text": "你好"}]}, {"translations": [{"text": "世界"}]}])

    monkeypatch.setattr("requests.post", fake_post)
    provider = MicrosoftTranslationProvider(
        endpoint="https://translator.example",
        api_key="microsoft-secret",
        region="westeurope",
        timeout_seconds=9,
    )

    result = provider.translate_batch(["Hello", "world"], "en", "zh-Hans")

    assert result == ["你好", "世界"]
    assert captured["json"] == [{"Text": "Hello"}, {"Text": "world"}]
    assert captured["params"] == {"api-version": "3.0", "from": "en", "to": "zh-Hans"}
    assert captured["headers"]["Ocp-Apim-Subscription-Key"] == "microsoft-secret"
    assert captured["headers"]["Ocp-Apim-Subscription-Region"] == "westeurope"


def test_translation_provider_maps_auth_error_without_secret(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return FakeResponse(401, {"error": {"message": "secret should not escape"}})

    monkeypatch.setattr("requests.post", fake_post)
    provider = GoogleTranslationProvider("project-1", "google-secret")

    with pytest.raises(ProviderError) as error:
        provider.translate_batch(["Hello"], "en", "zh")

    assert error.value.code == "TRANSLATION_AUTH_FAILED"
    assert "google-secret" not in str(error.value)
    assert "secret should not escape" not in str(error.value)


def test_translation_config_exposes_provider_flags_only():
    config = load_config({
        "TRANSLATION_GOOGLE_PROJECT_ID": "project-1",
        "TRANSLATION_GOOGLE_API_KEY": "google-secret",
        "TRANSLATION_MICROSOFT_API_KEY": "microsoft-secret",
        "TRANSLATION_MICROSOFT_REGION": "westeurope",
        "TRANSLATION_CLOUD_BASE_URL": "https://llm.example/v1",
        "TRANSLATION_CLOUD_API_KEY": "cloud-secret",
        "TRANSLATION_CLOUD_MODEL": "translate-model",
    })

    public = config.public_dict()
    assert public["translation"]["google"]["configured"] is True
    assert public["translation"]["google"]["public_fallback"] is True
    assert public["translation"]["microsoft"]["configured"] is True
    assert public["translation"]["cloud_model"]["configured"] is True
    assert "secret" not in repr(public)


def test_translation_config_supports_local_openai_compatible_model_without_key():
    config = load_config({
        "TRANSLATION_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
        "TRANSLATION_LOCAL_MODEL": "qwen2.5:3b",
    })

    public = config.public_dict()
    assert config.translation_local_configured is True
    assert public["translation"]["local_model"] == {
        "configured": True,
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:3b",
    }


def test_local_model_provider_uses_openai_compatible_endpoint_without_auth(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse(200, {"choices": [{"message": {"content": '["你好"]'}}]})

    monkeypatch.setattr("requests.post", fake_post)
    provider = ModelTranslationProvider(
        name="local",
        model="qwen2.5:3b",
        base_url="http://127.0.0.1:11434/v1",
    )

    assert provider.translate_batch(["Hello"], "en", "zh") == ["你好"]
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in captured["headers"]


def test_app_wires_configured_local_translation_provider():
    app = create_app({
        "TRANSLATION_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
        "TRANSLATION_LOCAL_MODEL": "qwen2.5:3b",
    })

    provider = app.extensions["translation_router"].providers["local"]
    assert provider is not None
    assert provider.name == "local"
    assert provider.model == "qwen2.5:3b"


def test_router_selects_fast_provider_and_auto_model_fallback():
    fast = object()
    local = object()
    cloud = object()
    router = TranslationRouter(google=fast, microsoft=None, local=local, cloud=cloud)

    selected = router.resolve(mode="fast", provider="google", local_ready=False)
    assert selected.provider is fast
    assert selected.provider_name == "google"
    assert selected.mode == "fast"

    selected = router.resolve(mode="model", provider="auto", local_ready=False)
    assert selected.provider is cloud
    assert selected.provider_name == "cloud"
    assert selected.mode == "model"


def test_model_provider_supports_injected_local_callable_and_preserves_order():
    provider = ModelTranslationProvider(translator=lambda texts, source, target: [text.upper() for text in texts])
    assert provider.translate_batch(["a", "b"], "en", "zh") == ["A", "B"]


def test_missing_provider_is_actionable():
    router = TranslationRouter()
    with pytest.raises(ProviderError, match="TRANSLATION_NOT_CONFIGURED"):
        router.resolve(mode="fast", provider="google", local_ready=False)


class FakeTranslationProvider:
    name = "fake"
    mode = "fast"
    model = None

    def translate_batch(self, texts, _source_language, _target_language):
        return ["译文: " + text for text in texts]


class FakeTranslationRouter:
    def __init__(self):
        self.provider = FakeTranslationProvider()

    def resolve(self, **_kwargs):
        from backend.translation import TranslationSelection

        return TranslationSelection(self.provider, "fake", "fast")


class FakeTranscriptionProvider:
    name = "fake"
    model = "fake-model"

    def start(self, _config):
        return None

    def push(self, _audio):
        return [TranscriptSegment("segment-1", "hello lecture", 0, 1000, True)]

    def flush(self):
        return []

    def close(self):
        return None


def test_batch_translation_endpoint_returns_segment_metadata():
    app = create_app({}, provider_factory=lambda _config: FakeTranscriptionProvider(), translation_router=FakeTranslationRouter())

    response = app.test_client().post(
        "/api/translate",
        json={
            "segments": [{"id": "segment-1", "text": "hello lecture"}],
            "source_language": "en",
            "target_language": "zh",
            "mode": "fast",
            "provider": "fake",
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["translations"][0] == {
        "segment_id": "segment-1",
        "target_language": "zh",
        "text": "译文: hello lecture",
        "status": "ready",
        "mode": "fast",
        "provider": "fake",
        "model": None,
    }


def test_socket_translation_uses_server_segment_text():
    app = create_app({}, provider_factory=lambda _config: FakeTranscriptionProvider(), translation_router=FakeTranslationRouter())
    client = socketio.test_client(app)
    assert client.emit(
        "start_transcription",
        {"mode": "local", "model": "fake", "language": "en", "sample_rate": 16000, "window_seconds": 0.2, "overlap_seconds": 0.01},
        callback=True,
    )["status"] == "starting"

    import base64
    import struct
    import time

    pcm = struct.pack("<" + "h" * 3200, *([0] * 3200))
    client.emit("audio_chunk", {"audio": base64.b64encode(pcm).decode(), "sample_rate": 16000, "sequence": 0})
    time.sleep(0.1)
    result = client.emit(
        "translate_segments",
        {
            "segment_ids": ["segment-1"],
            "source_language": "en",
            "target_language": "zh",
            "mode": "fast",
            "provider": "fake",
        },
        callback=True,
    )

    assert result["status"] == "success"
    assert result["translations"][0]["text"] == "译文: hello lecture"
    client.disconnect()
