"""LangGraph 编排器。

对应需求文档 6.3 节工作流图与6.5 节编排器设计。

流程：
    DocumentParser → HazardIdentifier → [条件:是否危大]
                                                ├─否→ 结束(非危大)
                                                └─是→ RegulationRetrieval
                                                          → ComplianceReviewer
                                                          → [条件:是否超规模]
                                                                ├─是→ ExpertArgumentation
                                                                └─否────────┐
                                                                         → ReportGenerator
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.agents.parsers import DocumentParserAgent, HazardIdentifierAgent
from app.agents.reviewers import (
    ComplianceReviewerAgent,
    ExpertArgumentationAgent,
    RegulationRetrievalAgent,
    ReportGeneratorAgent,
)
from app.agents.state import AgentRuntime, ReviewState, new_state


async def _await(coro) -> None:
    """等待一个协程，吞掉异常——进度上报不该影响主流程。"""
    try:
        await coro
    except Exception:  # noqa: BLE001
        pass


class ReviewOrchestrator:
    """审查流程编排器。

    不依赖 LLM 也能跑通全流程——这是刻意的设计：
    面试时可以稳定演示，不会因为 API 超时或额度不足而失败。
    接入 LLM 只需替换单个 Agent 的 run 实现，编排逻辑不动。
    """

    def __init__(self, runtime: AgentRuntime | None = None):
        self.runtime = runtime or AgentRuntime.create(str(uuid.uuid4()))

        # 审查真实起止时刻（UTC）。用 datetime 而非 perf_counter：
        # 统计页的「平均耗时」需要跨进程可比的绝对时间戳，
        # perf_counter 只在单进程内有意义，且无法落库。
        self._started_at = datetime.now(timezone.utc)
        self._finished_at: datetime | None = None

        # 共享同一个 runtime，保证预算与 trace 汇总在一次审查内
        self.parser = DocumentParserAgent(self.runtime)
        self.identifier = HazardIdentifierAgent(self.runtime)
        self.retriever = RegulationRetrievalAgent(self.runtime)
        self.reviewer = ComplianceReviewerAgent(self.runtime)
        self.expert = ExpertArgumentationAgent(self.runtime)
        self.reporter = ReportGeneratorAgent(self.runtime)

        self.graph = self._build_graph()

    # ------------------------------------------------------------ 图构建

    def _build_graph(self):
        g = StateGraph(ReviewState)

        # LangGraph 不会自动绑定实例方法，必须用 partial 显式绑定 self，
        # 否则节点会被当成未调用的 coroutine 对象，
        # 报错 InvalidUpdateError: Expected dict, got <coroutine object ...>
        b = functools.partial

        g.add_node("document_parser", b(self._node_document_parser))
        g.add_node("hazard_identifier", b(self._node_hazard_identifier))
        g.add_node("regulation_retrieval", b(self._node_regulation_retrieval))
        g.add_node("compliance_reviewer", b(self._node_compliance_reviewer))
        g.add_node("expert_argumentation", b(self._node_expert_argumentation))
        g.add_node("report_generator", b(self._node_report_generator))
        g.add_node("non_hazard_conclusion", b(self._node_non_hazard))

        g.set_entry_point("document_parser")

        g.add_conditional_edges(
            "hazard_identifier",
            self._route_after_identify,
            {
                "hazard": "regulation_retrieval",
                "non_hazard": "non_hazard_conclusion",
            },
        )
        g.add_edge("regulation_retrieval", "compliance_reviewer")

        g.add_conditional_edges(
            "compliance_reviewer",
            self._route_after_review,
            {
                "over_scale": "expert_argumentation",
                "normal": "report_generator",
            },
        )
        g.add_edge("expert_argumentation", "report_generator")

        g.add_edge("report_generator", END)
        g.add_edge("non_hazard_conclusion", END)

        return g.compile()

    # ------------------------------------------------------------ 路由函数

    @staticmethod
    def _route_after_identify(state: ReviewState) -> str:
        """条件路由：是否危大工程。

        直接从 hazard_result 判定，不依赖 is_hazard_project 字段——
        LangGraph 1.x 中条件路由拿到的 state 有时不含上一节点刚写入的可选字段，
        直接读会拿到默认值None 导致误判为「非危大」。
        """
        hazard = state.get("hazard_result") or {}
        return "hazard" if hazard.get("is_hazard_project") else "non_hazard"

    @staticmethod
    def _route_after_review(state: ReviewState) -> str:
        """条件路由：是否超过一定规模（决定要不要专家论证）。"""
        hazard = state.get("hazard_result") or {}
        if hazard.get("is_over_scale"):
            return "over_scale"
        # 兜底读state 字段，两处都拿不到时按普通流程走
        if state.get("is_over_scale"):
            return "over_scale"
        return "normal"

    # ------------------------------------------------------------ 节点实现

    async def _node_document_parser(self, state: ReviewState) -> dict[str, Any]:
        result = await self.parser.execute(state.get("document_text", ""))
        return {
            "doc_summary": result,
            "current_stage": "hazard_identifier",
        }

    async def _node_hazard_identifier(self, state: ReviewState) -> dict[str, Any]:
        summary = state.get("doc_summary", {}) or {}
        result = await self.identifier.execute(
            {"params": summary.get("params", {}), "text": state.get("document_text", "")[:2000]}
        )
        return {
            "hazard_result": result,
            "is_hazard_project": result.get("is_hazard_project"),
            "is_over_scale": result.get("is_over_scale"),
            "current_stage": "regulation_retrieval",
        }

    async def _node_non_hazard(self, state: ReviewState) -> dict[str, Any]:
        self.runtime.context.set("conclusion", "非危大工程")
        self.runtime.trace(
            "NonHazardConclusion",
            "识别结果：非危大工程",
            "流程提前结束，无需合规审查",
            1,
        )
        return {"current_stage": "done", "overall_conclusion": "not_applicable"}

    async def _node_regulation_retrieval(self, state: ReviewState) -> dict[str, Any]:
        summary = state.get("doc_summary", {}) or {}
        hazard = state.get("hazard_result", {}) or {}
        result = await self.retriever.execute(
            {
                "params": summary.get("params", {}),
                "categories": hazard.get("hazard_categories", []),
            }
        )
        return {"retrieved_clauses": result, "current_stage": "compliance_reviewer"}

    async def _node_compliance_reviewer(self, state: ReviewState) -> dict[str, Any]:
        result = await self.reviewer.execute(
            {
                "text": state.get("document_text", ""),
                "clauses": state.get("retrieved_clauses", []),
                "hazard": state.get("hazard_result", {}),
            }
        )
        return {
            "review_items": result.get("review_items", []),
            "review_summary": result,
            "current_stage": "expert_argumentation",
        }

    async def _node_expert_argumentation(self, state: ReviewState) -> dict[str, Any]:
        result = await self.expert.execute({"hazard": state.get("hazard_result", {})})
        return {"expert_points": result, "current_stage": "report_generator"}

    async def _node_report_generator(self, state: ReviewState) -> dict[str, Any]:
        result = await self.reporter.execute(
            {
                "review_result": state.get("review_summary", {}),
                "hazard": state.get("hazard_result", {}),
                "expert_points": state.get("expert_points", []),
            }
        )
        return {"report": result, "current_stage": "done"}

    # ------------------------------------------------------------ 执行入口

    async def run(
        self,
        document_text: str,
        user_id: str | None = None,
        project_id: str | None = None,
        review_id: str | None = None,
        on_progress: Callable[[str, dict], Any] | None = None,
    ) -> dict[str, Any]:
        """执行一次完整审查。

        实现说明：用显式的顺序编排而非完全依赖 LangGraph 调度。

        原因：LangGraph 1.2.x 的 Pregel 在绑定实例方法作为节点时，
        实测第二个及之后的节点不会被调度（节点函数从未被调用，
        但也不报错），行为难以预期。审查流程的分支是确定的两个判断，
        显式编排反而更可控、更易调试，且行为完全一致。

        图定义（build_graph）仍然保留并导出，
        便于面试展示「用 LangGraph 编排」的设计思路，
        也便于后续版本升级后切回框架调度。

        参数
        ----
        on_progress:
            步骤回调 `(agent_name, state) -> Any`，每个 Agent 执行完后调用。
            Celery 异步链路用它更新进度，让前端能显示「正在做合规审查…」
            而不是只转圈。同步链路不传。

            **可以是同步函数，也可以是协程函数** ——
            两种都支持是刻意的：调用方往往已经是 async 上下文，
            写 `async def` 更自然，但如果误写成 async 却忘了 await，
            回调会静默不执行（返回值是个从未被 await 的协程对象）。
            所以这里显式检查并调度，避免这类静默失败。
        """

        def _notify(agent: str) -> None:
            if on_progress is None:
                return
            try:
                r = on_progress(agent, state)
                if inspect.isawaitable(r):
                    # 协程回调必须真正执行，否则进度永远不更新
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        # 无运行中的事件循环（同步上下文）直接跑
                        asyncio.run(_await(r))
                        return
                    loop.create_task(_await(r))
            except Exception:  # noqa: BLE001
                # 进度上报失败不能影响审查主流程
                pass

        rid = review_id or str(uuid.uuid4())
        self.runtime.context.review_id = rid

        state = new_state(
            review_id=rid,
            document_text=document_text,
            user_id=user_id,
            project_id=project_id,
        )

        # 1. 文档解析
        state.update(await self._node_document_parser(state))
        _notify("document_parser")

        # 2. 危大识别
        state.update(await self._node_hazard_identifier(state))
        _notify("hazard_identifier")

        # 3. 条件分支：是否危大工程
        if self._route_after_identify(state) == "non_hazard":
            state.update(await self._node_non_hazard(state))
            _notify("non_hazard_conclusion")
            return self._finalize(state, rid)

        # 4. 法规检索
        state.update(await self._node_regulation_retrieval(state))
        _notify("regulation_retrieval")

        # 5. 合规审查
        state.update(await self._node_compliance_reviewer(state))
        _notify("compliance_reviewer")

        # 6. 条件分支：是否超规模（决定专家论证）
        if self._route_after_review(state) == "over_scale":
            state.update(await self._node_expert_argumentation(state))
            _notify("expert_argumentation")

        # 7. 报告生成
        state.update(await self._node_report_generator(state))
        _notify("report_generator")

        return self._finalize(state, rid)

    def _finalize(self, state: ReviewState, rid: str) -> dict[str, Any]:
        # 结束时刻在汇总前取，保证耗时包含报告生成
        self._finished_at = datetime.now(timezone.utc)
        summary = state.get("review_summary", {}) or {}
        return {
            "review_id": rid,
            "doc_summary": state.get("doc_summary", {}),
            "hazard_result": state.get("hazard_result", {}),
            "is_hazard_project": state.get("is_hazard_project"),
            "is_over_scale": state.get("is_over_scale"),
            "retrieved_clauses": state.get("retrieved_clauses", []),
            "review_items": state.get("review_items", []),
            "review_summary": summary,
            "expert_points": state.get("expert_points", []),
            "report": state.get("report", {}),
            "final_stage": state.get("current_stage"),
            "trace": self.runtime.trace_to_dict(),
            "token_usage": self.runtime.token_report(),
            "total_tokens": self.runtime.total_tokens(),
            "cost_estimate": self.runtime.estimate_cost(),
            # 真实起止时刻：落库时用它们算耗时。
            # 少了这两个字段，started_at/completed_at 会被赋成同一个
            # now，统计页的「平均耗时」永远是 0。
            "started_at": self._started_at,
            "finished_at": self._finished_at,
        }


async def run_review(
    document_text: str,
    user_id: str | None = None,
    project_id: str | None = None,
    on_progress: Callable[[str, dict], Any] | None = None,
) -> dict[str, Any]:
    """便捷入口。"""
    orchestrator = ReviewOrchestrator()
    return await orchestrator.run(
        document_text,
        user_id=user_id,
        project_id=project_id,
        on_progress=on_progress,
    )