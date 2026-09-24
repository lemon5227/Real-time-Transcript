# Changelog

Notable changes to this project, newest first. Each entry records what broke, why, and how it
was verified — not just what was edited.

Related documents:

- [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) — how to diagnose these again, plus the traps in
  this project's dev environment.
- [`docs/LATENCY.md`](docs/LATENCY.md) — caption latency measurements and tuning knobs.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — symptoms an operator sees, in plain language.

## Current state — 2026-09-24

| | |
| --- | --- |
| Tests | **309 passed**, 30 test files — counted in a clean venv built from `requirements-dev.txt` alone (CI's environment, not this machine's `.venv`) |
| Lint | `ruff check .` clean. The vendored Swift checkouts under `native/` carry ~1,000 third-party Python files; `.gitignore` now excludes SPM build trees so they are neither linted nor committed |
| CI | Green since `08dd17b` (2026-09-24). It had **never** passed — see the entry below |
| Git | `main`, pushed to `origin`. The 2026-09-24 diarization and download-progress work below is in the commits — see `git log` |
| Working path | Apple Silicon + MLX Parakeet. `faster_whisper`/`whisper` are not installed, and no `CLOUD_*` is configured, so those paths are untested on this machine |
| Caption latency | Windowed batch decoding: first caption after ~8s, then captions land at the live edge (median ≈0 s), 0.24–0.31× realtime |
| Long run | 10.6 min soak: **0 of 317** window decodes over the 2 s hop, RSS flat. Needs ~2.3 GB of free memory — swapping puts windows over the hop |
| Caption quality | 10 captions for the 127.3s lecture, none overlapping another, 4 phrases said twice |
| Translation | Microsoft configured and working. Google has **no API key**, and its keyless public endpoint is dead — the router now skips it |

Quick health check:

```bash
.venv/bin/python -m pytest -q --basetemp=/tmp/rtt-pytest   # tests
.venv/bin/python -m ruff check .                            # lint
curl -s --noproxy '*' http://127.0.0.1:5001/api/capabilities | python3 -m json.tool
```

## 2026-09-24 — CI had never been green, and the first download sat at 5%

### Fixed: every CI run in the repository's history failed
The first four runs of `ci.yml` — including the run on the commit that added it — died with
`ModuleNotFoundError: No module named 'requests'` in 12 tests. CI installs only
`requirements-dev.txt`, which had no `requests` in it. The local `.venv` has `requests`
transitively (via `requirements-mac.txt` → `parakeet-mlx`), so `pytest -q` passed on every
machine that mattered and the gap stayed invisible for the whole life of the workflow.

Fix: `requests>=2.31,<3` added to `requirements-dev.txt`. Guarded against a repeat by
`test_every_module_the_suite_patches_is_installed_for_development`, which scans the test
sources for `monkeypatch.setattr("module.attr", ...)` targets and asserts each third-party
root module imports. It failed exactly as intended when fed the pre-fix requirements. The
`setup-node@v4` → `v6` bump in the same file cleared the Node 20 deprecation notice.

### Fixed: model download progress was pinned at 5% until the file finished
The ROADMAP's standing "首次下载期间 UI 进度长时间停在 5%" item. `hf_hub_download` has no
progress callback — the only in-progress hook is `tqdm_class` — so the worker set the state
to 5%, blocked for minutes pulling gigabytes, then jumped to 100%. The frontend was never
the problem: it already polls `/api/models` every 750ms while a download runs.

`ModelManager._byte_progress_sink` now hands Hugging Face a `tqdm` subclass that mirrors
bytes into the model state (weights own 5..99%; 100 still belongs to the caller that
verified the file on disk). Three traps shaped it, each pinned by a test:

* A **disabled** tqdm bar returns early from `update()` and never touches `self.n`
  (verified: `update(50)` left `n=7`). Byte accounting therefore lives on the instance, not
  the inherited counter.
* Hugging Face injects `disable` only for *its own* bar subclass — a custom class gets the
  vanilla default `False`, which would stream live bars into stderr, i.e. the DMG's server
  log. The sink forces `disable=True`.
