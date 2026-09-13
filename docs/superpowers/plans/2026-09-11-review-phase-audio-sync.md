# 课后复习回听同步 Implementation Plan

> 当前状态：字幕与原声同步实现及自动化验证已完成；下一阶段验收顺序见[项目路线图](../../ROADMAP.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing课后复习页 turn saved original audio into a calm, readable learning timeline where the currently playing sentence is visible without stealing attention from the historical transcript.

**Architecture:** Preserve the current two-column review layout, IndexedDB audio repository, real-time/refined transcript switch, translations, notes, and exports. Reuse each segment's `startMs`/`endMs` in the existing `timeupdate` handler; add a low-contrast `.is-playing` state and keep the active card in view only when it is outside the visible transcript viewport. No new backend endpoint or data migration is needed.

**Tech Stack:** Flask templates, vanilla JavaScript, CSS, IndexedDB audio repository, pytest, Node syntax/contract tests.

## Global Constraints

- Real-time `segments` and post-class `refinedSegments` remain independent and selectable.
- The historical transcript remains the main visual content; current-playback feedback is subtle and must not become a live-page-style highlight wall.
- Audio remains local to the browser and the review page must continue to work when no audio is available.
- The implementation must not add a new framework, an autoplay behavior, or a second audio permission request.
- Every behavior change gets a failing contract or Node test before production code changes.

---

### Task 1: Add a regression contract for playback state

**Files:**
- Modify: `tests/test_frontend_contract.py`
- Modify: `static/styles.css`
- Modify: `static/review.js`

**Interfaces:**
- Consumes: `reviewAudio.currentTime`, `segmentsOf(state.selected)`, and the existing `.review-segment` cards.
- Produces: a visible `.review-segment.is-playing` state and a guarded `scrollIntoView({block: "nearest"})` call for the active card.

- [x] **Step 1: Write the failing test**

Add a frontend contract that requires `review.js` to use `scrollIntoView`, `is-playing`, and a visibility guard, and requires the stylesheet to define `.review-segment.is-playing` with a non-empty background or border rule.

- [x] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_frontend_contract.py::test_review_page_exposes_audio_synced_history_feedback -q`

Expected: FAIL because JavaScript toggles `is-playing` but the CSS has no active style and the handler never keeps an off-screen sentence visible.

- [x] **Step 3: Implement the smallest behavior change**

In `syncPlayingSegment()`, compute the active segment once, toggle the class by segment id, and call `scrollIntoView({block: "nearest", behavior: "smooth"})` only when the active card's `getBoundingClientRect()` is outside the review stream's vertical bounds. Add a quiet left accent and warm paper tint for `.is-playing`; keep `prefers-reduced-motion` behavior instant.

- [x] **Step 4: Run focused tests**

Run: `pytest tests/test_frontend_contract.py::test_review_page_exposes_audio_synced_history_feedback -q` and `node --check static/review.js`.

- [x] **Step 5: Commit**

```bash
git add static/review.js static/styles.css tests/test_frontend_contract.py
git commit -m "feat: sync review history with audio playback"
```

### Task 2: Verify the review foundation against existing features

**Files:**
- Test: `tests/test_frontend_audio_storage.py`
- Test: `tests/test_frontend_translation_review.py`
- Test: `tests/test_routes.py`

- [x] **Step 1: Run review and refinement tests**

Run: `pytest tests/test_frontend_contract.py tests/test_frontend_audio_storage.py tests/test_frontend_translation_review.py tests/test_fine_transcription.py tests/test_routes.py -q`.

- [x] **Step 2: Check all browser assets**

Run: `for file in static/*.js; do node --check "$file"; done`.

- [x] **Step 3: Run the full suite and inspect the diff**

Run: `pytest -q --basetemp=/tmp/rtt-pytest-review`, `ruff check .`, `git diff --check`, and `git status --short`.

- [x] **Step 4: Document the next review milestone**

After this slice is green, the next planned feature is a lightweight “课堂重点” summary generated from starred segments and notes; it must consume the existing local session record and must not block transcript/audio review.
