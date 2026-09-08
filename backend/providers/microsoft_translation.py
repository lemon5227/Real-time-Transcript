from __future__ import annotations

from typing import List, Sequence
from urllib.parse import urljoin

from ..translation import map_translation_response_error, validate_translation_batch
from .base import ProviderError

try:
    import requests
except ImportError:  # pragma: no cover - exercised in a core-only install
    requests = None


class MicrosoftTranslationProvider:
    name = "microsoft"
    mode = "fast"
    model = None

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        region: str = "",
        timeout_seconds: float = 20,
        http_client=None,
    ):
        self.endpoint = endpoint.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.region = region.strip()
        self.timeout_seconds = timeout_seconds
        self._http = http_client

    def translate_batch(
        self, texts: Sequence[str], source_language: str, target_language: str
    ) -> List[str]:
        values = validate_translation_batch(texts, source_language, target_language)
        if not self.endpoint or not self.api_key:
            raise ProviderError(
                "TRANSLATION_NOT_CONFIGURED",
                "Microsoft 翻译尚未配置",
                "设置 TRANSLATION_MICROSOFT_ENDPOINT 和 TRANSLATION_MICROSOFT_API_KEY",
            )
        if requests is None:
            raise ProviderError("TRANSLATION_DEPENDENCY_MISSING", "翻译云端依赖不可用")
        http = self._http or requests
        try:
            response = http.post(
                urljoin(self.endpoint + "/", "translate"),
                headers={
                    "Ocp-Apim-Subscription-Key": self.api_key,
                    **({"Ocp-Apim-Subscription-Region": self.region} if self.region else {}),
                    "Content-Type": "application/json",
                },
                params={"api-version": "3.0", "from": source_language, "to": target_language},
                json=[{"Text": value} for value in values],
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderError("TRANSLATION_TIMEOUT", "Microsoft 翻译请求超时", "稍后重试或切换 Google") from exc
        except requests.ConnectionError as exc:
            raise ProviderError("TRANSLATION_NETWORK_ERROR", "无法连接 Microsoft 翻译", "检查网络或切换 Google") from exc
        except requests.RequestException as exc:
            raise ProviderError("TRANSLATION_REQUEST_FAILED", "Microsoft 翻译请求失败") from exc
        if response.status_code >= 400:
            raise map_translation_response_error(response.status_code)
        try:
            payload = response.json()
            result = [str(item["translations"][0]["text"]).strip() for item in payload]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Microsoft 翻译返回格式无效") from exc
        if len(result) != len(values) or any(not item for item in result):
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Microsoft 翻译返回的数量不匹配")
        return result