* The Xet-backed path builds **two** bars from the same class (network bytes, then bytes
  written), so `_advance_download_state` refuses to let progress or byte counts move
  backwards.

`tqdm>=4.42.1,<5` joins `requirements-dev.txt` — same blind spot as `requests`: the suite
tests the reporting code, so it must be importable without the MLX extras.

Verified: 4 new tests in `tests/test_model_manager.py` drive the sink through the library's
real bar protocol; each fails when the fix is mutated away (sink removed, own-tally replaced
by `self.n`, `disable=True` dropped, backwards-guard flattened).

### Follow-ups landed the same day

* **Download failures now leave evidence.** `_download_runtime_model`'s blanket
  `except Exception` put only "check your network" on screen and nothing in the log; both
  download paths now write the traceback via `get_logger("models")`. The test collecting it
  attaches a handler to `realtime_transcript` directly — `caplog` captures at the *root*,
  and `configure_logging()` sets `propagate=False`, so a caplog-based test passes alone but
  fails in full-suite order once any earlier test has built the app.
* **`CLOUD_DEPENDENCY_MISSING` is covered.** The provider raises it with the fix in its
  action text (`requirements-cloud.txt`), but no test had ever forced `requests` to `None`.
  Two guards (`start` and `push`), one test.
* **`upload-artifact@v4` → `v6`** in `macos-dmg.yml`, matching the earlier `setup-node` bump;
  `test_deployment_files.py` pins the version, so it moved with the workflow.
* **The requirements guard test no longer needs Python 3.10.** It filtered stdlib names with
  `sys.stdlib_module_names`, which does not exist on 3.9 — the version README promises for
  cloud profiles. Import-checking every patched root is 3.9-safe and does the same job.

`runs-on: macos-14` was deliberately left alone: the image is supported until November, and
the Swift/CoreML build inside that workflow has never run on GitHub — moving runner images
could shift the Xcode version under a build nobody has verified end to end.

## 2026-09-22 — the vocabulary hint did nothing on the Mac path

Found while closing out the notes above: the settings panel promised something the shipped path
cannot do.

### Fixed: the 课程词汇 field promised a hint the MLX runtime cannot accept

The field said "识别前提示模型，识别后把明显拼错的词改回你写的写法" — that the model is primed with
the terms *and* that misspellings are corrected afterwards. The second half is true on every path.
The first is not true on the one this project ships: `Glossary.prompt` is read only by
`cloud_transcription.py` and `local_whisper.py`, and Parakeet's MLX runtime has no way to accept it —
`generate()`, `transcribe()` and `transcribe_stream()` take no prompt argument, because a transducer
is conditioned on audio alone.

So a student typing "Professor Müller, eigenvalue" into that field on the Mac path got the spelling
correction and **nothing else**, while the UI told them the model had been primed. That is worse than
no field at all: it invites a workaround that cannot work.

The hint text, the `glossary` module docstring and `docs/API.md` now name which providers do what,
and a test pins the set of providers allowed to read the hint — so wiring it into the MLX path, where
it would silently do nothing, fails the suite instead of shipping.

This also corrects the note in the entry below that listed `implicit` → "invisible" and
`social choice` → "social shock" as things the glossary should fix. It cannot and should not:
`correct_term()` requires the first letter to match and allows an edit budget of a fifth of the
term, so it repairs a misheard *spelling* (`gradien` → `gradient`) and never a word the model
substituted. Those two are model quality, and the vocabulary field is not the lever for them.

## 2026-09-22 — a retracted caption stayed on screen

Follow-on from the entry below, which left one defect open: two stored captions could end up
covering the same speech, and the pair was never compared.

### Fixed: the merger now compacts, and tells the client what to drop

`SegmentMerger.add()` only compared the *incoming* candidate. A stored caption still changes
underneath it when a later decode replaces it with a fuller span, and that growth can leave two
stored captions covering the same speech.

