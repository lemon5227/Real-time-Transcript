import pytest

from backend.config import load_config
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

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse(200, {"translations": [{"translatedText": "你好"}, {"translatedText": "世界"}]})

    monkeypatch.setattr("requests.post", fake_post)
    provider = GoogleTranslationProvider(
        project_id="project-1",
        api_key="google-secret",
        timeout_seconds=7,
    )

    result = provider.translate_batch(["Hello", "world"], "en", "zh-CN")

    assert result == ["你好", "世界"]
    assert captured["json"]["contents"] == ["Hello", "world"]
    assert captured["json"]["sourceLanguageCode"] == "en"
    assert captured["json"]["targetLanguageCode"] == "zh-CN"
    assert captured["headers"]["x-goog-api-key"] == "google-secret"
    assert "google-secret" not in str(provider.last_error if hasattr(provider, "last_error") else "")


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
    assert public["translation"]["microsoft"]["configured"] is True
    assert public["translation"]["cloud_model"]["configured"] is True
    assert "secret" not in repr(public)


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
