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


def test_change_log_and_maintenance_handbook_stay_reachable():
    """These exist so the next person can see project state and re-diagnose a fix.

    Asserting the cross-links keeps them from becoming orphan files nobody finds.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    maintenance = (ROOT / "docs/MAINTENANCE.md").read_text(encoding="utf-8")
    troubleshooting = (ROOT / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Current state" in changelog
    for link in ["docs/MAINTENANCE.md", "docs/LATENCY.md", "TROUBLESHOOTING.md"]:
        assert link in changelog
    assert "docs/LATENCY.md" in maintenance
    assert "CHANGELOG.md" in maintenance
    assert "docs/MAINTENANCE.md" in troubleshooting
    assert "CHANGELOG.md" in readme


def test_latency_docs_do_not_point_at_missing_probes():
    """A doc naming a renamed script is worse than no doc."""
    docs = (ROOT / "docs/LATENCY.md").read_text(encoding="utf-8")
    for name in [
        "measure_caption_latency.py",
        "measure_finalize_lag.py",
        "measure_translation_latency.py",
        "replay_lecture.py",
    ]:
        assert name in docs
        assert (ROOT / "tools" / name).is_file(), name + " is referenced but missing"


def test_macos_dmg_docs_explain_first_run_and_external_runtime():
    for relative_path in ["README.md", "QUICKSTART.md", "docs/DEPLOYMENT.md"]:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        for phrase in [
            "拾句-macOS.dmg",
            "Application Support/拾句",
            "Control-click",
            "model weights",
        ]:
            assert phrase in content, f"{phrase} missing from {relative_path}"


def test_docs_explain_github_macos_dmg_release_flow():
    for path in [ROOT / "README.zh-CN.md", ROOT / "README.macOS.md", ROOT / "docs" / "DEPLOYMENT.md"]:
        content = path.read_text(encoding="utf-8")
        for token in ["macOS DMG", "workflow_dispatch", "v*", "拾句-macOS.dmg", "Control-click"]:
            assert token in content, f"{token} missing from {path}"
