from __future__ import annotations

from typing import List, Sequence
from urllib.parse import quote

from ..translation import map_translation_response_error, validate_translation_batch
from .base import ProviderError

try:
    import requests
except ImportError:  # pragma: no cover - exercised in a core-only install
    requests = None


class GoogleTranslationProvider:
    name = "google"
    mode = "fast"
    model = None
    public_fallback = True

    def __init__(
        self,
        project_id: str,
        api_key: str,
        location: str = "global",
        timeout_seconds: float = 20,
        http_client=None,
    ):
        self.project_id = project_id.strip()
        self.api_key = api_key.strip()
        self.location = location.strip() or "global"
        self.timeout_seconds = timeout_seconds
        self._http = http_client

    def translate_batch(
        self, texts: Sequence[str], source_language: str, target_language: str
    ) -> List[str]:
        values = validate_translation_batch(texts, source_language, target_language)
        if not self.project_id or not self.api_key:
            return self._translate_public(values, source_language, target_language)
        if requests is None:
            raise ProviderError("TRANSLATION_DEPENDENCY_MISSING", "翻译云端依赖不可用")
        http = self._http or requests
        url = (
            "https://translation.googleapis.com/v3/projects/"
            + quote(self.project_id, safe="")
            + "/locations/"
            + quote(self.location, safe="")
            + ":translateText"
        )
        try:
            response = http.post(
                url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "sourceLanguageCode": source_language,
                    "targetLanguageCode": target_language,
                    "contents": values,
                    "mimeType": "text/plain",
                },
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderError("TRANSLATION_TIMEOUT", "Google 翻译请求超时", "稍后重试或切换 Microsoft") from exc
        except requests.ConnectionError as exc:
            raise ProviderError("TRANSLATION_NETWORK_ERROR", "无法连接 Google 翻译", "检查网络或切换 Microsoft") from exc
        except requests.RequestException as exc:
            raise ProviderError("TRANSLATION_REQUEST_FAILED", "Google 翻译请求失败") from exc
        if response.status_code >= 400:
            raise map_translation_response_error(response.status_code)
        try:
            payload = response.json()
            translations = payload["translations"]
            result = [str(item["translatedText"]).strip() for item in translations]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Google 翻译返回格式无效") from exc
        if len(result) != len(values) or any(not item for item in result):
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Google 翻译返回的数量不匹配")
        return result

    def _translate_public(
        self, values: Sequence[str], source_language: str, target_language: str
    ) -> List[str]:
        """Use Google's undocumented web endpoint as a no-key best-effort path."""
        if requests is None:
            raise ProviderError("TRANSLATION_DEPENDENCY_MISSING", "翻译云端依赖不可用")
        http = self._http or requests
        result = []
        try:
            for value in values:
                response = http.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": source_language,
                        "tl": target_language,
                        "dt": "t",
                        "q": value,
                    },
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    raise map_translation_response_error(response.status_code)
                payload = response.json()
                chunks = payload[0]
                translated = "".join(
                    str(chunk[0]) for chunk in chunks if isinstance(chunk, list) and chunk
                ).strip()
                if not translated:
                    raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Google 公共翻译返回空结果")
                result.append(translated)
        except ProviderError:
            raise
        except requests.Timeout as exc:
            raise ProviderError("TRANSLATION_TIMEOUT", "Google 公共翻译请求超时", "稍后重试或配置官方 API") from exc
        except requests.ConnectionError as exc:
            raise ProviderError("TRANSLATION_NETWORK_ERROR", "无法连接 Google 公共翻译", "检查网络或配置其他翻译服务") from exc
        except requests.RequestException as exc:
            raise ProviderError("TRANSLATION_REQUEST_FAILED", "Google 公共翻译请求失败") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Google 公共翻译返回格式无效") from exc
        return result
