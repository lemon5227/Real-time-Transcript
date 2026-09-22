# 拾句 · Real-time Transcript 中文指南

这是面向留学生的实时课堂转录与课后复习工具。进入课堂前启动服务，选择本地、云端或自动路径；下课后在复习页搜索字幕、补充笔记、标记重点并导出。项目实现、验收和暂缓事项见[项目路线图](docs/ROADMAP.md)。

## 快速启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-core.txt
cp .env.example .env
./start.sh --mode auto
```

本地模型按设备分开：Apple Silicon Mac 安装 `requirements-mac.txt` 走 MLX Parakeet，Windows/Linux/Intel Mac 安装 `requirements-local.txt`；检测到 NVIDIA 独显时标准 Whisper 自动走 CUDA。轻薄 CPU 本可使用 `requirements-cloud.txt` 配置云端，自动模式会在本地不适合时使用云端。Mac 上中文课堂请直接切换云端，避免偷偷退回 CPU Whisper。首次进入课堂页后，可在“上课前检查”中查看 MLX/CUDA/CPU、模型状态并测试麦克风。完整说明请看主 [README](README.md) 与 [QUICKSTART](QUICKSTART.md)。

课堂页：`http://127.0.0.1:5001/` · 复习页：`http://127.0.0.1:5001/review`

## 一键启动与部署

本机按设备能力自动准备环境：

```bash
./quickstart.sh
```

Apple Silicon Mac 会安装 MLX，能正常执行 `nvidia-smi` 的 Windows/Linux 设备会安装标准本地运行时，其他设备默认准备云端运行时。需要强制指定时使用 `./quickstart.sh --mode local` 或 `./quickstart.sh --mode cloud`；Windows PowerShell 对应 `.\quickstart.ps1 -Mode Local`。启动器会复用 `.venv` 和 `.env`，不会覆盖现有配置，也不会询问或打印 API Key。

Docker 默认启动轻量云端容器：

```bash
cp .env.example .env
docker compose up --build
```

如果希望在 Docker 中使用标准 CPU 本地模型：

```bash
TRANSCRIPT_DOCKERFILE=Dockerfile.local DOCKER_TRANSCRIPTION_MODE=local docker compose up --build
```

Mac 的 MLX 请使用原生启动，Docker 不能直接使用宿主机 Metal。需要云端访问时，也可以点击下面的按钮部署到 Render；部署时必须填写自己的 `CLOUD_BASE_URL`、`CLOUD_API_KEY` 和 `CLOUD_TRANSCRIPTION_MODEL`：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/lemon5227/Real-time-Transcript)

端口占用时可以使用 `TRANSCRIPT_PORT=5002 docker compose up --build`，然后打开 `http://localhost:5002/`。完整平台说明见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)。

### macOS DMG 自动构建

GitHub Actions 中的 `macOS DMG` 工作流会在 `workflow_dispatch` 手动运行时生成可下载的
`拾句-macOS.dmg` 构建产物；推送匹配 `v*` 的版本标签还会自动把同一个 DMG 附加到 GitHub Release：

```bash
git tag v0.1.0
git push origin v0.1.0
```

也可以在仓库的 **Actions → macOS DMG → Run workflow** 中输入版本号后运行。当前产物未签名，首次打开请使用 **Control-click → Open**；工作流不会下载模型权重。

## 字幕延迟调优（Apple Silicon / MLX）

实时字幕默认走 **滑窗批处理**（`MLX_LIVE_MODE=windowed`）：保留最近 `MLX_WINDOW_SECONDS` 秒音频，
每 `MLX_HOP_SECONDS` 秒把整个窗口重新解码一次，窗口右端始终贴着实时边界。窗口的作用是给模型
**左侧上下文**；因为窗口右端就是实时边界，窗口越长并不会增加延迟——延迟等于
`解码耗时 + 步长`，而不是窗口长度。

```dotenv
MLX_LIVE_MODE=windowed        # windowed（默认）| streaming
MLX_WINDOW_SECONDS=18.0       # 左侧上下文：越大越通顺，但开始更慢
MLX_HOP_SECONDS=2.0           # 字幕刷新频率
```

把一段 127.3 秒的真实课堂录音按原速回放实测：字幕基本贴着实时边界出现（中位约 0 秒，
最差 3.3 秒），解码只花 0.24 倍实时。窗口会从较短的长度开始逐步长到 `MLX_WINDOW_SECONDS`，
所以一次会话的第一条字幕约 8 秒就能出现，不必等窗口填满。窗口短于 10 秒时，在安静段会
**直接返回空结果**，所以默认取 18。

`MLX_LIVE_MODE=streaming` 会切回旧的 `transcribe_stream()` 解码器。它是唯一能输出草稿
（draft）字幕的路径，但在课堂音频上输出的是不成句的乱词，而且约 1.5 倍实时（越讲越落后），
所以不再是默认值。该路径会把最后 `右上下文` 个 encoder 帧压住不确认，1 个 encoder 帧 =
`8（下采样）× 160（hop）/ 16000 = 0.08 秒`，因此确认延迟是 `MLX_STREAM_RIGHT_CONTEXT × 0.08 秒`。

