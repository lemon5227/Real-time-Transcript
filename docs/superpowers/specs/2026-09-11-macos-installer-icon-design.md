# macOS 图标与轻量安装包设计

> 当前状态：图标、DMG 构建及 CI 工作流已实现；启动器超时修复已打入本地版本。Finder 安装后的首次完整启动仍待真实验证，见[项目路线图](../../ROADMAP.md)。

## 目标

把当前面向开发者的 Python/Flask 项目包装成 macOS 用户可以直接安装和启动的应用：用户双击 `.dmg` 中的应用图标，应用在本机准备运行环境，启动本地服务并打开课堂页面。

第一版只支持 Apple Silicon macOS，优先服务英文课堂的 MLX Parakeet 路径。安装包保持轻量，不内置 Python、MLX 依赖或 Parakeet 权重；首次启动时准备运行环境，模型仍由 Hugging Face 下载并保存在用户缓存中。

## 已确认的产品决策

- 分发格式：`Real-time Transcript.dmg`，内含 `Real-time Transcript.app` 和 Applications 快捷方式。
- 安装策略：小体积安装包，首次启动准备 Python/MLX 环境；后续启动复用用户目录中的虚拟环境。
- 运行形态：应用负责启动现有 Flask/Socket.IO 服务并打开 `http://127.0.0.1:5001/`；课堂界面继续使用浏览器，不引入第二套桌面 UI。
- 图标风格：扁平、大厂产品风格，沿用项目深色课堂阅读区、暖白纸张和编辑感排版；不使用图像生成的阴影、材质或装饰。
- 图标语义：使用“Live Page”统一表达实时字幕与课后阅读：深色课堂底、暖白字幕页、实时光标和一条薄荷色当前标记；不堆叠麦克风、声波、`A/文` 或第三方翻译 Logo。
- 第三方品牌：应用图标使用通用翻译符号；Google Translate / Microsoft Translator 的品牌名称或图标只出现在翻译设置中。
- 不在本次范围：Windows/Linux 安装器、Mac App Store、自动更新、Apple Developer ID 签名与公证服务。

## 图标设计

### 视觉语言

图标使用项目课堂页的深墨蓝作为底色，以暖白构成字幕页，以薄荷绿只标记“正在实时生成”的状态。所有形状使用圆角和有限的几何线条，保持在 16px Dock 尺寸下仍可辨认。图标必须是矢量源文件，避免图像生成产生的阴影、纹理、错字和不可控细节。

### 结构

正式构图命名为“Live Page”：深墨蓝圆角方形底；中央是一张由暖白几何块构成的字幕页；页面内部使用三条不同长度的横线表示历史字幕；左侧放置一条短竖向实时光标，底部横线使用薄荷色表示当前正在生成的句子。右上角保留一个极小的页面折角，让图标同时具备课堂实时性和课后复习属性。

图标只使用深墨蓝、暖白和薄荷绿三种颜色。主图形必须是单一平面构成，不添加阴影、渐变、纹理、边框高光、声波、麦克风、翻译字母或第三方 Logo。应用图标与 favicon 可以在小尺寸时隐藏页面折角，但不能改变核心字幕页和实时标记。

### 资产

- `static/favicon.svg` 更新为正式扁平标记，继续服务浏览器标签页。
- 新增 macOS 图标 SVG 源文件，使用独立 `viewBox`、无外部字体、无位图嵌入。
- 构建脚本将 SVG 渲染为 macOS `iconset` 所需的多尺寸 PNG，并由 `iconutil` 生成 `.icns`。
- 同一套 SVG 几何规则用于 favicon 和 `.icns`，允许应用图标与网页视觉统一但按尺寸调整细节。

## 安装包架构

### App Bundle

应用包采用以下职责边界：

```text
Real-time Transcript.app/
└── Contents/
    ├── Info.plist
    ├── MacOS/RealTimeTranscript       # 启动器
    ├── Resources/
    │   ├── app/                       # Python 源码与静态资源
    │   ├── icon.icns
    │   └── launch-assets/             # 首次启动提示与脚本资源
    └── _CodeSignature/                # 仅在签名构建时存在
```

启动器不把运行时状态写入 `/Applications` 下的应用包。用户可写状态统一放在：

```text
~/Library/Application Support/Real-time Transcript/
├── .venv/
├── .env
├── logs/
├── pids/
└── cache metadata/
```

