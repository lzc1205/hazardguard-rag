"""LLM 调用层。

对应 JD「Function Calling / Tool Use 43%」与「Prompt Engineering 47%」的落地。

**设计原则：LLM 是增强项，不是必需项。**

没配 API Key 时自动降级为规则模式——审查照样能跑完，
只是说理文字用模板而非生成。这样做的好处：
1. 面试演示不会因 API 超时或额度不足而失败
2. 无 Key 的环境下 CI / 测试仍可全绿
3. LLM 不可用时的降级路径本身就是「稳定性」能力的体现

**成本控制**（JD 成本优化 10%）：
- 危大识别、完整性检查等确定性判断不调 LLM
- 只有「说理」环节调用，输入裁剪到必要的条文片段
- 全部走 OpenAI 兼容接口，可切DeepSeek / 通义 / 本地模型
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


@dataclass
class ModelPricing:
    """模型计价（元/百万token）。

    单价随平台调整，硬编码会误导用户对成本的判断。
    实测参考（2026-10）：DeepSeek-flash 离峰输入约 ¥0.003、
    输出约 ¥0.6；忙时翻倍。此处按离峰价估算，属保守偏乐观。
    """

    input_per_million: float
    output_per_million: float


# 按模型名匹配，命中不了则回退到默认价
PRICING_TABLE: dict[str, ModelPricing] = {
    "deepseek-chat": ModelPricing(0.15, 0.66),
    "deepseek-v4-pro": ModelPricing(0.15, 0.66),
    "deepseek-flash": ModelPricing(0.003, 0.60),
    "qwen-plus": ModelPricing(0.8, 2.0),
    "qwen-flash": ModelPricing(0.18, 1.8),
    "qwen3-max": ModelPricing(2.5, 10.0),
}
DEFAULT_PRICING = ModelPricing(2.0, 6.0)


def get_pricing(model: str) -> ModelPricing:
    """查模型计价。"""
    key = (model or "").lower()
    for name, p in PRICING_TABLE.items():
        if name in key:
            return p
    return DEFAULT_PRICING


@dataclass
class LLMResponse:
    """LLM 调用结果，含成本与耗时观测。"""

    content: str
    tokens_used: int
    latency_ms: int
    model: str
    success: bool = True
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []

    @property
    def cost_estimate(self) -> float:
        """按输入/输出分别计价的成本估算（元）。"""
        p = get_pricing(self.model)
        cost = (
            self.prompt_tokens * p.input_per_million
            + self.completion_tokens * p.output_per_million
        ) / 1_000_000
        # 全量 token 都在但未拆分时（部分平台不返回明细），按输入价兜底
        if not self.prompt_tokens and not self.completion_tokens:
            cost = self.tokens_used * p.input_per_million / 1_000_000
        return round(cost, 6)


class LLMClient:
    """OpenAI 兼容的 LLM 客户端。

    不依赖 LangChain——直接用 httpx 调接口，依赖更少、可控性更高。
    LangChain 的价值在Chain 编排与工具调用，那部分见 agents/mcp_client.py。
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
    ):
        # 用 is None 判断而非 `or`：显式传空字符串应表示「禁用」，
        # 而不是「回退到全局配置」。否则测试里传空 Key 想验证降级路径，
        # 却因兜底拿到了真 Key 而失败。
        self.base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self.model = model or settings.LLM_MODEL
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=self.timeout)

    @property
    def available(self) -> bool:
        """是否配置了可用的 API Key。

        默认配置是 sk-xxx 占位符，必须识别出来——
        否则会拿着假Key 去请求，每次都失败还浪费 60 秒超时。
        """
        key = (self.api_key or "").strip()
        return bool(key) and key not in ("sk-xxx", "xxx", "") and not key.startswith("sk-xxx")

    async def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用对话补全接口。

        temperature 默认 0.1：合规审查要求输出稳定可复现，
        创造性在此没有价值，反而是幻觉的来源。

        两个参数默认读 .env 配置（LLM_TEMPERATURE / LLM_MAX_TOKENS），
        显式传参会覆盖配置——调参不必改代码。
        """
        temperature = (
            settings.LLM_TEMPERATURE if temperature is None else temperature
        )
        max_tokens = settings.LLM_MAX_TOKENS if max_tokens is None else max_tokens

        if not self.available:
            return LLMResponse(
                content="",
                tokens_used=0,
                latency_ms=0,
                model=self.model,
                success=False,
                error="未配置有效的 LLM_API_KEY",
            )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start = time.perf_counter()
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

                content = data["choices"][0]["message"]["content"] or ""
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total = usage.get("total_tokens") or (prompt_tokens + completion_tokens)

                # 原生 function calling：工具调用意图在 tool_calls 字段，
                # 此时 content 通常为空——必须解析出来，否则会误判为「模型没答」
                raw_calls = data["choices"][0]["message"].get("tool_calls") or []
                tool_calls = [
                    {
                        "id": tc.get("id"),
                        "name": (tc.get("function") or {}).get("name"),
                        "arguments": (tc.get("function") or {}).get("arguments"),
                    }
                    for tc in raw_calls
                ]

                return LLMResponse(
                    content=content,
                    tokens_used=total,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    model=data.get("model", self.model),
                    tool_calls=tool_calls,
                )

            except httpx.TimeoutException as e:
                last_error = f"超时（{self.timeout}s）"
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                # 4xx 是请求本身的问题，重试无意义
                if 400 <= e.response.status_code < 500:
                    break
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {e}"

            if attempt < self.max_retries:
                time.sleep(0.5 * (2**attempt))  # 指数退避

        return LLMResponse(
            content="",
            tokens_used=0,
            latency_ms=int((time.perf_counter() - start) * 1000),
            model=self.model,
            success=False,
            error=last_error or "未知错误",
        )

    async def complete_with_prompt(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """用单条 Prompt 调用（系统提示可选）。"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()


