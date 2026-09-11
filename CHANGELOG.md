# Changelog

Notable changes to this project, newest first. Each entry records what broke, why, and how it
was verified — not just what was edited.

Related documents:

- [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) — how to diagnose these again, plus the traps in
  this project's dev environment.
- [`docs/LATENCY.md`](docs/LATENCY.md) — caption latency measurements and tuning knobs.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — symptoms an operator sees, in plain language.

## Current state — 2026-09-11

| | |
| --- | --- |
| Tests | **200 passed**, 25 test files |
| Lint | `ruff check .` clean |
| Git | Committed on `main` on top of `4ad73aa`: `5741b8d` (review fixes), `65d0476` (caption latency), `04a2e99` (translation fallback), plus this documentation commit |
| Working path | Apple Silicon + MLX Parakeet. `faster_whisper`/`whisper` are not installed, and no `CLOUD_*` is configured, so those paths are untested on this machine |
| Caption latency | Draft 2.5s / confirmed 7.3s with `MLX_STREAM_RIGHT_CONTEXT=16` |
| Translation | Microsoft configured and working. Google has **no API key**, and its keyless public endpoint is dead — the router now skips it |

Quick health check:

```bash
.venv/bin/python -m pytest -q --basetemp=/tmp/rtt-pytest   # tests
.venv/bin/python -m ruff check .                            # lint
curl -s --noproxy '*' http://127.0.0.1:5001/api/capabilities | python3 -m json.tool
```

## 2026-09-11 — caption latency and translation availability

### Fixed: confirmed captions were held back by a hardcoded 5.12s

`backend/providers/mlx_parakeet.py` passed `context_size=(256, 64)` to the Parakeet stream. The
streaming decoder computes `finalized_length = max(0, length - drop_size)` with
`drop_size = context_size[1] * depth`, so the last 64 encoder frames could never be confirmed.
One encoder frame is `8 * 160 / 16000 = 0.08s`, making the lag exactly **64 × 0.08 = 5.12s**.
Audio shorter than that produced **no confirmed caption at all** until the session was stopped
and the provider flushed.

The right context is now `MLX_STREAM_RIGHT_CONTEXT` (default 32, clamped 1..256). Measured on
13.08s of five-sentence English:

| Right context | First confirmed caption |
| --- | --- |
| 64 (old hardcoded) | 12.54s |
| 32 (default) | 8.21–8.57s |
| 16 (current `.env`) | 7.25–7.34s |

### Fixed: streaming providers were fed in 3-second blocks

`AudioWindowBuffer` was always sized by `AUDIO_WINDOW_SECONDS` (3.0s), so even the live draft
caption only refreshed once every three seconds. Streaming providers
(`requires_contiguous_audio=True`) now use `STREAMING_CHUNK_SECONDS` (default 1.0) while
windowed providers (cloud, Whisper) keep the window size — they really do infer once per window.

1.0 was chosen over 0.5 from measurement: each inference step costs a fixed ~0.4s because the
retained mel buffer is re-encoded, so 3.0s runs at 0.19× realtime, 1.0s at 0.42×, and 0.5s at
**0.82×** with no headroom left.

### Fixed: translation never used the configured provider

Reported symptom: every translation failed with
`TRANSLATION_INVALID_RESPONSE — Google 公共翻译返回格式无效`, even though a Microsoft key was
configured and working.

Three things stacked up:

1. The Google provider is registered **unconditionally** in `backend/__init__.py`, falling back
   to the undocumented keyless `translate_a/single` endpoint when no key is set. Microsoft is
   only registered when `TRANSLATION_MICROSOFT_API_KEY` is present.
2. `TranslationRouter.resolve()` hardcoded the `fast` + `auto` candidate order as
   `["google", "microsoft"]`, so the keyless public path always won.
3. That public endpoint now answers with **HTTP 200 and a "Sorry…" consent page** instead of
   JSON. `response.json()` raised, which was reported as "invalid response format" — sending
   anyone debugging it after a parser bug instead of after a dead endpoint.

Changes:

- `GoogleTranslationProvider.best_effort` is true when no key is set.
- `TranslationRouter._preference_order()` sorts best-effort providers **last**. An explicitly
  named provider is never reordered — an explicit choice is a decision, not a hint.