模型权重继续遵循现有 Hugging Face 缓存规则：优先使用 `HF_HUB_CACHE`，其次 `HF_HOME`，否则使用 `~/.cache/huggingface/hub`。应用不复制或打包模型权重。

### 启动流程

```text
双击 .app
  ↓
启动器解析 Resources/app 与用户数据目录
  ↓
寻找 Python 3.10–3.13
  ├─ 找到：在用户数据目录创建/复用 .venv
  └─ 未找到：显示明确的一次性安装提示
  ↓
运行 quickstart 的 Mac MLX profile
  ↓
等待 /api/health 就绪
  ↓
打开默认浏览器课堂页面
```

启动器必须支持以下场景：

1. 第一次启动：创建用户运行目录、复制 `.env.example` 为用户目录 `.env`、安装依赖。
2. 之前使用过命令行版本：发现已有兼容环境时复用，不覆盖用户 `.env`。
3. 旧环境不兼容：只重建由应用管理的 `.venv`，不删除用户模型缓存、课堂记录或 `.env`。
4. 服务已运行：不重复启动第二个服务，只打开现有课堂页面。
5. 启动失败：写入用户日志目录，并显示可读的错误原因和日志位置。

## 首次启动与依赖策略

安装包不静默修改系统。启动器应优先使用用户已经安装的兼容 Python；没有时，通过明确的 macOS 提示引导安装 Python 3.12，并在安装完成后提供“重试”。依赖安装失败必须显示失败阶段（Python、pip、MLX 依赖或模型下载），不能只显示通用错误。

模型下载仍由后端的模型管理器触发，通过 `huggingface_hub` 写入 Hugging Face 缓存。安装包任务不替换模型下载器，但必须保留模型管理页面作为下载入口，并在后续任务中补齐 `model.safetensors` 的真实字节进度、速度和剩余时间。

## 进程与退出

- 启动器记录服务 PID，避免重复启动。
- 应用退出或用户执行停止操作时，向服务发送温和停止信号；不能删除用户数据和模型缓存。
- 进程异常退出后，下一次启动清理过期 PID 文件并重新检查端口。
- 日志默认写入 `~/Library/Application Support/Real-time Transcript/logs/`，不把 API key 写入日志。

## DMG 构建

新增 macOS 构建脚本，负责：

1. 校验当前构建机为 macOS，且具备 `iconutil`、`hdiutil` 和 SVG 渲染工具。
2. 组装临时 `.app` bundle，复制源代码、启动器、`Info.plist` 和 `.icns`。
3. 用 `hdiutil create` 生成带 Applications 快捷方式的 DMG。
4. 将产物放入 `dist/`，不把本地 `.venv`、`.env`、缓存、日志和模型复制进包。
5. 在构建结束打印产物路径、包大小和是否已签名。

第一版允许未签名构建，但 README 必须说明 Gatekeeper 首次打开方式；签名、公证和 CI 发布作为后续独立任务，不伪装成已完成的安全分发。

## 测试与验收

### 自动化测试

- 检查 `Info.plist` 的 bundle identifier、显示名称、可执行文件和 `.icns` 引用。
- 检查构建脚本不会复制 `.env`、`.venv`、模型或日志。
- 检查启动器使用用户可写数据目录，并能复用/重建虚拟环境。
- 检查 Python 版本提示、端口已占用、服务启动失败和日志路径提示。
- 检查 SVG 没有外部字体、位图引用和不允许的冷色/渐变。
- 在有 macOS 工具链的机器上执行 `iconutil --convert icns` 和 DMG 构建 smoke test。

### 手工验收

- 将 DMG 拖入 Applications，双击应用后能打开课堂页面。
- 在只有 Apple Python 3.9 的机器上，应用不会再次触发 `parakeet-mlx` 的 Python 版本错误。
- 首次模型下载期间可以看到明确状态；完成后重启应用不重复下载模型。
- 应用图标在 Finder、Dock、浏览器 favicon 三处视觉一致。
- 关闭并重新打开应用不会丢失 `.env`、模型缓存和浏览器中的课后复习记录。

## 后续扩展

安装包层只依赖一个稳定的启动器协议：`source_root`、`runtime_root`、`log_root`、`health_url` 和 `stop`。未来可以在不改变课堂前端和转录 provider 的情况下替换 Python 分发方式、加入 Developer ID 签名、公证、自动更新，或增加 Windows/Linux 的同构启动器。
