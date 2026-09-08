from typing import Any, Mapping, Optional

from flask import Flask, current_app, jsonify, redirect, render_template, request
from flask_socketio import SocketIO

from .config import AppConfig
from .device import device_public_dict, get_device_profile
from .models import SessionConfig
from .providers.base import ProviderError
from .providers.local_whisper import local_model_available

LOCAL_MODELS = (
    {"id": "parakeet-tdt-0.6b-v3", "label": "Parakeet TDT v3 · Mac MLX", "model_ref": "mlx-community/parakeet-tdt-0.6b-v3", "runtime": "mlx", "size": "~1.2GB", "speed": "最快", "quality": "很好", "resource": "中", "languages": "英语 / 24 种欧洲语言", "best_for": "Apple Silicon · 英语课堂"},
    {"id": "distil-small.en", "label": "Distil Small EN", "model_ref": "distil-small.en", "runtime": "standard", "size": "~336MB", "speed": "快", "quality": "好", "resource": "低", "languages": "英语", "best_for": "英语课堂 · 普通 CPU"},
    {"id": "tiny", "label": "Tiny", "model_ref": "tiny", "runtime": "standard", "size": "~75MB", "speed": "最快", "quality": "基础", "resource": "最低", "languages": "多语言", "best_for": "低配 CPU"},
    {"id": "base", "label": "Base", "model_ref": "base", "runtime": "standard", "size": "~145MB", "speed": "快", "quality": "不错", "resource": "低", "languages": "多语言", "best_for": "普通 CPU"},
    {"id": "small", "label": "Small", "model_ref": "small", "runtime": "standard", "size": "~465MB", "speed": "中等", "quality": "较好", "resource": "中", "languages": "多语言", "best_for": "课堂均衡"},
    {"id": "medium", "label": "Medium", "model_ref": "medium", "runtime": "standard", "size": "~1.5GB", "speed": "较慢", "quality": "更好", "resource": "高", "languages": "多语言", "best_for": "较高准确率"},
    {"id": "large-v3-turbo", "label": "Large v3 Turbo", "model_ref": "large-v3-turbo", "runtime": "standard", "size": "大模型", "speed": "GPU 快", "quality": "最高", "resource": "很高", "languages": "多语言", "best_for": "高性能 GPU"},
)


