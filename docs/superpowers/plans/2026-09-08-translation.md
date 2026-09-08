# 课堂翻译 Implementation Plan

> 使用 TDD 执行：每个任务先写会失败的测试，再实现最小行为，最后运行专用测试。翻译文本与原声资产解耦；不新增云端录音归档。

**Goal:** 为课堂转录增加可配置的快速翻译、精确翻译、实时翻译和课后翻译，并保持原文、音频和翻译可以独立失败和复习。

**Architecture:** 后端 `TranslationProvider` 适配 Google/Microsoft/本地/云端，`TranslationRouter` 负责模式选择；Socket.IO 负责实时小批次，HTTP 负责课后批次；前端把翻译按语言缓存到每个 segment 的 `translations` 字段和 IndexedDB session。

## Global constraints

- 实时翻译默认关闭；打开后快速模式默认使用用户选择的 Google 或 Microsoft。
- 精确翻译的 auto 只在已就绪本地模型和已配置云端模型之间选择；不静默回退到快速翻译。
- API keys 只由后端环境变量提供；响应、日志和前端源码不出现密钥。
- 翻译只接收文本，不接收原始音频，不影响本地音频保存。
- 只翻译 final segment；批次保持输入顺序、限制并发为 1、结果按 segment ID 合并。
- 旧课堂缺少 translations 时按空对象显示，原有复习与导出行为继续可用。

## 文件结构

- Create: `backend/translation.py` — provider protocol、错误类型、请求/响应解析和 router。
- Create: `backend/providers/google_translation.py` — Google Cloud Translation adapter。
- Create: `backend/providers/microsoft_translation.py` — Microsoft Translator adapter。
- Create: `backend/providers/model_translation.py` — 本地/云端模型翻译 adapter 边界。
- Modify: `backend/config.py` — 翻译 provider、endpoint、区域、模型和超时配置。
- Modify: `backend/routes.py` — capabilities、实时 Socket.IO 事件和课后 HTTP endpoint。
- Modify: `backend/session_manager.py` — 按 sid 校验 segment ID，并提供当前 final segment 文本快照。
- Modify: `backend/models.py` — session translation options and normalized translation payloads。
- Create: `static/translation-queue.js` — 前端 final segment 批处理、去重、并发控制和结果回调。
- Modify: `static/storage.js` — translations 归一化、缓存和增量保存。
- Modify: `static/app.js` — 设置、实时翻译展示、状态、错误和停止时落盘。
- Modify: `static/review.js` — 课后翻译、选择翻译、进度、重试和译文显示。
- Modify: `templates/live-transcript.html`, `templates/review.html`, `static/styles.css` — UI controls and visual states。
- Modify: `static/export.js` — 原文/原文+译文/仅译文导出选择。
- Modify: `docs/API.md`, `docs/PRIVACY.md`, `README.zh-CN.md`, `QUICKSTART.md` — 配置与隐私文档。
- Create/modify tests under `tests/` for provider, router, queue, routes, storage and frontend contracts。

## Task 1: Provider contracts and configuration

1. Write failing tests for provider request bodies, response order, structured errors and secret redaction.
2. Add translation config fields with empty-by-default behavior; `/api/config/public` exposes configured flags only.
3. Implement Google `translateText` adapter and Microsoft `translate` adapter behind a common protocol.
4. Implement model adapter boundary using injected callable/HTTP client; missing configuration returns `TRANSLATION_NOT_CONFIGURED`.
5. Run provider/config tests, syntax checks, then commit `feat: add translation provider adapters`.

## Task 2: Router and backend endpoints

1. Write failing tests for fast/model/auto selection and unavailable-provider errors.
2. Implement `TranslationRouter.resolve(mode, provider, local_ready)` with explicit result metadata.
3. Add `/api/translation/capabilities` or extend capabilities with configured providers and selected defaults.
4. Add Socket.IO `translate_segments`/`translation_result`; validate segment IDs belong to the current session and only accept final segment text.
5. Add `POST /api/translate` for bounded after-class batches and return per-segment results.
6. Run route/socket tests and commit `feat: route realtime and batch translation`.

## Task 3: Frontend queue and live display

1. Write failing Node contract tests for final-only filtering, 3–5 segment batching, deduplication, ordered results and concurrency 1.
2. Implement `static/translation-queue.js` with `enqueue`, `flush`, `stop`, and `getState`.
3. Add translation controls to live settings: mode, fast provider, precise strategy, target language; default real-time mode off.
4. On `transcript_segment`, render original immediately and enqueue only final segment; merge results by `segment_id` and persist incrementally.
5. Add recoverable error UI and “重试翻译”; never stop or hide original transcript on translation failure.
6. Run frontend queue/contract tests and commit `feat: add live translation queue`.

## Task 4: Review translation and export

1. Write failing tests for batch progress, cached results, retrying only failed items, and legacy sessions.
2. Add review controls for whole-class, selected-segment and single-segment translation.
3. Implement cached incremental updates in IndexedDB and status/progress rendering.
4. Add export selection for original, bilingual and translated-only output while preserving timestamp ordering.
5. Add responsive styling and keyboard/focus behavior.
6. Run review/export tests and commit `feat: add after-class translation review`.

## Task 5: Documentation and end-to-end verification

1. Document Google/Microsoft environment configuration, local/cloud model routing and privacy boundaries.
2. Run full Python and Node tests, `node --check`, `compileall`, `ruff` if installed, and `git diff --check`.
3. Browser verify: off, Google fast, Microsoft fast, local precise, cloud fallback, after-class cache/retry, export and legacy sessions.
4. Confirm no audio upload is introduced by translation and no credentials or model weights are tracked.
5. Commit docs and final verification evidence.
