# Caption latency

Why live captions used to trail the speaker by several seconds, what was measured, and how to
tune it. All numbers below are from a real run on this machine (Apple Silicon, MLX Parakeet
TDT v3), not estimates.

## Where the delay lives

A word goes through four stages before it is readable on screen:

| Stage | Cost | Configurable? |
| --- | --- | --- |
| Audio buffered until a delivery chunk is full | up to `STREAMING_CHUNK_SECONDS` | yes |
| Streaming model refuses to confirm the last `right context` frames | `MLX_STREAM_RIGHT_CONTEXT × 0.08s` | yes |
| Sentence must reach punctuation or the word cap | up to `max_words_per_segment` words | in code |
| Translation queues, then calls the provider | `debounceMs` + round trip | yes |

## The 0.08s conversion

Parakeet's streaming decoder keeps `context_size = (left, right)` and computes:

```python
finalized_length = max(0, length - drop_size)   # drop_size = context_size[1] * depth
```

so the last `right` encoder frames can never be confirmed. One encoder frame is

```
subsampling(8) × hop_length(160) / 16000 Hz = 0.08 seconds
```

The previous hardcoded `context_size=(256, 64)` therefore held every caption back by
**5.12 seconds**. With audio shorter than that, no confirmed caption appeared at all until the
session was stopped and the provider was flushed.

## Measured confirmation lag

19.35s of speech pushed in 1-second blocks into the stream directly:

| Right context | First confirmed token | Lag (`right × 0.08`) | Cumulative tokens |
| --- | --- | --- | --- |
| 64 (old) | t = 6.0s | 5.12s | 59 |
| 16 | t = 2.0s | 1.28s | 67 |

Accuracy on a single 4.84s sample (character similarity, not a rigorous WER):

| Right context | 32 | 16 | 8 |
| --- | --- | --- | --- |
| Similarity | 0.62 | 0.79 | 0.88 |

Too small a sample to be conclusive, but it shows 16 is not a cliff. The shipped default is
**32**, which halves the lag without betting the transcript on a 0.64s look-ahead.

## CPU cost of smaller delivery chunks

24.2s of speech, right context 32:

| `STREAMING_CHUNK_SECONDS` | Total | Realtime ratio | Per call |
| --- | --- | --- | --- |
| 3.0 | 3.69s | 0.15× | 410ms |
| 1.0 | 8.92s | 0.37× | 357ms |
| 0.5 | 17.17s | 0.71× | 350ms |

Each inference step has a fixed cost of roughly 0.35–0.41s because the retained mel buffer is
re-encoded, independent of chunk size. That is why 0.5s is not the default: at 0.82× realtime it
would leave almost no headroom on a loaded machine. At 1.0s there is a 2.4× margin.

## End-to-end before and after

Same 13.08s, five-sentence English recording, measured over the socket API with audio pushed in
realtime.

| | Old (`rc=64`, `chunk=3.0`) | New (`rc=32`, `chunk=1.0`) |
| --- | --- | --- |
| First caption on screen (draft) | 3.81s | **2.26s** |
| First confirmed caption | 12.54s | **8.32s** |
| Caption events | 7 | **16** |
| Confirmed segments | 3 | **4** |
| Post-speech catch-up | 0.00s | 0.00s |

The remaining 8.32s is dominated by "a whole sentence has to finish", not by the model: the
first sentence's period lands at roughly 3.4s of audio, plus the 2.56s look-ahead, plus up to
1s of delivery granularity.

Reproduced across five runs of the same clip, the shipped defaults settle at:

| Metric | Steady state |
| --- | --- |
| First caption on screen (draft) | 2.25–3.03s |
| First confirmed caption | 8.21–8.57s |
| Post-speech catch-up | 0.00s |
| Caption to Chinese | 2.28s |

The **first run after starting the server is always slower** (up to ~4.2s for the first draft)
because the Metal kernels are compiled on first use. Discard it when comparing configurations.
Model loading itself is not part of this measurement: `transcription_ready` is only emitted
after the model is loaded.

Translation, measured from confirmed caption to Chinese on screen: **2.28s**, most of which is
the translation provider's own round trip. The queue contributes only `debounceMs`.

### Which provider `fast` + `auto` picks

Candidates are ordered so that a **best-effort** provider never outranks a configured one.
`GoogleTranslationProvider.best_effort` is true when no API key is set, because the keyless
`translate_a/single` web endpoint is a convenience that can start answering with a consent page
instead of JSON at any time. When it does, it raises `TRANSLATION_PUBLIC_UNAVAILABLE` and the
router moves on to the next provider rather than failing the batch.

Naming a provider explicitly (`provider=microsoft`) skips the reordering entirely — an explicit
choice is a decision, not a hint.

## Tuning

```dotenv
MLX_STREAM_RIGHT_CONTEXT=32    # only for the MLX/Parakeet streaming path
STREAMING_CHUNK_SECONDS=1.0    # streaming providers only; windowed ones keep AUDIO_WINDOW_SECONDS
```

Both are reported through `/api/capabilities` as `audio.streaming_chunk_seconds` and
`audio.streaming_lag_seconds` so the UI can show the current trade-off instead of guessing.

Going lower than 16 is a real accuracy risk on lecture vocabulary. If captions still feel slow
at 16, the next lever is not the model but `max_words_per_segment` in
`backend/providers/mlx_parakeet.py`, which decides how long a run-on sentence may delay its own
translation.

## How to re-measure

Prepare a wav with clear sentence breaks:

```bash
say -v Samantha -r 170 -o /tmp/speech.aiff "First sentence. Second sentence. Third sentence."
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/speech.aiff speech.wav
```

Then, from the repository root against a live server:

```bash
# Confirms the right x 0.08s rule without the server or the browser.
.venv/bin/python tools/measure_finalize_lag.py 32 speech.wav

# End-to-end caption latency; name each run so the output stays readable.
no_proxy='*' RTT_AUDIO=speech.wav RTT_LABEL="rc=32 chunk=1.0" \
  .venv/bin/python tools/measure_caption_latency.py

# Caption-to-Chinese latency, with the browser queue's batching varied.
no_proxy='*' RTT_BATCH=2 RTT_DEBOUNCE=150 \
  .venv/bin/python tools/measure_translation_latency.py
```

Use audio of **at least 13 seconds with several sentences**. A 5-second clip produces no
confirmed caption at all under any right context, which makes an A/B look identical when it
is not. Do not start the server with a bare `command &` either — the process dies with the
shell. Use a terminal or a task runner that keeps it alive.
