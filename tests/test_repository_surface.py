from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_standalone_generator_is_not_part_of_realtime_repository():
    assert not (ROOT / "Auto-Subtitle-Generator-Standalone").exists()


def test_runtime_requirements_are_split_by_execution_mode():
    assert (ROOT / "requirements-core.txt").exists()
    assert (ROOT / "requirements-local.txt").exists()
    assert (ROOT / "requirements-cloud.txt").exists()


def test_pytest_only_collects_isolated_tests():
    pytest_config = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "testpaths = tests" in pytest_config
