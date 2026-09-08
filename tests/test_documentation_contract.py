from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_local_cloud_and_review_workflows():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in ["local mode", "cloud mode", "post-class review", "/api/capabilities", "/review"]:
        assert phrase in readme


def test_api_docs_match_the_runtime_endpoint_names():
    docs = (ROOT / "docs/API.md").read_text(encoding="utf-8")
    for endpoint in ["/api/health", "/api/capabilities", "/api/models", "/api/translate", "start_transcription", "audio_chunk", "translate_segments"]:
        assert endpoint in docs


def test_privacy_docs_cover_local_audio_and_text_only_translation():
    docs = (ROOT / "docs/PRIVACY.md").read_text(encoding="utf-8")
    for phrase in ["saves original audio locally", "text-only path", "TRANSLATION_GOOGLE_API_KEY", "clearing site data"]:
        assert phrase in docs
