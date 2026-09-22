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

If captions are late **only at the start of a session**, that is the sliding window warming up. The
window opens short and grows into `MLX_WINDOW_SECONDS`, so a session shows its first caption after
about 8 seconds and the rest of the session runs at the live edge. That 8 seconds is the head guard
plus the audio the model needs to form a sentence, and it is by design.

It is **not** tunable from `.env`. Lowering `MLX_WINDOW_SECONDS` does not help — the window now
grows into its full length instead of waiting for it, and shorter windows were measured to drop
whole clauses. Lowering `MLX_HOP_SECONDS` does not help either: the cold start is set by the head
guard, not by how often the window is decoded.

If captions stay behind **throughout** the lecture, check the decode pace the session logged:

```bash
grep "会话结束" logs/realtime-transcript.log     # 实时倍率=0.24x
```

Below 1.0 the worker is keeping up and the delay is not the machine. Above it the model cannot
decode as fast as the lecture arrives; raise `MLX_HOP_SECONDS` so each window covers more new
audio, and check that nothing else is competing for the GPU.

**Check memory pressure before anything else.** This was measured, not guessed: the same 10.6-minute
run through the same code took a flat 0.31× realtime with **no window over the 2s hop** when the
machine had room, and 0.39× with **21 of 317 windows over the hop** (worst 4.6s, so captions
visibly lagging) when a second copy of the model was resident and the machine was swapping 12 GB of
13 GB. The model needs roughly 2.3 GB:

```bash
sysctl vm.swapusage                              # macOS: watch "used"
```

If swap is nearly full, close other applications — a browser with many tabs, an IDE, a video call —
and start the session again. Nothing in the app can compensate for being paged out.

A session that repeatedly warns `解码偏慢` in the log is genuinely falling behind — the warning is
based on cumulative decode time, not on a single call.

The first run after starting the server is always a few seconds slower while the GPU kernels
compile; that is not a regression. Full measurements and the other knobs are in
[`docs/LATENCY.md`](docs/LATENCY.md).

## Captions are unreadable word salad

You are on the streaming decoder. `MLX_LIVE_MODE=streaming` selects the older
`transcribe_stream()` path, which on lecture audio produced word salad and ran at roughly 1.5×
realtime. Remove it from the backend `.env` (or set `MLX_LIVE_MODE=windowed`) and restart.

The streaming path holds a fixed amount of audio back before it confirms text — that amount is
`MLX_STREAM_RIGHT_CONTEXT × 0.08` seconds — but the lag is not why the text was bad. The
`keep_original_attention=False` default swapped the encoder to local attention. The windowed path
has neither problem.

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
