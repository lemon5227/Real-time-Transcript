# Troubleshooting

## Page or Socket.IO does not connect

Confirm the process is running on `127.0.0.1:5001`, refresh the page and check the live status pill. If the browser cannot load the Socket.IO client CDN, serve a local copy of the compatible Socket.IO browser client in a future deployment build; the Python core remains usable.

## Microphone permission is denied

Use the localhost URL, allow microphone access in browser settings, and close another application that has exclusive access. The app uses one mono input and accepts the browser's actual sample rate.

## Local model fails or is too slow

Select a smaller model, install the local requirements in the active virtual environment, or use `./start.sh --mode cloud`. Auto mode also falls back to cloud after a local model startup failure when cloud variables are complete.

## Cloud mode fails

Check that `CLOUD_BASE_URL` is the API root, the key is valid, and the configured service accepts `POST /audio/transcriptions` with a multipart WAV file. The server maps timeout, auth and rate-limit errors into an actionable UI message.

## Review records are missing

The review page uses IndexedDB in the browser. Allow site storage for `127.0.0.1`; live transcription does not crash if IndexedDB is unavailable, but it cannot persist sessions.

## Logs and privacy

The default binding is localhost. Do not commit `.env`. Audio, full captions and API keys are not intentionally logged by the runtime. See [`docs/PRIVACY.md`](docs/PRIVACY.md) and [`docs/API.md`](docs/API.md).
