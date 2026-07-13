from typing import Optional

from .utils import _save_to_run_folder, _emit

try:
    from log_utils import ContextLogger
except ImportError:
    ContextLogger = None  # type: ignore


def research_tool_fn(context: str, logger: Optional["ContextLogger"] = None) -> str:
    """
    Performs end-to-end research on the given context using the AI Gateway LLM.

    Uses DeepSeek V4 Pro with a deep-research system prompt to produce a
    comprehensive, structured research report.

    Args:
        context: The topic to research.
        logger: Optional ContextLogger for structured logging.
    Returns:
        A detailed research report string.
    """
    _emit(logger, "info", f"Starting Deep Research", extra={"context": context[:200]})

    prompt = (
        f"You are an expert research analyst. Your task is to produce a comprehensive, "
        f"detailed, and well-structured research report on the following topic.\n\n"
        f"TOPIC: {context}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Research thoroughly as if you had access to all human knowledge.\n"
        f"2. Include key dates, milestones, important contextual facts, and nuanced details.\n"
        f"3. Organise the report with clear headings and sections.\n"
        f"4. This will be used as source material for a documentary/storyboard script, "
        f"so make it rich with narrative-worthy details.\n"
        f"5. Write in a professional, authoritative tone.\n"
        f"6. Aim for at least 1000 words of substantive content.\n\n"
        f"Output the research report directly. No meta-commentary, no self-references."
    )

    try:
        from ai_gateway import generate

        response = generate(
            task="story",
            prompt=prompt,
            options={"max_tokens": 8192, "temperature": 0.3},
        )
        report = response.content

        _save_to_run_folder(report, "research_report.md")
        _emit(logger, "info", "Deep Research completed",
              extra={"report_length": len(report), "provider": response.provider})
        return report

    except Exception as e:
        _emit(logger, "error", f"Deep Research failed: {e}", extra={"error": str(e)})
        return f"An error occurred during research: {str(e)}"


def web_grounded_research_tool_fn(context: str, logger: Optional["ContextLogger"] = None) -> str:
    """
    Performs fast research using the AI Gateway LLM.

    The LLM is prompted to provide a factual, well-informed summary drawing on
    its training knowledge. (Note: this does not perform live web search unless
    the underlying model has native search capabilities.)

    Args:
        context: The topic to research.
        logger: Optional ContextLogger for structured logging.
    Returns:
        A concise, factual summary.
    """
    _emit(logger, "info", f"Starting Web-Grounded Research", extra={"context": context[:200]})

    prompt = (
        f"Perform a comprehensive analysis to provide a detailed, factual summary "
        f"about: {context}. Include key dates, milestones, and important contextual "
        f"facts. This will be used as a source for a documentary/storyboard script.\n\n"
        f"Output the summary directly. No meta-commentary."
    )

    try:
        from ai_gateway import generate

        response = generate(
            task="story",
            prompt=prompt,
            options={"max_tokens": 4096, "temperature": 0.3},
        )
        report = response.content

        _save_to_run_folder(report, "web_research_report.md")
        _emit(logger, "info", "Web-Grounded Research completed",
              extra={"report_length": len(report)})
        return report

    except Exception as e:
        _emit(logger, "error", f"Web-Grounded Research failed: {e}", extra={"error": str(e)})
        return f"An error occurred during web-grounded research: {str(e)}"


def web_search_research_tool_fn(
    context: str,
    time_range: str = "OneYear",
    max_results: int = 5,
    max_context_chars: int = 8000,
    logger: Optional["ContextLogger"] = None,
) -> str:
    """
    联网搜索 + DeepSeek 推理两阶段研究流水线。

    阶段 1: 调用豆包搜索 Custom API 获取最新网页搜索结果，自动清洗
              （用 Summary 字段、限制条数和总字符数）。
    阶段 2: 将清洗后的搜索结果注入 prompt，发给 DeepSeek 做深度推理，
              生成一份有来源依据的详细研究报告。

    Args:
        context: 研究主题（也是搜索关键词）。
        time_range: 搜索时间范围（OneDay/OneWeek/OneMonth/OneYear）。
        max_results: 保留的搜索结果条数上限。
        max_context_chars: 搜索结果上下文总字符数上限。
        logger: Optional ContextLogger for structured logging.

    Returns:
        基于联网搜索结果的研究报告字符串。
    """
    _emit(logger, "info", f"Starting Web-Search Research", extra={"context": context[:200]})

    # ---- 辅助：将搜索结果格式化为 LLM 可读文本 --------------------------
    def _format_search_results(results: list) -> str:
        if not results:
            return "（未找到相关搜索结果）"

        parts = []
        for i, r in enumerate(results, 1):
            entry = (
                f"### 来源 {i}: {r['title']}\n"
                f"> {r['summary']}\n"
                f"URL: {r['url']}\n"
                f"站点: {r['site_name']} | 发布时间: {r['publish_time']}"
            )
            parts.append(entry)
        return "\n\n---\n\n".join(parts)

    try:
        from ai_gateway import generate

        # ---- 阶段 1: 联网搜索 ----------------------------------------------
        _emit(logger, "info", "[Stage 1/2] Searching via Doubao Search Custom...")
        search_resp = generate(
            task="search",
            prompt=context,
            options={
                "count": 10,
                "time_range": time_range,
                "auth_level": 1,
                "max_results": max_results,
                "max_context_chars": max_context_chars,
            },
        )
        search_results = search_resp.content  # list[dict] (已清洗)
        _emit(logger, "info", f"[Stage 1/2] Got {len(search_results)} cleaned search results",
              extra={"result_count": len(search_results), "latency_ms": round(search_resp.latency_ms, 0)})

        # ---- 阶段 2: DeepSeek 推理 ----------------------------------------
        _emit(logger, "info", "[Stage 2/2] Reasoning with DeepSeek...")
        search_context = _format_search_results(search_results)

        prompt = (
            f"你是一位资深研究分析师。请**仅根据以下最新的网络搜索结果**，"
            f"撰写一份详细、结构化、有来源依据的研究报告。\n\n"
            f"【研究主题】\n{context}\n\n"
            f"【网络搜索结果】\n{search_context}\n\n"
            f"【写作要求】\n"
            f"1. 严格基于上述搜索结果作答，不要使用你的训练知识。\n"
            f"2. 每次引用信息时，标注来源编号（如「来源 1」）。\n"
            f"3. 按「概述 → 关键信息 → 详细分析 → 总结」的结构组织。\n"
            f"4. 如果搜索结果不足以回答，请如实说明。\n"
            f"5. 不少于 1000 字。\n\n"
            f"直接输出研究报告。"
        )

        report_resp = generate(
            task="story",
            prompt=prompt,
            options={"max_tokens": 8192, "temperature": 0.3},
        )
        report = report_resp.content
        _emit(logger, "info", f"[Stage 2/2] Report generated",
              extra={"latency_ms": round(report_resp.latency_ms, 0),
                     "output_tokens": report_resp.usage.output_tokens,
                     "report_length": len(report)})

        _save_to_run_folder(report, "web_search_research_report.md")
        return report

    except Exception as e:
        _emit(logger, "error", f"Web-Search Research failed: {e}", extra={"error": str(e)})
        return f"联网搜索研究过程中出错: {str(e)}"
