# Caption latency

Why live captions used to trail the speaker, what was measured, and how to tune it. Every number
below comes from a real run on this machine (Apple Silicon, MLX Parakeet TDT v3) and from a real
127.3 s classroom recording, not from estimates.

## The default live path: a window that ends at the live edge

The shipped live decoder is `MLX_LIVE_MODE=windowed`. It keeps the last `MLX_WINDOW_SECONDS`
(default 18) of audio and re-decodes that entire window every `MLX_HOP_SECONDS` (default 2),
always ending the window at the live edge. The window starts shorter than that and grows into it,
so the first caption of a session does not wait for a full window of audio (see cold start below).

The window exists to buy **left context** — the model sees the run-up to the sentence it is
deciding, which is what makes the output read like a sentence. Because the window ends at the live
edge, its length costs no latency: the caption appears as soon as a window containing it has been
decoded. Latency is `decode time + hop`, not `window length`.

Measured over the socket API on the real lecture (127.3 s of accented English, pushed at realtime):

| Metric | Value |
| --- | --- |
| Cold start (the session's first caption) | **8.3 s** — was ~18 s before the window grew |
| Wait before a caption appears (median) | **0.6 s** — text lands at the live edge |
| Wait before a caption appears (worst) | 1.5 s |
| Lag of the final wording (median) | 0.9 s |
| Captions the reader ends up with | 10 |
| In-place revisions behind those | 37 |
| Captions still overlapping another | 0 |
| Phrases the transcript says twice | 4 |
| Decode pace | **0.24–0.29× realtime** (≈3.4× headroom) |
| Dropped audio | 0.00 s |
| Errors | none |

These are from the final verified run. Repeated runs move a little — 10–12 captions, 28–41 revisions
behind them — and the movement is not sampling noise: repeated runs over the *same* window schedule
return byte-identical text (three runs each of four window lengths gave identical caption counts,
word counts and scores). What changes between live replays is where the delivery boundaries fall,
because the audio worker resizes its delivery when it falls behind — and a different boundary is a
different window. The *timing* is stable across all of it, which is the part the architecture
controls.

Two numbers here are easy to confuse, so both are reported. The **wait before a caption appears** is
what the reader feels: the first time text for that stretch of audio reaches the screen. The **lag
of the final wording** is when that caption was last corrected, and it is larger by construction —
a sentence that ended early in the window is republished, with better wording, when the window
containing it is decoded. Its worst case tracks the window length (7.3 s observed on the final run,
19.2 s on earlier ones), but nothing appears on screen for the first time at that point.

### Cold start: the window starts short and grows

`push()` used to emit nothing until a full `MLX_WINDOW_SECONDS` had accumulated, so a session showed
no caption for the first ~18 seconds. Nothing about the design required that wait: a window that
ends at the live edge *is* "everything heard so far, capped at the window length", so it starts
short and grows into its full length by itself. The gate in `push()` was the only thing forcing it
to wait, and it only has to be long enough for the first decode to publish something.

The gate is now `head guard + hop` (6 s with the defaults). Measured on the real lecture, over the
first 26 s of audio:

| Gate (first decode at) | Cold start | Decode time for those 26 s |
| --- | --- | --- |
| **6 s (current)** | **8.0 s** | 7.3 s |
| 8 s | 8.0 s | 6.1 s |
| 10 s | 10.0 s | 5.6 s |
| 12 s | 12.0 s | 5.3 s |
| 18 s (old behaviour) | 18.0 s | 3.5 s |

**The gate does not set the cold start — the head guard does.** With a 6 s window the guard leaves
only 2 s of publishable audio (offsets 4–6 s), which is too little for the model to form a sentence
there, so that decode publishes nothing and the first caption comes from the 8 s window. Measured in
audio seconds, which are wall seconds at realtime pace. The table above is the provider's own
timing; the end-to-end replay in the summary table measures **8.3 s**, the same figure plus the
decode. 6 s is kept as the gate over 8 s because it is strictly more responsive — it also catches a
lecture whose first sentence happens to land early — and the extra decode costs well under a second.

The opening is not more expensive per second than the rest of the session: 7.3 s of decode for 26 s
of audio is 0.28× realtime, the same as the steady state, because the window is short exactly while
the decodes are cheap.

A first attempt used a doubling schedule (6 → 12 → 18 s) instead. It measured a **12.0 s** cold
start — worse than the simple rule above — because the 6 s decode publishes nothing and the next
attempt then has to wait until 12 s. Growth by the hop is both simpler and better; the reason is
that the doubling was fixing a problem the window already solved by itself.

The head guard (default 4 s) drops sentences that *start* inside the first few seconds of the
window. Those are the ones the model has no context for, and they are exactly the garbled
openings. The sentence that follows them is decoded again, in full, by the next window — so
nothing is lost, it just arrives with its opening intact.

One case the guard cannot rescue is the first few seconds of a session: they sit inside the guard
of every window, and a window cannot slide left of zero, so that opening is never published. The
loss is bounded by the guard and happens once per session; the alternative is showing the garbled
opening the guard exists to hide.

`MLX_WINDOW_SECONDS=4` is the enforced floor, but the useful floor is much higher. Windows of 10 s
or less return **empty output** across quiet stretches and garble the opening of every window,
because there is no left context to work from. Only windows of roughly 12 s and up stay readable,
which is why the default is 18.

### Window length: what the sweep shows, and how it misleads

The window ends at the live edge, so its length costs nothing per caption. Before the growth above
it cost the cold start; now it is nearly free, so a longer window would be an easy accuracy win —
if longer windows actually gave better text. They do not, and the way the measurement misleads is
worth recording so it is not repeated.

Windows 12/18/24/30 s, the real provider and the real merger over the real recording, scored
against the full-file batch decode (the same path the refine pass uses) as a reference:

| Window | Words | Captions | 5-grams said twice | Similarity to refine | Cold start, before growth |
| --- | --- | --- | --- | --- | --- |
| 12 s | 187 | 12 | 0 | 0.456 | 12 s |
| **18 s (default)** | **217** | **11** | **4** | **0.556** | **18 s** |
| 24 s | 172 | 11 | 0 | 0.500 | 24 s |
| 30 s | 244 | 11 | 10 | **0.609** | 30 s |
| refine (reference) | 206 | — | 0 | — | — |

Read the similarity column alone and 30 s wins — which is what a first pass concluded, and it is
wrong. The transcript shows why: 30 s scores highest because it says two of the reference's
sentences **twice** ("and when we take the explicit … similarly we can write" appears at two
different timestamps), and a sentence said twice matches more of the reference's words. Its 244
words against the reference's 206 are that duplication, not extra coverage. Similarity cannot tell
"says more" from "says the same thing twice", so it must not be used on its own to tune this.

18 s stays the default. It is the only window whose word count lands on the reference's, and the
shorter windows lose content instead — 12 s drops clauses (187 words), 24 s drops the most of all
(172). Duplication and omission trade against each other across the range, 18 s sits between them,
and the spread is small next to how much the model's wording moves from sentence to sentence. That
is not enough to retune a shipped default on a single recording.

Counting repeated phrases rather than scoring similarity is what made this visible, so
`tools/replay_lecture.py` reports it: it needs no reference transcript and it catches the failure a
reader actually notices.

### Decode pace is the number to watch

Each session logs its own pace:

```
会话结束 sid=... 状态=success 确认字幕=10 段 静音跳过=0.00s 丢弃=0.00s 实时倍率=0.24x
```

`实时倍率` is cumulative decode time over cumulative audio time. Below 1.0 the worker keeps up;
above it the caption drifts and the audio queue grows. A windowed provider that warns
`解码偏慢` is genuinely in trouble — the check is deliberately cumulative rather than per call,
because dividing one 18 s window decode by the 1 s chunk that happened to trigger it reports
"0.74×" for a path that is really running 4× faster than realtime.

### Does it hold up for a whole lecture?

The average pace is not the question. The live path decodes one window every `MLX_HOP_SECONDS` (2 s),
so **a single window slower than the hop is a caption arriving late**, however comfortable the
average looks. Measured over 10.6 minutes (the real recording tiled five times, 317 decodes):

| | |
| --- | --- |
| Decode wall time per window | median 0.62 s, p95 0.84 s, **max 1.28 s** |
| Windows slower than the 2 s hop | **0 of 317** |
| Peak RSS over the run | 938 MB → 949 MB (flat — no leak) |
| Cumulative pace | 0.31× realtime |

Nothing drifts: the per-tenth medians wander between 0.45 s and 0.76 s with no upward trend, so
there is no thermal or memory creep over ten minutes.

**The same run under memory pressure is a different story, and this is the failure mode to know
about.** With a second copy of the model resident and the machine swapping 12 GB of its 13 GB, the
same code over the same audio gave 0.39× realtime with **21 of 317 windows over the hop, worst
4.6 s** — captions visibly lagging the speaker. The model needs roughly 2.3 GB; nothing in the app
compensates for being paged out. `TROUBLESHOOTING.md` leads with this check for a reason.

## The streaming decoder is opt-in, and it is not usable for lectures

`MLX_LIVE_MODE=streaming` selects the original `transcribe_stream()` path. It is kept because it is
the only decoder that can emit *draft* tokens, which the caption feed can show before a sentence
settles.

It should not be used for lectures. On the same 127.3 s recording:

| Path | Output | Time for 127.3 s |
| --- | --- | --- |
| Batch `transcribe()` | readable | 8.8 s |
| `transcribe_stream()` | word salad | 62–69 s |

Two independent problems. The text is unusable at both `right_context=16` and `=32`, so
`MLX_STREAM_RIGHT_CONTEXT` was never the cause; and the decoder runs at roughly **1.5× realtime**
(1.50 s of decode per 1.0 s of audio, logged as `解码偏慢 ... 倍率=1.50x 队列=7`), so it falls
further behind the longer the lecture runs.

A variant sweep over a 40 s excerpt isolated the reason — `keep_original_attention=False` swaps the
encoder to local attention, and `depth=1` then keeps only one of its 24 layers global:

| Variant | Time | Output |
| --- | --- | --- |
| batch `transcribe()` | 2.3 s | readable |
| `depth=1`, local attention, rc=32 | 21.1 s | garbage |
| `depth=1`, **original** attention, rc=32 | 12.4 s | semi-readable |
| `depth=4`, local attention | 31.6 s | garbage |
| `depth=1`, rc=64 | 42.3 s | garbage |
| `depth=24` | 61.3 s | empty |

The best streaming configuration is still both slower and worse than plain batch decoding on a
window, which is why the windowed path is the default and `PARAKEET_KEEP_ORIGINAL_ATTENTION`
defaults to `True` for anyone who opts back into streaming.

### Background: the 0.08 s conversion

The streaming decoder keeps `context_size = (left, right)` and computes:

```python
finalized_length = max(0, length - drop_size)   # drop_size = context_size[1] * depth
```

so the last `right` encoder frames can never be confirmed. One encoder frame is

```
subsampling(8) × hop_length(160) / 16000 Hz = 0.08 seconds
```

The old hardcoded `context_size=(256, 64)` therefore held every caption back by **5.12 seconds**.
With audio shorter than that, no confirmed caption appeared until the session stopped and the
provider was flushed.

| Right context | First confirmed token | Lag (`right × 0.08`) |
| --- | --- | --- |
| 64 (old) | t = 6.0 s | 5.12 s |
| 16 | t = 2.0 s | 1.28 s |

This only applies to `MLX_LIVE_MODE=streaming`; the windowed path has no right-context lag.

### Background: CPU cost of smaller delivery chunks

24.2 s of speech, right context 32, streaming path:

| `STREAMING_CHUNK_SECONDS` | Total | Realtime ratio |
| --- | --- | --- |
| 3.0 | 3.69 s | 0.15× |
| 1.0 | 8.92 s | 0.37× |
| 0.5 | 17.17 s | 0.71× |

Each inference step has a fixed cost of roughly 0.35–0.41 s because the retained mel buffer is
re-encoded, independent of chunk size. The windowed path does not re-encode a retained buffer per
step, which is where most of its headroom comes from.

## Tuning

```dotenv
MLX_LIVE_MODE=windowed        # windowed (default) | streaming
MLX_WINDOW_SECONDS=18.0       # left context; 4.0 is the floor, ~12 is the practical floor
MLX_HOP_SECONDS=2.0           # how often a caption may update; the main latency lever
MLX_STREAM_RIGHT_CONTEXT=32   # streaming path only; ignored when windowed
STREAMING_CHUNK_SECONDS=1.0   # streaming providers only
```

Lowering `MLX_HOP_SECONDS` makes captions update more often at a linear CPU cost. It does not make
the *first* caption appear any sooner: the cold start is set by the head guard and the audio the
model needs to form a sentence, not by the gate or the window (see the table above — a gate of 6 s
and of 8 s both land at 8.0 s). `MLX_WINDOW_SECONDS` is the readability knob, and because the
window grows into it, raising it no longer delays the first caption. Read the sweep above before
changing either: longer is not better here.

The active configuration is reported through `/api/capabilities` under `audio.live`:

| Field | Meaning |
| --- | --- |
| `audio.live.mode` | `windowed` or `streaming` — which decoder is running |
| `audio.live.window_seconds` | the live window length |
| `audio.live.hop_seconds` | how often a caption may update |
| `audio.live.confirmation_lag_seconds` | held-back audio: **0** when windowed, `right × 0.08` when streaming |

`audio.streaming_lag_seconds` and `audio.streaming_chunk_seconds` describe the streaming decoder
only. `streaming_lag_seconds` is non-zero even when the windowed path is running, so read it
together with `audio.live.mode` rather than as the delay of the current session.

The MLX worker also adapts delivery size when it falls behind: it grows from the configured base in
0.25-second steps up to 1.5 seconds when the audio queue reaches four chunks or an inference call
consumes almost the whole delivery interval, and returns to the base when the queue is empty.

## Why review has a separate fine pass

The live path is optimized for captions that arrive while someone is still speaking, so it decodes
a window rather than the lecture and it cannot see past the live edge. The review page keeps that
real-time result and offers a separate batch pass over the saved original audio. Parakeet processes
up to 10 minutes at a time with 15 seconds of overlap, which improves sentence boundaries and
terminology consistency without making live delivery wait. The refined result is stored separately
and can be compared, edited, translated, or exported without destroying the in-class record.

The refinement job shares the process-local MLX model cache with live transcription. The cache
lease is exclusive: if a live session is still using Parakeet, the post-class job waits for the
lease rather than loading a second copy. This keeps memory reasonable on a 16 GB MacBook Air, at
the cost of serializing concurrent MLX work. `ProviderFactory` keeps the loaded weights in that
cache, so sequential sessions skip the model reload — worth roughly 0.7 s per session.

## How to re-measure

The most useful measurement is a real recording, replayed at realtime pace. This is what produced
every number in the table above:

```bash
# 16 kHz mono extraction of a lecture recording
ffmpeg -i lecture.webm -ac 1 -ar 16000 -vn /tmp/rt_class.wav

# Replays it over the socket API and reports every caption plus its latency.
no_proxy='*' .venv/bin/python tools/replay_lecture.py /tmp/rt_class.wav
```

Then read the same run back out of the log, which is the artefact a bad transcript gets judged on:

```bash
grep "确认字幕" logs/realtime-transcript.log      # id= 新增/更新 起始-结束ms 文本
grep "会话结束" logs/realtime-transcript.log      # 段数、丢弃、实时倍率
```

`id=` plus `新增`/`更新` is what makes the log diagnosable: the merger reuses the id when it
replaces an earlier decode, so without them you cannot tell "the caption was corrected in place"
from "the feed is full of near-duplicates".

The replay prints three sections, and they answer different questions. `=== captions ===` is what
the reader ends up seeing — it applies `transcript_segment_removed` too, so a caption the server
retracted is gone from it, and the header reports how many were retracted. `=== readability ===`
counts captions that still overlap another and phrases the transcript says twice — neither needs a
reference transcript, and both are what a reader actually notices. `=== latency ===` reports the
cold start (wall-clock seconds until the first caption of all), the wait before each caption
appears, and the lag of the final wording. Do not read the last two as the same number; see the
note above the latency table.

For synthetic clips and the streaming path:

```bash
# End-to-end caption latency on a controlled clip; name each run so output stays readable.
no_proxy='*' RTT_AUDIO=speech.wav RTT_LABEL="window=18" \
  .venv/bin/python tools/measure_caption_latency.py

# Streaming path only: confirms the right x 0.08s rule without the server or the browser.
.venv/bin/python tools/measure_finalize_lag.py 32 speech.wav

# Caption-to-Chinese latency, varying the browser queue's batching.
no_proxy='*' RTT_BATCH=2 RTT_DEBOUNCE=150 \
  .venv/bin/python tools/measure_translation_latency.py
```

Prepare a clip with clear sentence breaks:

```bash
say -v Samantha -r 170 -o /tmp/speech.aiff "First sentence. Second sentence. Third sentence."
afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/speech.aiff speech.wav
```

Use audio of **at least 13 seconds with several sentences**. A 5-second clip produces no confirmed
caption at all, which makes an A/B look identical when it is not. Do not start the server with a
bare `command &` either — the process dies with the shell.
