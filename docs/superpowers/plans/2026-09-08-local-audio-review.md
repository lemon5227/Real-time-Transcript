# 本地原声课后复习 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每节课堂默认保存仅存在本机的原声，并在课后复习页提供可搜索字幕与同步音频回放。

**Architecture:** 复用现有麦克风 `MediaStream`，保留 PCM 到 Socket.IO 的实时转录路径，新增独立的 `MediaRecorder` 路径。`EchoAudioRepository` 隔离音频资产管理，优先使用 OPFS 存放分片，回退到 IndexedDB；IndexedDB 的课堂记录只保存音频清单和状态。复习页通过清单读取音频 Blob，播放器时间与字幕 `startMs/endMs` 联动。

**Tech Stack:** 原生 JavaScript、Web Audio API、MediaRecorder、OPFS/File System API、IndexedDB、HTML `<audio>`、现有 CSS、Python pytest、Node.js 合约测试、Chrome 浏览器验收。

## Global Constraints

- 默认保存课堂原声，设置项为“保存原声”，默认值必须为 `true`。
- 原声只保存在当前浏览器/设备本地；不得新增服务端音频归档接口或把录音写入日志、Git、服务器临时目录。
- 复用当前已授权的 `MediaStream`，不得为了录音再次申请麦克风权限。
- 使用约 10 秒 `MediaRecorder` 分片，并为每片保存严格递增 `sequence` 与单调时间偏移。
- OPFS 为主存储，IndexedDB 分片为回退；任何存储失败都不得删除或阻断已经得到的字幕。
- 复习页必须兼容旧的无音频课堂记录；旧记录继续可以阅读、搜索、编辑、标记和导出字幕。
- 音频格式必须记录真实 `mimeType` 和扩展名，不得假定所有浏览器都使用 WebM。
- 所有异步音频写入必须按序排队；重复 `sessionId + sequence` 写入不得产生重复片段。
- 浏览器存储不是永久备份；界面必须说明清除网站数据、无痕窗口或配额不足可能导致原声丢失，并提供手动导出。
- 每个任务结束前运行该任务的专用测试；最终任务运行完整测试、语法检查、静态检查和浏览器验收。

---

## 文件结构与职责

实现前固定以下边界：

- Create `static/audio-storage.js`: `EchoAudioRepository` 的公共接口、OPFS/IndexedDB 后端选择、分片清单读取与播放 Blob 组合。
- Create `static/audio-recorder.js`: `MediaRecorder` 适配器、MIME 选择、10 秒分片、顺序写入和停止时等待写入。
- Modify `static/storage.js`: IndexedDB 版本升级、音频清单/分片 store、课堂记录的 `audio` 字段兼容化、级联清理方法。
- Modify `static/app.js`: “保存原声”偏好、课前能力检查、录音器生命周期、停止流程等待、错误降级和课堂记录持久化。
- Modify `static/review.js`: 播放器加载、对象 URL 生命周期、字幕点击跳转、播放高亮、音频导出和删除联动。
- Modify `templates/live-transcript.html`: 默认开启的保存原声控件、能力/隐私提示和录音保存状态挂钩。
- Modify `templates/review.html`: 音频播放器、状态信息和原声导出控件。
- Modify `static/styles.css`: 实时页保存控件、复习页播放器、状态徽章和当前字幕样式。
- Modify `tests/test_frontend_contract.py`: 页面和脚本 hook 合约。
- Create `tests/test_frontend_audio_storage.py`: 存储接口和分片行为的 Node 合约测试。
- Create `tests/test_frontend_audio_recorder.py`: MediaRecorder 适配器的 Node 合约测试。
- Modify `docs/PRIVACY.md`, `README.zh-CN.md`, `QUICKSTART.md`: 本地原声保存、清理、导出和云端隐私说明。

## 任务之间的稳定接口

`static/audio-storage.js` 必须暴露以下接口；后续任务只依赖这些方法，不直接调用 OPFS 或 IndexedDB：

```javascript
window.EchoAudioRepository = {
  createRecording({ sessionId, mimeType, extension, startedAt }),
  appendChunk({ sessionId, sequence, blob, startMs, endMs }),
  finalizeRecording({ sessionId, durationMs }),
  getManifest(sessionId),
  getPlayableBlob(sessionId),
  deleteRecording(sessionId),
  getUsage()
};
```

