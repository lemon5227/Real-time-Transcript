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

## Captions feel far behind the speaker

Nothing is wrong with the machine; the Parakeet stream holds a fixed amount of audio back before
it will confirm text. That amount is `MLX_STREAM_RIGHT_CONTEXT × 0.08` seconds, and it used to be
hardcoded to 5.12 seconds.

Set it in the backend `.env` and restart the server:

```dotenv
MLX_STREAM_RIGHT_CONTEXT=16   # 1.28s. 32 gives 2.56s with more decoding context.
```

Measured on this machine, a 13-second five-sentence clip goes from 8.4s to 7.3s for the first
confirmed caption. Do not go below 8 — below that the model has too little right context and
lecture terminology starts coming out wrong. Full measurements and the other knobs are in
[`docs/LATENCY.md`](docs/LATENCY.md).

The first run after starting the server is always a few seconds slower while the GPU kernels
compile; that is not a regression.

## Real-time translation always fails

Check which provider is actually configured:

```bash
curl -s --noproxy '*' http://127.0.0.1:5001/api/capabilities | python3 -m json.tool
```

If `translation.google.configured` is `false` you have no Google API key, and the keyless Google
web endpoint the app would otherwise fall back to is **no longer usable** — it returns a
consent/verification page instead of JSON. Configuration looks fine but every request fails.

Fix it by configuring either provider:

```dotenv
# Option A — Microsoft Translator (see README for how to create the resource)
TRANSLATION_MICROSOFT_API_KEY=your-key
TRANSLATION_MICROSOFT_REGION=

# Option B — Google Cloud Translation API key
TRANSLATION_GOOGLE_API_KEY=your-key
```

Restart the server afterwards. The router prefers a provider with a key over the keyless
best-effort path and falls through to the next provider when one fails, so a single working key
is enough.

## Logs and privacy

The default binding is localhost. Do not commit `.env`. Audio, full captions and API keys are not intentionally logged by the runtime. See [`docs/PRIVACY.md`](docs/PRIVACY.md) and [`docs/API.md`](docs/API.md).

For developer-facing diagnosis — how to reproduce a problem, and the traps in this project's dev
environment — see [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md).
