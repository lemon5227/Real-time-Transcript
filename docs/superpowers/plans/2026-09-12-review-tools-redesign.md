# Review Tools Redesign Implementation Plan

> 当前状态（2026-09-13）：Task 1–4 已实现并提交；Task 5 的全量自动化检查已通过。Task 5 的真实浏览器/课堂状态尚未验收。当前状态以[项目路线图](../../ROADMAP.md)为准；下方原始步骤清单保留作实施记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the post-class review tools into a compact subtitle-first interface, make Microsoft Translator the new default, and improve the readability and state feedback of subtitle actions.

**Architecture:** Keep the Flask templates, vanilla JavaScript state, IndexedDB session/audio storage, and refinement/translation APIs. The review detail will use one shared two-column tools row; `review.js` will own selected segment IDs and rerender-safe action state; existing CSS tokens will express selected, complete, busy, and error states. Live and review pages will default to Microsoft while honoring a saved explicit Google preference.

**Tech Stack:** Flask/Jinja, vanilla JavaScript, CSS custom properties, pytest contract tests, Node syntax checks, CUA browser verification.

## Global Constraints

- Preserve the existing left classroom library and right review-detail layout.
- Do not change transcription, refinement, translation API, IndexedDB, or audio-storage contracts.
- New users with no saved preference default to `microsoft`; saved explicit `google` preferences remain unchanged.
- Fast translation defaults to Chinese and Microsoft Translator.
- Refined transcription automatically becomes active at `ready`; real-time transcript remains available.
- Use existing design tokens and keep mint, amber, neutral, and cinnabar semantics consistent.
- Keep current public IDs used by existing tests and handlers, or provide a compatibility alias.
- Desktop tools use two equal columns; narrow screens stack them without horizontal overflow.
- Every state has text or an accessible label in addition to color.

## File Map

- Modify `tests/test_frontend_contract.py`: contracts for Microsoft defaults, compact tools, batch selection, and readable action states.
- Modify `templates/live-transcript.html`: Microsoft-first fast translation options and selected default.
- Modify `templates/review.html`: compact tool modules, translation disclosure, and conditional selection toolbar.
- Modify `static/app.js`: Microsoft initial provider state while preserving saved preference restoration.
- Modify `static/review.js`: selected IDs, batch toolbar rendering, rerender-safe checkbox state, translation/重点 labels, and refinement transitions.
- Modify `static/styles.css`: equal-height tools, normalized controls, subtle status states, and responsive reflow.

## Task 1: Add failing frontend contracts

**Files:** Modify `tests/test_frontend_contract.py`.

**Interfaces:** Consume existing HTML/JavaScript asset responses; produce executable expectations for default provider, compact tools, and visual-state hooks.

- [x] **Step 1: Add the Microsoft default test.** Assert the live and review HTML contain `<option value="microsoft" selected>Microsoft Translator</option>` and `static/app.js` contains `translationProvider: "microsoft"`.
- [x] **Step 2: Add the compact-tools test.** Assert review HTML contains `review-tools-row`, `review-transcript-tool`, `review-translation-tool`, `review-selection-toolbar`, `review-selected-count`, and `clear-segment-selection`.
- [x] **Step 3: Add action-state assertions.** Assert JavaScript contains `已选择`, `已翻译`, and `已标记`; assert the stylesheet contains `.review-tools-row`, `.review-selection-toolbar`, `.segment-action`, `.is-selected`, and `.is-translated`.
- [x] **Step 4: Run the new tests and verify the expected failure.** At plan execution, run `pytest tests/test_frontend_contract.py::test_translation_defaults_to_microsoft_for_new_users tests/test_frontend_contract.py::test_review_page_exposes_readable_segment_action_states -q`; it failed against the then-current Google/default large-panel implementation.
- [x] **Step 5: Commit the failing contracts.** Run `git add tests/test_frontend_contract.py && git commit -m "test: specify review tools and Microsoft defaults"`.

## Task 2: Make Microsoft the default provider

**Files:** Modify `static/app.js`, the translation settings block in `templates/live-transcript.html`, and the provider select in `templates/review.html`.

**Interfaces:** Consume the existing `SETTINGS_KEY` preference object; produce Microsoft as the no-preference default while restoring saved Google or Microsoft values exactly.

- [x] **Step 1: Change the initial live state.** Set `state.translationProvider` to `"microsoft"`; keep the existing saved-provider condition so a stored value still wins.
- [x] **Step 2: Change live settings markup.** Order the fast-service options as Microsoft first and Google second, with Microsoft marked `selected`; keep `restorePreferences()` assigning the saved value afterward.
- [x] **Step 3: Change review settings markup.** Use the same Microsoft-first option order in `#review-translation-provider`; keep `reviewTranslationSettings()` and the `/api/translate` payload unchanged.
- [x] **Step 4: Run the focused test.** Run `pytest tests/test_frontend_contract.py::test_translation_defaults_to_microsoft_for_new_users -q`; it must pass.
- [x] **Step 5: Commit the provider change.** Run `git add static/app.js templates/live-transcript.html templates/review.html tests/test_frontend_contract.py && git commit -m "feat: default translation to Microsoft"`.

## Task 3: Implement compact review tools and subtitle actions

**Files:** Modify `templates/review.html`, `static/review.js`, and `tests/test_frontend_contract.py`.

