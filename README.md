# Whiteboard Animation AI

> [!NOTE]
> **最新发布**：管线已全面升级 —— 多供应商 AI 网关（DeepSeek + Qwen / Seedream + MiniMax + Seedance / HappyHorse + Doubao Search + Doubao Embedding），全功能 Web UI，图片语义检索复用系统，分阶段评审工作流。SAM 3 物体分割仍为可选 —— 仅需 API Key 即可运行完整管线。

一个智能代理管线，从简单的文本提示自动生成高质量、带完整旁白的白板动画视频。支持 CLI 和 Web UI 两种交互方式。

## 概述

Whiteboard Animation AI 是一个完整的端到端框架。输入一个主题或上下文，它自动完成一切：研究主题、撰写引人入胜的叙事脚本、规划视觉分镜、生成自定义白板风格画作、为绘制过程制作动画、合成语音旁白，并烧录精确同步的字幕。

它采用代理方式自主运行，即 **导演代理（Director Agent）** 将用户请求分解为可管理的场景，通过统一的 **AI 网关** 将任务委派给专门的子代理/工具，最后将所有内容缝合为最终视频。

全新的 **Web UI** 提供完整的浏览器端操作体验：创建项目、实时追踪进度、分阶段评审（研究 → 导演方案 → 场景生成）、浏览历史作品、查看 API 费用统计和结构化运行日志。

---

## 演示视频