- `TranslationRouter.resolve_all()` returns the whole candidate chain; `resolve()` is now
  "take the first". New `translate_with_fallback()` tries each in turn and returns the provider
  that actually served the text, so the reported provider name stays truthful.
- `_looks_like_html()` classifies a consent page as `TRANSLATION_PUBLIC_UNAVAILABLE` with an
  actionable message instead of a format error.
- Both call sites (`POST /api/translate` and the `translate_segments` socket handler) use the
  chain.

Verified end to end: the same request now returns Chinese from `[microsoft]`, caption→Chinese in
2.28s.

### Changed: translation queue latency

`static/app.js` uses `batchSize: 2` and `debounceMs: 150` (was 5 and a hardcoded 350ms).
`max_words_per_segment` dropped 24 → 16 so a run-on sentence stops delaying its own translation.
`translation-queue.js` now takes `debounceMs` as an option instead of hardcoding it.

### Added

- `docs/LATENCY.md`, and `tools/measure_{caption,finalize,translation}_latency.py` to reproduce
  every number above against a live server.
- `CHANGELOG.md` (this file) and `docs/MAINTENANCE.md`, the developer debugging handbook. Two
  documentation-contract tests keep them cross-linked and keep `docs/LATENCY.md` from pointing at
  a renamed probe script.
- `/api/capabilities` now reports `audio.streaming_chunk_seconds` and
  `audio.streaming_lag_seconds`, so the UI can show the current trade-off.
- `.env.example` gained `AUDIO_STARTUP_TIMEOUT_SECONDS`, `STREAMING_CHUNK_SECONDS` and
  `MLX_STREAM_RIGHT_CONTEXT` — all three were read by `config.py` but documented nowhere.
- `requirements-dev.txt` gained `websocket-client`. Without it the socket.io client silently
  falls back to HTTP long-polling, which makes every measurement pessimistic.

## 2026-09-10 — logic and implementation review

Seven issues found by reading the project end to end. Tests went 158 → 177.

| # | Problem | Root cause | Fix |
| --- | --- | --- | --- |
| 1 | Overlap dedup never triggered | Threshold was a fixed 30% overlap ratio, but the shipped 3.0s window / 0.5s overlap is only 16.7%. Tests only covered a 50% overlap | Rewrote `segment_merger.py`: absolute `MIN_DUPLICATE_OVERLAP_MS=100`, containment handling gated on duration similarity, compare only the last 8 segments |
| 2 | Cloud mode never saved money on silence | Silent windows were zeroed but still sent as a WAV request | `voice_gate.filter()` returns `(audio, is_silent)`; windowed providers now skip silent windows, streaming providers still receive zeros to keep their timeline intact |
| 3 | Stop could return success while the worker kept running | `stop()` did not check whether `provider.start()` had finished | Worker returns early if the stop flag is set after `start()`; `stop()` reports `status="stopping"` on join timeout; a `_closing` map prevents loading two models at once |
| 4 | `AUDIO_STARTUP_TIMEOUT_SECONDS` was a dead setting | Assigned in `session_manager` but never read; the real limit was a hardcoded 30 in the frontend | Added `_check_startup_timeout()` driven by arriving audio (the worker is blocked inside `start()`); value now reaches the browser through `/api/capabilities` |
| 5 | socket.io accepted any origin | Flask-CORS had an allowlist, `socketio.init_app` had `"*"` | Both now use `config.cors_origins`; `docker-compose.yml` and Render's `RENDER_EXTERNAL_URL` feed the same list |
| 6 | Resampling aliased | Decimation had no anti-alias filter; 20kHz folded back to ~4kHz | Windowed-sinc low-pass (`_antialias_taps`, 65 taps, `lru_cache`) applied before decimation |
| 7 | Cloud mode ignored the glossary; Whisper hallucinated | Cloud requests sent no `prompt`; Whisper invented text for silent windows | Cloud provider sends the glossary prompt; segments with `no_speech_prob > 0.85` are dropped |

Not a bug, verified while reviewing: `audio-storage.getPlayableBlob()` concatenating MediaRecorder
chunks into one Blob is correct per spec — all chunks from one recorder instance are playable
when concatenated.