每个方法返回 Promise。`createRecording` 和 `finalizeRecording` 返回 `AudioManifest`；`appendChunk`、`deleteRecording` 返回 `Promise<void>`；`getManifest` 返回 `AudioManifest | null`；`getPlayableBlob` 返回 `Blob | null`；`getUsage` 返回 `{ usage, quota } | null`。

`static/audio-recorder.js` 必须暴露：

```javascript
window.EchoAudioRecorder.create({
  stream,
  sessionId,
  repository,
  now
}) -> recorder

recorder.start() -> Promise<AudioManifest>
recorder.stop() -> Promise<AudioManifest>
recorder.getState() -> "idle" | "recording" | "stopping" | "stopped" | "failed"
```

`now` 是可注入的单调时钟函数，默认使用 `performance.now()`，便于测试时间偏移而不依赖墙上时钟。

---

### Task 1: 建立音频资产存储接口与 IndexedDB 迁移

**Files:**
- Create: `static/audio-storage.js`
- Create: `tests/test_frontend_audio_storage.py`
- Modify: `static/storage.js`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: 现有 `EchoStore.normalizeSession()`、`EchoStore.saveSession()` 和 IndexedDB 数据库 `realtime-transcript`。
- Produces: `window.EchoAudioRepository`、`AudioManifest`、IndexedDB 音频清单和分片存储方法。

- [ ] **Step 1: Write the failing storage contract test**

在 `tests/test_frontend_audio_storage.py` 中用 `subprocess.run(["node", "-e", script])` 加载 `static/audio-storage.js`。Node 脚本提供一个内存后端和 `Blob`，先断言目标接口不存在时失败；接口建立后覆盖以下行为：

```javascript
const repository = createAudioRepository({ backend: memoryBackend });
await repository.createRecording({
  sessionId: "session-1",
  mimeType: "audio/webm;codecs=opus",
  extension: "webm",
  startedAt: "2026-09-08T10:00:00.000Z"
});
await repository.appendChunk({ sessionId: "session-1", sequence: 1, blob: new Blob(["B"]), startMs: 10000, endMs: 20000 });
await repository.appendChunk({ sessionId: "session-1", sequence: 0, blob: new Blob(["A"]), startMs: 0, endMs: 10000 });
await repository.appendChunk({ sessionId: "session-1", sequence: 0, blob: new Blob(["A-duplicate"]), startMs: 0, endMs: 10000 });
const manifest = await repository.finalizeRecording({ sessionId: "session-1", durationMs: 20000 });
const playable = await repository.getPlayableBlob("session-1");
if (manifest.status !== "ready" || manifest.chunkCount !== 2) process.exit(1);
if (await playable.text() !== "AB") process.exit(2);
```

同时增加 `normalizeSession()` 合约：传入旧课堂记录时 `audio` 为 `null` 或 `status: "unavailable"`，传入新记录时保留 `audio.enabled/status/storage/mimeType/chunkCount/bytes/durationMs`。

- [ ] **Step 2: Run the test and verify it fails for the missing interface**

Run: `python3 -m pytest tests/test_frontend_audio_storage.py -q`

Expected: FAIL because `createAudioRepository`/`EchoAudioRepository` 尚未定义，而不是 Node 语法错误。

- [ ] **Step 3: Implement the storage-agnostic repository**

在 `static/audio-storage.js` 中实现 `createAudioRepository(dependencies)`，默认创建 `window.EchoAudioRepository`。仓储层只调用后端的以下内部方法：

```javascript
backend.createManifest(manifest)
backend.getManifest(sessionId)
backend.putChunk({ sessionId, sequence, blob, startMs, endMs })
backend.listChunks(sessionId)
backend.delete(sessionId)
```

实现要求：`appendChunk` 先读取现有清单，若清单中已有相同序号则直接返回；写入后递增 `chunkCount` 和 `bytes`，并保持 `status: "recording"`。`getPlayableBlob` 按数字 `sequence` 排序后执行 `new Blob(blobs, { type: manifest.mimeType })`。`finalizeRecording` 只有在所有调用方写入 Promise 已由录音器等待后才设置 `status: "ready"`。

- [ ] **Step 4: Upgrade IndexedDB without breaking old sessions**