`STREAMING_CHUNK_SECONDS` 只对流式 provider 生效。每次推理有约 0.4 秒的固定开销，所以
切得越碎实时字幕越顺滑，但 CPU 代价越高：3.0s 为 0.19 倍实时，1.0s 为 0.42 倍，
0.5s 为 0.82 倍——已经贴着极限，不适合做默认值。

完整实测数据、滑窗与流式的对比，以及在本机自行复测的脚本见
[`docs/LATENCY.md`](docs/LATENCY.md)。

## 翻译 API（可选）

当前翻译已经接入 Google 和 Microsoft 两个快速翻译服务。没有保存过服务偏好时默认使用 Microsoft Translator；已有用户保存的 Google 选择会保留。打开右侧设置中的“翻译”，选择“快速翻译”，即可查看和调整服务。

### Google Cloud Translation

1. 打开 [Google Cloud Translation 设置](https://docs.cloud.google.com/translate/docs/setup)，创建或选择一个 Google Cloud 项目。
2. 启用 **Cloud Translation API**，并按 Google Cloud 要求配置结算账户。
3. 进入 **API 和服务 → 凭据 → 创建凭据 → API 密钥**。
4. 建议把 API 密钥限制为只能调用 Cloud Translation API，并复制项目 ID 和 API 密钥。

Google 官方价格页当前列出标准文本翻译每月前 500,000 个字符免费，超出后再计费；免费额度和账户资格以官方价格页为准。普通 API 密钥应使用项目的 Basic v2 接口，项目 ID 在本项目中可选但建议保留：

```dotenv
TRANSLATION_GOOGLE_PROJECT_ID=your-project-id
TRANSLATION_GOOGLE_API_KEY=your-google-api-key
TRANSLATION_GOOGLE_LOCATION=global
```

官方入口：[获取凭据](https://docs.cloud.google.com/translate/docs/authentication) · [价格](https://cloud.google.com/products/translate/pricing)

### Microsoft Translator

1. 打开 [Azure Portal](https://portal.azure.com/)，创建 **Translator** 资源。
2. 选择订阅、资源组、区域和名称；个人测试可选择 **F0 免费层**（若所在区域/账户可用）。
3. 部署完成后进入资源 → **资源管理 → 密钥和终结点（Keys and Endpoint）**。
4. 复制 Key 以及 Endpoint；使用 Global endpoint 时保持默认地址即可。若使用区域终结点，再填写资源区域。

Azure 官方价格页当前列出 F0 每月 2,000,000 个字符免费；服务本身还会按订阅层级限制吞吐，超过免费额度或限制后需要升级/等待。配置如下：

```dotenv
TRANSLATION_MICROSOFT_ENDPOINT=https://api.cognitive.microsofttranslator.com
TRANSLATION_MICROSOFT_API_KEY=your-microsoft-key
TRANSLATION_MICROSOFT_REGION=
```

官方入口：[创建 Translator 资源并获取 Key](https://learn.microsoft.com/azure/ai-services/translator/how-to/create-translator-resource) · [价格](https://azure.microsoft.com/pricing/details/cognitive-services/translator/) · [服务限制](https://learn.microsoft.com/azure/ai-services/translator/service-limits)

两家的 Key 都只放在后端 `.env`，不要粘贴到浏览器前端或提交到 GitHub。Google 无 Key 公共通道仍保留作临时兜底，但可能限流；要上课稳定使用，建议配置 Azure F0 或 Google Cloud 官方 Key。

## 隐私

原声默认按 10 秒片段保存在当前浏览器本机，云端转录只会把实时处理所需的音频窗口发送到 `.env` 中配置的服务，不建立云端录音归档。翻译是独立的文本路径：可选 Google Cloud 或 Microsoft 做快速翻译，也可使用 Ollama/LM Studio 等 OpenAI-compatible 本地服务，或使用云端模型做精确翻译；翻译不会上传原声。API Key 只在后端环境变量中保存。课堂右侧的“更多设置”可以查看模型状态，并在上课前提前下载本地模型。详见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

实时翻译默认关闭。开启后，新用户默认使用 Microsoft Translator；Google Cloud 是可选服务。Google 无 Key 公共网页通道不稳定，可能遇到验证或限流，不建议依赖；需要稳定翻译时，在 `.env` 配置 `TRANSLATION_MICROSOFT_*` 或 `TRANSLATION_GOOGLE_*`。需要本地模型精翻时配置 `TRANSLATION_LOCAL_BASE_URL` 和 `TRANSLATION_LOCAL_MODEL`，需要云端模型精翻时配置 `TRANSLATION_CLOUD_*`。没有翻译配置也不影响转录、原声保存和课后复习。