The mechanism is narrower than the note in the entry below described, and the fix follows from it.
`_find_duplicate()` scans the stored list **backwards and stops at the first match**, so a candidate
that matches a *later* stored caption is never compared with an earlier one — and the replacement
extends the matched caption to the hull of the two spans, which reaches **leftwards** into that
earlier caption. The earlier caption is the one that is never examined. On the real lecture this left
`mlx-w-17` (46640–56560 ms) and `mlx-w-21` (49360–59600 ms) both on screen, 73% of the shorter span
being the same speech.

`add()` now ends with a compaction pass over the tail. Real provider and real merger over the real
recording:

| Window | Compaction | Captions | Still overlapping |
| --- | --- | --- | --- |
| 18s | off | 11 | **1** |
| 18s | on | 10 | **0** |
| 30s | off | 11 | **2** |
| 30s | on | 8 | **0** |

At 30s it also absorbs stray fragments — `"Okay."` and `"X PC, sorry and"` were each stored beside
the sentence that already contained them — which is why that count drops by three.

### Added: `transcript_segment_removed`

Merging two stored captions means one of them has to leave the screen, and the socket protocol had
no way to say so: only `transcript_segment`, add-or-update by id. `SegmentMerger.add()` now returns a
`MergeOutcome` carrying `segments` **and** `removed_ids`, so a caller cannot take the captions and
silently ignore the deletions — the return type is what stops the duplicate coming back. The session
manager emits one `transcript_segment_removed` per dropped id and logs `撤回字幕`, with a `撤回`
count in the session summary; the live page deletes the row from both the feed and its stored
transcript; `tools/replay_lecture.py` applies the event too, so its readability numbers describe what
the reader keeps.

A guard that filtered a just-announced caption out of `removed_ids` was written and then **removed**:
a search over 300k random `add()` calls had compaction fire in 8.3% of them and never once announced
and retracted the same id. The invariant is structural — the loser of a merge is always the caption
with the smaller end, and a caption that a candidate just grew always ends at that candidate's end —
so it is recorded as a comment instead of shipped as unreachable code.

### Verified

`285 passed`, `ruff check .` clean. The three new merger tests were confirmed to **fail** with the
compaction pass disabled, so they guard the behaviour rather than describing it.

Replayed the real 127.3s lecture through the socket API at realtime pace: 10 captions, 0 still
overlapping another, cold start 8.4s, wait before a caption appears median 0.6s, no errors. That run
happened not to retract anything — its delivery boundaries did not produce the leftward growth above,
and the transcript needed no compaction. The retraction path is covered by the session-manager test
(the event and its payload) and by the offline comparison in the table, where compaction does fire.

## 2026-09-22 — the cold start, and why the window stays at 18s

Follow-on to the entry below, which left two questions open: how long a session stays silent before
its first caption, and whether a longer window would now be a free accuracy win.

### Fixed: the first caption waited for a full window

`push()` emitted nothing until `MLX_WINDOW_SECONDS` of audio had accumulated, so a session showed
no caption for its first ~18 seconds. Nothing in the design required that wait. A window that ends
at the live edge *is* "everything heard so far, capped at the window length", so it starts short and
grows into its full length by itself — the gate in `push()` was the only thing forcing it to wait.
The gate is now the first window, `head guard + hop`.

Measured on the real lecture over the first 26s of audio:

| Gate (first decode at) | Cold start | Decode time for those 26s |
| --- | --- | --- |
| **6s (current)** | **8.0s** | 7.3s |
| 8s | 8.0s | 6.1s |
| 10s | 10.0s | 5.6s |
| 18s (old behaviour) | 18.0s | 3.5s |

**The gate does not set the cold start — the head guard does.** A 6s window leaves only 2s of
publishable audio past the 4s guard, too little for the model to form a sentence there, so that
decode publishes nothing and the first caption comes from the 8s window. 6s is kept over 8s because
it is strictly more responsive — it also catches a lecture whose first sentence lands early — and
the extra decode costs well under a second. The opening runs at 0.28× realtime, the same as the
steady state, because the window is short exactly while decodes are cheap.

