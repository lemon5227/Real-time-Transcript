"""Application factory for the real-time lecture transcription workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from .config import AppConfig, load_config
from .providers.factory import ProviderFactory
from .routes import register_routes, register_socket_handlers
from .session_manager import SessionManager


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")


def create_app(
    environ: Optional[Mapping[str, str]] = None,
    *,
    config: Optional[AppConfig] = None,
    provider_factory=None,
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
    app.extensions["app_config"] = app_config
    app.extensions["session_manager"] = manager
    register_routes(app, app_config)
    if not getattr(socketio, "_rtt_handlers_registered", False):
        register_socket_handlers(socketio)
        socketio._rtt_handlers_registered = True
    socketio.init_app(app)
    return app


__all__ = ["create_app", "socketio"]
