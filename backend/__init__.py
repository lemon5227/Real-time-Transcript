"""Application factory for the real-time lecture transcription workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from .config import AppConfig, load_config
from .fine_transcription import FineTranscriptionManager
from .logging_setup import configure_logging
from .model_manager import ModelManager
from .providers.diarization import create_diarizer
from .providers.factory import ProviderFactory
from .providers.google_translation import GoogleTranslationProvider
from .providers.microsoft_translation import MicrosoftTranslationProvider
from .providers.mlx_parakeet import MlxModelCache
from .routes import LOCAL_MODELS, register_routes, register_socket_handlers
from .session_manager import SessionManager
from .translation import ModelTranslationProvider, TranslationRouter

socketio = SocketIO(async_mode="threading")


def create_app(
    environ: Optional[Mapping[str, str]] = None,
    *,
    config: Optional[AppConfig] = None,
    provider_factory=None,
    translation_router=None,
) -> Flask:
    """Create a lightweight Flask app without importing model runtimes."""
    configure_logging()
    app_config = config or load_config(environ)
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent.parent / "static"),
    )
    app.config.update(
        SECRET_KEY=app_config.secret_key,
        APP_HOST=app_config.host,
        APP_PORT=app_config.port,
    )
    CORS(app, origins=list(app_config.cors_origins) or "*")

    resolved_provider_factory = provider_factory or ProviderFactory(app_config)
    manager = SessionManager(
        resolved_provider_factory,
        emit=lambda sid, event, payload: socketio.emit(event, payload, to=sid),
        startup_timeout_seconds=app_config.audio_startup_timeout_seconds,
        streaming_chunk_seconds=app_config.streaming_chunk_seconds,
        diarizer_factory=lambda session_config: create_diarizer(
            app_config.diarization_command,
            enabled=session_config.enable_diarization and app_config.diarization_enabled,
            variant=session_config.diarization_variant,
        ),
    )
    model_cache = getattr(resolved_provider_factory, "mlx_model_cache", None) or MlxModelCache()
    fine_transcription_manager = FineTranscriptionManager(model_cache=model_cache)
    model_manager = ModelManager(LOCAL_MODELS)
    translation_router = translation_router or _create_translation_router(app_config)
    app.extensions["app_config"] = app_config
    app.extensions["session_manager"] = manager
    app.extensions["model_manager"] = model_manager
    app.extensions["translation_router"] = translation_router
    app.extensions["fine_transcription_manager"] = fine_transcription_manager
    register_routes(app, app_config)
    if not getattr(socketio, "_rtt_handlers_registered", False):
        register_socket_handlers(socketio)
        socketio._rtt_handlers_registered = True
    # The socket has to follow the same origin policy as the HTTP API. Left open
    # it let any web page drive the local server: start transcription, download
    # models and spend the configured translation quota.
    socketio.init_app(
        app, cors_allowed_origins=list(app_config.cors_origins) or "*"
    )
    return app


__all__ = ["create_app", "socketio"]


def _create_translation_router(config: AppConfig) -> TranslationRouter:
    return TranslationRouter(
        google=(
            GoogleTranslationProvider(
                project_id=config.translation_google_project_id,
                api_key=config.translation_google_api_key,
                location=config.translation_google_location,
                timeout_seconds=config.translation_timeout_seconds,
            )
        ),
        microsoft=(
            MicrosoftTranslationProvider(
                endpoint=config.translation_microsoft_endpoint,
                api_key=config.translation_microsoft_api_key,
                region=config.translation_microsoft_region,
                timeout_seconds=config.translation_timeout_seconds,
            )
            if config.translation_microsoft_configured
            else None
        ),
        local=(
            ModelTranslationProvider(
                name="local",
                model=config.translation_local_model,
                base_url=config.translation_local_base_url,
                api_key=config.translation_local_api_key,
                timeout_seconds=config.translation_timeout_seconds,
            )
            if config.translation_local_configured
            else None
        ),
        cloud=(
            ModelTranslationProvider(
                name="cloud",
                model=config.translation_cloud_model,
                base_url=config.translation_cloud_base_url,
                api_key=config.translation_cloud_api_key,
                timeout_seconds=config.translation_timeout_seconds,
            )
            if config.translation_cloud_configured
            else None
        ),
    )