在 `static/storage.js` 将数据库版本从 `1` 升到 `2`，保留 `sessions`，新增 `audioManifests` 和 `audioChunks` object store。`audioChunks` 使用组合键 `["sessionId", "sequence"]` 或等价的稳定复合字符串键；读取时必须按 `sequence` 排序。`normalizeSession()` 为旧记录补上 `audio: null`，新记录保留音频清单但不把 Blob 写进 `sessions`。

向 `EchoStore` 增加内部持久化方法供 `audio-storage.js` 的 IndexedDB 后端使用：

```javascript
saveAudioManifest(manifest)
getAudioManifest(sessionId)
saveAudioChunk(chunk)
listAudioChunks(sessionId)
deleteAudioAssets(sessionId)
```

这些方法继续复用现有的数据库打开和事务关闭逻辑，并在数据库不支持时返回可读错误。

- [ ] **Step 5: Add OPFS backend selection and fallback**

在 `audio-storage.js` 中检查 `navigator.storage.getDirectory` 和当前来源能力；可用时选择 OPFS 后端，每节课使用独立目录和有序分片文件；不可用或初始化失败时选择 `EchoStore` 的 IndexedDB 后端。OPFS 文件名只使用 `sessionId` 和数字序号，不使用课程标题。

`getUsage()` 调用 `navigator.storage.estimate()`；浏览器没有该 API 时返回 `null`，不能把 `null` 当作剩余空间为零。

- [ ] **Step 6: Run focused tests and static checks**

Run: `python3 -m pytest tests/test_frontend_audio_storage.py tests/test_frontend_contract.py -q`

Expected: PASS，且输出没有失败项。再运行 `node --check static/audio-storage.js` 和 `git diff --check`。

- [ ] **Step 7: Commit the storage layer**

```bash
git add static/audio-storage.js static/storage.js tests/test_frontend_audio_storage.py tests/test_frontend_contract.py
git commit -m "feat: add local audio asset storage"
```

---

### Task 2: 实现 MediaRecorder 分片适配器

