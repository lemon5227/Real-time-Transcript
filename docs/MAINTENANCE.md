# Maintenance handbook

For whoever debugs this project next — including future me. `TROUBLESHOOTING.md` covers what an
*operator* sees. This file covers what a *developer* needs: how to reproduce a problem, which
traps in this environment waste an hour if you don't know about them, and where the sharp edges
in the code are.

Read [`CHANGELOG.md`](../CHANGELOG.md) for what changed and why, and
[`docs/LATENCY.md`](LATENCY.md) for latency numbers.

## First five minutes

```bash
# Is it running? Note --noproxy, see trap 3.
curl -s --noproxy '*' http://127.0.0.1:5001/api/health

# What is it actually configured with? Do not read .env and assume — ask the app.
curl -s --noproxy '*' http://127.0.0.1:5001/api/capabilities | python3 -m json.tool

# Tests and lint.
.venv/bin/python -m pytest -q --basetemp=/tmp/rtt-pytest
.venv/bin/python -m ruff check .
```

`/api/capabilities` is the source of truth for effective configuration. It reports
`audio.streaming_chunk_seconds`, `audio.streaming_lag_seconds`, which cloud provider is
configured, and the startup timeout.

## Symptoms → cause → action

### Captions lag the speaker by several seconds

Check the confirmation lag the model is running with — `audio.streaming_lag_seconds` from
`/api/capabilities`. It is `MLX_STREAM_RIGHT_CONTEXT × 0.08`. It used to be hardcoded to 64,
which meant **5.12 seconds** of held-back audio and, for clips shorter than that, no confirmed
caption at all until the session stopped.

Lower `MLX_STREAM_RIGHT_CONTEXT` in `.env` and restart. Below ~8 the model loses too much right
context to decode with; the next lever after that is `max_words_per_segment` in
`backend/providers/mlx_parakeet.py`. See [`LATENCY.md`](LATENCY.md) for measured trade-offs.

Note what does *not* help: changing `AUDIO_WINDOW_SECONDS` moved the end-to-end confirmed
caption from 11.38s to 11.34s. Window size is not the bottleneck.

The MLX model is cached after the first session, so a second session in the same server process
should not download or reload the weights. The streaming decoder itself is still recreated for
every session. Do not remove the cache lease: `transcribe_stream().__enter__()` changes the shared
encoder attention mode, so concurrent streams must not use the same model object.

### Captions freeze and then jump in a block

The provider is being fed in oversized chunks. Streaming providers should use
`STREAMING_CHUNK_SECONDS`, not `AUDIO_WINDOW_SECONDS`. If you touched the branch in
`session_manager._run_session` that builds `AudioWindowBuffer`, make sure the
`requires_contiguous_audio` split is still there — routing the streaming chunk size into the
windowed path makes cloud and Whisper infer 3× more often for no benefit.

On MLX, the delivery buffer may grow from the configured base to 1.5 seconds when the inference
queue is falling behind, then shrink back after it recovers. This is intentional; a busy machine
should catch up while an idle machine keeps frequent draft updates.

### Translation reports a format error

Almost certainly not a format error. As of 2026-09 the keyless Google endpoint
(`translate_a/single`) returns **HTTP 200 with an HTML "Sorry…" consent page**. Probe it:

```bash
curl -s --noproxy '*' -G "https://translate.googleapis.com/translate_a/single" \
  --data-urlencode "client=gtx" --data-urlencode "sl=en" \
  --data-urlencode "tl=zh" --data-urlencode "dt=t" --data-urlencode "q=Hello" | head -c 200
```

HTML back means the provider is dead, not that parsing broke. Configure a real key, or rely on
Microsoft. `_looks_like_html()` now reports this as `TRANSLATION_PUBLIC_UNAVAILABLE` and the
router falls through to the next provider.

If a **configured** provider is being ignored, check `TranslationRouter.resolve_all()` order.
`best_effort` providers must sort last; an explicitly named provider is never reordered.

### A session ends but the model keeps producing captions

`provider.start()` blocks while the model loads. A stop that arrives during that window has to
be handled after `start()` returns — look for the early `return` guarded by `stop_event` in
`session_manager._run_session`. Removing it revives zombie workers that announce
`transcription_ready` for a session the client already closed.

Related: `provider.flush()` is called **only** on stop (`session_manager.py`, in the same worker).
Anything that depends on final output must be triggered there.

### Stop reports success but a segment never appears

Because flush happens on the worker thread, `stop()` can return before the tail is emitted. It
reports `status="stopping"` with a detail when the join times out. If you see "success" but a
missing final caption, check whether a recent change suppressed post-stop emits — a global
"don't emit after stop" guard once swallowed the final flush, which is the *legitimate* last few
seconds of captions. The correct fix targets only zombie workers, not all post-stop emissions.

