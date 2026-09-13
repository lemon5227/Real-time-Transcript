# Classroom Focus Workbench Design

**Status:** Implemented; current live-device validation is tracked in the [project roadmap](../../ROADMAP.md).

## Goal

Make the live lecture screen feel like a full-height classroom workbench: the transcript remains the visual focus, the right rail has useful depth without becoming a settings dump, and students can reclaim reading space when needed.

## Visual structure

- Keep the dark transcript surface as the primary reading area.
- Keep the top bar visible while the classroom surface is used; the main call to action remains “开始听课” in the upper-right.
- Make the right rail stretch to the same height as the left stage.
- Use a two-zone right rail: a light control area for path and translation, and a dark “课后继续学习” area for session state and review handoff.
- Keep advanced selectors and model management behind “更多设置”, not in the quick rail.

## Interaction states

- The right rail has an explicit collapse button. Expanded state uses the current two-column width; collapsed state becomes a narrow rail and gives the reclaimed width to the transcript.
- The lecture introduction is visible on first load, can be collapsed manually, and enters the compact focus state when the transcript is scrolled down. It can be restored from the compact control.
- Top-bar start/stop remains available in every state. The old full-width bottom control strip stays removed.
- Collapse state and introduction state are local UI preferences only; recording, model, and audio preferences keep their existing storage behavior.

## Responsive behavior

- Desktop keeps the two-column workbench and aligned rail.
- Narrow screens hide the rail body behind a compact control entry and keep the top-bar start action accessible.
- All state changes use native buttons, `aria-expanded`, and visible focus styles.

## Verification

- Frontend contract tests assert the full-height rail, collapse hooks, dark review block, top-bar action, and focus-mode hooks.
- Browser verification checks collapsed/expanded geometry, no document overflow, the top-bar action, and the dark review block.