**Files:**
- Create: `static/audio-recorder.js`
- Create: `tests/test_frontend_audio_recorder.py`
- Modify: `templates/live-transcript.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Task 1 的 `EchoAudioRepository`，现有 `MediaStream`。
- Produces: `EchoAudioRecorder.create()`，可等待的 `start()`/`stop()`，有序分片写入和真实 MIME 类型清单。

- [ ] **Step 1: Write the failing recorder test with a fake MediaRecorder**

在 `tests/test_frontend_audio_recorder.py` 中用 Node `vm` 加载脚本，注入一个假 `MediaRecorder`：`start(10000)` 记录 timeslice，随后触发两个 `dataavailable` 事件；`stop()` 触发最后一个事件和 `stop` 事件。注入可控 `now()`，断言：

```javascript
const recorder = EchoAudioRecorder.create({ stream: {}, sessionId: "s1", repository, now: clock.now });
await recorder.start();
fakeRecorder.emitChunk("A");
clock.advance(10000);
fakeRecorder.emitChunk("B");
const manifest = await recorder.stop();
if (fakeRecorder.timeslice !== 10000) process.exit(1);
if (repository.chunks.map(chunk => chunk.sequence).join(",") !== "0,1") process.exit(2);
if (manifest.status !== "ready" || manifest.chunkCount !== 2) process.exit(3);
```

再增加一个延迟写入测试：让第二个 `appendChunk` 延迟完成，断言 `stop()` 的 Promise 在第二个写入完成前不会 resolve。

- [ ] **Step 2: Run the recorder test and verify the expected failure**

Run: `python3 -m pytest tests/test_frontend_audio_recorder.py -q`

Expected: FAIL because `static/audio-recorder.js` 尚未提供 `EchoAudioRecorder.create`。

- [ ] **Step 3: Implement MIME selection and recorder state**

在 `static/audio-recorder.js` 实现候选 MIME 顺序：`audio/webm;codecs=opus`、`audio/webm`、`audio/mp4`、`audio/ogg;codecs=opus`。只使用 `MediaRecorder.isTypeSupported()` 返回真的类型；全部不支持时抛出带有 `MEDIA_RECORDER_UNSUPPORTED` 的错误。

`create()` 返回的对象维护 `idle/recording/stopping/stopped/failed` 状态。`start()` 创建清单后执行 `mediaRecorder.start(10000)`；`dataavailable` 忽略空 Blob，为每个非空 Blob 生成递增 `sequence`、基于 `now()` 的 `startMs/endMs`，并把 Promise 串入单链 `writeQueue`。

- [ ] **Step 4: Implement stop ordering and failure status**

`stop()` 只允许执行一次：设置 `stopping`，调用 `mediaRecorder.stop()`，等待 `stop` 事件、最后一个 `dataavailable` 以及 `writeQueue` 全部完成，然后调用 `finalizeRecording`。写入失败时将清单标记为 `partial`/`failed`，保留已成功分片并以错误原因 reject；不得重复调用底层 `stop()`。

录音器所有监听器在结束后移除，避免一个 `MediaRecorder` 的事件污染下一节课堂。

- [ ] **Step 5: Load scripts in dependency order**

在 `templates/live-transcript.html` 中将脚本顺序改为：`storage.js`、`audio-storage.js`、`audio-buffer.js`、`audio-recorder.js`、`app.js`。增加前端合约，断言两个新脚本被页面加载，且页面包含 `MediaRecorder` 能力检查字符串。

- [ ] **Step 6: Run focused tests and syntax checks**

Run: `python3 -m pytest tests/test_frontend_audio_recorder.py tests/test_frontend_audio_storage.py tests/test_frontend_contract.py -q`

Expected: PASS。再运行 `node --check static/audio-recorder.js`、`node --check static/audio-storage.js` 和 `git diff --check`。

- [ ] **Step 7: Commit the recorder adapter**

```bash
git add static/audio-recorder.js templates/live-transcript.html tests/test_frontend_audio_recorder.py tests/test_frontend_contract.py
git commit -m "feat: record local audio in durable chunks"
```

---

### Task 3: 接入实时听课生命周期和默认设置

**Files:**
- Modify: `static/app.js`
- Modify: `templates/live-transcript.html`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Task 1 的存储仓储和 Task 2 的录音器；现有 `startListening()`、`stopListening()`、`finishSession()`。
- Produces: `state.saveAudio` 默认值、音频 readiness 状态、可等待的停止流程、带 `audio` 清单的课堂记录。

- [ ] **Step 1: Write failing UI and lifecycle contract assertions**

在 `tests/test_frontend_contract.py` 增加断言：实时页包含 `save-audio`、`audio-readiness-status`、`audio-storage-status`；`app.js` 包含 `saveAudio: true`、`EchoAudioRecorder`、`createRecording`、`正在保存原声` 和 `仅保存字幕`。这些断言在控件和逻辑尚未加入前必须失败。

- [ ] **Step 2: Run the contract test and confirm it fails for missing hooks**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: FAIL on the first missing audio hook, not on an import or test collection error。

- [ ] **Step 3: Add the default-on recording preference and visible controls**

在 `templates/live-transcript.html` 的会话设置中加入：

```html
<label class="audio-save-option" for="save-audio">
  <input id="save-audio" type="checkbox" checked>
  <span>保存原声 <small>推荐 · 仅本机</small></span>
