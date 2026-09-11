"""Provider-neutral translation contracts and routing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

from .providers.base import ProviderError

MAX_TRANSLATION_ITEMS = 25
MAX_TRANSLATION_CHARS = 12_000


class TranslationProvider(Protocol):
    name: str
    mode: str
    model: Optional[str]

    def translate_batch(
        self,
        texts: Sequence[str],
        source_language: str,
        target_language: str,
    ) -> List[str]:
        ...


def validate_translation_batch(
    texts: Sequence[str], source_language: str, target_language: str
) -> List[str]:
    if not source_language or len(source_language) > 32:
        raise ProviderError("TRANSLATION_INVALID_LANGUAGE", "源语言代码无效")
    if not target_language or len(target_language) > 32:
        raise ProviderError("TRANSLATION_INVALID_LANGUAGE", "目标语言代码无效")
    values = [str(text).strip() for text in texts]
    if not values or any(not value for value in values):
        raise ProviderError("TRANSLATION_INVALID_TEXT", "翻译文本不能为空")
    if len(values) > MAX_TRANSLATION_ITEMS:
        raise ProviderError("TRANSLATION_BATCH_TOO_LARGE", "翻译批次超过段落数量限制")
    if sum(len(value) for value in values) > MAX_TRANSLATION_CHARS:
        raise ProviderError("TRANSLATION_BATCH_TOO_LARGE", "翻译批次超过字符数限制")
    return values


def map_translation_response_error(status_code: int) -> ProviderError:
    if status_code in {401, 403}:
        return ProviderError(
            "TRANSLATION_AUTH_FAILED", "翻译服务鉴权失败", "检查翻译服务凭据和区域配置"
        )
    if status_code == 429:
        return ProviderError(
            "TRANSLATION_RATE_LIMITED", "翻译服务限流", "稍后重试或切换另一个翻译服务"
        )
    if status_code >= 500:
        return ProviderError(
            "TRANSLATION_PROVIDER_UNAVAILABLE", "翻译服务暂时不可用", "稍后重试"
        )
    return ProviderError("TRANSLATION_REQUEST_FAILED", "翻译服务返回错误")


def parse_model_translation_content(content: object, expected: int) -> List[str]:
    if isinstance(content, list):
        values = content
    else:
        raw = str(content or "").strip()
        try:
            values = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "模型返回的翻译不是有效 JSON") from exc
    if not isinstance(values, list) or len(values) != expected:
        raise ProviderError("TRANSLATION_INVALID_RESPONSE", "模型返回的翻译数量不匹配")
    result = [str(value).strip() for value in values]
    if any(not value for value in result):
        raise ProviderError("TRANSLATION_INVALID_RESPONSE", "模型返回了空译文")
    return result


class ModelTranslationProvider:
    """A callable or OpenAI-compatible local/cloud translation adapter.

    A local runtime can be injected so importing the web app never imports a
    heavy translation model. Local servers such as Ollama or LM Studio can
    also be used through their OpenAI-compatible endpoint without an API key.
    """

    mode = "model"

    def __init__(
        self,
        translator: Optional[Callable[[Sequence[str], str, str], Sequence[str]]] = None,
        *,
        name: str = "local",
        model: Optional[str] = None,
        base_url: str = "",
        api_key: str = "",
        timeout_seconds: float = 30,
        http_client=None,
    ):
        self.name = name
        self.model = model
        self.translator = translator
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._http = http_client

    def translate_batch(self, texts, source_language: str, target_language: str) -> List[str]:
        values = validate_translation_batch(texts, source_language, target_language)
        if self.translator is not None:
            try:
                result = list(self.translator(values, source_language, target_language))
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError("TRANSLATION_MODEL_FAILED", "翻译模型执行失败") from exc
            if len(result) != len(values) or any(not str(item).strip() for item in result):
                raise ProviderError("TRANSLATION_INVALID_RESPONSE", "翻译模型返回的数量不匹配")
            return [str(item).strip() for item in result]

        requires_api_key = self.name != "local"
        if not self.base_url or not self.model or (requires_api_key and not self.api_key):
            raise ProviderError(
                "TRANSLATION_NOT_CONFIGURED",
                "本地翻译模型尚未配置" if self.name == "local" else "云端翻译模型尚未配置",
                "启动本地 OpenAI-compatible 服务，或配置云端翻译模型",
            )
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - core-only install
            raise ProviderError("TRANSLATION_DEPENDENCY_MISSING", "翻译云端依赖不可用") from exc

        http = self._http or requests
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Translate each item into the target language. "
                        "Return only a JSON array with the same number and order of items."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_language": source_language,
                            "target_language": target_language,
                            "texts": values,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        label = "本地模型翻译" if self.name == "local" else "云端模型翻译"
        try:
            response = http.post(
                self.base_url + "/chat/completions",
                headers=headers,
                json=body,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderError("TRANSLATION_TIMEOUT", label + "超时", "稍后重试或切换另一种模型") from exc
        except requests.ConnectionError as exc:
            raise ProviderError("TRANSLATION_NETWORK_ERROR", "无法连接" + label, "检查模型服务是否已启动或切换另一种模型") from exc
        except requests.RequestException as exc:
            raise ProviderError("TRANSLATION_REQUEST_FAILED", label + "请求失败") from exc
        if response.status_code >= 400:
            raise map_translation_response_error(response.status_code)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "云端模型返回格式无效") from exc
        return parse_model_translation_content(content, len(values))


@dataclass(frozen=True)
class TranslationSelection:
    provider: Optional[TranslationProvider]
    provider_name: str
    mode: str
    model: Optional[str] = None


class TranslationRouter:
    def __init__(
        self,
        *,
        google: Optional[TranslationProvider] = None,
        microsoft: Optional[TranslationProvider] = None,
        local: Optional[TranslationProvider] = None,
        cloud: Optional[TranslationProvider] = None,
    ):
        self.providers = {
            "google": google,
            "microsoft": microsoft,
            "local": local,
            "cloud": cloud,
        }

    def resolve(self, mode: str, provider: str = "auto", local_ready: bool = False) -> TranslationSelection:
        selections = self.resolve_all(mode, provider, local_ready)
        if not selections:
            if str(mode or "off").strip().lower() == "off":
                return TranslationSelection(None, "none", "off")
            raise ProviderError(
                "TRANSLATION_NOT_CONFIGURED",
                "所选翻译服务尚未配置",
                "检查翻译设置，或关闭实时翻译",
            )
        return selections[0]

    def resolve_all(
        self, mode: str, provider: str = "auto", local_ready: bool = False
    ) -> List[TranslationSelection]:
        """Every usable provider, best first.

        Returning the whole chain lets the caller fall through when the preferred
        provider is momentarily failing, which is what "auto" promises.
        """
        normalized_mode = str(mode or "off").strip().lower()
        if normalized_mode == "off":
            return []

        normalized_provider = str(provider or "auto").strip().lower()
        if normalized_mode == "fast":
            if normalized_provider != "auto":
                names = [normalized_provider]
            else:
                names = self._preference_order(["google", "microsoft"])
            resolved_mode = "fast"
        elif normalized_mode in {"model", "precise", "auto"}:
            if normalized_provider == "auto":
                names = ["local", "cloud"] if local_ready else ["cloud", "local"]
            else:
                names = [normalized_provider]
            resolved_mode = "model"
        else:
            raise ProviderError("TRANSLATION_INVALID_MODE", "实时翻译模式无效")

        return [
            TranslationSelection(provider_object, name, resolved_mode, getattr(provider_object, "model", None))
            for name in names
            for provider_object in [self.providers.get(name)]
            if provider_object is not None
        ]

    def _preference_order(self, names: List[str]) -> List[str]:
        """Push best-effort providers behind configured ones.

        The keyless Google web endpoint is a convenience, not a service: it can
        start returning consent/verification pages at any time, and it must never
        shadow a provider the operator actually configured a key for.
        """
        return sorted(
            names,
            key=lambda name: (
                bool(getattr(self.providers.get(name), "best_effort", False)),
                names.index(name),
            ),
        )


def translate_with_fallback(
    selections: Sequence[TranslationSelection],
    texts: Sequence[str],
    source_language: str,
    target_language: str,
) -> Tuple[List[str], TranslationSelection]:
    """Translate with the first provider that works, falling through on failure."""
    if not selections:
        raise ProviderError(
            "TRANSLATION_NOT_CONFIGURED",
            "所选翻译服务尚未配置",
            "检查翻译设置，或关闭实时翻译",
        )
    failures: List[ProviderError] = []
    for selection in selections:
        if selection.provider is None:
            continue
        try:
            return (
                selection.provider.translate_batch(texts, source_language, target_language),
                selection,
            )
        except ProviderError as exc:
            failures.append(exc)
    raise failures[0] if failures else ProviderError(
        "TRANSLATION_NOT_CONFIGURED", "所选翻译服务尚未配置"
    )


def __getattr__(name):
    """Lazily expose concrete providers without creating an import cycle."""
    if name == "GoogleTranslationProvider":
        from .providers.google_translation import GoogleTranslationProvider

        return GoogleTranslationProvider
    if name == "MicrosoftTranslationProvider":
        from .providers.microsoft_translation import MicrosoftTranslationProvider

        return MicrosoftTranslationProvider
    raise AttributeError(name)

__all__ = [
    "ModelTranslationProvider",
    "TranslationProvider",
    "TranslationRouter",
    "TranslationSelection",
    "translate_with_fallback",
    "validate_translation_batch",
]
