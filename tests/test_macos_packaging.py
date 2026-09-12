import os
import signal
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

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


def test_canonical_app_icon_keeps_the_canvas_transparent_and_the_page_legible():
    source = _svg_text(ICON_PATH)

    assert '<rect width="1024" height="1024"' not in source
    assert 'stroke="#0B1020"' in source
    assert 'fill="#F5F3EE"' in source


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


@pytest.mark.parametrize(
    ("configured_port", "expected_port"),
    [(None, "8765"), ("54321", "54321")],
)
def test_macos_launcher_exports_the_port_it_waits_for_to_the_server(
    tmp_path, configured_port, expected_port
):
    contents = tmp_path / "拾句.app" / "Contents"
    launcher_dir = contents / "MacOS"
    source_root = contents / "Resources" / "app"
    local_bin = tmp_path / ".local" / "bin"
    support_root = tmp_path / "support"
    port_capture = tmp_path / "server-port"
    launcher_dir.mkdir(parents=True)
    source_root.mkdir(parents=True)
    local_bin.mkdir(parents=True)
    (source_root / "quickstart.py").write_text("# fake bootstrap entrypoint\n", encoding="utf-8")

    launcher = launcher_dir / "RealTimeTranscript"
    launcher.write_text(LAUNCHER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)

    fake_python = local_bin / "python3.13"
    fake_python.write_text(
        "#!/bin/bash\nprintf '%s' \"${PORT:-unset}\" > \"$PORT_CAPTURE\"\nexec /bin/sleep 20\n",
        encoding="utf-8",
    )
    fake_curl = local_bin / "curl"
    fake_curl.write_text(
        "#!/bin/bash\n"
        "url=\"\"\nfor argument in \"$@\"; do url=\"$argument\"; done\n"
        "server_port=\"$(cat \"$PORT_CAPTURE\" 2>/dev/null || true)\"\n"
        "[[ \"$server_port\" != unset && \"$url\" == \"http://127.0.0.1:${server_port}/api/health\" ]]\n",
        encoding="utf-8",
    )
    for executable in [fake_python, fake_curl]:
        executable.chmod(0o755)
    for name in ["open", "osascript"]:
        stub = local_bin / name
        stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)

    environment = os.environ.copy()
    environment.pop("PORT", None)
    if configured_port:
        environment["PORT"] = configured_port
    environment.update(
        {
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "PORT_CAPTURE": str(port_capture),
            "TRANSCRIPT_APP_SUPPORT_DIR": str(support_root),
            "TRANSCRIPT_STARTUP_TIMEOUT_SECONDS": "2",
        }
    )
    try:
        result = subprocess.run(
            ["/bin/bash", str(launcher)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert port_capture.read_text(encoding="utf-8") == expected_port
    finally:
        pid_file = support_root / "pids" / "server.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass


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