</label>
<small id="audio-storage-status">原声会按片段持续保存在本机浏览器</small>
```

在 readiness panel 增加原声行，状态显示“可保存”“浏览器不支持”“仅字幕模式”或“检查中”。`savePreferences()`/`restorePreferences()` 读取 `saveAudio`，只有明确保存为 `false` 时才关闭；缺少旧偏好时必须为 `true`。

- [ ] **Step 4: Add preflight capability checks and privacy copy**

在 `app.js` 增加 `checkAudioReadiness()`：当 `saveAudio` 开启时检查 `window.MediaRecorder`、`EchoAudioRepository.getUsage()` 和仓储初始化；不可用时将 readiness 标记为错误，阻止默认“保存原声”静默失败，并把操作指向保存原声复选框。用户取消勾选后允许仅保存字幕，并明确更新隐私提示。

隐私文案必须区分：本地路径为“原声和字幕保存在本机”；云端路径为“实时转录会向已配置云服务发送音频，但本地原声仍只保存在本机，不建立云端录音归档”。

- [ ] **Step 5: Start the recorder from the already-authorized stream**

在 Socket.IO `start_transcription` 成功、`state.sessionId` 设置完成后：

```javascript
if (state.saveAudio) {
  state.audioRecorder = window.EchoAudioRecorder.create({
    stream: state.stream,
    sessionId: state.sessionId,
    repository: window.EchoAudioRepository,
    now: window.performance.now.bind(window.performance)
  });
  await state.audioRecorder.start();
}
```

录音器必须接收现有 `state.stream`，不能再次调用 `getUserMedia()`。启动成功后状态显示“正在保存原声”；若默认录音启动失败，立即停止本次转录并提示“原声保存不可用，请关闭保存原声后重试”。

- [ ] **Step 6: Make stop/final-save ordering explicit and idempotent**

将当前同步的 `finishSession()` 重构为 `completeSession(stopResult, audioManifest)`，增加以下状态：`audioStopPromise`、`stopResult`、`finishing`。`stopListening()` 先 `flushPendingAudio()`，并并行发起 `audioRecorder.stop()` 和 Socket.IO `stop_transcription`；只有二者都完成后才：

1. 用最终 `stopResult.segments` 更新字幕。
2. 把 `audioManifest` 合并到 `session.audio`。
3. `EchoStore.saveSession(session)`。
4. 释放 Web Audio、录音流、计时器和录音器引用。
5. 显示“本次听课已保存，原声已保存”或“字幕已保存，原声部分保存/未保存”。

Socket.IO ack 和 `transcription_stopped` 事件都调用同一个 `receiveStopResult()`，只接受第一份结果；重复事件不能重复释放资源、重复保存或重复生成课堂。

- [ ] **Step 7: Preserve transcript on audio failure**

音频写入中途失败时，调用 `EchoAudioRepository` 的状态更新并继续等待转录停止；最终课堂 `audio.status` 为 `partial`/`failed`，字幕仍然写入 `sessions`。`persistSession()` 的增量保存必须同时带上当前 `audio` 清单，不能覆盖掉已有 `chunkCount`。

- [ ] **Step 8: Add styles and run focused verification**

为保存控件、音频 readiness 行和状态提示增加桌面/移动端样式，沿用现有色彩变量和焦点样式；不改变录音主按钮布局。

Run: `python3 -m pytest tests/test_frontend_contract.py tests/test_frontend_audio_storage.py tests/test_frontend_audio_recorder.py -q`

Expected: PASS。再运行 `node --check static/app.js`、`git diff --check`，并通过浏览器确认：勾选默认存在、取消勾选后可以进入仅字幕模式、同一条麦克风流没有二次授权弹窗。

- [ ] **Step 9: Commit live integration**

```bash
git add static/app.js templates/live-transcript.html static/styles.css tests/test_frontend_contract.py
git commit -m "feat: save local audio during live lectures"
```

---

### Task 4: 构建课后播放器与字幕时间轴联动

**Files:**
- Modify: `templates/review.html`
- Modify: `static/review.js`
- Modify: `static/styles.css`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Task 1 的 `getManifest()`/`getPlayableBlob()`，课堂中的 `audio` 清单和已有字幕 `startMs/endMs`。
- Produces: 复习页播放器、音频状态、字幕点击定位、播放高亮、对象 URL 释放。

- [ ] **Step 1: Write failing review-page hook assertions**

在 `tests/test_frontend_contract.py` 中增加断言：复习页包含 `reviewAudio`、`reviewAudioStatus`、`export-audio`；`review.js` 包含 `getPlayableBlob`、`currentTime`、`timeupdate`、`revokeObjectURL`。先运行测试，确认在 UI 和逻辑加入前失败。

- [ ] **Step 2: Run the contract test and confirm the expected failure**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: FAIL on the missing player hook。

- [ ] **Step 3: Add accessible player markup and status states**

在 `templates/review.html` 的详情工具栏下方加入：

```html
<section class="review-audio" aria-labelledby="review-audio-title">
  <div class="review-audio-heading">
    <div><span class="detail-kicker">ORIGINAL AUDIO / 原声</span><h3 id="review-audio-title">回听这节课</h3></div>
    <span id="reviewAudioStatus" class="audio-status">读取中…</span>
  </div>
  <audio id="reviewAudio" controls preload="metadata" aria-label="课堂原声"></audio>
  <div class="review-audio-meta"><span id="reviewAudioMeta">音频信息读取中…</span><button id="export-audio" type="button">导出原声</button></div>
