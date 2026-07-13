"""
豆包搜索 Custom — 独立联网搜索 API 适配器。

API 文档: https://docs.volcengine.com/docs/87772/2272953

此 provider 只负责调用豆包搜索 Custom API 获取网页搜索结果，不参与推理。
搜索结果将被注入到 DeepSeek 的 prompt 中，实现 "搜索先行，结果注入" 的联网搜索能力。
"""

import os
import time
import uuid
from typing import Any

import requests

from ..models import GatewayRequest, GatewayResponse, UsageStats
from .base import AbstractBaseProvider

# 默认 API 地址
SEARCH_API_URL = "https://open.feedcoopapi.com/search_api/web_search"

# 搜索结果清洗的默认参数
DEFAULT_COUNT = 10
DEFAULT_MAX_RESULTS = 5
DEFAULT_MAX_CONTEXT_CHARS = 8000


class DoubaoSearchProvider(AbstractBaseProvider):
    """豆包搜索 Custom API 适配器。"""

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        api_key = os.getenv(config.get("api_key_env", ""))
        if not api_key:
            raise ValueError(
                f"豆包搜索 API key not found. "
                f"Set environment variable '{config.get('api_key_env', 'DOUBAO_SEARCH_API_KEY')}'."
            )
        self._api_key = api_key
        self._endpoint = config.get("endpoint", SEARCH_API_URL)
        self._timeout = int(config.get("timeout", 15))

    # ------------------------------------------------------------------
    def generate(self, request: GatewayRequest) -> GatewayResponse:
        """
        执行联网搜索。

        Args:
            request:
                - prompt: 搜索关键词（1-100 字符）
                - options:
                    - count: 返回条数（默认 10，最多 50）
                    - time_range: 时间范围（OneDay/OneWeek/OneMonth/OneYear 或日期范围 "YYYY-MM-DD..YYYY-MM-DD"）
                    - auth_level: 0=不限制, 1=仅高权威来源
                    - sites: 指定搜索站点（"|" 分隔）
                    - block_hosts: 屏蔽站点（"|" 分隔）

        Returns:
            GatewayResponse，content 为清洗后的搜索结果列表 list[dict]。
        """
        request_id = str(uuid.uuid4())
        start = time.time()

        # ---- 从 options 提取搜索参数 ---------------------------------------
        count = request.options.get("count", DEFAULT_COUNT)
        time_range = request.options.get("time_range")
        auth_level = request.options.get("auth_level", 0)
        sites = request.options.get("sites")
        block_hosts = request.options.get("block_hosts")

        # ---- 构建请求体 ----------------------------------------------------
        payload: dict[str, Any] = {
            "Query": request.prompt,
            "SearchType": "web",
            "Count": count,
            "ContentFormats": "markdown",
            "Filter": {
                "NeedContent": True,
                "NeedUrl": True,
            },
        }

        if time_range:
            payload["TimeRange"] = time_range

        if auth_level > 0:
            payload["AuthInfoLevel"] = auth_level

        if sites:
            payload.setdefault("Filter", {})
            payload["Filter"]["Sites"] = sites

        if block_hosts:
            payload.setdefault("Filter", {})
            payload["Filter"]["BlockHosts"] = block_hosts

        # ---- 调用 API ------------------------------------------------------
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            raw = requests.post(
                self._endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            raw.raise_for_status()
            data = raw.json()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"豆包搜索 API 请求失败: {exc}") from exc

        # ---- 提取搜索结果 --------------------------------------------------
        result = data.get("Result", {})
        web_results = result.get("WebResults", [])

        # ---- 清洗结果 ------------------------------------------------------
        cleaned = self._clean_results(web_results, request.options)

        latency = (time.time() - start) * 1000

        usage = UsageStats()
        usage.images = len(cleaned)  # 用 images 字段记录结果条数（语义复用）
        usage.cost = self.calculate_cost(usage)

        return GatewayResponse(
            request_id=request_id,
            task=request.task,
            provider=self.name,
            model=self.model,
            content=cleaned,
            usage=usage,
            latency_ms=latency,
            raw_response=data,
        )

    # ------------------------------------------------------------------
    def _clean_results(self, web_results: list, options: dict) -> list[dict]:
        """
        清洗搜索结果，适合喂给 LLM。

        策略：
        1. 优先使用 Summary 字段（500-1000 字），不用全文 Content
        2. 限制条数（默认 5 条）
        3. 限制总字符数（默认 8000 字符）

        Args:
            web_results: API 返回的原始结果列表。
            options: 可覆盖清洗参数（max_results, max_context_chars）。

        Returns:
            清洗后的结果列表，每条包含 title, url, summary, snippet, site_name, publish_time。
        """
        if not web_results:
            return []

        max_results = options.get("max_results", DEFAULT_MAX_RESULTS)
        max_chars = options.get("max_context_chars", DEFAULT_MAX_CONTEXT_CHARS)

        cleaned = []
        total_chars = 0

        for item in web_results[:max_results]:
            # 取 Summary，没有则回退到 Snippet
            summary = item.get("Summary") or item.get("Snippet") or ""

            entry = {
                "title": item.get("Title", ""),
                "url": item.get("Url", ""),
                "summary": summary,
                "snippet": item.get("Snippet", ""),
                "site_name": item.get("SiteName", ""),
                "publish_time": item.get("PublishTime", ""),
            }

            if total_chars + len(summary) > max_chars:
                break

            cleaned.append(entry)
            total_chars += len(summary)

        return cleaned

    # ------------------------------------------------------------------
    def calculate_cost(self, usage: UsageStats) -> float:
        """豆包搜索 Custom 免费额度 500 次/月，超量后按量计费。这里按 0 计算。"""
        return 0.0

    # ------------------------------------------------------------------
    def is_retryable(self, error: Exception) -> bool:
        error_str = str(error).lower()
        retryable_markers = [
            "429", "rate limit",
            "500", "502", "503", "504",
            "timeout", "connection", "reset by peer",
            "service unavailable",
        ]
        return any(marker in error_str for marker in retryable_markers)
