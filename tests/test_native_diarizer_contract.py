from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = ROOT / "native" / "nemotron-diarizer"


def test_native_helper_package_is_pinned_and_has_the_expected_executable():
    package = (NATIVE_ROOT / "Package.swift").read_text(encoding="utf-8")

    assert 'name: "EchoNoteNemotron"' in package
    assert 'name: "echonote-nemotron-diarizer"' in package
    assert 'exact: "0.17.1"' in package
    assert 'product(name: "FluidAudio"' in package


def test_native_helper_uses_json_only_on_stdout():
    source = (NATIVE_ROOT / "Sources" / "NemotronDiarizer" / "main.swift").read_text(
        encoding="utf-8"
    )

    assert '"speaker_turns"' in source
    assert '"pcm16_base64"' in source
    assert "Nemotron3Diarizer" in source
    assert "print(" not in source


def test_native_helper_documents_shared_model_cache_and_protocol():
    readme = (NATIVE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "HF_HUB_CACHE" in readme
    assert "JSON Lines" in readme
    assert "DIARIZATION_MODEL_UNAVAILABLE" in readme
