# Storyboard AI

> [!NOTE]
> **Latest Release**: The pipeline now runs on a multi-provider AI Gateway architecture (DeepSeek + Qwen + MiniMax + Seedance), completely decoupled from Google Gemini. SAM3 object segmentation remains optional — you can run the entire pipeline with just your API keys.

An intelligent agentic pipeline that automates the creation of high-quality, fully narrated whiteboard animation videos from a simple text prompt.

## Overview

Storyboard AI is a complete end-to-end framework. It takes in a high-level topic or context and handles everything: researching the topic, writing a compelling narrative script, planning the visual storyboard, generating custom whiteboard-style artwork, animating the drawing process, synthesizing voiceover narration, and burning perfectly timed subtitles.

It operates autonomously using an agentic approach, meaning the **Director Agent** breaks down the user request into manageable scenes, delegates tasks to specialized sub-agents/tools through a unified **AI Gateway**, and finally stitches everything back together.

### Demo Video: *What is Adhik Maas and its relation with Shalivahan Shaka (HINDI) (strictly 4 scenes)*

> [!IMPORTANT]
> The entire demo video below was generated automatically from a **single input prompt/instruction**: the title itself.

https://github.com/user-attachments/assets/433e86bc-7ad4-433b-8b09-117e1f3af9e9

**Major Pipeline Steps Executed:**
1. **Web Search**: Performed web-grounded research to gather facts about Adhik Maas and Shalivahan Shaka.
2. **Grounded Image Generation**: Generated custom whiteboard illustrations utilizing internet-grounded reference images for scene visual accuracy.
3. **Whiteboard Animation + Video Gen**: Segmented objects and calculated vector sketch contours using SAM 3 for custom drawing animation, alongside AI video generation (Doubao-Seedance-2.0) to stitch dynamic segments.
4. **Multi-Provider AI Stack**: Powered by DeepSeek V4 Pro (LLM), Qwen-Image-2.0-Pro (image), MiniMax Speech-2.8-HD (TTS), and Doubao-Seedance-2.0 (video).

---

## Core Features

- **Multi-Provider AI Gateway**: A unified gateway (`ai_gateway/`) that routes all AI calls through a configurable middleware chain (logging → cost tracking → retry) to the best provider for each task:
  - **DeepSeek V4 Pro** — LLM reasoning (Director, Research, Image Prompt, Narration Refinement)
  - **Qwen-Image-2.0-Pro** (Alibaba DashScope) — Whiteboard-style image generation
  - **MiniMax Speech-2.8-HD** — High-quality TTS with native word/sentence-level subtitles
  - **Doubao-Seedance-2.0** (Volcengine Ark) — Video generation from static frames
  - **Doubao Search Custom** — Web search with configurable time range & authority filtering
- **Centralized Configuration** (`gateway.yaml`): Single source of truth for providers, routes, retry policies, pricing, and database settings. Environment variables inject API keys — no hardcoded secrets.
- **Usage & Cost Tracking**: Built-in SQLite database (`ai_gateway.db`) logs every AI request with token counts, image counts, character counts, duration, resolution, and cost — enabling full observability.
- **Automatic Retry with Exponential Backoff**: Configurable per provider; handles rate limits (429), server errors (5xx), and transient network failures gracefully.
- **Web Search & Research Options**: Supports both AI-powered Deep Research and fast Web Search (via Doubao Search) to write highly detailed and factual scripts.
- **Reference Image Grounding**: Automatically searches the web for reference images of real-world entities (e.g., historical figures or landmarks) for each scene and feeds them to the image generator to maintain accurate structural accuracy.
- **Multi-lingual Support**: Prompts the Director, generates script narration, and creates subtitles dynamically across multiple languages (e.g., Hindi, English, Spanish, Chinese).
- **SAM 3 Integration** (Optional): Integrates the state-of-the-art **Segment Anything Model 3** to isolate object boundaries for multi-pass whiteboard drawing. Fully optional — runs in single-pass mode without it.
- **Custom Animation Engine**: Translates segmented object contours into fluid, custom stroke-by-stroke hand-drawn whiteboard animations.
- **Fast Mode**: Parallel scene generation using multi-threading for significantly faster pipeline execution.

---

## Key Advantages

