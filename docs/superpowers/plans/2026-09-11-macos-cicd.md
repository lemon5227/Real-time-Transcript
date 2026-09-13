# macOS DMG CI/CD Implementation Plan

> 当前状态（2026-09-13）：GitHub Actions 的 DMG 构建与 Release 工作流已实现并有自动化契约测试；修复版 DMG 已在本机构建、检查。当前 `main` 尚有未推送提交，最新版本的 GitHub 工作流运行及 Release 产物未验证；按[项目路线图](../../ROADMAP.md)跟踪。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GitHub Actions build a reproducible `拾句-macOS.dmg` on Apple Silicon runners, publish it as a workflow artifact, and attach it to version-tagged GitHub Releases.

**Architecture:** Keep `scripts/build-macos-dmg.sh` as the single source of truth for app packaging. A macOS 14 GitHub-hosted runner installs only the SVG renderer needed by the script, runs packaging smoke checks, uploads the DMG for every manual/tagged run, and uses the preinstalled `gh` CLI to attach tagged builds to a Release. The workflow never downloads model weights or stores credentials in the repository.

**Tech Stack:** GitHub Actions, macOS 14, Homebrew `librsvg`, `iconutil`, `hdiutil`, `rsvg-convert`, `rsync`, GitHub CLI, pytest contract tests.

## Global Constraints

- The workflow must build only on `macos-14` Apple Silicon and must fail clearly when a required packaging tool is unavailable.
- The artifact filename is exactly `拾句-macOS.dmg`; model weights, `.env`, `.venv`, recordings, and logs stay outside the app bundle.
- Manual runs must upload an artifact; pushes of tags matching `v*` must also create or update a GitHub Release asset.
- No signing secret is required for the first version; the produced DMG is explicitly unsigned.
- CI tests must not require model downloads, a real microphone, or a configured translation API.

---

### Task 1: Define the workflow contract

**Files:**
- Modify: `tests/test_deployment_files.py`
- Create: `.github/workflows/macos-dmg.yml`

**Interfaces:**
- Consumes: `scripts/build-macos-dmg.sh` and the repository's canonical `static/app-icon.svg`.
- Produces: a workflow named `macOS DMG`, an artifact named `shiju-macos-dmg`, and a release asset named `拾句-macOS.dmg` for `v*` tags.

- [x] **Step 1: Write the failing contract test**

Assert that the workflow has `workflow_dispatch`, `push.tags: v*`, `runs-on: macos-14`, `brew install librsvg`, `bash scripts/build-macos-dmg.sh`, `actions/upload-artifact@v4`, `hdiutil imageinfo`, `permissions.contents: write`, and a tag-only `gh release` step.

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_deployment_files.py::test_macos_dmg_workflow_builds_and_publishes_artifact -q`

Expected: FAIL because `.github/workflows/macos-dmg.yml` does not exist.

- [x] **Step 3: Implement the workflow**

Use a manual input named `version` and derive a safe version from the tag with `${GITHUB_REF_NAME#v}`. Run the builder with `OUTPUT_DIR="$RUNNER_TEMP/shiju-dist"`, validate the output with `hdiutil imageinfo`, upload the exact DMG path, and run `gh release create "$GITHUB_REF_NAME" "$OUTPUT_DIR/拾句-macOS.dmg" --verify-tag --generate-notes` only when `github.ref_type == 'tag'`.

- [x] **Step 4: Run the focused test and YAML-independent checks**

Run: `pytest tests/test_deployment_files.py::test_macos_dmg_workflow_builds_and_publishes_artifact -q` and `git diff --check`.

- [x] **Step 5: Commit**

```bash
git add .github/workflows/macos-dmg.yml tests/test_deployment_files.py
git commit -m "ci: build and publish macos dmg"
```

### Task 2: Document release and download behavior

**Files:**
- Modify: `README.zh-CN.md`
- Modify: `README.macOS.md`
- Modify: `docs/DEPLOYMENT.md`
- Test: `tests/test_documentation_contract.py`

**Interfaces:**
- Consumes: the workflow's manual and tag triggers.
- Produces: contributor instructions for `workflow_dispatch`, `v*` tags, unsigned first launch, and the artifact/release filename.

- [x] **Step 1: Write the failing documentation contract**

Assert that the three documents mention `macOS DMG`, `workflow_dispatch`, `v*`, `拾句-macOS.dmg`, and the unsigned first-launch Control-click → Open path.

- [x] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_documentation_contract.py::test_docs_explain_github_macos_dmg_release_flow -q`

Expected: FAIL because the release flow is not documented.

- [x] **Step 3: Add concise instructions**

Document both paths:

```bash
git tag v0.1.0
git push origin v0.1.0
```

and GitHub Actions → `macOS DMG` → Run workflow. Explain that the Actions artifact is available on manual runs, while a `v*` tag attaches the same DMG to a Release.

- [x] **Step 4: Run focused documentation tests**

Run: `pytest tests/test_documentation_contract.py::test_docs_explain_github_macos_dmg_release_flow -q`.

- [x] **Step 5: Commit**

```bash
git add README.zh-CN.md README.macOS.md docs/DEPLOYMENT.md tests/test_documentation_contract.py
git commit -m "docs: explain macos release artifacts"
```

### Task 3: Verify the local packaging path remains unchanged

**Files:**
- Test: `tests/test_macos_packaging.py`
- Test: `tests/test_deployment_files.py`

- [x] **Step 1: Run all packaging and deployment tests**

Run: `pytest tests/test_macos_packaging.py tests/test_deployment_files.py -q`.

- [x] **Step 2: Run the local macOS smoke build**

Run: `bash scripts/build-macos-dmg.sh` and `hdiutil imageinfo dist/拾句-macOS.dmg`.

- [x] **Step 3: Run the complete suite and static checks**

Run: `pytest -q --basetemp=/tmp/rtt-pytest-cicd`, `ruff check .`, `git diff --check`, and `git status --short`.
