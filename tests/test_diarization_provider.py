import base64
import sys

import numpy as np
import pytest

from backend.providers.base import ProviderError
from backend.providers.diarization import (
    DiarizationPrepareError,
    JsonlNemotronDiarizer,
    NullSpeakerDiarizer,
    create_diarizer,
    prepare_nemotron_models,
)

HELPER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "start":
        print(json.dumps({"type": "ready", "variant": request["variant"]}), flush=True)
    elif request["type"] == "audio":
        print(json.dumps({
            "type": "speaker_turns",
            "turns": [{"speaker_id": "speaker_0", "start_ms": request["start_ms"], "end_ms": request["start_ms"] + 800, "confidence": 0.87}],
        }), flush=True)
    elif request["type"] == "flush":
        print(json.dumps({"type": "speaker_turns", "turns": []}), flush=True)
    elif request["type"] == "stop":
        print(json.dumps({"type": "stopped"}), flush=True)
        break
'''


def helper_command(tmp_path, source=HELPER):
    path = tmp_path / "helper.py"
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


PREPARE_HELPER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "prepare":
        print(json.dumps({"type": "download_progress", "fraction": 0.25}), flush=True)
        print(json.dumps({"type": "download_progress", "fraction": 0.75}), flush=True)
        print(json.dumps({"type": "ready", "variant": request["variant"]}), flush=True)
    elif request["type"] == "stop":
        print(json.dumps({"type": "stopped"}), flush=True)
        break
'''

PREPARE_FAILURE_HELPER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "prepare":
        print(json.dumps({"type": "error", "code": "DIARIZATION_MODEL_UNAVAILABLE", "message": "模型下载失败"}), flush=True)
    elif request["type"] == "stop":
        print(json.dumps({"type": "stopped"}), flush=True)
        break
'''


def test_prepare_streams_download_progress_until_ready(tmp_path):
    progress = []
    prepare_nemotron_models(
        helper_command(tmp_path, PREPARE_HELPER),
        variant="low",
        on_progress=progress.append,
    )
    assert progress == [0.25, 0.75]


def test_prepare_surfaces_helper_errors(tmp_path):
    with pytest.raises(DiarizationPrepareError) as excinfo:
        prepare_nemotron_models(helper_command(tmp_path, PREPARE_FAILURE_HELPER))

    assert excinfo.value.code == "DIARIZATION_MODEL_UNAVAILABLE"
    assert "模型下载失败" in str(excinfo.value)


def test_prepare_rejects_a_missing_command():
    with pytest.raises(DiarizationPrepareError) as excinfo:
        prepare_nemotron_models("")
    assert excinfo.value.code == "DIARIZATION_HELPER_UNAVAILABLE"


def test_jsonl_helper_round_trips_audio_and_speaker_turns(tmp_path):
    provider = JsonlNemotronDiarizer(helper_command(tmp_path), variant="low")

    provider.start(sample_rate=16000)
    turns = provider.push(np.zeros(1600, dtype=np.float32), start_ms=1200)

    assert len(turns) == 1
    assert turns[0].speaker_id == "speaker_0"
    assert turns[0].start_ms == 1200
    assert provider.flush() == []
    provider.close()


def test_jsonl_helper_rejects_invalid_protocol_payload(tmp_path):
    bad_helper = r'''
import sys
for line in sys.stdin:
    print("not-json", flush=True)
'''
    provider = JsonlNemotronDiarizer(helper_command(tmp_path, bad_helper))

    with pytest.raises(ProviderError, match="DIARIZATION_PROTOCOL_ERROR"):
        provider.start(sample_rate=16000)

    provider.close()


def test_jsonl_helper_reports_early_exit(tmp_path):
    provider = JsonlNemotronDiarizer(
        helper_command(tmp_path, "import sys\nsys.exit(3)\n")
    )

    with pytest.raises(ProviderError, match="DIARIZATION_HELPER_FAILED"):
        provider.start(sample_rate=16000)


def test_null_provider_and_disabled_factory_are_safe():
    provider = create_diarizer("", enabled=False, variant="low")

    assert isinstance(provider, NullSpeakerDiarizer)
    provider.start(sample_rate=16000)
    assert provider.push(np.zeros(160, dtype=np.float32), start_ms=0) == []
    assert provider.flush() == []
    provider.close()


def test_audio_payload_is_pcm16_base64(monkeypatch, tmp_path):
    class FakeProcess:
        stdin = None
        stdout = None
        stderr = None

    provider = JsonlNemotronDiarizer(helper_command(tmp_path))
    captured = {}

    def fake_send(payload):
        captured.update(payload)
        return {"type": "speaker_turns", "turns": []}

    monkeypatch.setattr(provider, "_request", fake_send)
    provider._process = FakeProcess()
    provider.push(np.array([-1.0, 0.0, 1.0], dtype=np.float32), start_ms=0)

    decoded = base64.b64decode(captured["pcm16_base64"])
    assert len(decoded) == 6
    assert int.from_bytes(decoded[:2], "little", signed=True) == -32767
    assert int.from_bytes(decoded[-2:], "little", signed=True) == 32767
