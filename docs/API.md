# API contract

The HTTP server listens on `127.0.0.1:5001` by default. Socket.IO uses the same origin. Request and event names below are the runtime contract used by the browser client.

## REST

### `GET /api/health`

Response:

```json
{"status":"ok","service":"real-time-transcript"}
```

### `GET /api/config/public`

Returns non-sensitive configuration. It never includes `CLOUD_API_KEY`.

```json
{
  "host":"127.0.0.1",
  "port":5001,
  "debug":false,
  "transcription_mode":"auto",
  "local_model":"small",
  "cloud":{"configured":false,"base_url":"","model":"","timeout_seconds":30},
  "audio":{"max_queue":32,"window_seconds":3.0,"overlap_seconds":0.5}
}
```

### `GET /api/models`

Returns the local model catalog and whether its optional runtime dependency can be imported. Model weights are loaded only when a session starts.

```json
{"models":[{"id":"small","label":"Small","size":"~465MB","best_for":"课堂均衡","available":false}]}
```

### `GET /api/capabilities`

Returns device, local path, cloud configuration state and audio defaults. The cloud object contains `base_url` and `model`, but never the API key.

```json
{
  "local":{"available":true,"device":{"kind":"cpu","device":"cpu","label":"CPU","recommended_model":"small"},"recommended_model":"small"},
  "cloud":{"configured":false,"base_url":"","model":""},
  "audio":{"sample_rate":16000,"max_queue":32,"window_seconds":3.0,"overlap_seconds":0.5}
}
```

## Socket.IO

Connect to the default namespace, then use acknowledgements for control events. Every connection may own one active session only.

### `start_transcription`

Client payload:

```json
{"mode":"auto","model":"small","language":"en","sample_rate":48000,"enable_vad":true}
```

`mode` is `auto`, `local` or `cloud`. The server normalizes incoming audio to 16 kHz. A successful acknowledgement and the `transcription_started` event have this shape:

```json
{"status":"success","session_id":"session-…","provider":"local","model":"small"}
```

On failure:

```json
{"status":"error","error":{"code":"NO_TRANSCRIPTION_PROVIDER","message":"…","action":"…"}}
```

### `audio_chunk`

Client payload:

```json
{"audio":"<base64 PCM16 little-endian mono>","sample_rate":48000,"sequence":12}
```

`sequence` must increase for every chunk. The server bounds the session queue and rejects malformed or oversized payloads. Success acknowledgement:

```json
{"status":"accepted"}
```

The server emits `transcript_segment` as final segments become available:

```json
{"id":"local-1","text":"Today we will discuss…","start_ms":0,"end_ms":3000,"is_final":true,"confidence":null}
```

### `stop_transcription`

Client payload is `{}`. The acknowledgement and `transcription_stopped` event contain the final merged segments:

```json
{
  "status":"success",
  "session_id":"session-…",
  "segments":[{"id":"local-1","text":"…","start_ms":0,"end_ms":3000,"is_final":true,"confidence":null}]
}
```

Stopping twice is safe; a second stop returns `{ "status":"success", "already_stopped":true }`. A `transcription_error` event includes `code`, `message` and an optional actionable `action`.

## Cloud provider boundary

The cloud provider sends each normalized audio window as an in-memory WAV `POST` to `${CLOUD_BASE_URL}/audio/transcriptions`, with `Authorization: Bearer <CLOUD_API_KEY>`, the configured model and language. Provider adapters can be replaced without changing the browser contract.
