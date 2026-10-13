"""Celery 审查任务。

对应 JD「异步任务」与「可观测性」。

**为什么需要异步**：
多 Agent 编排在接入 LLM 后单次审查约 1.5 秒，
但真实场景是几百页的施工方案 —— 解析 + 8 个维度并发审查 + 报告生成，
可能跑几分钟甚至更久。同步执行会导致：
1. HTTP 超时，前端拿不到结果
2. 用户以为失败而重复提交，浪费 LLM token
3. 无法展示进度，只能转圈

**关键设计：任务状态写库，前端轮询数据库**
不用 WebSocket（复杂度高、断连难处理），也不依赖 Celery 的 result backend 展示——
进度数据存在 `async_tasks` 表里，与业务数据同源，接口可直接查。

**步骤级进度**：
每个 Agent 完成就更新一次进度百分比，前端能看到「正在做合规审查…」
而不是一个不知道在干什么的转圈。
"""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.celery_app import celery_app
from app.database import async_session
from app.models.chat import AsyncTask

# 阶段 → 进度百分比。对应 6 个 Agent 的执行顺序。
STAGE_PROGRESS: list[tuple[str, int]] = [
    ("document_parser", 10),
    ("hazard_identifier", 25),
    ("regulation_retrieval", 45),
    ("compliance_reviewer", 70),
    ("expert_argumentation", 85),
    ("report_generator", 95),
]

# Agent 名称 → 前端展示用的中文标签
STAGE_LABELS: dict[str, str] = {
    "document_parser": "文档解析",
    "hazard_identifier": "危大识别",
    "regulation_retrieval": "法规检索",
    "compliance_reviewer": "合规审查",
    "expert_argumentation": "专家论证",
    "report_generator": "报告生成",
    "non_hazard_conclusion": "非危大结论",
}


def _percent_for(agent: str) -> int:
    for name, pct in STAGE_PROGRESS:
        if name in agent:
            return pct
    return 5


