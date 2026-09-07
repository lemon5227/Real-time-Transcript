from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_local_cloud_and_review_workflows():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in ["local mode", "cloud mode", "post-class review", "/api/capabilities", "/review"]:
        assert phrase in readme


def test_api_docs_match_the_runtime_endpoint_names():
    docs = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for endpoint in ["/api/health", "/api/capabilities", "/api/models", "start_transcription", "audio_chunk"]:
        assert endpoint in docs
