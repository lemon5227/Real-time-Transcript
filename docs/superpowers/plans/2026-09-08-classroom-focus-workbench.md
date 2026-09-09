# Classroom Focus Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a rich full-height two-column classroom layout with a collapsible right rail and a scroll-aware lecture-introduction focus mode.

**Architecture:** Keep the existing server-rendered HTML and vanilla `static/app.js` state model. Add independent UI state hooks for the rail and lecture intro, then use CSS grid transitions/classes so collapsing the rail changes only layout, while the transcript and settings behavior remain decoupled.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript, CSS Grid, existing pytest frontend contracts, browser smoke verification.

## Global Constraints

- Keep the top-bar start/stop action as the primary recording control.
- Keep advanced model, language, microphone, audio, and model-management controls inside “更多设置”.
- Preserve the dark transcript surface and add a dark review handoff block in the right rail.
- Do not reintroduce the full-width bottom control strip.
- Preserve `prefers-reduced-motion`, keyboard focus, and native semantic controls.

---

### Task 1: Add the approved layout contract

**Files:**
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces assertions for `quick-rail-toggle`, `quick-rail-review`, `toggle-lecture-intro`, `lecture-intro-collapsed`, `quick-settings-collapsed`, sticky top-bar styling, and full-height rail styling.

- [ ] **Step 1: Write the failing test**

Add assertions to `test_live_workbench_keeps_frequent_controls_in_a_right_rail` for the new HTML and JavaScript hooks and the CSS selectors that represent the expanded/collapsed workbench.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python3 -m pytest -q tests/test_frontend_contract.py -k 'frequent_controls_in_a_right_rail'`

Expected: FAIL because the new hooks do not exist yet.

### Task 2: Build the full-height right rail

**Files:**
- Modify: `templates/live-transcript.html`
- Modify: `static/styles.css`

**Interfaces:**
- Produces `#toggle-quick-settings` with `aria-expanded`, `#quick-rail-review`, and a rail that stretches with `.workspace-classroom`.

- [ ] **Step 1: Add semantic rail controls**

Add a collapse button to the quick-rail heading and a dark `quick-rail-review` section with status copy, local-audio reassurance, and a link to `/review`.

- [ ] **Step 2: Add expanded/collapsed geometry**

Use the existing two-column grid for expanded mode and a narrow second column for `body.quick-settings-collapsed`. Hide the rail body content in collapsed mode while leaving an accessible expand control visible.

- [ ] **Step 3: Run the focused contract test**

Run: `python3 -m pytest -q tests/test_frontend_contract.py -k 'frequent_controls_in_a_right_rail'`

Expected: PASS.

### Task 3: Add lecture-introduction focus mode

**Files:**
- Modify: `templates/live-transcript.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`

**Interfaces:**
- Produces `#toggle-lecture-intro`, `#lecture-intro-toggle-label`, and the `stage-panel.is-header-collapsed` state.

- [ ] **Step 1: Add the failing behavior contract**

Assert that the template contains the intro toggle hooks and that the JavaScript contains `setupLectureFocus`, `lecture-intro-collapsed`, and a transcript-feed scroll listener.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python3 -m pytest -q tests/test_frontend_contract.py -k 'lecture_focus'`

Expected: FAIL because the focus hooks do not exist yet.

- [ ] **Step 3: Implement the smallest state transition**

Add a manual toggle, persist only the local UI preference, and collapse the intro when the transcript feed scrolls past a small threshold. Restore it when returning to the top or activating the compact toggle.

- [ ] **Step 4: Style the compact state**

Animate only height/opacity/transform, keep the top-bar visible, and disable the transition under `prefers-reduced-motion`.

- [ ] **Step 5: Run the focused contract test**

Run: `python3 -m pytest -q tests/test_frontend_contract.py -k 'lecture_focus'`

Expected: PASS.

### Task 4: Wire rail state and responsive details

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`

**Interfaces:**
- Produces `setupQuickRail`, `quick-settings-collapsed`, and synchronized `aria-expanded` state.

- [ ] **Step 1: Add the rail toggle behavior**

Toggle the body class and button label/ARIA state without changing model or recording state.

- [ ] **Step 2: Add desktop and narrow-screen rules**

Keep the right rail aligned on desktop; on narrow screens make it a compact entry instead of squeezing the transcript.

- [ ] **Step 3: Run the complete frontend contract suite**

Run: `python3 -m pytest -q tests/test_frontend_contract.py`

Expected: PASS.

### Task 5: Verify the workbench

**Files:**
- Verify: `templates/live-transcript.html`, `static/app.js`, `static/styles.css`, `tests/test_frontend_contract.py`

- [ ] **Step 1: Run the full test and static checks**

Run: `python3 -m pytest -q`; `node --check static/app.js`; `python3 -m compileall -q backend app.py`; `ruff check backend tests`; `git diff --check`.

Expected: every command exits with code 0.

- [ ] **Step 2: Browser smoke test**

Load `http://127.0.0.1:5001/`, verify the top-bar start action, expanded rail, collapse/expand state, dark review block, and no document-level overflow.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --stat` and `git status --short`.

Expected: only the approved UI, test, and documentation changes are present.
