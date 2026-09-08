# API contract

The HTTP server listens on `127.0.0.1:5001` by default. Socket.IO uses the same origin. Request and event names below are the runtime contract used by the browser client.

## REST

### `GET /api/health`

Response:

```json
{"status":"ok","service":"real-time-transcript"}
```

### `GET /api/config/public`

Returns non-sensitive configuration. It never includes transcription or translation API keys.

```json
{
  "host":"127.0.0.1",
  "port":5001,
  "debug":false,
  "transcription_mode":"auto",
  "local_model":"small",
  "cloud":{"configured":false,"base_url":"","model":"","timeout_seconds":30},
  "audio":{"max_queue":32,"window_seconds":3.0,"overlap_seconds":0.5},
  "translation":{"google":{"configured":false},"microsoft":{"configured":false,"region":""},"cloud_model":{"configured":false,"model":""},"timeout_seconds":20}
}
```

### `GET /api/models`

Returns the local model catalog, dependency availability and weight-cache readiness. This endpoint never loads a model. The catalog includes `distil-small.en` for English lectures on thin laptops; `tiny`, `base` and `small` remain the multilingual choices.

```json
{"models":[{"id":"small","label":"Small","size":"~465MB","best_for":"课堂均衡","available":true,"dependency_available":true,"weights_available":true,"status":"ready","downloaded_bytes":483617219,"total_bytes":0,"progress":100,"message":"模型已下载，可以开始听课"}]}
```

`status` is one of `ready`, `not_downloaded`, `runtime_download`, `downloading`, `failed` or `dependency_missing`. `runtime_download` means the installed local runtime manages its own model cache, so the session may download weights during the explicit start step. `POST /api/models/<model_id>/download` starts an asynchronous download when the runtime exposes a direct model URL and returns the current model state; poll `GET /api/models` for progress. `POST /api/models/<model_id>/cancel` requests cancellation. Downloads are stored in the Whisper cache and promoted atomically after completion.

### `GET /api/capabilities`

Returns device, local path, cloud configuration state, audio defaults and translation provider availability. It never returns an API key.

```json
{
  "local":{"available":true,"ready_models":["small"],"device":{"kind":"cpu","device":"cpu","label":"CPU","recommended_model":"small"},"recommended_model":"small"},
  "cloud":{"configured":false,"base_url":"","model":""},
  "audio":{"sample_rate":16000,"max_queue":32,"window_seconds":3.0,"overlap_seconds":0.5},
  "translation":{"google":{"configured":false},"microsoft":{"configured":false,"region":""},"cloud_model":{"configured":false,"model":""},"timeout_seconds":20}
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

### `translate_segments` Socket.IO event

The live page sends final segment IDs through the active session. The server resolves the selected provider and emits/acks `translation_result`; it never receives audio through this event.

Client payload:

```json
{"segment_ids":["local-1"],"source_language":"en","target_language":"zh","mode":"fast","provider":"google"}
```

Response:

```json
{"status":"success","provider":"google","mode":"fast","model":null,"translations":[{"segment_id":"local-1","target_language":"zh","text":"……","status":"ready","mode":"fast","provider":"google","model":null}]}
```

`mode` is `fast`, `model`, `precise` or `auto`. Fast mode accepts `google` or `microsoft`; model mode accepts `local`, `cloud` or `auto`. If the selected provider is not configured, the response contains `TRANSLATION_NOT_CONFIGURED` and the original caption remains usable.

### `POST /api/translate`

Used by the review page for bounded after-class batches. The server accepts at most 25 segments and a bounded total text size, returns results in the same order, and does not persist the submitted text server-side.

```json
{"session_id":"session-…","segments":[{"id":"local-1","text":"Today we discuss…"}],"source_language":"en","target_language":"zh","mode":"fast","provider":"microsoft"}
```

## Cloud provider boundary

The cloud provider sends each normalized audio window as an in-memory WAV `POST` to `${CLOUD_BASE_URL}/audio/transcriptions`, with `Authorization: Bearer <CLOUD_API_KEY>`, the configured model and language. Provider adapters can be replaced without changing the browser contract.

## Translation provider boundary

Google fast translation first uses the no-key public web path when official credentials are absent; setting `TRANSLATION_GOOGLE_PROJECT_ID`, `TRANSLATION_GOOGLE_API_KEY` and `TRANSLATION_GOOGLE_LOCATION` enables the official Cloud Translation path. Microsoft fast translation uses `TRANSLATION_MICROSOFT_ENDPOINT`, `TRANSLATION_MICROSOFT_API_KEY` and the optional region. Precise local translation uses the OpenAI-compatible `TRANSLATION_LOCAL_BASE_URL` and `TRANSLATION_LOCAL_MODEL` (local API key is optional); precise cloud translation uses `TRANSLATION_CLOUD_BASE_URL`, `TRANSLATION_CLOUD_API_KEY` and `TRANSLATION_CLOUD_MODEL`. All keys are backend-only.
