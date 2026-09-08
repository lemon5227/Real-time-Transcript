"""Application factory for the real-time lecture transcription workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from .config import AppConfig, load_config
from .model_manager import ModelManager
from .providers.factory import ProviderFactory
from .providers.google_translation import GoogleTranslationProvider
from .providers.microsoft_translation import MicrosoftTranslationProvider
from .routes import LOCAL_MODELS, register_routes, register_socket_handlers
from .session_manager import SessionManager
from .translation import ModelTranslationProvider, TranslationRouter

socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")


def create_app(
    environ: Optional[Mapping[str, str]] = None,
    *,
    config: Optional[AppConfig] = None,
    provider_factory=None,
    translation_router=None,
) -> Flask:
    """Create a lightweight Flask app without importing model runtimes."""
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

    manager = SessionManager(
        provider_factory or ProviderFactory(app_config),
        emit=lambda sid, event, payload: socketio.emit(event, payload, to=sid),
    )
    model_manager = ModelManager(LOCAL_MODELS)
    translation_router = translation_router or _create_translation_router(app_config)
    app.extensions["app_config"] = app_config
    app.extensions["session_manager"] = manager
    app.extensions["model_manager"] = model_manager
    app.extensions["translation_router"] = translation_router
    register_routes(app, app_config)
    if not getattr(socketio, "_rtt_handlers_registered", False):
        register_socket_handlers(socketio)
        socketio._rtt_handlers_registered = True
    socketio.init_app(app)
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
