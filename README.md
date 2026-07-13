# Whiteboard Animation AI

> [!NOTE]
> **最新发布**：管线现已迁移至多供应商 AI 网关架构（DeepSeek + Qwen + MiniMax + Seedance），完全解耦 Google Gemini。SAM3 物体分割仍为可选 —— 仅需 API Key 即可运行完整管线。

一个智能代理管线，从简单的文本提示自动生成高质量、带完整旁白的白板动画视频。

## 概述

Whiteboard Animation AI 是一个完整的端到端框架。输入一个主题或上下文，它自动完成一切：研究主题、撰写引人入胜的叙事脚本、规划视觉分镜、生成自定义白板风格画作、为绘制过程制作动画、合成语音旁白，并烧录精确同步的字幕。

它采用代理方式自主运行，即 **导演代理（Director Agent）** 将用户请求分解为可管理的场景，通过统一的 **AI 网关** 将任务委派给专门的子代理/工具，最后将所有内容缝合为最终视频。

---

## 演示视频

[![观看演示](https://i1.hdslb.com/bfs/archive/e4fe881edc67986ab204e0d7461508ad8accf56b.jpg)](https://www.bilibili.com/video/BV1azNU6BEzN/)

> 点击上方图片观看白板手绘动画演示视频（Bilibili）

---

## 核心功能

- **多供应商 AI 网关**：统一网关（`ai_gateway/`）通过可配置的中间件链（日志 → 成本追踪 → 重试）将所有 AI 调用路由到各任务的最佳供应商：
  - **DeepSeek V4 Pro** — LLM 推理（导演、研究、图像提示词、旁白润色）
  - **Qwen-Image-2.0-Pro**（阿里云 DashScope）— 白板风格图像生成
  - **MiniMax Speech-2.8-HD** — 高质量 TTS，原生返回词/句级别的字幕时间戳
  - **Doubao-Seedance-2.0**（火山引擎 Ark）— 静态帧生成动态视频
  - **Doubao Search Custom** — 联网搜索，支持可配置的时间范围和权威过滤
- **集中配置**（`gateway.yaml`）：供应商、路由、重试策略、定价和数据库设置的单一来源头。环境变量注入 API Key —— 无硬编码密钥。
- **用量与成本追踪**：内建 SQLite 数据库（`ai_gateway.db`）记录每次 AI 请求的 token 数量、图片数量、字符数、时长、分辨率和费用 —— 实现完整的可观测性。
- **自动重试与指数退避**：按供应商可配置；优雅处理频率限制（429）、服务器错误（5xx）和暂时性网络故障。
- **联网搜索与研究选项**：支持 AI 驱动的深度研究和快速联网搜索（通过 Doubao Search），撰写高度详细且事实准确的脚本。
- **参考图片锚定**：为每个场景自动搜索现实实体的参考图（如历史人物或地标），提供给图像生成器以保持准确的结构还原度。
- **多语言支持**：以多种语言（如印地语、英语、西班牙语、中文）动态提示导演代理、生成脚本旁白和创建字幕。
- **SAM 3 集成**（可选）：集成最先进的 **Segment Anything Model 3** 来隔离物体边界，实现多通道白板绘制。完全可选 —— 不配置时以单通道模式运行。
- **自定义动画引擎**：将分割后的物体轮廓转化为流畅、逐笔手绘风格的白板动画。
- **快速模式**：使用多线程并行生成场景，大幅加快管线执行速度。

---

## 关键优势

- **锚定且动态的视频**：仅需 **一个文本指令或提示** 即可启动。导演代理自主处理研究、脚本撰写、场景构图、图像/视频生成、音频节奏和合成。
- **供应商灵活性**：AI 网关将管线与任何单一 AI 供应商解耦。编辑 `gateway.yaml` 即可更换供应商 —— 无需修改代码。
- **大幅节省成本**：将静态线稿动画拉伸并与音频旁白长度动态匹配。例如，一个 4 场景的项目仅需 32 秒的原始视觉素材（8 秒 × 4 场景），但动画引擎拉伸并调度素描路径，创作出完整的、高质量的 **2 分 30 秒视频**，无需昂贵的视频生成 API 调用。
- **完整可观测性**：每次 AI 调用都记录延迟、token 使用量和费用 —— 让您完全掌握管线支出。

---

## 配置与安装

### 1. 环境配置（`.env`）

在 `genai-pipeline` 文件夹中创建 `.env` 文件（或复制 `genai-pipeline/.env.example`），配置以下 API Key：

```ini
# DeepSeek — LLM 推理（导演、研究、图像提示词、旁白润色）
DEEPSEEK_API_KEY="your-deepseek-api-key-here"

# 阿里云 DashScope — Qwen-Image-2.0-Pro 图像生成
DASHSCOPE_API_KEY="your-dashscope-api-key-here"

# MiniMax — Speech-2.8-HD TTS 语音合成
MINIMAX_API_KEY="your-minimax-api-key-here"

# 火山引擎 Ark — Doubao-Seedance-2.0 视频生成
ARK_API_KEY="your-ark-api-key-here"

# 火山引擎 Doubao Search — 联网搜索（每月 500 次免费额度）
DOUBAO_SEARCH_API_KEY="your-doubao-search-api-key-here"

# 可选 — SAM3 自托管端点（留空则跳过分割阶段）
# SAM_API_URL="https://sam3-app-xxxxx.run.app/predict"
```

### 2. AI 网关配置（`gateway.yaml`）

AI 网关通过 `genai-pipeline/ai_gateway/gateway.yaml` 配置。该文件定义：

| 配置段 | 用途 |
|--------|------|
| `providers` | 每个供应商的模型名称、端点、超时和 API Key 环境变量 |
| `routes` | 将逻辑任务（`story`、`search`、`image`、`voice`、`video`）映射到供应商 |
| `retry` | 最大重试次数、退避策略（指数/固定/线性）、延迟范围 |
| `database` | 日志和成本追踪的 SQLite 路径（生产环境可切换至 PostgreSQL） |
| `pricing` | 按供应商的 CNY 定价，用于费用计算 |

您可以直接修改此文件来更换供应商、调整重试行为或更新定价 —— 无需修改代码。

### 3. SAM 3 模型托管（FastAPI & GCP Cloud Run — 可选）

白板绘制序列生成器可利用实例分割实现高级多通道绘制。我们托管了一个自包含的 FastAPI 服务器，封装了 **Segment Anything Model 3（SAM 3）**。

- **可选配置**：如果未设置 `SAM_API_URL`，管线跳过分割阶段，以单通道模式运行白板动画。新用户仅需 API Key 即可直接运行管线。
- **托管说明**：关于获取权重、配置 Docker 容器以及使用 GPU 加速器（NVIDIA L4）部署到 Google Cloud Run 的完整说明，请参阅 `sam3-hosting/` 文件夹中的 [SAM 3 托管指南](./sam3-hosting/README.md)。

### 4. Python 环境与依赖

设置 Python 环境（推荐 Conda 或 venv）并安装依赖：

- **核心 GenAI 管线**：通过根目录的 [requirements.txt](./requirements.txt) 安装：
  ```bash
  pip install -r requirements.txt
  ```
- **SAM 3 模型服务器**：如果不使用 Docker 自托管，通过 [sam3-hosting/requirements.txt](./sam3-hosting/requirements.txt) 安装。

---

## 如何运行与查看输出

### 第一步：安装 Python 依赖

```bash
# 核心管线
pip install -r requirements.txt

# SAM 3 服务器（仅在自托管时需要）
pip install -r sam3-hosting/requirements.txt
```

### 第二步：运行管线 CLI

```bash
# 进入核心代理目录
cd genai-pipeline

# 运行交互式管线脚本
python pipeline.py
```

### 第三步：交互式 CLI 配置

CLI 会引导您完成：

1. **主题/提示词**：输入视频的主题（例如"The History of Space Travel"）。
2. **研究模式**：选择 `[1]` 深度研究、`[2]` 联网搜索（快速）或 `[3]` 不研究。
3. **参考图片**：启用或禁用联网搜索视觉参考（`Y/n`）。
4. **快速模式**：启用所有场景的并行图像/音频生成以节省时间（`Y/n`）。
5. **旁白语言**：输入脚本的目标语言（如 `hindi`、`english`、`chinese`）。
6. **视频生成**：启用或禁用通过 Seedance 生成 AI 视频（`y/N`）。

### 第四步：找到输出

所有中间产物和最终输出保存在 `genai-pipeline/output/run_<时间戳>/` 下：

- **`whiteboard-animation-ai_final_video.mp4`**：完成的、拼接好的白板动画视频，包含旁白、背景绘制路径和烧录字幕。
- **`scene_<N>/`**：每个场景的独立文件夹，包含生成的原始图片、旁白音频（`.mp3`）、SAM 3 分割掩码、字幕和场景级素描视频。
- **`ai_gateway.db`**：包含请求日志和成本追踪的 SQLite 数据库（自动创建于 `genai-pipeline/` 中）。

---

## 项目架构

```
用户提示词
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│                    管线编排器                              │
│                    (pipeline.py)                          │
│                                                          │
│  研究 ──▶ 导演 ──▶ 逐场景管线 ──▶ 合并                     │
│     │              │              │                       │
│     ▼              ▼              ▼                       │
│  ┌──────────────────────────────────────────────────┐    │
│  │              AI 网关（ai_gateway/）                │    │
│  │                                                    │    │
│  │  日志中间件 ──▶ 成本中间件 ──▶ 重试中间件 ──▶ 路由    │    │
│  │                                                    │    │
│  │  故事 ──▶ DeepSeek      图像 ──▶ Qwen               │    │
│  │  语音 ──▶ MiniMax       视频 ──▶ Seedance           │    │
│  │  搜索 ──▶ Doubao Search                            │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  工具：image_prompt · image_gen · tts · draw_animation   │
│        segmentation · subtitle · merge · concat          │
└──────────────────────────────────────────────────────────┘
    │
    ▼
  最终视频 (.mp4) + 字幕 + 成本日志
```

详细架构文档请参见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)。  
AI 网关实现计划请参见 [docs/AI_Gateway_Implementation_Plan.md](./docs/AI_Gateway_Implementation_Plan.md)。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM 推理 | DeepSeek V4 Pro |
| 图像生成 | Qwen-Image-2.0-Pro（阿里云 DashScope） |
| 语音合成（TTS） | MiniMax Speech-2.8-HD |
| 视频生成 | Doubao-Seedance-2.0（火山引擎 Ark） |
| 联网搜索 | Doubao Search Custom |
| 图像分割 | SAM 3（Segment Anything Model 3）— 可选 |
| 动画引擎 | NumPy + OpenCV（自研） |
| 视频处理 | FFmpeg + OpenCV |
| 网关配置 | YAML + 环境变量 |
| 可观测性 | SQLAlchemy + SQLite（请求日志、用量、成本） |

---

## 开发路线图

我们正在积极开发新功能以扩展兼容性和部署便利性：

- **广泛模型支持**：扩展语言模型覆盖范围，新增 **Sarvam AI** 及其他供应商。
- **生产级数据库**：为多用户部署提供 PostgreSQL 支持。
- **Web UI**：基于浏览器的管线配置和运行界面。

---

## 已完成功能

- **多供应商 AI 网关**：从仅支持 Google Gemini 迁移至统一的多供应商架构，涵盖 DeepSeek、Qwen、MiniMax、Seedance 和 Doubao Search。
- **独立运行模式（无需 SAM 3 服务器）**：仅需 API Key 即可开箱运行管线。未配置 `SAM_API_URL` 时，跳过 SAM3 分割阶段，以单通道模式运行白板绘制。
- **用量与成本追踪**：基于 SQLite 记录每次 AI 请求的 token 数量、延迟和按供应商拆分的费用。
- **自动重试与指数退避**：按供应商可配置的重试策略，确保 API 调用的可靠性。
- **原生 TTS 字幕**：MiniMax Speech-2.8-HD 直接返回词/句级别的字幕时间戳，无需额外的转写步骤。