A first attempt used a doubling schedule (6 → 12 → 18s) and measured a **12.0s** cold start — worse
than the simple rule, because after the fruitless 6s decode the next attempt waits until 12s. It is
recorded in `docs/LATENCY.md` so the simpler rule is not "improved" back into a slower one.

### Measured: a longer window does not improve the text

Window length costs nothing per caption, and once the cold start stopped scaling with it a longer
window looked free. It is not. Windows 12/18/24/30s, the real provider and the real merger over the
real recording, against the full-file batch decode (the refine pass) as reference:

| Window | Words | 5-grams said twice | Similarity to refine | Cold start, before the fix |
| --- | --- | --- | --- | --- |
| 12s | 187 | 0 | 0.456 | 12s |
| **18s (default)** | **217** | **4** | **0.556** | **18s** |
| 24s | 172 | 0 | 0.500 | 24s |
| 30s | 244 | 10 | **0.609** | 30s |
| refine (reference) | 206 | 0 | — | — |

Read similarity alone and 30s wins, which is what the first pass concluded. The transcript says
otherwise: 30s scores highest because it says two of the reference's sentences **twice** ("and when
we take the explicit … similarly we can write" appears at two different timestamps), and a repeated
sentence matches more of the reference's words. Its 244 words against the reference's 206 are that
duplication, not coverage. Similarity cannot tell "says more" from "says the same thing twice".

18s stays the default: it is the only window whose word count lands on the reference's, and the
shorter windows lose content instead — 12s drops clauses, 24s drops the most of all. Duplication and
omission trade against each other across the range, so the spread is not enough to retune a shipped
default on one recording.

The model also turned out to be **deterministic for a fixed decode schedule**: three runs of each
window gave identical caption counts, word counts and scores. These are measurements rather than
averages, and the run-to-run variation seen in live replays comes from where the delivery
boundaries fall, not from sampling.

### Added: the replay tool reports readability and the cold start

`tools/replay_lecture.py` prints a `=== readability ===` section — captions that still overlap
another, and phrases the transcript says twice, neither of which needs a reference transcript — and
a `cold start` line. Counting repeated phrases is what exposed the 30s result, so it lives in the
tool rather than in a throwaway script. `covers_the_same_speech()` in `backend/segment_merger.py`
became public so the tool applies the merger's own rule instead of reimplementing it.

### Measured: it holds up for a whole lecture, unless the machine is swapping

The average pace is not what matters. The live path decodes one window every `MLX_HOP_SECONDS`
(2 s), so a single window slower than the hop is a caption arriving late however comfortable the
average looks. Over 10.6 minutes (the real recording tiled five times, 317 decodes):

| | |
| --- | --- |
| Decode wall time per window | median 0.62 s, p95 0.84 s, max **1.28 s** |
| Windows slower than the 2 s hop | **0 of 317** |
| Peak RSS across the run | 938 MB → 949 MB (flat, no leak) |
| Cumulative pace | 0.31× realtime |

The per-tenth medians wander between 0.45 s and 0.76 s with no upward trend, so nothing creeps over
ten minutes. The same run with a second copy of the model resident and the machine swapping 12 GB of
13 GB gave 0.39× realtime with **21 of 317 windows over the hop, worst 4.6 s** — captions visibly
lagging. That is an operating requirement, not a bug, so `TROUBLESHOOTING.md` now leads the
"captions stay behind" section with the swap check.

### Verified

Replayed the real 127.3s lecture through the socket API at realtime pace with the new provider:

| Metric | Value |
| --- | --- |
| Cold start (first caption of the session) | **8.3 s** (was ~18 s) |
| Wait before a caption appears | median 0.6 s, worst 1.5 s |
| Lag of the final wording | median 0.9 s, worst 7.3 s |
| Captions the reader ends up with | 10 (37 in-place revisions behind them) |
| Captions still overlapping another | 0 |
| Decode pace / dropped audio / errors | 0.29× realtime / 0.00 s / none |

