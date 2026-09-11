from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = ROOT / "static" / "app-icon.svg"
FAVICON_PATH = ROOT / "static" / "favicon.svg"
LAUNCHER_PATH = ROOT / "packaging" / "macos" / "launcher.sh"
PLIST_PATH = ROOT / "packaging" / "macos" / "Info.plist"
BUILD_SCRIPT_PATH = ROOT / "scripts" / "build-macos-dmg.sh"
REQUIRED_COLORS = {"#0B1020", "#F5F3EE", "#70E0C0"}


def _svg_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canonical_app_icon_is_a_flat_live_page_svg():
    source = _svg_text(ICON_PATH)
    root = ElementTree.fromstring(source)

    assert root.tag.endswith("svg")
    assert root.attrib["viewBox"] == "0 0 1024 1024"
    assert REQUIRED_COLORS <= set(source.split('"'))
    assert "linearGradient" not in source
    assert "radialGradient" not in source
    assert "filter=" not in source
    assert "<image" not in source
    assert "font-family" not in source
    assert "http://" not in source.replace("http://www.w3.org/2000/svg", "")


def test_favicon_is_a_compact_standalone_version_of_the_live_page_mark():
    source = _svg_text(FAVICON_PATH)
    root = ElementTree.fromstring(source)

    assert root.tag.endswith("svg")
    assert root.attrib["viewBox"] == "0 0 32 32"
    assert REQUIRED_COLORS <= set(source.split('"'))
    assert "linearGradient" not in source
    assert "radialGradient" not in source
    assert "filter=" not in source
    assert "<image" not in source
    assert "font-family" not in source
    assert "http://" not in source.replace("http://www.w3.org/2000/svg", "")


def test_macos_launcher_uses_external_user_runtime_and_bounded_health_check():
    source = _svg_text(LAUNCHER_PATH)

    for token in [
        "TRANSCRIPT_RUNTIME_DIR",
        "TRANSCRIPT_ENV_FILE",
        "Application Support/拾句",
        "quickstart.py",
        "--mode auto",
        "/api/health",
        "PYTHONUNBUFFERED=1",
        "osascript",
        "trap",
        'wait_for_health "$STARTUP_TIMEOUT_SECONDS" "$existing_pid"',
        "/opt/homebrew/bin",
        "$HOME/.local/bin",
        "python3.12",
        "brew install python@3.12",
    ]:
        assert token in source
    assert "/Users/" not in source
    assert ".worktrees" not in source


def test_macos_bundle_metadata_declares_app_icon_and_runtime_entrypoint():
    root = ElementTree.parse(PLIST_PATH).getroot()
    children = list(root.find("dict"))
    values = {
        children[index].text: children[index + 1].text
        for index in range(0, len(children), 2)
        if children[index].tag == "key" and children[index + 1].tag == "string"
    }

    assert values["CFBundlePackageType"] == "APPL"
    assert values["CFBundleExecutable"] == "RealTimeTranscript"
    assert values["CFBundleDisplayName"] == "拾句"
    assert values["CFBundleName"] == "拾句"
    assert values["CFBundleIconFile"] == "拾句"


def test_macos_dmg_builder_is_explicit_about_platform_tools_and_exclusions():
    source = _svg_text(BUILD_SCRIPT_PATH)

    for token in [
        "iconutil",
        "hdiutil",
        "rsvg-convert",
        "rsync",
        "MacOS/RealTimeTranscript",
        "Resources/app",
        "拾句.icns",
        "拾句-macOS.dmg",
        "拾句.app",
        "Applications",
        "CODESIGN_IDENTITY",
        ".env",
        ".venv",
        ".cache",
        "model",
    ]:
        assert token in source
    assert "Darwin" in source
