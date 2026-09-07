"""Development entry point for the real-time lecture transcription app."""

from backend import create_app, socketio


app = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host=app.config["APP_HOST"],
        port=app.config["APP_PORT"],
        debug=False,
    )