`279 passed`, `ruff check .` clean. `docs/LATENCY.md` and the READMEs now carry the measured cold
start instead of the ~18s they promised, and `TROUBLESHOOTING.md` / `docs/MAINTENANCE.md` no longer
tell operators to lower `MLX_WINDOW_SECONDS` for a faster start — it does not affect the cold start,
and it was measured not to improve the text either.

## 2026-09-22 — live captions were unreadable on real lecture audio

A 127.3 s recording of an actual lecture (accented English, Opus 48 kHz, converted to 16 kHz mono)
produced a live transcript with no readable sentences, while the post-class batch pass over the
same audio was usable. The backend also logged **nothing**, so there was no log to check the
recording against. Five defects came out of it.

### Fixed: the backend logged nothing

`backend/logging_setup.py` is new. `app.py` configures a rotating file handler at
`logs/realtime-transcript.log` (5 MB × 3); importing the app for tests stays console-only.
`TRANSCRIPT_LOG_DIR` / `TRANSCRIPT_RUNTIME_DIR` move the location.

The session log records each confirmed caption verbatim with its **id** and whether it was new or a
revision. The id matters because the merger reuses it when it replaces an earlier decode; without
it, "the caption was corrected in place" is indistinguishable from "the feed is full of
near-duplicates" — the first thing worth knowing about a bad live transcript. Sessions also log a
summary carrying the decode pace:

```
会话结束 sid=... 状态=success 确认字幕=12 段 静音跳过=0.00s 丢弃=0.00s 实时倍率=0.24x
```

### Fixed: the MLX model could not load offline

`parakeet_mlx.from_pretrained()` calls `hf_hub_download` first and only falls back to a filesystem
path on exception, so a cached model failed to load whenever the network was unreachable
(`httpx.ProxyError: 502`) — and the generic `MLX_RUNTIME_UNAVAILABLE` message hid the cause.
`resolve_local_model_source()` now resolves the snapshot with
`snapshot_download(..., local_files_only=True)` before handing a path to `from_pretrained`, and
failures log the real exception. Loading from cache takes 1–2 s.

### Fixed: the streaming decoder was why live captions were unusable

`transcribe_stream()` with `keep_original_attention=False` swaps the encoder to local attention,
and `depth=1` then keeps only one of its 24 layers global. On the same 127.3 s recording:

| Path | Output | Time for 127.3 s |
| --- | --- | --- |
| Batch `transcribe()` | readable | 8.8 s |
| `transcribe_stream()` | word salad | 62–69 s |

The text was unusable at both `right_context=16` and `=32`, so `MLX_STREAM_RIGHT_CONTEXT` was never
the cause. The decoder also runs at roughly **1.5× realtime**, so it fell further behind the longer
the lecture ran. A variant sweep over a 40 s excerpt isolated the setting;
`PARAKEET_KEEP_ORIGINAL_ATTENTION` now defaults to `True` for anyone who opts back into streaming.

### Changed: the live path is a window decoded in batch

`backend/providers/mlx_windowed.py` is new and is the default (`MLX_LIVE_MODE=windowed`). It keeps
the last `MLX_WINDOW_SECONDS` (18) of audio and re-decodes the whole window every
`MLX_HOP_SECONDS` (2), always ending the window at the live edge. Window length buys left context
and costs no latency, which is the entire point: latency is `decode time + hop`, not window length.
A head guard (4 s) drops sentences that start inside the window's opening, where the model has no
context and produces exactly the garbled output that made the transcript unreadable.

Windows of 10 s or less were measured to return **empty output** across quiet stretches, so 18 is
the default and 4 the enforced floor. The first caption of a session waits for one full window
(~18 s) — superseded the same day by the entry above, which makes the window start short and grow.
`MLX_LIVE_MODE=streaming` keeps the old decoder available for anyone who wants draft tokens.

### Changed: `/api/capabilities` now says which live decoder is running

`audio.streaming_lag_seconds` is 1.28 s out of the box, and it was reported unconditionally — a
client reading only that field would expect the shipped configuration to lag by 1.28 s when the
windowed path has no such lag at all. A new `audio.live` block reports `mode`, `window_seconds`,
`hop_seconds` and `confirmation_lag_seconds` (0 when windowed). The old fields stay for the
streaming decoder.