- **Grounded & Dynamic Videos**: Requires **only a single text instruction or prompt** to start. The Director Agent autonomously handles research, scriptwriting, scene composition, image/video generation, audio pacing, and compilation.
- **Provider Flexibility**: The AI Gateway decouples the pipeline from any single AI vendor. Swap providers by editing `gateway.yaml` — no code changes needed.
- **Huge Cost Savings**: Stretches and paces static line-art animations dynamically to match the audio narration length. For example, a 4-scene project requires only 32 seconds of total raw visual sequences (8 seconds × 4 scenes), but the animation engine stretches and times the sketch paths to create a complete, high-quality **2 min 30 sec video** without expensive video-generation API calls.
- **Full Observability**: Every AI call is logged with latency, token usage, and cost — giving you complete visibility into pipeline spend.

---

## Setup & Configuration

### 1. Environment Configuration (`.env`)

Create a `.env` file in the `genai-pipeline` folder (or copy `genai-pipeline/.env.example`) and configure the following API keys:

```ini
# DeepSeek — LLM reasoning (Director, Research, Image Prompt, Narration Refinement)
DEEPSEEK_API_KEY="your-deepseek-api-key-here"

# Alibaba DashScope — Qwen-Image-2.0-Pro image generation
DASHSCOPE_API_KEY="your-dashscope-api-key-here"

# MiniMax — Speech-2.8-HD TTS voice synthesis
MINIMAX_API_KEY="your-minimax-api-key-here"

# Volcengine Ark — Doubao-Seedance-2.0 video generation
ARK_API_KEY="your-ark-api-key-here"

# Volcengine Doubao Search — Web search (500 free queries/month)
DOUBAO_SEARCH_API_KEY="your-doubao-search-api-key-here"

# Optional — SAM3 self-hosting endpoint (leave empty to skip segmentation)
# SAM_API_URL="https://sam3-app-xxxxx.run.app/predict"
```

### 2. AI Gateway Configuration (`gateway.yaml`)

The AI Gateway is configured via `genai-pipeline/ai_gateway/gateway.yaml`. This file defines:

| Section | Purpose |
|---------|---------|
| `providers` | Model name, endpoint, timeout, and API key env var for each provider |
| `routes` | Maps logical tasks (`story`, `search`, `image`, `voice`, `video`) to providers |
| `retry` | Max retries, backoff strategy (exponential/fixed/linear), delay bounds |
| `database` | SQLite path for logging and cost tracking (switch to PostgreSQL for production) |
| `pricing` | Per-provider pricing in CNY for cost calculation |

You can modify this file to swap providers, adjust retry behavior, or update pricing — no code changes needed.

### 3. SAM 3 Model Hosting (FastAPI & GCP Cloud Run — Optional)

The whiteboard drawing sequence generator can utilize instance segmentation for advanced multi-pass drawing. We host a self-contained FastAPI server that wraps the **Segment Anything Model 3 (SAM 3)**.

- **Optional Setup**: If no `SAM_API_URL` is configured, the pipeline skips the segmentation phase and runs the whiteboard animation in single-pass mode. This allows new users to start running the pipeline directly using just their API keys.
- **Hosting Instructions**: For complete setup instructions on obtaining weights, configuring the Docker container, and deploying to Google Cloud Run with GPU accelerators (NVIDIA L4), please refer to the detailed [SAM 3 Hosting Guide](./sam3-hosting/README.md) in the `sam3-hosting/` folder.

### 4. Python Environment & Dependencies

Set up your Python environment (Conda or venv recommended) and install the dependencies:

- **Core GenAI Pipeline**: Install via the root [requirements.txt](./requirements.txt):
  ```bash
  pip install -r requirements.txt
  ```
- **SAM 3 Model Server**: Install via [sam3-hosting/requirements.txt](./sam3-hosting/requirements.txt) if self-hosting without Docker.

---

## How to Run & View Outputs

### Step 1: Install Python Dependencies

```bash
# Core pipeline
pip install -r requirements.txt

# SAM 3 server (only if self-hosting)
pip install -r sam3-hosting/requirements.txt
```

### Step 2: Run the Pipeline CLI

```bash
# Navigate to the core agent directory
cd genai-pipeline

# Run the interactive pipeline script
python pipeline.py
```

### Step 3: Interactive CLI Setup