[![观看演示](https://i1.hdslb.com/bfs/archive/e4fe881edc67986ab204e0d7461508ad8accf56b.jpg)](https://www.bilibili.com/video/BV1azNU6BEzN/)

> 点击上方图片观看白板手绘动画演示视频（Bilibili）

---

## 核心功能

- **多供应商 AI 网关**：统一网关（`ai_gateway/`）通过可配置的中间件链（日志 → 成本追踪 → 重试）将所有 AI 调用路由到各任务的最佳供应商：
  - **DeepSeek V4 Pro** — LLM 推理（导演、研究、图像提示词、旁白润色、脚本压缩）
  - **Qwen-Image-2.0-Pro**（阿里云 DashScope）— 白板风格图像生成（默认）
  - **Doubao Seedream 4.5**（火山引擎 Ark）— 备选图像生成模型
  - **MiniMax Speech-2.8-HD** — 高质量 TTS，原生返回词/句级别的字幕时间戳
  - **Doubao-Seedance-2.0**（火山引擎 Ark）— 静态帧生成动态视频
  - **HappyHorse 1.0 I2V**（阿里云 DashScope）— 备选图生视频模型，支持 3–15 秒时长
  - **Doubao Search Custom** — 联网搜索，支持可配置的时间范围和权威过滤
  - **Doubao Embedding Vision**（火山引擎 Ark）— 多模态向量嵌入，驱动图片语义检索
- **全功能 Web UI**：基于 Flask 的浏览器界面，支持：
  - 控制台概览（已生成视频数、活跃任务、成功率、API 总费用）
  - 一键新建项目，配置语言、研究模式、图片/视频模型、目标时长
  - 实时任务进度追踪，分阶段状态显示
  - **分阶段评审工作流**：管线在研究完成和导演规划完成后自动暂停，等待用户在 Web UI 中审核批准或提出修改意见后继续
  - **导演方案在线编辑**：审核阶段可直接在浏览器中编辑场景脚本和视觉方案
  - 作品画廊（浏览、预览、下载已生成的视频）
  - API 费用统计面板
  - 结构化运行日志查看器
- **图片语义检索复用系统**：生成的每张图片自动计算内容哈希、生成缩略图、提取多模态嵌入向量并存入 `image_library` 表。新场景生成前先检索语义相似的已有图片 —— 命中则直接复用，大幅节省 API 调用成本。
- **集中配置**（`gateway.yaml`）：供应商、路由、重试策略、定价和数据库设置的单一来源头。环境变量注入 API Key —— 无硬编码密钥。
- **用量与成本追踪**：内建 SQLite 数据库（`ai_gateway.db`）通过 SQLAlchemy ORM 记录每次 AI 请求的 token 数量、图片数量、字符数、时长、分辨率和费用 —— 实现完整的可观测性。支持 Alembic 数据库迁移。
- **全量业务数据持久化**：任务（jobs）、运行记录（runs）、场景详情（scenes）、媒体资产（media_assets）全部通过 ORM 存入数据库，重启不丢失，支持历史回溯。
- **自动重试与指数退避**：按供应商可配置；优雅处理频率限制（429）、服务器错误（5xx）和暂时性网络故障。
- **联网搜索与研究选项**：支持 AI 驱动的深度研究和快速联网搜索（通过 Doubao Search），撰写高度详细且事实准确的脚本。
- **参考图片锚定**：为每个场景自动搜索现实实体的参考图（如历史人物或地标），提供给图像生成器以保持准确的结构还原度。
- **智能脚本压缩**：当导演生成的旁白脚本超过目标视频时长对应的字数/字符预算时，自动调用 LLM 进行智能压缩 —— 保留核心叙事、情感节奏和关键事实，裁剪冗余内容。支持 CJK 语言（按字符数）和非 CJK 语言（按单词数）的分别预算策略。
- **可配置目标时长**：用户可设定目标视频时长（180 / 240 / 300 秒），管线自动调整脚本长度和动画节奏以匹配目标。
- **多语言支持**：以多种语言（如印地语、英语、西班牙语、中文）动态提示导演代理、生成脚本旁白和创建字幕。
- **SAM 3 集成**（可选）：集成最先进的 **Segment Anything Model 3** 来隔离物体边界，实现多通道白板绘制。完全可选 —— 不配置时以单通道模式运行。
- **自定义动画引擎**：将分割后的物体轮廓转化为流畅、逐笔手绘风格的白板动画。
- **快速模式**：使用多线程并行生成场景，大幅加快管线执行速度。

---

## 关键优势

- **锚定且动态的视频**：仅需 **一个文本指令或提示** 即可启动。导演代理自主处理研究、脚本撰写、场景构图、图像/视频生成、音频节奏和合成。
- **供应商灵活性**：AI 网关将管线与任何单一 AI 供应商解耦。编辑 `gateway.yaml` 即可更换供应商 —— 无需修改代码。
- **双模式交互**：CLI 命令行适合快速测试和脚本集成；Web UI 适合完整的项目管理、评审协作和历史回溯。
- **分阶段人工审核**：研究和导演规划阶段可插入人工审核，确保脚本质量和方向正确，避免全自动"跑偏"后重来的浪费。
- **图片复用节省成本**：语义检索 + 内容哈希去重，避免为相似场景重复生成图片，显著降低 API 调用费用。
- **大幅节省成本**：将静态线稿动画拉伸并与音频旁白长度动态匹配。例如，一个 4 场景的项目仅需 32 秒的原始视觉素材（8 秒 × 4 场景），但动画引擎拉伸并调度素描路径，创作出完整的、高质量的 **2 分 30 秒视频**，无需昂贵的视频生成 API 调用。
- **完整可观测性**：每次 AI 调用都记录延迟、token 使用量和费用 —— 让您完全掌握管线支出。

---

## 配置与安装

### 1. 环境配置（`.env`）

在 `genai-pipeline` 文件夹中创建 `.env` 文件（或复制 `genai-pipeline/.env.example`），配置以下 API Key：

```ini
# DeepSeek — LLM 推理（导演、研究、图像提示词、旁白润色）
DEEPSEEK_API_KEY="your-deepseek-api-key-here"

# 阿里云 DashScope — Qwen-Image-2.0-Pro 图像生成 & HappyHorse 视频生成
DASHSCOPE_API_KEY="your-dashscope-api-key-here"

# MiniMax — Speech-2.8-HD TTS 语音合成
MINIMAX_API_KEY="your-minimax-api-key-here"

# 火山引擎 Ark — Seedance 视频生成 / Seedream 图像生成 / Embedding 嵌入
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
| `routes` | 将逻辑任务（`story`、`search`、`image`、`image_doubao`、`voice`、`video`、`video_happyhorse`、`embedding`）映射到供应商 |
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

- **核心 GenAI 管线 + Web UI**：通过根目录的 [requirements.txt](./requirements.txt) 安装：
  ```bash
  pip install -r requirements.txt
  ```
- **SAM 3 模型服务器**：如果不使用 Docker 自托管，通过 [sam3-hosting/requirements.txt](./sam3-hosting/requirements.txt) 安装。

---

## 如何运行与查看输出

### 方式一：Web UI（推荐）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 Web 服务
cd web_app
python app.py
```

浏览器访问 `http://127.0.0.1:5000`，即可在可视化界面中完成所有操作：
- 控制台查看项目概览和活跃任务
- 新建项目：配置主题、语言、研究模式、图片/视频模型、目标时长
- 实时追踪任务进度，在研究和导演阶段进行审核
- 画廊中浏览、预览和下载历史视频
- 费用统计和运行日志查看

Web 服务默认端口和调试模式可通过环境变量配置：

```bash
# Windows PowerShell
$env:WEB_PORT="8080"; $env:WEB_DEBUG="0"; python app.py

# Linux / macOS
WEB_PORT=8080 WEB_DEBUG=0 python app.py
```

### 方式二：CLI 命令行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 进入核心代理目录
cd genai-pipeline

# 3. 运行交互式管线脚本
python pipeline.py
```

### CLI 交互式配置

CLI 会引导您完成：

1. **主题/提示词**：输入视频的主题（例如"The History of Space Travel"）。
2. **研究模式**：选择 `[1]` 深度研究、`[2]` 联网搜索（快速）或 `[3]` 不研究。
3. **参考图片**：启用或禁用联网搜索视觉参考（`Y/n`）。
4. **快速模式**：启用所有场景的并行图像/音频生成以节省时间（`Y/n`）。
5. **旁白语言**：输入脚本的目标语言（如 `hindi`、`english`、`chinese`）。
6. **视频生成**：选择 AI 视频模型 —— `[1]` Seedance、`[2]` HappyHorse 或 `[N]` 跳过。
7. **导演视频提示词**：启用后由导演代理为视频生成编写提示词（`Y/n`）。
8. **目标时长**：设定目标视频时长 `[180/240/300]` 秒（默认 240 秒）。

### 找到输出

所有中间产物和最终输出保存在 `genai-pipeline/output/run_<时间戳>/` 下：

- **`whiteboard-animation-ai_final_video.mp4`**：完成的、拼接好的白板动画视频，包含旁白、背景绘制路径和烧录字幕。
- **`whiteboard-animation-ai_final_video.srt`**：合并后的字幕文件。
- **`scene_<N>_final.mp4`**：每个场景的独立最终视频。
- **`ai_gateway.db`**：包含请求日志和成本追踪的 SQLite 数据库（自动创建于 `genai-pipeline/` 中）。

---

## 项目架构

```
用户提示词 / Web UI
          │
          ▼
┌──────────────────────────────────────────────────────────────┐
│                      管线编排器                                │
│                      (pipeline.py)                            │
│                                                              │
│  研究 ──▶ [审核] ──▶ 导演 ──▶ [审核] ──▶ 逐场景管线 ──▶ 合并   │
│     │                │                │              │        │
│     ▼                ▼                ▼              ▼        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │               AI 网关（ai_gateway/）                     │  │
│  │                                                        │  │
│  │  日志中间件 ──▶ 成本中间件 ──▶ 重试中间件 ──▶ 路由       │  │
│  │                                                        │  │
│  │  故事 ──▶ DeepSeek      图像 ──▶ Qwen / Seedream        │  │
│  │  语音 ──▶ MiniMax       视频 ──▶ Seedance / HappyHorse  │  │
│  │  搜索 ──▶ Doubao Search  嵌入 ──▶ Doubao Embedding      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  工具：image_prompt · image_gen · image_library · tts         │
│        segmentation · draw_animation · video_gen              │
│        subtitle · merge · concat · script_compress            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              数据层（SQLAlchemy ORM + SQLite）           │  │
│  │                                                        │  │
│  │  jobs · runs · scenes · media_assets                    │  │
│  │  image_library · ai_request_logs · ai_usage · run_logs  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              Web UI（Flask + 原生 HTML/CSS/JS）          │  │
│  │                                                        │  │
│  │  控制台 · 新建项目 · 审核工作流 · 画廊 · 费用 · 日志      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
          │
          ▼
    最终视频 (.mp4) + 字幕 (.srt) + 成本日志
```

详细架构文档请参见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)。  
AI 网关实现计划请参见 [docs/AI_Gateway_Implementation_Plan.md](./docs/AI_Gateway_Implementation_Plan.md)。  
数据库与图片复用方案请参见 [docs/数据库接入与图片复用方案.md](./docs/数据库接入与图片复用方案.md)。  
动画生成流程详解请参见 [docs/动画生成流程详解.md](./docs/动画生成流程详解.md)。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| LLM 推理 | DeepSeek V4 Pro |
| 图像生成 | Qwen-Image-2.0-Pro（阿里云 DashScope）/ Doubao Seedream 4.5（火山引擎 Ark） |
| 语音合成（TTS） | MiniMax Speech-2.8-HD |
| 视频生成 | Doubao-Seedance-2.0（火山引擎 Ark）/ HappyHorse 1.0 I2V（阿里云 DashScope） |
| 联网搜索 | Doubao Search Custom |
| 向量嵌入 | Doubao Embedding Vision（火山引擎 Ark） |
| 图像分割 | SAM 3（Segment Anything Model 3）— 可选 |
| 动画引擎 | NumPy + OpenCV（自研） |
| 视频处理 | FFmpeg + OpenCV |
| Web 后端 | Flask + threading |
| Web 前端 | 原生 HTML/CSS/JS（无框架） |
| 数据库 ORM | SQLAlchemy 2.0 + Alembic（迁移管理） |
| 数据库 | SQLite（开发）/ PostgreSQL（生产可切换） |
| 网关配置 | YAML + 环境变量 |

---

## 开发路线图

我们正在积极开发新功能以扩展兼容性和部署便利性：

- **广泛模型支持**：扩展语言模型覆盖范围，新增 **Sarvam AI** 及其他供应商。
- **生产级数据库**：为多用户部署提供 PostgreSQL 支持。
- **Docker 一键部署**：提供包含 Web UI + Pipeline 的完整 Docker 镜像。
- **用户认证系统**：为多用户场景提供登录和权限管理。

---

## 已完成功能

- **全功能 Web UI**：基于 Flask 的浏览器端完整操作界面，包含控制台、项目管理、分阶段审核工作流、作品画廊、费用统计和运行日志。
- **图片语义检索复用系统**：基于 Doubao Embedding Vision 的多模态向量嵌入 + 内容哈希去重，自动识别并复用相似场景图片，显著降低 API 成本。
- **双图片生成模型**：Qwen-Image-2.0-Pro 和 Doubao Seedream 4.5 可供选择。
- **双视频生成模型**：Doubao-Seedance-2.0 和 HappyHorse 1.0 I2V 可供选择。
- **分阶段审核工作流**：管线的研究阶段和导演规划阶段支持人工审核介入 —— 批准、驳回修改或在线编辑方案后继续。
- **智能脚本压缩**：超出时长预算时自动调用 LLM 压缩脚本，支持 CJK/非 CJK 语言的分别预算策略。
- **可配置目标时长**：支持 180/240/300 秒目标时长设定，管线自动调整脚本和动画节奏。
- **全量业务数据持久化**：jobs、runs、scenes、media_assets 全部通过 ORM 存入数据库，重启不丢失。
- **数据库迁移管理**：通过 Alembic 管理数据库 schema 版本升级。
- **多供应商 AI 网关**：从仅支持 Google Gemini 迁移至统一的多供应商架构，涵盖 DeepSeek、Qwen、Seedream、MiniMax、Seedance、HappyHorse、Doubao Search 和 Doubao Embedding。
- **独立运行模式（无需 SAM 3 服务器）**：仅需 API Key 即可开箱运行管线。未配置 `SAM_API_URL` 时，跳过 SAM3 分割阶段，以单通道模式运行白板绘制。
- **用量与成本追踪**：基于 SQLite + ORM 记录每次 AI 请求的 token 数量、延迟和按供应商拆分的费用。
- **自动重试与指数退避**：按供应商可配置的重试策略，确保 API 调用的可靠性。
- **原生 TTS 字幕**：MiniMax Speech-2.8-HD 直接返回词/句级别的字幕时间戳，无需额外的转写步骤。
