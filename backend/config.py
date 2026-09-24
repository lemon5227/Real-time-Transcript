import os
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from .glossary import parse_glossary
from .models import SUPPORTED_MODES
from .voice_gate import normalize_threshold


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


def _parse_choice(value: str, name: str, allowed: Tuple[str, ...]) -> str:
    candidate = str(value or "").strip().lower()
    if candidate not in allowed:
        raise ValueError("%s must be one of %s" % (name, ", ".join(allowed)))
    return candidate


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
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
    translation_google_project_id: str
    translation_google_api_key: str
    translation_google_location: str
    translation_microsoft_endpoint: str
    translation_microsoft_api_key: str
    translation_microsoft_region: str
    translation_local_base_url: str
    translation_local_api_key: str
    translation_local_model: str
    translation_cloud_base_url: str
    translation_cloud_api_key: str
    translation_cloud_model: str
    translation_timeout_seconds: float
    audio_max_queue: int
    audio_window_seconds: float
    audio_overlap_seconds: float
    audio_vad_threshold: float
    audio_glossary: Tuple[str, ...]
    audio_startup_timeout_seconds: float
    streaming_chunk_seconds: float
    mlx_stream_right_context: int
    # Live caption strategy on Apple Silicon. "windowed" re-decodes a sliding
    # window in full context, which is what keeps a real accented lecture
    # readable; "streaming" is the original incremental decoder.
    mlx_live_mode: str
    mlx_window_seconds: float
    mlx_hop_seconds: float
    diarization_enabled: bool
    diarization_command: str
    diarization_variant: str
    diarization_queue: int

    @property
    def streaming_confirmation_lag_seconds(self) -> float:
        """How long the streaming model holds audio back before confirming text.

        One encoder frame is 8 (subsampling) * 160 (hop) / 16000 = 0.08s, and the
        decoder refuses to finalize the last `right context` frames. Keep this in
        sync with `ENCODER_FRAME_SECONDS` in providers/mlx_parakeet.py.
        """
        return round(self.mlx_stream_right_context * 0.08, 3)

    @property
    def live_confirmation_lag_seconds(self) -> float:
        """How long the *active* live decoder holds audio back before confirming.

        Only the streaming decoder withholds a right context. The default windowed
        path ends its window at the live edge, so it has no such lag -- reporting
        the streaming figure unconditionally told clients to expect 1.28s of delay
        that the shipped configuration does not have.
        """
        if self.mlx_live_mode == "streaming":
            return self.streaming_confirmation_lag_seconds
        return 0.0

    @property
    def cloud_configured(self) -> bool:
        return bool(
            self.cloud_base_url
            and self.cloud_api_key
            and self.cloud_transcription_model
        )

    @property
    def translation_google_configured(self) -> bool:
        # Cloud Translation Basic (v2) authenticates with the API key. The
        # project is useful for console/billing context but is not required
        # in the request itself.
        return bool(self.translation_google_api_key)

    @property
    def translation_microsoft_configured(self) -> bool:
        return bool(self.translation_microsoft_endpoint and self.translation_microsoft_api_key)

    @property
    def translation_cloud_configured(self) -> bool:
        return bool(
            self.translation_cloud_base_url
            and self.translation_cloud_api_key
            and self.translation_cloud_model
        )

    @property
    def translation_local_configured(self) -> bool:
        return bool(self.translation_local_base_url and self.translation_local_model)

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
                "vad_threshold": self.audio_vad_threshold,
                "glossary": list(self.audio_glossary),
                "startup_timeout_seconds": self.audio_startup_timeout_seconds,
                "streaming_chunk_seconds": self.streaming_chunk_seconds,
                "streaming_lag_seconds": self.streaming_confirmation_lag_seconds,
                # What the live decoder is actually configured to do. Without this
                # a client cannot tell the windowed default from the streaming
                # fallback, or why `streaming_lag_seconds` does not apply to it.
                "live": {
                    "mode": self.mlx_live_mode,
                    "window_seconds": self.mlx_window_seconds,
                    "hop_seconds": self.mlx_hop_seconds,
                    "confirmation_lag_seconds": self.live_confirmation_lag_seconds,
                },
            },
            "diarization": {
                "enabled": self.diarization_enabled,
                "configured": bool(self.diarization_command),
                "variant": self.diarization_variant,
                "queue": self.diarization_queue,
            },
            "translation": {
                "google": {
                    "configured": self.translation_google_configured,
                    "public_fallback": True,
                },
                "microsoft": {
                    "configured": self.translation_microsoft_configured,
                    "region": self.translation_microsoft_region,
                },
                "local_model": {
                    "configured": self.translation_local_configured,
                    "base_url": self.translation_local_base_url,
                    "model": self.translation_local_model,
                },
                "cloud_model": {
                    "configured": self.translation_cloud_configured,
                    "model": self.translation_cloud_model,
                },
                "timeout_seconds": self.translation_timeout_seconds,
            },
        }


