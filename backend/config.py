import os
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from .models import SUPPORTED_MODES


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("%s must be a boolean" % name)


def _parse_int(value: str, name: str, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be an integer" % name) from exc
    if parsed < minimum:
        raise ValueError("%s must be at least %d" % (name, minimum))
    return parsed


def _parse_float(value: str, name: str, minimum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be a number" % name) from exc
    if parsed < minimum:
        raise ValueError("%s must be at least %s" % (name, minimum))
    return parsed


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    debug: bool
    cors_origins: Tuple[str, ...]
    transcription_mode: str
    local_model: str
    cloud_base_url: str
    cloud_api_key: str
    cloud_transcription_model: str
    cloud_timeout_seconds: float
    audio_max_queue: int
    audio_window_seconds: float
    audio_overlap_seconds: float

    @property
    def cloud_configured(self) -> bool:
        return bool(
            self.cloud_base_url
            and self.cloud_api_key
            and self.cloud_transcription_model
        )

    def public_dict(self) -> Dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "debug": self.debug,
            "transcription_mode": self.transcription_mode,
            "local_model": self.local_model,
            "cloud": {
                "configured": self.cloud_configured,
                "base_url": self.cloud_base_url,
                "model": self.cloud_transcription_model,
                "timeout_seconds": self.cloud_timeout_seconds,
            },
            "audio": {
                "max_queue": self.audio_max_queue,
                "window_seconds": self.audio_window_seconds,
                "overlap_seconds": self.audio_overlap_seconds,
            },
        }


def load_config(environ: Optional[Mapping[str, str]] = None) -> AppConfig:
    if environ is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        source: Mapping[str, str] = os.environ
    else:
        source = environ

    host = source.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _parse_int(source.get("PORT", "5001"), "PORT", 1)
    debug = _parse_bool(source.get("DEBUG", "false"), "DEBUG")
    mode = source.get("TRANSCRIPTION_MODE", "auto").strip().lower()
    if mode not in SUPPORTED_MODES:
        raise ValueError("TRANSCRIPTION_MODE must be auto, local or cloud")

    origins = tuple(
        origin.strip()
        for origin in source.get(
            "CORS_ORIGINS", "http://127.0.0.1:5001,http://localhost:5001"
        ).split(",")
        if origin.strip()
    )
    window_seconds = _parse_float(
        source.get("AUDIO_WINDOW_SECONDS", "3.0"), "AUDIO_WINDOW_SECONDS", 0.2
    )
    overlap_seconds = _parse_float(
        source.get("AUDIO_OVERLAP_SECONDS", "0.5"), "AUDIO_OVERLAP_SECONDS", 0.0
    )
    if overlap_seconds >= window_seconds:
        raise ValueError("AUDIO_OVERLAP_SECONDS must be less than AUDIO_WINDOW_SECONDS")

    cloud_base_url = source.get("CLOUD_BASE_URL", "").strip().rstrip("/")
    cloud_api_key = source.get("CLOUD_API_KEY", "").strip()
    cloud_model = source.get("CLOUD_TRANSCRIPTION_MODEL", "").strip()
    if cloud_base_url and not cloud_api_key:
        raise ValueError("CLOUD_API_KEY is required when CLOUD_BASE_URL is set")

    return AppConfig(
        host=host,
        port=port,
        debug=debug,
        cors_origins=origins,
        transcription_mode=mode,
        local_model=source.get("LOCAL_MODEL", "small").strip() or "small",
        cloud_base_url=cloud_base_url,
        cloud_api_key=cloud_api_key,
        cloud_transcription_model=cloud_model,
        cloud_timeout_seconds=_parse_float(
            source.get("CLOUD_TIMEOUT_SECONDS", "30"), "CLOUD_TIMEOUT_SECONDS", 1.0
        ),
        audio_max_queue=_parse_int(
            source.get("AUDIO_MAX_QUEUE", "32"), "AUDIO_MAX_QUEUE", 1
        ),
        audio_window_seconds=window_seconds,
        audio_overlap_seconds=overlap_seconds,
    )