### Fixed: near-duplicate captions piled up

The windowed path re-decodes a sentence as it grows, and `SegmentMerger`'s containment rule
required similar durations — which a growing re-decode violates. The first replay of the real
lecture ended with 41 captions for 127 s, most of them revisions of each other.

The merger now compares **the audio the two segments cover**: if their overlap covers 60% or more
of the shorter span they are the same utterance, and the fuller decode wins.

Coverage was chosen over text similarity because a later pass can reword half a sentence ("And now
I highlight that E, B, and C" against "But A, B, and C are the plane parameters" scores 0.70), and
over onset proximity because the model's word timings drift by a few hundred milliseconds between
passes — a 300 ms onset tolerance was splitting one sentence into two separate captions.

The threshold sits in the measured gap between the two populations. A first attempt used 75%,
which was set just above the duplicates rather than in the gap, and a pair at 73% — the same speech
with boundaries 2.7 s apart — survived as two overlapping captions. The populations on the real
lecture are far apart: every true re-decode covered ≥73% of the shorter span, while a lecturer
repeating a phrase, or starting a new sentence after "Okay", stayed at ≤20%.

### Fixed: the slow-decode warning measured the wrong thing

`_process_window()` divided one decode by the 1 s chunk that triggered it, so the windowed path
logged `解码偏慢 ... 倍率=0.74x` **every two seconds** — about 50 lines per session — for a path
that was really running 4× faster than realtime. The ratio is now cumulative over the session
(`实时倍率`), and the warning is rate-limited to one line per 10 s.

### Fixed: a flaky macOS launcher test, and the real bug behind it

`test_macos_launcher_reuses_the_existing_huggingface_cache` failed on roughly half of runs, a
different parametrisation each time. The cause was in `packaging/macos/launcher.sh`, not the test:
`wait_for_health()` anchored its deadline to `SECONDS` at shell start, so 1–2 s of launcher setup
silently ate a 2 s timeout, and polling every second meant the first check fired the instant the
child was spawned — a 2 s budget bought a single useful retry. The budget now starts at the call
and polls every 0.2 s, counting attempts rather than seconds because macOS ships bash 3.2 without
`EPOCHREALTIME`. Production was masked by the 90 s default.

### Verified

Replayed the real 127.3 s lecture through the socket API at realtime pace with
`tools/replay_lecture.py` (new):

| Metric | Value |
| --- | --- |
| Wait before a caption appears | median ≈0 s, worst 3.3 s |
| Lag of the final wording | median 0.6 s |
| Captions the reader ends up with | 12 (10–12 across runs), down from 41 |
| Decode pace | 0.24× realtime |
| Dropped audio / errors | 0.00 s / none |

`276 passed`, `ruff check .` clean. `docs/LATENCY.md` was rewritten — its claim that the streaming
path is "optimized for readable captions" was the opposite of what the recording showed — and
`tools/measure_finalize_lag.py` now uses the provider's attention setting instead of hardcoding the
one that produced word salad.

### Known remaining issues

- ~~**A stored caption can grow into a neighbour and stay duplicated.**~~ Fixed the same day — see
  the entry above. The mechanism turned out to be narrower than described here: `_find_duplicate`
  scans the stored list backwards and stops at the first match, so a candidate that matches a
  *later* caption is never compared with an earlier one, and the match extends the later caption
  leftwards into that earlier caption's span. Compaction now closes it.
- ASR word errors on accented lecture vocabulary persist (`implicit` → "invisible", `social
  choice` → "social shock"). These are model quality, not pipeline bugs. **Not** a glossary matter,
  as the entry above explains: the glossary repairs a misheard spelling, never a substituted word.
- ~~A session's first caption waits ~18 s for the first full window.~~ Fixed the same day — see the
  entry above. The cold start is now ~8 s, which is the head guard plus the audio the model needs to
  form a sentence, and it no longer scales with the window length.

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