# ---------------------------------------------------------------- 全局单例

_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """获取全局 LLM 客户端（复用连接池）。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def close_llm() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


# ---------------------------------------------------------------- 输出解析


def parse_structured(text: str) -> dict[str, str]:
    """解析「字段：值」格式的模型输出。

    Prompt 里已限定输出格式，但仍需防御性处理——
    模型偶尔会加markdown 标记或多写一句说明。

    没有用 LLM 来做解析：解析规则是确定的，
    用 LLM 解析 LLM 的输出等于再引入一次不确定性。
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        for sep in ("：", ":"):
            if sep in line:
                key, _, value = line.partition(sep)
                # markdown 加粗会留下尾部 **，需前后都清
                key = re.sub(r"[*#\-\s]", "", key)
                if key and len(key) <= 12:
                    result[key] = value.strip().rstrip("*")
                break
    return result


# 严重程度映射。
# 顺序很重要：必须先匹配「重要」再匹配「严重」——
# 否则「重要程度」这类表述会因包含「严重」二字被误判为 critical。
_SEVERITY_PATTERNS: list[tuple[str, str]] = [
    ("重要", "major"),
    ("严重", "critical"),
    ("一般", "minor"),
    ("建议", "suggestion"),
]


def extract_severity(text: str) -> str:
    """从输出中提取严重程度，映射到枚举值。

    优先匹配「严重程度：X」的显式标注——比全文找关键词可靠，
    因为说明文字里可能出现「不严重」「并非严重问题」这类否定表述。
    """
    m = re.search(r"严重程度[:：]\s*(\S+)", text)
    if m:
        value = m.group(1)
        for cn, en in _SEVERITY_PATTERNS:
            if cn in value:
                return en
        return "minor"

    # 无显式标注时才退回全文匹配
    for cn, en in _SEVERITY_PATTERNS:
        if cn in text:
            return en
    return "minor"


def extract_citation(text: str) -> str | None:
    """提取输出中的规范条款引用，用于校验模型是否编造依据。"""
    patterns = [
        r"[《【]([^》】]+)[》】][^\n]{0,20}?第[\d.]+条",
        r"(GB|JGJ|JTG|CJJ|DB\d*)\s*[\d\-—.]+",
        r"住建部令第\d+号",
        r"建办质〔\d{4}〕\d+号",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return None