# 拾句 Nemotron CoreML helper

`echonote-nemotron-diarizer` is a macOS 14+ Swift helper used by the Python
session manager. It keeps FluidAudio/CoreML on one thread and communicates over
JSON Lines so the Parakeet MLX worker never waits for speaker identification.

The helper accepts:

```json
{"type":"start","sample_rate":16000,"variant":"low"}
{"type":"audio","start_ms":1200,"pcm16_base64":"..."}
{"type":"flush"}
{"type":"stop"}
```

It returns `ready`, `speaker_turns`, `stopped`, or an error such as
`DIARIZATION_MODEL_UNAVAILABLE`. stdout is reserved for JSON protocol messages;
diagnostic text goes to stderr and is consumed by the Python parent.

The model is loaded from the shared FluidAudio cache. Set `NEMOTRON_MODEL_DIR`
to the parent directory containing `nemotron-3-diarization` to reuse an
existing download. Otherwise FluidAudio uses its normal Application Support
cache and downloads the requested preset on first use. `HF_HOME` and
`HF_HUB_CACHE` remain available to the model downloader for shared Hugging Face
assets.

Build locally on Apple Silicon with:

```bash
./build.sh
```

The build wrapper applies the small CoreML output-backing compatibility patch
needed by current macOS ANE runtimes before invoking SwiftPM. It is safe to run
again after the dependency checkout is already patched.

The service is intentionally macOS-only. Linux CI validates the Python protocol
client without importing CoreML.