def _translation_error_result(exc: Exception) -> dict:
    if isinstance(exc, ProviderError):
        error = exc.to_dict()
    else:
        message = str(exc)
        code, _, detail = message.partition(":")
        error = {
            "code": code or "TRANSLATION_REQUEST_FAILED",
            "message": detail.strip() or "翻译请求无效",
            "action": "检查翻译设置后重试",
        }
    return {"status": "error", "error": error}


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
        manager = current_app.extensions["model_manager"]
        return jsonify({"models": manager.list_models()})

    @app.post("/api/models/<model_id>/download")
    def download_model(model_id: str):
        manager = current_app.extensions["model_manager"]
        try:
            model = manager.start_download(model_id)
        except KeyError:
            return jsonify({"error": "unknown model"}), 404
        if model["status"] == "dependency_missing":
            return jsonify({"model": model, "error": "local dependency missing"}), 409
        if model["status"] == "downloading":
            return jsonify({"model": model}), 202
        if model["status"] == "failed":
            return jsonify({"model": model, "error": model.get("message")}), 502
        return jsonify({"model": model}), 202 if model["status"] != "ready" else 200

    @app.post("/api/models/<model_id>/cancel")
    def cancel_model_download(model_id: str):
        manager = current_app.extensions["model_manager"]
        try:
            model = manager.cancel_download(model_id)
        except KeyError:
            return jsonify({"error": "unknown model"}), 404
        return jsonify({"model": model})

    @app.get("/api/capabilities")
    def capabilities():
        profile = get_device_profile()
        device = device_public_dict(profile)
        model_states = current_app.extensions["model_manager"].list_models()
        return jsonify({
            "local": {
                "available": any(model["dependency_available"] for model in model_states),
                "ready_models": [model["id"] for model in model_states if model["status"] == "ready"],
                "device": device,
                "runtime": device["runtime"],
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
            "translation": config.public_dict()["translation"],
        })

    @app.post("/api/translate")
    def translate_batch():
        payload = request.get_json(silent=True) or {}
        try:
            segments = payload.get("segments") or []
            if not isinstance(segments, list) or not segments:
                raise ValueError("TRANSLATION_INVALID_TEXT: 至少提供一段字幕")
            items = []
            for segment in segments:
                if not isinstance(segment, Mapping) or not segment.get("id"):
                    raise ValueError("TRANSLATION_INVALID_TEXT: 字幕段格式无效")
                items.append({"id": str(segment["id"]), "text": str(segment.get("text") or "").strip()})
            if any(not item["text"] for item in items):
                raise ValueError("TRANSLATION_INVALID_TEXT: 翻译文本不能为空")
            router = current_app.extensions["translation_router"]
            selection = router.resolve(
                mode=str(payload.get("mode") or "fast"),
                provider=str(payload.get("provider") or "auto"),
                local_ready=bool(payload.get("local_ready", False)),
            )
            if selection.provider is None:
                return jsonify({"status": "disabled", "translations": []})
            translated = selection.provider.translate_batch(
                [item["text"] for item in items],
                str(payload.get("source_language") or "en"),
                str(payload.get("target_language") or "zh"),
            )
            target_language = str(payload.get("target_language") or "zh")
            return jsonify({
                "status": "success",
                "provider": selection.provider_name,
                "mode": selection.mode,
                "model": selection.model,
                "translations": [
                    {
                        "segment_id": item["id"],
                        "target_language": target_language,
                        "text": text,
                        "status": "ready",
                        "mode": selection.mode,
                        "provider": selection.provider_name,
                        "model": selection.model,
                    }
                    for item, text in zip(items, translated)
                ],
            })
        except (ProviderError, ValueError, TypeError) as exc:
            result = _translation_error_result(exc)
            return jsonify(result), 400 if result["error"]["code"].startswith("TRANSLATION_INVALID") else 502


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
            dropped = manager.push_audio(
                flask_request.sid,
                encoded_audio=str(payload.get("audio") or ""),
                sample_rate=int(payload.get("sample_rate") or 16000),
                sequence=int(payload.get("sequence") or 0),
            )
        except (ProviderError, ValueError, TypeError) as exc:
            result = error_result(exc)
            socketio.emit("transcription_error", result["error"], to=flask_request.sid)
            return result
        if dropped:
            socketio.emit(
                "audio_backpressure",
                {
                    "code": "AUDIO_BACKPRESSURE",
                    "message": "本地处理较慢，已跳过少量音频，仍在继续转录",
                },
                to=flask_request.sid,
            )
        return {"status": "accepted", "dropped": dropped}

    @socketio.on("stop_transcription")
    def handle_stop(_data: Optional[Mapping[str, Any]] = None):
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        result = manager.stop(flask_request.sid)
        socketio.emit("transcription_stopped", result, to=flask_request.sid)
        return result

    @socketio.on("translate_segments")
    def handle_translate_segments(data: Optional[Mapping[str, Any]] = None):
        from flask import request as flask_request

        manager = current_app.extensions["session_manager"]
        router = current_app.extensions["translation_router"]
        payload = dict(data or {})
        try:
            segment_ids = [str(item) for item in (payload.get("segment_ids") or [])]
            items = manager.translation_segments(flask_request.sid, segment_ids)
            selection = router.resolve(
                mode=str(payload.get("mode") or "fast"),
                provider=str(payload.get("provider") or "auto"),
                local_ready=bool(payload.get("local_ready", False)),
            )
            if selection.provider is None:
                return {"status": "disabled", "translations": []}
            target_language = str(payload.get("target_language") or "zh")
            translated = selection.provider.translate_batch(
                [item["text"] for item in items],
                str(payload.get("source_language") or "en"),
                target_language,
            )
            result = {
                "status": "success",
                "provider": selection.provider_name,
                "mode": selection.mode,
                "model": selection.model,
                "translations": [
                    {
                        "segment_id": item["id"],
                        "target_language": target_language,
                        "text": text,
                        "status": "ready",
                        "mode": selection.mode,
                        "provider": selection.provider_name,
                        "model": selection.model,
                    }
                    for item, text in zip(items, translated)
                ],
            }
            socketio.emit("translation_result", result, to=flask_request.sid)
            return result
        except (ProviderError, ValueError, TypeError) as exc:
            result = error_result(exc)
            socketio.emit("translation_error", result["error"], to=flask_request.sid)
            return result