### Tests fail with `PermissionError` on `pytest-of-unknown`

Sandbox artifact, not a code problem. Always pass `--basetemp=/tmp/rtt-pytest`.

## Environment traps

These have each cost real time. They are properties of this machine, not of the code.

1. **`grep` silently returns nothing** on some repo files (`backend/config.py`, `static/app.js`)
   — probably a binary-detection false positive. The command exits non-zero with no output, so it
   looks like "no matches". **Use the Grep tool, not `grep`.** This one has been hit twice.

2. **A server started with `command &` or `nohup &` dies with the shell.** It logs "listening",
   then the port is gone. Start it as a background task that outlives the shell, and verify with
   `lsof -ti :5001` rather than trusting the log.

3. **`curl` to localhost goes through Clash and fails** with `upstream connect failed`. Always
   pass `--noproxy '*'`. Python clients need `no_proxy='*'` (the measurement tools set it
   themselves).

4. **The first inference in a fresh process is much slower** — Metal kernels compile on first
   use. The first draft caption has measured 4.23s cold versus 2.25–3.03s warm on identical
   audio. `transcription_ready` is emitted *after* the model loads, so model loading is not part
   of caption latency — but kernel compilation is. **Discard the first run when comparing
   configurations.**

5. **Short test audio makes an A/B look identical when it is not.** A 4.84s clip produced no
   confirmed caption under *any* right context (the sentence's period lands past the end of the
   audio), so before and after fixes both measured 11.38s. Use **≥13s with several sentences**.
   Generate one:

   ```bash
   say -v Samantha -r 170 -o /tmp/speech.aiff \
     "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
   afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/speech.aiff speech.wav
   ```

6. **Writing a value into `.env` and observing the default proves nothing.** If they are equal,
   reading it tells you nothing. Verify by temporarily setting a *non-default* value, calling
   `load_config()` (which runs `load_dotenv()` when passed no argument), and checking you read
   the odd value back. `.env` is read **once at startup** — restart after editing.

7. **The `code 400, message Bad HTTP/0.9 request type` line in the server log is noise.** It is
   logged during the WebSocket upgrade and the upgrade still succeeds. Confirmed by forcing
   `transports=["websocket"]` and checking `client.transport() == "websocket"`. Do not chase
   `async_mode` because of it.

8. **A missing `websocket-client` silently degrades the client to HTTP long-polling.** The
   socket.io client only prints a one-line notice. It turned out not to matter much here
   (confirmed captions 8.57/8.37s on polling versus 8.22/8.30s on WebSocket) but it makes
   measurements pessimistic and it is easy to misread as a server problem.

9. **Do not write "（当前）" or similar markers into `config` comments.** They go stale the moment
   someone changes the value and then actively mislead.

## Sharp edges worth knowing before you edit

- **Streaming token timestamps are relative to the current sliding mel window**, not to the
  session. `finalized_tokens[-1].end` sits near 1.1s forever. `mlx_parakeet` therefore rebuilds
  positions from token *durations* plus an internal cursor. Do not "simplify" that into using
  `token.start` directly.

- **`stream.result.sentences` is only complete after the audio loop ends.** During streaming the
  live text is in `draft_tokens`, and `finalized_tokens` lags by the right context. Anything
  reading `sentences` for real-time output is wrong.

- **`TranslationSelection.provider_name` is the slot name** (`"google"`, `"microsoft"`), not the
  provider object's own `.name`. `translate_with_fallback()` returns the selection that actually
  served the request — report *that*, otherwise a fallback attributes the text to the wrong
  service.

- **`AudioWindowBuffer` is a delivery buffer for streaming providers, not a semantic window.**
  The overlap is forced to 0 for `requires_contiguous_audio` providers because their timeline
  must stay continuous. Do not "restore" overlap for them.

- **The dedup in `segment_merger` only helps windowed providers.** A streaming provider never
  re-decodes the same audio, so overlap-based dedup has nothing to do there. Whisper
  hallucinations are handled separately by the `no_speech_prob` filter, and they are *not*
  reachable by dedup when consecutive windows do not overlap.

## Reproducing latency numbers

`tools/` holds the three probes. All of them drive a live server over the socket API.

```bash
# Confirms the right x 0.08s rule without a server or browser.
.venv/bin/python tools/measure_finalize_lag.py 32 speech.wav

# End-to-end caption latency.
no_proxy='*' RTT_AUDIO=speech.wav RTT_LABEL="rc=32" \
  .venv/bin/python tools/measure_caption_latency.py

# Caption to Chinese, with the browser queue's batching reproduced.
no_proxy='*' RTT_BATCH=2 RTT_DEBOUNCE=150 \
  .venv/bin/python tools/measure_translation_latency.py
```

Report the median of several runs, throw away the first (trap 4), and compare only runs on the
same audio (trap 5).
