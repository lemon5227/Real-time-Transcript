# Real-time Transcript 跨平台一键部署设计

> 当前状态：跨平台启动和部署入口已有实现与自动化测试；Windows/CUDA 和云端实测按当前路线图暂缓。见[项目路线图](../../ROADMAP.md)。

## 目标

让新用户可以根据设备能力，用最少的步骤启动课堂转录：

- Apple Silicon Mac 原生使用 MLX Parakeet。
- 有 NVIDIA 独显的 Windows/Linux 设备原生使用 CUDA 加速的标准本地模型。
- 普通 CPU 设备可以使用本地 CPU 模型，也可以切换到云端转录。
- 不具备本地模型能力的设备可以通过 Render 一键部署云端版。
- Docker 提供可复现的云端/CPU 运行环境。

项目仍然只维护 `Real-time-Transcript`，不重新引入已经拆分出去的
`Auto-Subtitle-Generator-Standalone` 或 `Auto-Subtitle-on-Generative-AI`。

## 方案选择

### 方案 A：只提供 Docker 和 Render

实现最少，但无法让另一台 Mac 自动进入 MLX，也无法让独显电脑自然使用 CUDA。
用户还需要理解 Docker 与宿主机硬件的关系。

### 方案 B：只提供跨平台原生安装脚本

本地性能最好，但云端部署、环境复现和远程访问仍然需要用户自行配置。

### 方案 C：跨平台原生启动 + Docker + Render（采用）

原生启动负责发挥硬件能力，Docker 负责可复现运行，Render 负责云端一键部署。
三条路径共享同一个 Flask/Socket.IO 应用和环境变量，不复制业务逻辑。

## 用户入口

### 原生一键启动

macOS/Linux 提供 `./quickstart.sh`，Windows 提供 `quickstart.ps1`。
脚本必须幂等：重复执行不会覆盖用户的 `.env`、模型缓存或课堂数据。

默认自动检测顺序：

1. Apple Silicon macOS：创建/复用虚拟环境，安装 `requirements-mac.txt`，选择 MLX。
2. 能执行 `nvidia-smi` 的设备：创建/复用虚拟环境，安装 `requirements-local.txt`，选择标准本地模型并由运行时使用 CUDA。
3. 其他设备：安装 `requirements-cloud.txt`，选择云端模式；如果用户显式传入
   `--local`，则安装标准本地依赖并使用 CPU。

允许显式覆盖自动判断：

```text
./quickstart.sh --mode auto|local|cloud
./quickstart.ps1 -Mode auto|local|cloud
```

脚本不能安装 NVIDIA 官方驱动。检测不到可用 CUDA 时，必须给出明确提示，并让用户选择
CPU 或云端，而不是宣称已经启用 GPU。

如果 `.env` 不存在，脚本只从 `.env.example` 创建初始配置，不打印或询问 API Key，避免
密钥出现在终端历史和日志中。云端模式启动前应检查必要配置是否完整，并给出可执行的
配置提示。

### Docker 一键运行

根目录提供 `Dockerfile`、`Dockerfile.local`、`.dockerignore` 和
`docker-compose.yml`：

- 默认 `docker compose up --build` 构建轻量云端镜像，使用 `requirements-cloud.txt`。
- CPU 本地镜像通过显式的 local profile 或构建参数选择 `requirements-local.txt`。
- 容器通过 `HOST=0.0.0.0` 暴露服务，宿主机映射到 `5001`。
- `.env` 通过运行时注入，绝不复制到镜像层。
- Docker 默认不宣称支持 Apple Metal。Mac 用户要使用 MLX，使用原生
  `quickstart.sh`。
- NVIDIA Docker 加速不作为默认路径；文档说明需要宿主机驱动和 NVIDIA Container
  Toolkit，原生安装仍是独显电脑的推荐路径。

容器运行时使用生产 WSGI 命令，保持单 worker 以适配当前进程内的会话管理；Socket.IO
允许长轮询，避免依赖特定云平台的 WebSocket worker 配置。

### Render 一键部署

根目录提供 `render.yaml`，并在中文 README 中提供 Deploy to Render 按钮：

- 使用轻量云端 Docker 镜像。
- `TRANSCRIPTION_MODE=cloud`、`HOST=0.0.0.0`。
- `/api/health` 作为健康检查。
- `SECRET_KEY` 由平台生成。
- `CLOUD_BASE_URL`、`CLOUD_API_KEY`、`CLOUD_TRANSCRIPTION_MODEL` 作为部署时填写的
  私密环境变量。
- 不绑定具体转录厂商，兼容现有的 OpenAI-compatible `/audio/transcriptions` 接口。

云端部署不安装 MLX/CUDA，也不把本地模型缓存放入服务镜像。用户可以从任何电脑或手机
浏览器访问部署地址；云端原声处理仍遵循现有隐私说明。

## 代码边界

- 硬件检测只负责选择安装 profile 和启动 mode，不直接加载模型。
- ProviderFactory 继续负责最终 provider 选择和运行时校验。
- `start.sh` 继续负责启动已经准备好的环境；`quickstart` 负责准备环境后调用它。
- Docker/Render 只增加部署层，不复制 Flask 路由、Socket.IO 处理或转录逻辑。
- 所有路径都继续使用现有 `TRANSCRIPTION_MODE`、`CLOUD_*`、`LOCAL_MODEL` 等变量。

## 错误与恢复

- Python、pip 或虚拟环境不可用：显示安装依赖和可复制的修复命令。
- NVIDIA 驱动缺失或 `nvidia-smi` 失败：显示“未启用 CUDA”，允许继续 CPU/云端模式。
- Apple Silicon 依赖安装失败：提示检查 Python 版本和 MLX 依赖，不静默切换到 CPU Whisper。
- 云端配置缺失：应用仍可打开设置页，但开始课堂时显示缺失的具体变量。
- Docker 端口被占用：输出可修改的端口映射方式，不删除已有容器或数据。
- Render 云端转录 API 不可用：沿用现有错误码和前端状态提示，不伪造本地可用状态。

## 安全与隐私

- `.env` 不进入 Git 和 Docker build context 的有效内容层。
- API Key 只通过运行时环境变量注入，并由后端调用云端接口。
- README 和部署文档明确说明云端模式会把实时音频窗口发送到用户配置的端点。
- 不自动代用户创建第三方云服务账号、不收集 API Key、不新增遥测。

## 验证标准

实现完成后至少验证：

1. `bash -n quickstart.sh start.sh` 通过。
2. PowerShell 脚本可以被静态解析，且模式参数和路径逻辑完整。
3. `docker compose config` 通过；默认和 local profile 都引用正确的 Dockerfile。
4. Render Blueprint 的服务、健康检查和环境变量定义完整。
5. `PYTHONPATH=. pytest -q`、`ruff check backend tests`、`node --check static/app.js` 和
   `git diff --check` 通过。
6. 原生 auto/local/cloud 三种模式的启动命令和 README 示例一致。
7. 不同平台的状态页能显示实际运行路径，不把 Docker CPU、Mac MLX、CUDA 和云端混为同一
   个模型运行时。

## 非目标

- 不自动安装或升级 NVIDIA、系统音频驱动。
- 不在 Render 上运行 MLX/CUDA。
- 不在本次部署工作中更改字幕分句、自动跟随、翻译或复习业务逻辑。
- 不引入 Kubernetes、Terraform 或账号级多服务编排。