async def _run_task(
    review_text: str,
    *,
    title: str,
    user_id: str,
    celery_task_id: str,
    project_id: str | None,
    document_id: str | None,
    review_type: str,
) -> dict[str, Any]:
    """实际执行逻辑，异步部分集中在这里便于单测。"""
    from app.agents.orchestrator import run_review
    from app.database import get_db
    from app.models.chat import AsyncTask
    from app.services.review_persistence import (
        persist_review_result,
        update_task_progress,
    )

    async for db in get_db():
        # 任务记录必须由 Worker 自己建，不能依赖调用方建。
        # 否则从 Celery Beat / CLI / 别的服务投递时，
        # 任务能执行但没有记录可查 —— 前端轮询会拿到 404。
        task_row = (
            await db.execute(
                select(AsyncTask).where(AsyncTask.celery_task_id == celery_task_id)
            )
        ).scalar_one_or_none()
        if task_row is None:
            task_row = AsyncTask(
                celery_task_id=celery_task_id,
                task_type="review",
                status="processing",
                progress=0,
                current_stage="任务已启动",
                created_by=uuid.UUID(user_id),
            )
            db.add(task_row)
            await db.commit()

        await update_task_progress(
            db,
            celery_task_id,
            progress=5,
            stage="正在解析方案文档",
            status="processing",
        )

        try:
            # 进度回调：每完成一个 Agent 就落库一次。
            #
            # **刻意用独立 session 且不用 create_task**：
            # 早期实现用 create_task 异步上报，实测两个 bug——
            # "This transaction is closed"（与主流程抢同一 session）
            # "Event loop is closed"（审查结束循环已在收尾，
            #   残留 task 想执行就炸）
            #
            # 进度步骤只有 6 次，每次一条 UPDATE，
            # 直接 await 完全够用，而正确性是硬约束。
            async def on_progress(agent: str, _state) -> None:
                pct = _percent_for(agent)
                key = agent.split(".")[-1]
                label = STAGE_LABELS.get(key, agent)
                try:
                    async with async_session() as pdb:
                        await update_task_progress(
                            pdb,
                            celery_task_id,
                            progress=pct,
                            stage=f"正在执行{label}",
                        )
                except Exception:  # noqa: BLE001
                    # 进度上报失败不能影响审查主流程
                    pass

            result = await run_review(
                review_text,
                user_id=user_id,
                on_progress=on_progress,
            )

            review = await persist_review_result(
                db,
                result,
                title=title,
                user_id=uuid.UUID(user_id),
                project_id=uuid.UUID(project_id) if project_id else None,
                document_id=uuid.UUID(document_id) if document_id else None,
                review_type=review_type,
                status="completed",
            )

            payload = {
                "review_id": str(review.id),
                "score": review.overall_score,
                "conclusion": review.overall_conclusion,
                "items": len(result.get("review_items", [])),
                "expert_points": len(result.get("expert_points", [])),
                "is_hazard_project": result.get("is_hazard_project"),
                "is_over_scale": result.get("is_over_scale"),
                "total_tokens": result.get("total_tokens", 0),
                "cost_estimate": result.get("cost_estimate", 0),
            }

            # 结果写回任务记录，前端轮询到 completed 时能直接拿 review_id
            t = (
                await db.execute(
                    select(AsyncTask).where(AsyncTask.celery_task_id == celery_task_id)
                )
            ).scalar_one_or_none()
            if t:
                t.result = payload  # JSONB 列，直接赋 dict
                await db.commit()

            await update_task_progress(
                db,
                celery_task_id,
                progress=100,
                stage="审查完成",
                status="completed",
            )

            return payload

        except Exception as exc:  # noqa: BLE001
            # 失败记录必须用**全新的 session**：上面的 db 可能已经
            # 因异常进入失效状态（rollback 后再 commit 会抛
            # "This transaction is closed"），复用它会让失败原因
            # 丢失，前端只能看到任务永远停在 running。
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass

            try:
                async with async_session() as edb:
                    t = (
                        await edb.execute(
                            select(AsyncTask).where(
                                AsyncTask.celery_task_id == celery_task_id
                            )
                        )
                    ).scalar_one_or_none()
                    if t:
                        t.status = "failed"
                        t.current_stage = f"执行失败：{exc}"[:500]
                        t.progress = 0
                        t.error_message = (
                            f"{exc}\n{traceback.format_exc()[:2000]}"
                        )
                        await edb.commit()
            except Exception:  # noqa: BLE001
                # 连失败记录都写不进去时不能静默——至少留下日志
                logger.exception(f"写入任务失败状态失败：{celery_task_id}")
            raise


@celery_app.task(
    bind=True,
    name="app.tasks.review_tasks.run_review_task",
    max_retries=2,
    default_retry_delay=10,
    acks_late=True,
)
def run_review_task(
    self,
    review_text: str,
    *,
    title: str,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
    review_type: str = "full",
) -> dict[str, Any]:
    """后台执行合规审查。

    Celery Worker 是同步进程，所以用 asyncio.run 驱动内部异步逻辑。
    """
    return asyncio.run(
        _run_task(
            review_text,
            title=title,
            user_id=user_id,
            celery_task_id=self.request.id or "",
            project_id=project_id,
            document_id=document_id,
            review_type=review_type,
        )
    )


@celery_app.task(name="app.tasks.review_tasks.index_knowledge")
def index_knowledge_task() -> dict[str, Any]:
    """重建知识库向量索引。

    换 Embedding 模型后必须执行；
    耗时随知识库规模增长，同样不适合放在 HTTP 请求里。
    """

    async def _run() -> dict[str, Any]:
        from app.rag.vector_store import index_seed_knowledge
        from app.database import get_db

        async for db in get_db():
            return await index_seed_knowledge(db)
        return {}

    return asyncio.run(_run())