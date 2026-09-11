from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Sequence

from ..translation import map_translation_response_error, validate_translation_batch
from .base import ProviderError

try:
    import requests
except ImportError:  # pragma: no cover - exercised in a core-only install
    requests = None


def _looks_like_html(response) -> bool:
    """True when a supposedly-JSON endpoint answered with a web page."""
    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            content_type = str(headers.get("Content-Type", "")).lower()
        except (AttributeError, TypeError):
            content_type = ""
    if "html" in content_type:
        return True
    try:
        body = response.text if hasattr(response, "text") else ""
    except (AttributeError, TypeError, ValueError):
        return False
    return str(body).lstrip()[:1] == "<"


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

    @property
    def best_effort(self) -> bool:
        """Without a key this is the undocumented web endpoint, not a service.

        It answers with consent/verification HTML often enough that it must never
        outrank a provider the operator actually configured.
        """
        return not self.api_key

    def translate_batch(
        self, texts: Sequence[str], source_language: str, target_language: str
    ) -> List[str]:
        values = validate_translation_batch(texts, source_language, target_language)
        if not self.api_key:
            return self._translate_public(values, source_language, target_language)
        if requests is None:
            raise ProviderError("TRANSLATION_DEPENDENCY_MISSING", "翻译云端依赖不可用")
        http = self._http or requests
        # API keys are supported by Cloud Translation Basic (v2), while the
        # Advanced v3 REST API requires OAuth/service-account credentials.
        url = "https://translation.googleapis.com/language/translate/v2"
        try:
            response = http.post(
                url,
                headers={"Content-Type": "application/json"},
                params={"key": self.api_key},
                json={
                    "q": values,
                    "source": source_language,
                    "target": target_language,
                    "format": "text",
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
            translations = payload["data"]["translations"]
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
                if response.status_code == 429 and self._http is None:
                    translated = self._translate_public_with_curl(
                        value, source_language, target_language
                    )
                    result.append(translated)
                    continue
                if response.status_code >= 400:
                    raise map_translation_response_error(response.status_code)
                if _looks_like_html(response):
                    # Google answers the undocumented endpoint with a 200 and a
                    # consent/verification page once it decides to throttle.
                    # Reporting that as "invalid format" sent people hunting for a
                    # parser bug instead of configuring a real provider.
                    raise ProviderError(
                        "TRANSLATION_PUBLIC_UNAVAILABLE",
                        "Google 公共翻译已不可用（返回验证页）",
                        "配置 Google 官方 API Key，或改用 Microsoft Translator",
                    )
                payload = response.json()
                translated = self._parse_public_translation(payload)
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

    def _translate_public_with_curl(
        self, value: str, source_language: str, target_language: str
    ) -> str:
        curl = shutil.which("curl")
        if not curl:
            raise map_translation_response_error(429)
        command = [
            curl,
            "--silent",
            "--show-error",
            "--http2",
            "--get",
            "--max-time",
            str(max(1, self.timeout_seconds)),
            "https://translate.googleapis.com/translate_a/single",
            "--data-urlencode",
            "client=gtx",
            "--data-urlencode",
            "sl=" + source_language,
            "--data-urlencode",
            "tl=" + target_language,
            "--data-urlencode",
            "dt=t",
            "--data-urlencode",
            "q=" + value,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 1,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("TRANSLATION_TIMEOUT", "Google 公共翻译请求超时", "稍后重试或配置官方 API") from exc
        except OSError as exc:
            raise ProviderError("TRANSLATION_NETWORK_ERROR", "无法连接 Google 公共翻译", "检查网络或配置其他翻译服务") from exc
        if completed.returncode != 0:
            raise map_translation_response_error(429)
        try:
            return self._parse_public_translation(json.loads(completed.stdout))
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("TRANSLATION_INVALID_RESPONSE", "Google 公共翻译返回格式无效") from exc

    @staticmethod
    def _parse_public_translation(payload: object) -> str:
        chunks = payload[0]  # type: ignore[index]
        return "".join(
            str(chunk[0]) for chunk in chunks if isinstance(chunk, list) and chunk
        ).strip()
