# Mac MLX 实时转录质量与速度改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 修复 Mac MLX 实时字幕的绝对时间轴，减少无效推理工作，改善英文课程断句，并保证自动跟随和非默认端口启动稳定可用。

**Architecture:** 保留 Parakeet MLX 的流式推理与现有 draft/final 课堂速记结构。在 provider 边界把滚动窗口 token 的相对时间转换为录音绝对时间；稳定流不再构造未使用的完整 rolling result。前端仅由真实用户滚动意图暂停跟随；本地服务自动允许自身实际端口的 localhost origin。课后精细转录不在本次范围内。

**Tech Stack:** Python 3.12、Flask-SocketIO、Parakeet MLX、NumPy、pytest、Node.js browser-contract tests、原有无框架 JavaScript。

## Global Constraints

- 本次只修改 Mac 本地实时链路；云端转录和 Windows/CUDA 暂不接入。
- 保留原声保存、翻译队列、课后复习数据结构。
- 不把滚动窗口 token 的相对时间戳当作录音绝对时间戳。
- 每个行为变更必须先有失败测试，再写生产代码。
- 不打印 \`.env\` 中的密钥，不把音频或模型缓存加入仓库。

---

### Task 1: 建立 MLX 绝对时间轴

**Files:**
- Modify: \`tests/test_providers.py\`
- Modify: \`backend/providers/mlx_parakeet.py\`

**Interfaces:**
- Consumes: Parakeet stream 的累计 \`finalized_tokens\`、当前 \`draft_tokens\`、\`mel_buffer.shape[1]\`、模型 \`preprocessor_config.hop_length\`。
- Produces: provider 输出的 \`TranscriptSegment.start_ms/end_ms\` 使用录音绝对时间，并保留静音间隔。

- [ ] **Step 1: Write the failing tests**

增加一个 fake stream 回归测试。fake model 的 \`preprocessor_config.sample_rate\` 为 \`16000\`、\`hop_length\` 为 \`160\`；第一次 push 后 fake stream 的 mel buffer 为 101 帧，第二次 push 后为 130 帧；第二次 push 产生一个相对时间为 \`0.16..0.32\` 秒且带句号的 token。第二次 push 的预期输出为：

\`\`\`python
assert [(item.start_ms, item.end_ms) for item in result] == [(860, 1020)]
\`\`\`

再增加两个句号 token 组的间隔测试：第一组通过第二次 push 产生 \`0.16..0.32\` 秒 token，第三次 push 后 mel buffer 为 140 帧并产生相对时间 \`0.80..0.96\` 秒 token；预期第二组的绝对开始时间大于第一组结束时间至少 700ms，不能从 \`_timeline_cursor_ms\` 连续压缩。

- [ ] **Step 2: Run the focused tests and verify they fail**

\`\`\`bash
.venv/bin/pytest tests/test_providers.py -k "rolling_token_times or absolute_token_groups" -v
\`\`\`

Expected: FAIL because the current implementation uses the accumulated token duration as the group start.

- [ ] **Step 3: Implement the minimal mapping**

在 \`MlxParakeetProvider\` 中保存最近一次窗口原点。每次 \`add_audio()\` 后使用：

\`\`\`python
origin_seconds = max(
    0.0,
    self._audio_samples / sample_rate
    - mel_frames * hop_length / sample_rate,
)
\`\`\`

仅给本次新增 finalized token 创建浅拷贝并加上窗口原点；draft token 使用最近一次窗口原点转换。不要修改第三方 stream 内部 token。输出 segment 时使用 token 的绝对最小 start 和最大 end；只有 token 没有有效时间戳时才使用 cursor fallback，cursor 只能保证单调不能替代时间轴。

- [ ] **Step 4: Run the focused tests and verify they pass**

\`\`\`bash
.venv/bin/pytest tests/test_providers.py -k "rolling_token_times or absolute_token_groups" -v
\`\`\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add tests/test_providers.py backend/providers/mlx_parakeet.py
git commit -m "fix: align streaming mlx captions to recording time"
\`\`\`

### Task 2: 减少实时推理无效工作并改善断句

**Files:**
- Modify: \`tests/test_providers.py\`
- Modify: \`backend/providers/mlx_parakeet.py\`

**Interfaces:**
- Consumes: Task 1 的绝对 token 时间。
- Produces: 稳定字幕路径不读取未使用的完整 rolling result；断句优先使用标点和停顿，仅在过长时切分。

- [ ] **Step 1: Write the failing tests**

增加一个稳定 stream 测试：fake stream 提供 \`finalized_tokens\` 和 \`draft_tokens\`，其 \`result\` 属性在被访问时抛出 \`AssertionError("result should not be built")\`；provider push 应正常返回 draft/final，而不是访问该属性。

增加一个停顿断句测试：两个已确认 token 组分别为 \`" first thought."\` 和 \`" second thought."\`，两组之间绝对 token 时间间隔为 \`0.6\` 秒；provider 应输出两个 segment，不能等待 16 词上限。

- [ ] **Step 2: Run the focused tests and verify they fail**

\`\`\`bash
.venv/bin/pytest tests/test_providers.py -k "unused_full_result or run_on_pause" -v
\`\`\`

Expected: FAIL because \`push()\` 当前先读取 \`self._stream.result\`，断句当前不识别停顿。

- [ ] **Step 3: Implement the minimal speed and quality changes**

在 \`push()\` 中先判断稳定协议；稳定协议直接走 \`_push_stable_stream()\`，非稳定协议才读取 \`stream.result\`。断句规则按以下顺序执行：句号、问号、感叹号；相邻 token 间隔至少 \`0.45s\`；最后才使用“已超过 8 秒且已有至少 20 个词”的硬上限。保留现有域名中间句点保护。draft 的 32 词限制只用于展示，不用于改变历史 final 语义。

- [ ] **Step 4: Run provider tests**

\`\`\`bash
.venv/bin/pytest tests/test_providers.py -v
\`\`\`

Expected: PASS，且重复、域名句点、flush 和现有 16 词兼容测试根据新规则更新为真实行为。

- [ ] **Step 5: Commit**

\`\`\`bash
git add tests/test_providers.py backend/providers/mlx_parakeet.py
git commit -m "perf: avoid redundant mlx result decoding"
\`\`\`

### Task 3: 修复自动跟随的状态边界

**Files:**
- Modify: \`static/app.js\`
- Modify: \`tests/test_frontend_contract.py\`

**Interfaces:**
- Consumes: 现有 draft/final Socket.IO 事件和 \`scrollToLatest()\`。
- Produces: 程序滚动不会改变跟随开关；只有用户 wheel、touch 或键盘滚动意图会暂停；历史字幕仍保留，draft 只更新一行。

- [ ] **Step 1: Write the failing contract test**

在 \`tests/test_frontend_contract.py\` 增加断言：暂停函数必须检查 \`state.followScrollLock\`，滚动事件处理必须继续使用 \`returnLatest\` 而不能通过新字幕事件关闭 toggle。

- [ ] **Step 2: Run the contract test and verify it fails**

\`\`\`bash
.venv/bin/pytest tests/test_frontend_contract.py -k "follow" -v
\`\`\`

Expected: FAIL because \`followScrollLock\` 当前定义了但没有参与暂停判断。

- [ ] **Step 3: Implement the minimal fix**

修改 \`pauseFollowForUserIntent(event)\`，在 toggle 检查前忽略程序滚动保护期和非真实事件：

\`\`\`javascript
if (state.followScrollLock || (event && event.isTrusted === false) || !toggle.checked) return;
\`\`\`

所有 wheel、touchstart 和键盘调用传入 event；保留现有“回到最新”按钮和历史字幕展示，不在 \`addSegment()\` 中修改 toggle 状态。

- [ ] **Step 4: Run frontend checks**

\`\`\`bash
.venv/bin/pytest tests/test_frontend_contract.py -k "follow" -v
for file in static/*.js; do node --check "$file"; done
\`\`\`

Expected: PASS and no JavaScript syntax errors.

- [ ] **Step 5: Commit**

\`\`\`bash
git add tests/test_frontend_contract.py static/app.js
git commit -m "fix: keep auto-follow stable during programmatic scroll"
\`\`\`

### Task 4: 修复手动换端口的 Socket.IO origin 和测量误导

**Files:**
- Modify: \`backend/config.py\`
- Modify: \`tests/test_config.py\`
- Modify: \`tools/measure_caption_latency.py\`
- Modify: \`tests/test_frontend_contract.py\`
- Modify: \`docs/DEPLOYMENT.md\`

**Interfaces:**
- Consumes: \`PORT\`、已有 \`CORS_ORIGINS\`。
- Produces: 本地服务实际端口的 localhost origin 自动加入；延迟工具同时显示 window 和 streaming chunk 参数。

- [ ] **Step 1: Write the failing tests**

增加：

\`\`\`python
def test_local_port_is_allowed_when_port_is_overridden():
    config = load_config({"PORT": "5002"})
    assert "http://127.0.0.1:5002" in config.cors_origins
    assert "http://localhost:5002" in config.cors_origins
\`\`\`

增加工具契约断言，要求 \`measure_caption_latency.py\` 的输出字段包含 \`streaming_chunk_seconds\`，避免把 \`window_seconds=3.0\` 误认为当前流式块大小。

- [ ] **Step 2: Run focused tests and verify they fail**

\`\`\`bash
.venv/bin/pytest tests/test_config.py tests/test_documentation_contract.py tests/test_frontend_contract.py -k "port or latency" -v
\`\`\`

Expected: FAIL on the overridden-port origin or missing measurement field.

- [ ] **Step 3: Implement the local-runtime change**

在 \`load_config()\` 中，对本地绑定地址自动加入实际 \`PORT\` 对应的 \`http://127.0.0.1:PORT\` 和 \`http://localhost:PORT\`，同时保留用户配置的其他 origin。测量工具打印：

\`\`\`text
window_seconds=...
streaming_chunk_seconds=...
streaming_lag_seconds=...
\`\`\`

部署文档补充 \`PORT=5002 .venv/bin/python app.py\` 的裸启动示例，并说明 Docker Compose 的端口覆盖已经同步 CORS。

- [ ] **Step 4: Run focused tests**

\`\`\`bash
.venv/bin/pytest tests/test_config.py tests/test_documentation_contract.py tests/test_frontend_contract.py -k "port or latency" -v
\`\`\`

Expected: PASS.

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/config.py tests/test_config.py tools/measure_caption_latency.py tests/test_frontend_contract.py docs/DEPLOYMENT.md
git commit -m "fix: allow local socket origin on the active port"
\`\`\`

### Task 5: 全量验证和 MLflow 课程回归

**Files:**
- Modify: \`docs/LATENCY.md\` only if probe semantics or measured values changed.
- Do not add downloaded audio, model cache, credentials, or logs to the repository.

- [ ] **Step 1: Run static and unit verification**

\`\`\`bash
.venv/bin/ruff check .
.venv/bin/python -m compileall -q backend app.py tools
.venv/bin/pytest -q
for file in static/*.js; do node --check "$file"; done
\`\`\`

Expected: all commands exit 0.

- [ ] **Step 2: Run the same real audio regression**

使用 \`/tmp/rtt-mlflow-test/mlflow.wav\`，启动当前 main 的本地服务并运行：

\`\`\`bash
no_proxy='*' RTT_AUDIO=/tmp/rtt-mlflow-test/mlflow.wav RTT_LABEL='main-after-mlx-fix' .venv/bin/python tools/measure_caption_latency.py
\`\`\`

记录首个 draft、首个 confirmed、确认延迟、最终 segment 数、相邻重复数和最大字幕时间；最大字幕时间必须接近音频时长，而不是集中在前几秒。

- [ ] **Step 3: Inspect the diff and repository state**

\`\`\`bash
git diff origin/main...HEAD --stat
git status --short --branch
\`\`\`

确认只包含本计划相关代码、测试和文档，不包含 \`.env\`、模型缓存、音频或临时日志。

- [ ] **Step 4: Report evidence and remaining scope**

报告真实测试数值；如果实时速度仍低于 1.0x，记录瓶颈，下一轮再讨论更小右上下文、音频块大小或模型量化，不在本轮盲目改变模型架构。

