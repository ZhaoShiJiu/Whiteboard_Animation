# Output Directory

所有管线运行的产物都保存在此目录下，每次运行一个独立的子目录。

## 目录结构

```
output/
├── run_20260714_055116/                          # 按时间戳命名的运行目录
│   ├── whiteboard-animation-ai_final_video.mp4   # ★ 最终合成视频
│   ├── scene_1_final.mp4                          # 各场景的分段视频
│   ├── scene_2_final.mp4
│   ├── ...
│   ├── video_plan.json                            # 导演生成的分镜计划
│   ├── web_research_report.md                     # 联网研究报告
│   ├── prompts_log.txt                            # 图像生成提示词日志
│   ├── narration_refinement_log.txt               # 旁白润色日志
│   └── run.log                                    # 完整运行日志 (JSON lines)
├── run_20260713_150050/
└── ...
```

## Docker vs 本地运行

| 运行方式 | 输出路径 |
|---------|---------|
| **本地** (`python pipeline.py`) | `genai-pipeline/output/run_<时间戳>/` |
| **Docker** (容器内部) | `/app/genai-pipeline/output/run_<时间戳>/` |
| **Docker** (宿主机) | `./genai-pipeline/output/run_<时间戳>/`（通过 volume 挂载，与本地相同） |

## 在 Web UI 中查看

访问 `http://localhost:5000` → 点击左侧 **「作品画廊」** → 在线预览或下载视频。
