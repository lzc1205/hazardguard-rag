"""多智能体共享状态。

对应需求文档 6.2 节。

LangGraph 的 State 是所有 Agent 唯一的通信媒介——Agent 之间不直接调用，
只通过读写 State 传递结构化数据。这保证了：
1. 执行顺序由编排器决定，流程可控可测
2. 每个环节的输入输出可追溯（写入 agent_trace）
3. 单个 Agent 出错不影响其他（状态隔离）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from app.agents.context import ContextBudgetGuard, MemoryStore, ReviewContext


def merge_lists(left: list | None, right: list | None) -> list:
    """并联合并——多个 Agent 可能同时向同一字段追加（如审查意见）。"""
    return (left or []) + (right or [])


class ReviewState(TypedDict, total=False):
    """LangGraph 状态图的数据载体。

    用 Annotated + reducer 让字段合并策略显式声明，
    否则后写的 Agent 会覆盖前一个的结果。
    """

    # ---- 输入 ----
    review_id: str
    user_id: str | None
    project_id: str | None
    document_text: str
    document_meta: dict

    # ---- Agent 中间产物 ----
    doc_summary: dict                      # 文档解析Agent
    hazard_result: dict                    # 危大识别 Agent
    retrieved_clauses: list[dict]          # 法规检索 Agent
    review_items: Annotated[list, merge_lists]      # 合规审查 Agent（可并行追加）
    expert_points: Annotated[list, merge_lists]     # 专家论证 Agent
    report: dict                           # 报告生成 Agent

    # ---- 控制流标记（条件路由依据）----
    is_hazard_project: bool | None
    is_over_scale: bool | None
    current_stage: str
    error: str | None
    degraded: bool

    # ---- 中间产物（节点间传递）----
    review_summary: dict                  # 合规审查 Agent 的汇总结果
    overall_conclusion: str | None       # 非危大分支的结论

    # ---- 观测数据（JD 成本优化 10% / 可观测 11%）----
    agent_trace: Annotated[list, merge_lists]
    token_usage: dict
    total_tokens: int


@dataclass
class AgentTraceEntry:
    """单条 Agent 执行记录。"""

    agent: str
    input_summary: str
    output_summary: str
    duration_ms: int
    tokens: int = 0
    tools_called: list[str] = field(default_factory=list)
    refusals: int = 0
    degraded: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "input_summary": self.input_summary[:200],
            "output_summary": self.output_summary[:200],
            "duration_ms": self.duration_ms,
            "tokens": self.tokens,
            "tools_called": self.tools_called,
            "refusals": self.refusals,
            "degraded": self.degraded,
            "error": self.error,
        }


class AgentTimer:
    """Agent 执行计时器——可观测性的基础组件。"""

    def __init__(self):
        self._start = time.perf_counter()

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

    def reset(self) -> None:
        self._start = time.perf_counter()


@dataclass
class AgentRuntime:
    """Agent 运行时容器，持有单次审查的全部隔离资源。

    借鉴 Spring 的 Request Scope 思路：每个审查请求一份独立实例，
    绝不复用，避免跨用户串数据。
    """

    context: ReviewContext
    budget_guard: ContextBudgetGuard
    memory: MemoryStore
    traces: list[AgentTraceEntry] = field(default_factory=list)

    @classmethod
    def create(cls, review_id: str, user_id: str | None = None) -> "AgentRuntime":
        ctx = ReviewContext(review_id=review_id, user_id=user_id)
        return cls(
            context=ctx,
            budget_guard=ContextBudgetGuard(ctx.budget),
            memory=ctx.memory,
        )

    def trace(
        self,
        agent: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: int = 0,
        tokens: int = 0,
        tools: list[str] | None = None,
        refusals: int = 0,
        degraded: bool = False,
        error: str | None = None,
    ) -> None:
        self.traces.append(
            AgentTraceEntry(
                agent=agent,
                input_summary=input_summary,
                output_summary=output_summary,
                duration_ms=duration_ms,
                tokens=tokens,
                tools_called=tools or [],
                refusals=refusals,
                degraded=degraded,
                error=error,
            )
        )

    def trace_to_dict(self) -> list[dict]:
        return [t.to_dict() for t in self.traces]

    def token_report(self) -> dict:
        return self.budget_guard.budget.usage_report()

    def total_tokens(self) -> int:
        return self.budget_guard.budget.total_consumed()

    def estimate_cost(self, price_per_1k: float = 0.002) -> float:
        """按单千 token 单价估算成本（元）。

        实际单价因模型而异，此处仅用于展示成本量级。
        """
        return round(self.total_tokens() / 1000 * price_per_1k, 4)


def summarize(text: str, limit: int = 200) -> str:
    """生成摘要用于 trace 记录——trace 存全文会让数据库迅速膨胀。"""
    if not text:
        return ""
    cleaned = " ".join(text.split())
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


def new_state(review_id: str, document_text: str = "", **kwargs: Any) -> ReviewState:
    """构造初始状态。"""
    return ReviewState(
        review_id=review_id,
        user_id=kwargs.get("user_id"),
        project_id=kwargs.get("project_id"),
        document_text=document_text,
        document_meta=kwargs.get("document_meta", {}),
        retrieved_clauses=[],
        review_items=[],
        expert_points=[],
        agent_trace=[],
        doc_summary={},
        hazard_result={},
        review_summary={},
        report={},
        is_hazard_project=None,
        is_over_scale=None,
        current_stage="init",
        error=None,
        degraded=False,
        token_usage={},
        total_tokens=0,
    )