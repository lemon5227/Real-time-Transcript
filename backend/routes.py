from typing import Any, Mapping, Optional

from flask import Flask, current_app, jsonify, redirect, render_template
from flask_socketio import SocketIO

from .config import AppConfig
from .device import device_public_dict, get_device_profile
from .models import SessionConfig
from .providers.base import ProviderError
from .providers.local_whisper import local_model_available

LOCAL_MODELS = (
    {"id": "tiny", "label": "Tiny", "size": "~75MB", "best_for": "低配 CPU"},
    {"id": "base", "label": "Base", "size": "~145MB", "best_for": "普通 CPU"},
    {"id": "small", "label": "Small", "size": "~465MB", "best_for": "课堂均衡"},
    {"id": "medium", "label": "Medium", "size": "~1.5GB", "best_for": "较高准确率"},
    {"id": "large-v3-turbo", "label": "Large v3 Turbo", "size": "大模型", "best_for": "高性能 GPU"},
)


def register_routes(app: Flask, config: AppConfig) -> None:
    @app.get("/")
    def live_page():
        return render_template("live-transcript.html")

    @app.get("/review")
    def review_page():
        return render_template("review.html")

    @app.get("/realtime.html")
    def legacy_live_page():
        return redirect("/", code=302)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "real-time-transcript"})

    @app.get("/api/config/public")
    def public_config():
        return jsonify(config.public_dict())

    @app.get("/api/models")
    def models():
        return jsonify({
            "models": [dict(model, available=local_model_available(model["id"])) for model in LOCAL_MODELS]
        })

    @app.get("/api/capabilities")
    def capabilities():
        profile = get_device_profile()
        device = device_public_dict(profile)
        return jsonify({
            "local": {
                "available": any(local_model_available(model["id"]) for model in LOCAL_MODELS),
                "device": device,
                "recommended_model": device["recommended_model"],
            },
            "cloud": {
                "configured": config.cloud_configured,
                "base_url": config.cloud_base_url,
                "model": config.cloud_transcription_model,
            },
            "audio": {
                "sample_rate": 16000,
                "max_queue": config.audio_max_queue,
                "window_seconds": config.audio_window_seconds,
                "overlap_seconds": config.audio_overlap_seconds,
            },
        })


def register_socket_handlers(socketio: SocketIO) -> None:
    """Register process-wide handlers that resolve state from the active app."""
    def error_result(exc: Exception) -> dict:
        if isinstance(exc, ProviderError):
            error = exc.to_dict()
        else:
            message = str(exc)
            code, _, detail = message.partition(":")
            error = {
                "code": code or "INVALID_REQUEST",
                "message": detail.strip() or "请求无效",
                "action": "检查设置后重试",
            }
        return {"status": "error", "error": error}

    @socketio.on("connect")
    def handle_connect():
        return {"status": "success"}

    @socketio.on("disconnect")
    def handle_disconnect():
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        manager.cleanup(flask_request.sid)

    @socketio.on("start_transcription")
    def handle_start(data: Optional[Mapping[str, Any]] = None):
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        config = current_app.extensions["app_config"]
        payload = dict(data or {})
        mode = str(payload.get("mode") or config.transcription_mode)
        try:
            session_config = SessionConfig(
                mode=mode,
                model=payload.get("model"),
                language=str(payload.get("language") or "en"),
                sample_rate=int(payload.get("sample_rate") or 16000),
                enable_vad=bool(payload.get("enable_vad", True)),
                window_seconds=float(payload.get("window_seconds") or config.audio_window_seconds),
                overlap_seconds=float(payload.get("overlap_seconds") or config.audio_overlap_seconds),
                max_queue=config.audio_max_queue,
            )
            result = manager.start(flask_request.sid, session_config)
        except (ProviderError, ValueError, TypeError) as exc:
            result = error_result(exc)
            socketio.emit("transcription_error", result["error"], to=flask_request.sid)
            return result
        socketio.emit("transcription_started", result, to=flask_request.sid)
        return result

    @socketio.on("audio_chunk")
    def handle_audio(data: Optional[Mapping[str, Any]] = None):
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        payload = dict(data or {})
        try:
            manager.push_audio(
                flask_request.sid,
                encoded_audio=str(payload.get("audio") or ""),
                sample_rate=int(payload.get("sample_rate") or 16000),
                sequence=int(payload.get("sequence") or 0),
            )
        except (ProviderError, ValueError, TypeError) as exc:
            result = error_result(exc)
            socketio.emit("transcription_error", result["error"], to=flask_request.sid)
            return result
        return {"status": "accepted"}

    @socketio.on("stop_transcription")
    def handle_stop(_data: Optional[Mapping[str, Any]] = None):
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        result = manager.stop(flask_request.sid)
        socketio.emit("transcription_stopped", result, to=flask_request.sid)
        return result