</section>
```

没有音频、部分保存、读取失败和旧课堂无 `audio` 字段时，播放器分别显示可理解的中文状态，但字幕详情继续可用。

- [ ] **Step 4: Load and release Blob URLs safely**

在 `review.js` 增加 `audioUrl` 状态和 `releaseAudioUrl()`。每次选择课堂前释放旧 URL；选择后先显示“正在读取原声”，调用 `getManifest()` 和 `getPlayableBlob()`，用 `URL.createObjectURL(blob)` 设置播放器 `src`，并显示格式、大小和时长。页面卸载时也释放 URL。

如果读取失败，设置播放器为空、状态为“原声读取失败”，保留字幕列表和编辑能力。

- [ ] **Step 5: Add subtitle seeking and active highlighting**

渲染每个字幕卡片时设置 `data-segment-index`，给时间和卡片增加键盘可操作的按钮行为。点击卡片或时间时执行：

```javascript
reviewAudio.currentTime = Math.max(0, Number(segment.startMs || 0) / 1000);
reviewAudio.play().catch(function () {});
```

监听 `timeupdate`，找到满足 `startMs <= currentTimeMs < endMs` 的段，切换 `.is-playing`；不存在 `endMs` 时使用下一段 `startMs` 或当前音频时长作为结束边界。只有自动跟随开启时滚动当前段，避免用户阅读时被强制拉走。

- [ ] **Step 6: Add original-audio export**

`export-audio` 调用 `getPlayableBlob()`，根据 `manifest.extension` 创建下载链接，文件名沿用课堂标题并清理路径字符；点击后在短延迟内 revoke URL。没有可用原声时按钮禁用并显示“暂无可导出的原声”。

- [ ] **Step 7: Style the player and active segment**

为播放器区域增加与现有 `detail-toolbar` 一致的间距、背景、边框和移动端布局；`.review-segment.is-playing` 使用低干扰的 mint 左边框/背景，不覆盖重点标记的 amber 状态；所有新增按钮保留可见 focus ring。

- [ ] **Step 8: Run focused tests and browser smoke check**

Run: `python3 -m pytest tests/test_frontend_contract.py -q` and `node --check static/review.js`.

Expected: PASS。浏览器中打开已有课堂，确认播放器、音频状态、字幕点击跳转和播放高亮均可用；切换两节课堂后检查旧对象 URL 已释放且没有控制台错误。

- [ ] **Step 9: Commit review playback**

```bash
git add templates/review.html static/review.js static/styles.css tests/test_frontend_contract.py
git commit -m "feat: add synced audio playback to review"
```

---

### Task 5: 删除、空间提示、导出和文档完善

**Files:**
- Modify: `static/review.js`
- Modify: `static/app.js`
- Modify: `templates/review.html`
- Modify: `templates/live-transcript.html`
- Modify: `static/styles.css`
- Modify: `docs/PRIVACY.md`
- Modify: `README.zh-CN.md`
- Modify: `QUICKSTART.md`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: Task 1 的 `deleteRecording()`/`getUsage()`，Task 3 的保存状态，Task 4 的播放器。
- Produces: 音频级联删除、导出和空间提示，完整的本地/云端隐私文案。

- [ ] **Step 1: Write failing deletion and documentation contract assertions**

在 `tests/test_frontend_contract.py` 增加断言：`review.js` 在删除流程中调用 `deleteRecording`，页面包含“导出原声”和“仅保存在本机”文案；隐私文档包含“清除网站数据”和“云端实时处理”说明。先运行测试确认缺失时失败。

- [ ] **Step 2: Run the contract test and confirm the expected failure**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`

Expected: FAIL on the first missing deletion/documentation hook。

- [ ] **Step 3: Make classroom deletion cascade to audio**

在删除课堂的确认回调中先执行 `EchoAudioRepository.deleteRecording(id)`，成功后再执行 `EchoStore.deleteSession(id)`；如果课堂没有音频清单，删除仍视为成功；如果音频清理失败，保留课堂记录并显示“课堂未删除，原声清理失败”。删除完成后释放当前播放器 URL，清空详情并刷新列表。

- [ ] **Step 4: Show storage usage without blocking class start**

