# Real-time Transcript 中文指南

这是面向留学生的实时课堂转录与课后复习工具。进入课堂前启动服务，选择本地、云端或自动路径；下课后在复习页搜索字幕、补充笔记、标记重点并导出。

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

## 翻译 API（可选）

当前翻译已经接入 Google 和 Microsoft 两个快速翻译服务。打开右侧设置中的“翻译”，选择“快速翻译”，再选择对应服务即可。

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

实时翻译默认关闭。快速翻译默认会尝试 Google 公共通道（免 Key，但不保证稳定）；需要更稳定的官方通道时，在 `.env` 配置 `TRANSLATION_GOOGLE_*` 或 `TRANSLATION_MICROSOFT_*`。需要本地模型精翻时配置 `TRANSLATION_LOCAL_BASE_URL` 和 `TRANSLATION_LOCAL_MODEL`，需要云端模型精翻时配置 `TRANSLATION_CLOUD_*`。没有翻译配置也不影响转录、原声保存和课后复习。