The CLI will guide you through:

1. **Context/Prompt**: Enter the main topic for your video (e.g., "The History of Space Travel").
2. **Research Mode**: Choose between `[1]` Deep Research, `[2]` Web Search (Fast), or `[3]` None.
3. **Reference Images**: Enable or disable internet search for visual references (`Y/n`).
4. **Fast Mode**: Enable parallel image/audio generation for all scenes to save time (`Y/n`).
5. **Narration Language**: Enter the target language for the script (e.g., `hindi`, `english`, `chinese`).
6. **Video Generation**: Enable or disable AI video generation via Seedance (`y/N`).

### Step 4: Locate Outputs

All intermediate assets and final outputs are saved under `genai-pipeline/output/run_<timestamp>/`:

- **`storyboard_final_video.mp4`**: The completed, stitched whiteboard animation video with narration, background drawing paths, and burned subtitles.
- **`scene_<N>/`**: Individual folders for each scene containing the raw generated images, voiceover audio (`.mp3`), SAM 3 segmentation masks, subtitles, and scene-level sketch videos.
- **`ai_gateway.db`**: SQLite database with request logs and cost tracking (automatically created in `genai-pipeline/`).

---

## Project Architecture

```
User Prompt
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│                    Pipeline Orchestrator                  │
│                    (pipeline.py)                          │
│                                                          │
│  Research ──▶ Director ──▶ Per-Scene Pipeline ──▶ Merge  │
│     │              │              │                       │
│     ▼              ▼              ▼                       │
│  ┌──────────────────────────────────────────────────┐    │
│  │              AI Gateway (ai_gateway/)              │    │
│  │                                                    │    │
│  │  Logging MW ──▶ Cost MW ──▶ Retry MW ──▶ Router   │    │
│  │                                                    │    │
│  │  story ──▶ DeepSeek     image ──▶ Qwen             │    │
│  │  voice ──▶ MiniMax      video ──▶ Seedance         │    │
│  │  search ─▶ Doubao Search                           │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  Tools: image_prompt · image_gen · tts · draw_animation  │
│         segmentation · subtitle · merge · concat         │
└──────────────────────────────────────────────────────────┘
    │
    ▼
  Final Video (.mp4) + Subtitles + Cost Logs
```

For a detailed architecture document, see [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).  
For the AI Gateway implementation plan, see [docs/AI_Gateway_Implementation_Plan.md](./docs/AI_Gateway_Implementation_Plan.md).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM Reasoning | DeepSeek V4 Pro |
| Image Generation | Qwen-Image-2.0-Pro (Alibaba DashScope) |
| Voice Synthesis (TTS) | MiniMax Speech-2.8-HD |
| Video Generation | Doubao-Seedance-2.0 (Volcengine Ark) |
| Web Search | Doubao Search Custom |
| Image Segmentation | SAM 3 (Segment Anything Model 3) — optional |
| Animation Engine | NumPy + OpenCV (custom) |
| Video Processing | FFmpeg + OpenCV |
| Gateway Config | YAML + env vars |
| Observability | SQLAlchemy + SQLite (request logs, usage, cost) |

---

## Roadmap & Upcoming Features

We are actively developing new features to expand compatibility and ease of deployment:

- **Broad Model Support**: Expanding language model coverage with **Sarvam AI** and additional providers.
- **Production Database**: PostgreSQL support for multi-user deployments.
- **Web UI**: Browser-based interface for configuring and running pipelines.

---

## Completed Features

- **Multi-Provider AI Gateway**: Migrated from Google Gemini-only to a unified multi-provider architecture with DeepSeek, Qwen, MiniMax, Seedance, and Doubao Search.
- **Standalone Mode (No SAM 3 Server Needed)** : Runs the pipeline out-of-the-box using only API keys. If no `SAM_API_URL` is provided, skips SAM3 segmentation and runs whiteboard drawing in single-pass mode.
- **Usage & Cost Tracking**: SQLite-backed logging of every AI request with token counts, latency, and cost breakdown by provider.
- **Automatic Retry with Exponential Backoff**: Configurable per-provider retry policies for resilient API calls.
- **Native TTS Subtitles**: MiniMax Speech-2.8-HD returns word/sentence-level timestamps directly, eliminating the need for a separate transcription step.