在实时设置或复习页增加小型本地存储提示，调用 `getUsage()` 显示已用空间/估算配额；返回 `null` 时显示“浏览器未提供空间估算”。空间提示仅用于预警，不得因为无法估算而阻止本地录音。

- [ ] **Step 5: Complete privacy, export, and recovery copy**

更新文档和界面，明确写出：

- 默认保存的是本机原声，不是云端备份。
- 云端转录会向已配置服务发送实时处理所需音频。
- 清除网站数据、无痕模式、浏览器回收存储或配额不足可能导致本地原声丢失。
- 下课后可导出原声作为浏览器存储之外的备份。
- 音频保存失败时字幕仍会保留，复习页会标记原声状态。

- [ ] **Step 6: Run focused tests and docs checks**

Run: `python3 -m pytest tests/test_frontend_contract.py -q`, `node --check static/app.js`, `node --check static/review.js`, and `git diff --check`。

Expected: PASS，且文档中没有占位标记或与设计规格冲突的云端录音表述。

- [ ] **Step 7: Commit cleanup and documentation**

```bash
git add static/review.js static/app.js templates/review.html templates/live-transcript.html static/styles.css docs/PRIVACY.md README.zh-CN.md QUICKSTART.md tests/test_frontend_contract.py
git commit -m "docs: explain local audio retention and export"
```

---

### Task 6: 端到端验收与完成前验证

**Files:**
- Modify only if verification uncovers a concrete regression: files from Tasks 1–5.
- Test: all repository tests and browser smoke flow.

**Interfaces:**
- Consumes: 全部实现任务。
- Produces: 可在本地模型和云端模式下验证的完整课堂录音复习流程。

- [ ] **Step 1: Run the complete automated suite**

Run:

```bash
python3 -m pytest -q
node --check static/app.js
node --check static/review.js
node --check static/audio-storage.js
node --check static/audio-recorder.js
python3 -m compileall -q backend app.py
ruff check backend tests
git diff --check
```

Expected: pytest 0 failures，所有 `node --check`、`compileall`、`ruff` 和 `git diff --check` 命令退出码为 0。

- [ ] **Step 2: Verify local-model browser flow**

在 `http://127.0.0.1:5001/` 使用本地模型：确认“保存原声”默认勾选；启动后确认状态依次出现“正在保存原声/正在实时转录”；使用浏览器测试音频或合成麦克风输入运行至少 20 秒；结束后确认“字幕已保存、原声已保存”。

- [ ] **Step 3: Verify cloud browser flow**

在云端模式运行同样的短课流程，确认隐私文案显示云端实时处理提示，复习页仍能从本地加载原声；不得出现新的服务端录音文件或音频归档请求。

- [ ] **Step 4: Verify refresh, seeking, export, and deletion**

刷新页面，打开复习页并选择刚才的课堂；确认播放器可以播放，点击字幕能定位，播放时字幕高亮，导出原声生成正确扩展名文件；删除课堂后确认列表、播放器和本地音频资产都消失。

- [ ] **Step 5: Verify failure and legacy paths**

使用没有音频字段的旧课堂，确认仍可完整复习字幕；在浏览器中模拟 `MediaRecorder` 不可用或音频写入失败，确认用户看到明确原因、可以选择仅保存字幕，且字幕最终仍被保存。

- [ ] **Step 6: Inspect final diff and report evidence**

Run: `git status --short` and `git diff HEAD~6 --stat`，确认没有模型权重、录音文件、浏览器缓存、凭据或临时下载文件进入 Git。最终报告实际运行的命令、测试结果、浏览器验收结果，以及“本地浏览器存储不是永久备份”的限制。

---

## Plan self-review

- 规格中的默认本地保存、云端不归档、OPFS/IndexedDB 回退、分片顺序、失败保留字幕、同步回放、删除、导出、旧记录兼容、空间提示和未来扩展边界均有对应任务。
- 已扫描本计划，不包含占位文本、未定义的接口名称或不具备执行内容的步骤。
- Task 1 定义 `EchoAudioRepository`，Task 2 定义 `EchoAudioRecorder`，Task 3–5 只消费这两个接口，名称和返回类型一致。
- 不新增服务端音频 API；本计划只改浏览器端采集、存储和复习能力。