**Interfaces:** Consume `segmentsOf()`, refinement polling, `translateSegments()`, `saveSelected()`, and segment translations; produce `state.selectedSegmentIds`, `renderSelectionToolbar()`, rerender-safe selection, and readable action labels without changing request payloads.

- [x] **Step 1: Replace the two large panels with two compact modules.** Put `#review-transcript-tool` and `#review-translation-tool` inside one `#review-tools-row`; retain all current refinement, translation, and export IDs.
- [x] **Step 2: Add the visible translation summary.** Add `#review-translation-summary` showing `中文 · 快速翻译 · Microsoft Translator`; put low-frequency fields and `translate-session`/`translate-failed` inside one native `<details class="translation-more">` disclosure.
- [x] **Step 3: Add the conditional batch toolbar.** Add one `#review-selection-toolbar` with `#review-selected-count` and `#clear-segment-selection`; keep exactly one `#translate-selected` element.
- [x] **Step 4: Add state-backed selection.** Add `selectedSegmentIds: []`, make `selectedReviewSegments()` filter that array, and add `updateSegmentSelection(input, segment)` to update IDs, card class, and toolbar.
- [x] **Step 5: Preserve selection across rerenders.** Reset selected IDs in `selectSession(id)`; restore checkbox state and `.is-selected` in `renderDetail()`; call `renderSelectionToolbar()` after rendering and from `updateTranslationControls()`.
- [x] **Step 6: Make row actions stateful.** Give translation and star controls the `segment-action` class; use `翻译`, `翻译中`, `已翻译`, or `重试`; use `☆ 重点` and `★ 已标记`; preserve existing aria labels and force-refresh behavior.
- [x] **Step 7: Synchronize summary and listeners.** Derive the provider label from the selected option, make batch translation use state-backed selection, and make clear-selection reset the array then rerender.
- [x] **Step 8: Run focused checks.** Run `pytest tests/test_frontend_contract.py tests/test_frontend_translation_review.py -q` and `for file in static/*.js; do node --check "$file"; done`; both must pass.
- [x] **Step 9: Commit the structure and behavior change.** Run `git add templates/review.html static/review.js tests/test_frontend_contract.py && git commit -m "feat: streamline review tools and subtitle actions"`.

## Task 4: Polish visual states and responsive layout

**Files:** Modify `static/styles.css` and `tests/test_frontend_contract.py`.

**Interfaces:** Consume the new tool/action/state hooks; produce equal-height desktop modules, 32px-or-taller controls, readable selected/translated/starred states, and stacked narrow-screen layout.

- [x] **Step 1: Add CSS contracts.** Require `.review-tools-row`, `.review-tool`, `.review-selection-toolbar`, `.segment-action`, and `min-height: 32px` in the stylesheet contract.
- [x] **Step 2: Style the tool row.** Use `grid-template-columns: repeat(2, minmax(0, 1fr))`, shared padding/border/radius, a cool neutral surface for transcript tools, and a very light warm surface for translation tools; reserve amber fill for primary actions only.
- [x] **Step 3: Normalize action controls.** Add shared size, padding, border, hover, and `focus-visible` rules for `.segment-action`; use subtle mint for `.review-segment.is-selected` and `.segment-action.is-translated`, and amber only for starred state.
- [x] **Step 4: Style the batch toolbar and disclosure.** Hide the toolbar when `[hidden]`, make it compact and readable, and keep advanced translation options visually secondary.
- [x] **Step 5: Add narrow-screen rules.** Stack `.review-tools-row`, allow the translation summary to wrap, make the primary tool button full width, and keep segment actions at least 34px tall without horizontal overflow.
- [x] **Step 6: Run style checks.** Run `pytest tests/test_frontend_contract.py -q`, `ruff check .`, and `git diff --check`; all must pass.
- [x] **Step 7: Commit the visual change.** Run `git add static/styles.css tests/test_frontend_contract.py && git commit -m "style: polish review tools and subtitle actions"`.

## Task 5: Browser verification and regression suite

**Files:** Modify none unless verification finds a regression; test the full existing suite and browser-visible states.

**Interfaces:** Consume `http://localhost:5001/review` and one local classroom record; produce verified empty, selected, refined, selected-subtitle, translated, and responsive states.

- [x] **Step 1: Run the full suite.** Run `pytest -q --basetemp=/tmp/rtt-pytest-review-tools`, JavaScript `node --check` for every `static/*.js`, and `git diff --check`.
- [ ] **Step 2: Verify empty state.** Open `/review` in the in-app browser; confirm no review tools appear without a selected class and there is no horizontal overflow.
- [ ] **Step 3: Verify selected class.** Open a local classroom; confirm the empty state is replaced, the two tools are equal columns on desktop, Microsoft appears in the translation summary, and labels are readable.
- [ ] **Step 4: Verify selection and translation.** Check two subtitles and confirm the toolbar count changes from `已选择 1 段` to `已选择 2 段` without losing either checkbox; run translation; confirm `已翻译`; clear selection; confirm the toolbar disappears.
- [ ] **Step 5: Verify refinement.** Start refinement; confirm progress and real-time fallback; when ready, confirm the refined source becomes active and real-time remains switchable.
- [ ] **Step 6: Inspect final UI and console.** Check desktop and narrow screenshots for clipped controls, contrast, focus visibility, and console errors; if a defect appears, add a focused failing test before the smallest fix.
