"""Agent 基类：统一重试、降级、Trace 记录。

对应 JD「稳定性/可靠性」12% 与「可重试：LLM 调用失败自动重试 2 次，
仍失败则标记该环节失败但不中断整体流程（降级处理）」（需求文档 6.1节）。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from app.agents.state import AgentRuntime, summarize
from app.agents.context import strip_injection

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")


class BaseAgent(ABC, Generic[TIn, TOut]):
    """所有审查 Agent 的基类。"""

    name: str = "base_agent"
    stage: str = "unknown"
    max_retries: int = 2
    retry_delay: float = 0.5

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    @abstractmethod
    async def run(self, payload: TIn) -> TOut:
        """执行 Agent 逻辑。子类实现。"""

    async def execute(self, payload: TIn) -> TOut:
        """带重试、Trace 记录与降级的统一入口。

        注意：重试只包裹「可恢复失败」（网络抖动、限流），
        业务性失败（如检索不到证据）不重试——重试也不会变好。
        """
        start = time.perf_counter()
        input_summary = summarize(self._describe_input(payload))
        tools: list[str] = []
        refusals = 0
        degraded = False

        try:
            # 预算检查：超预算则降级而非直接拒绝执行
            plan = self.runtime.budget_guard.check(self.stage, self._estimate_tokens(payload))
            if plan.get("degraded"):
                degraded = True
                tools.append(f"degraded:{','.join(plan.get('actions', []))}")

            result = None
            last_error: Exception | None = None

            for attempt in range(self.max_retries + 1):
                try:
                    result = await self.run(payload)
                    break
                except (TimeoutError, ConnectionError) as e:  # 可恢复错误
                    last_error = e
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2**attempt))
                        continue
                    # 重试用尽→ 降级返回，不中断整体流程
                    degraded = True
                    result = self.fallback(payload, last_error)
                except Exception as e:  # noqa: BLE001 业务性失败不重试
                    degraded = True
                    result = self.fallback(payload, e)
                    self.runtime.trace(
                        self.name,
                        input_summary,
                        f"执行失败已降级: {type(e).__name__}",
                        int((time.perf_counter() - start) * 1000),
                        degraded=True,
                        error=f"{type(e).__name__}: {e}",
                    )
                    return result

            tokens = self._count_tokens(result)
            self.runtime.budget_guard.record(self.stage, tokens)

            out_summary = summarize(self._describe_output(result))
            refusals = self._count_refusals(result)

            self.runtime.trace(
                self.name,
                input_summary,
                out_summary,
                int((time.perf_counter() - start) * 1000),
                tokens=tokens,
                tools=tools,
                refusals=refusals,
                degraded=degraded,
            )
            return result

        except Exception as e:  # noqa: BLE001 兜底，绝不让单个 Agent 拖垮全局
            self.runtime.trace(
                self.name,
                input_summary,
                "执行异常",
                int((time.perf_counter() - start) * 1000),
                degraded=True,
                error=f"{type(e).__name__}: {e}",
            )
            return self.fallback(payload, e)

    def fallback(self, payload: TIn, error: Exception | None) -> TOut:
        """降级返回。子类可覆盖以返回更有意义的默认结果。"""
        raise error if error else RuntimeError("agent fallback")

    # ------------------------------------------------------------ 辅助方法

    def _describe_input(self, payload: TIn) -> str:
        return str(payload)[:200]

    def _describe_output(self, result: TOut) -> str:
        return str(result)[:200]

    def _estimate_tokens(self, payload: TIn) -> int:
        from app.agents.context import estimate_tokens

        return estimate_tokens(str(payload)) * 2  # 预留输出空间

    def _count_tokens(self, result: TOut) -> int:
        from app.agents.context import estimate_tokens

        return estimate_tokens(str(result))

    def _count_refusals(self, result: TOut) -> int:
        """统计结果中的拒答数量——拒答率是本项目核心指标。"""
        if isinstance(result, dict):
            items = result.get("review_items") or result.get("refused_items") or []
            return sum(1 for i in items if isinstance(i, dict) and i.get("refused"))
        return 0

    @staticmethod
    def sanitize(text: str) -> str:
        """清理用户输入中的提示注入痕迹。"""
        return strip_injection(text)