"""Development entry point for the real-time lecture transcription app."""

from backend import create_app, socketio
from backend.logging_setup import configure_logging, default_log_directory

# A real run keeps a rotating log file so a bad live transcript can be inspected
# after class; importing the app for tests stays console-only.
configure_logging(log_directory=default_log_directory())

app = create_app()


if __name__ == "__main__":
    socketio.run(
        app,
        host=app.config["APP_HOST"],
        port=app.config["APP_PORT"],
        debug=False,
        allow_unsafe_werkzeug=True,
    )
