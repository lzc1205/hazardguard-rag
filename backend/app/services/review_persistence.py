"""审查落库逻辑（同步 / 异步共用）。

**为什么要抽出来**：
审查的落库代码有 40 多行（Review → ReviewItem → ExpertPoint → 统计），
如果同步接口和 Celery 任务各写一份，几周后必然出现
「同步版本修了 bug、异步版本忘了改」这种极难排查的不一致。

所以落库只有这一份，两条链路都调它。

同步链路：接口 → run_review() → persist_review_result()
异步链路：接口 → .delay() → Worker → run_review() → persist_review_result()
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import AsyncTask
from app.models.review import ExpertPoint, Review, ReviewItem


async def persist_review_result(
    db: AsyncSession,
    result: dict[str, Any],
    *,
    title: str,
    user_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    review_type: str = "full",
    status: str = "completed",
) -> Review:
    """把审查结果写入数据库。

    同步与异步链路唯一的差异就是 status：
    同步直接 completed；异步先写 processing，Worker 跑完再更新。

    **产出 0 条意见时不标 completed**：
    实测发现审查中断（如 LLM 全程超时）会留下一条
    `status=completed` 但没有任何 review_items 的记录，
    在列表页显示为「完成」却是空的，极具误导性。
    这里显式降级为 `failed`，让使用者知道这次没跑出结果。
    """
    # 真实起止时刻：编排器已在 __init__ 记 started_at、_finalize 记 finished_at。
    # 缺任一时才退回 now——**绝不能让两者都取 now**，
    # 否则 started_at == completed_at，统计页「平均耗时」恒为 0，
    # 会让人误以为审查不调用 LLM。
    now = datetime.now(timezone.utc)
    started_at = result.get("started_at") or now
    finished_at = result.get("finished_at") or now

    summary = result.get("review_summary") or {}
    hazard = result.get("hazard_result") or {}
    review_items = result.get("review_items") or []

    if status == "completed" and not review_items:
        status = "failed"

    review = Review(
        project_id=project_id,
        document_id=document_id,
        title=title,
        status=status,
        review_type=review_type,
        is_hazard_project=result.get("is_hazard_project"),
        hazard_categories=hazard.get("hazard_categories"),
        is_over_scale=result.get("is_over_scale"),
        hazard_params=hazard.get("matched_params"),
        overall_score=summary.get("overall_score"),
        overall_conclusion=summary.get("overall_conclusion"),
        started_at=started_at,
        completed_at=finished_at if status == "completed" else None,
        created_by=user_id,
        agent_trace=result.get("trace"),
        token_summary=result.get("token_usage"),
        cost_estimate=result.get("cost_estimate"),
    )
    db.add(review)
    await db.flush()

    for idx, item in enumerate(review_items):
        db.add(
            ReviewItem(
                review_id=review.id,
                category=item.get("category", "other"),
                item_title=item.get("item_title", ""),
                scheme_content=item.get("scheme_content"),
                regulation_reference=item.get("regulation_reference"),
                citation_verified=item.get("citation_verified", False),
                compliance_status=item.get("compliance_status", "needs_confirmation"),
                severity=item.get("severity", "minor"),
                review_comment=item.get("review_comment", ""),
                modification_suggestion=item.get("modification_suggestion"),
                agent_source=item.get("agent_source", "ComplianceReviewerAgent"),
                sort_order=idx,
                created_by=user_id,
            )
        )

    for idx, p in enumerate(result.get("expert_points", [])):
        db.add(
            ExpertPoint(
                review_id=review.id,
                point_title=p.get("point_title", ""),
                point_content=p.get("point_content", ""),
                risk_level=p.get("risk_level"),
                related_regulation=p.get("related_regulation"),
                sort_order=idx,
                created_by=user_id,
            )
        )

    await db.commit()
    await db.refresh(review)
    return review


async def update_task_progress(
    db: AsyncSession,
    task_id: str,
    *,
    progress: int,
    stage: str,
    status: str | None = None,
) -> None:
    """更新任务进度，供前端轮询。

    进度更新失败不能影响审查主流程——
    可观测性是辅助能力，不该成为单点故障。
    """
    try:
        task = (
            await db.execute(
                select_async_task().where(AsyncTask.celery_task_id == task_id)
            )
        ).scalar_one_or_none()
        if not task:
            return
        task.progress = max(0, min(100, progress))
        task.current_stage = stage
        if status:
            task.status = status
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()


def select_async_task():
    """延迟导入 select，避免循环依赖。"""
    from sqlalchemy import select

    return select(AsyncTask)