def load_config(environ: Optional[Mapping[str, str]] = None) -> AppConfig:
    if environ is None:
        try:
            from dotenv import load_dotenv

            env_file = os.environ.get("TRANSCRIPT_ENV_FILE", "").strip()
            if env_file:
                load_dotenv(dotenv_path=env_file)
            else:
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
    if host in {"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"}:
        local_origins = (
            "http://127.0.0.1:%d" % port,
            "http://localhost:%d" % port,
        )
        origins += tuple(origin for origin in local_origins if origin not in origins)
    # Managed hosts publish the public URL only once the container is running,
    # so it cannot be written into the image. Without it a deployed instance
    # would reject its own browser origin.
    external_url = source.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if external_url and external_url not in origins:
        origins += (external_url,)
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

    translation_google_project_id = source.get("TRANSLATION_GOOGLE_PROJECT_ID", "").strip()
    translation_google_api_key = source.get("TRANSLATION_GOOGLE_API_KEY", "").strip()
    translation_google_location = source.get("TRANSLATION_GOOGLE_LOCATION", "global").strip() or "global"
    translation_microsoft_endpoint = source.get(
        "TRANSLATION_MICROSOFT_ENDPOINT", "https://api.cognitive.microsofttranslator.com"
    ).strip().rstrip("/")
    translation_microsoft_api_key = source.get("TRANSLATION_MICROSOFT_API_KEY", "").strip()
    translation_microsoft_region = source.get("TRANSLATION_MICROSOFT_REGION", "").strip()
    translation_local_base_url = source.get("TRANSLATION_LOCAL_BASE_URL", "").strip().rstrip("/")
    translation_local_api_key = source.get("TRANSLATION_LOCAL_API_KEY", "").strip()
    translation_local_model = source.get("TRANSLATION_LOCAL_MODEL", "").strip()
    translation_cloud_base_url = source.get("TRANSLATION_CLOUD_BASE_URL", "").strip().rstrip("/")
    translation_cloud_api_key = source.get("TRANSLATION_CLOUD_API_KEY", "").strip()
    translation_cloud_model = source.get("TRANSLATION_CLOUD_MODEL", "").strip()

    return AppConfig(
        secret_key=source.get("SECRET_KEY", "development-only-change-me").strip()
        or "development-only-change-me",
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
        translation_google_project_id=translation_google_project_id,
        translation_google_api_key=translation_google_api_key,
        translation_google_location=translation_google_location,
        translation_microsoft_endpoint=translation_microsoft_endpoint,
        translation_microsoft_api_key=translation_microsoft_api_key,
        translation_microsoft_region=translation_microsoft_region,
        translation_local_base_url=translation_local_base_url,
        translation_local_api_key=translation_local_api_key,
        translation_local_model=translation_local_model,
        translation_cloud_base_url=translation_cloud_base_url,
        translation_cloud_api_key=translation_cloud_api_key,
        translation_cloud_model=translation_cloud_model,
        translation_timeout_seconds=_parse_float(
            source.get("TRANSLATION_TIMEOUT_SECONDS", "20"), "TRANSLATION_TIMEOUT_SECONDS", 1.0
        ),
        audio_max_queue=_parse_int(
            source.get("AUDIO_MAX_QUEUE", "64"), "AUDIO_MAX_QUEUE", 1
        ),
        audio_window_seconds=window_seconds,
        audio_overlap_seconds=overlap_seconds,
        audio_vad_threshold=normalize_threshold(source.get("AUDIO_VAD_THRESHOLD")),
        audio_glossary=parse_glossary(source.get("AUDIO_GLOSSARY", "")),
        audio_startup_timeout_seconds=_parse_float(
            source.get("AUDIO_STARTUP_TIMEOUT_SECONDS", "45"), "AUDIO_STARTUP_TIMEOUT_SECONDS", 1.0
        ),
        streaming_chunk_seconds=_parse_float(
            source.get("STREAMING_CHUNK_SECONDS", "1.0"), "STREAMING_CHUNK_SECONDS", 0.1
        ),
        mlx_stream_right_context=_parse_int(
            source.get("MLX_STREAM_RIGHT_CONTEXT", "32"), "MLX_STREAM_RIGHT_CONTEXT", 1
        ),
        mlx_live_mode=_parse_choice(
            source.get("MLX_LIVE_MODE", "windowed"),
            "MLX_LIVE_MODE",
            ("windowed", "streaming"),
        ),
        mlx_window_seconds=_parse_float(
            source.get("MLX_WINDOW_SECONDS", "18.0"), "MLX_WINDOW_SECONDS", 4.0
        ),
        mlx_hop_seconds=_parse_float(
            source.get("MLX_HOP_SECONDS", "2.0"), "MLX_HOP_SECONDS", 0.1
        ),
        diarization_enabled=_parse_bool(
            source.get("DIARIZATION_ENABLED", "true"), "DIARIZATION_ENABLED"
        ),
        diarization_command=source.get("DIARIZATION_COMMAND", "").strip(),
        diarization_variant=source.get("DIARIZATION_VARIANT", "low").strip() or "low",
        diarization_queue=_parse_int(
            source.get("DIARIZATION_QUEUE", "16"), "DIARIZATION_QUEUE", 1
        ),
    )
