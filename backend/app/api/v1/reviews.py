"""审查 API。

对应需求文档 4.3 节。审查是本系统最核心的业务能力，
接口设计要点：
- 审查是耗时操作，同步返回会超时，这里返回完整结果（Demo 规模）
- 每个审查都落库，保留 agent_trace 供前端可视化（JD 可观测 11%）
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.database import get_db
from app.models.project import Document
from app.models.review import ExpertPoint, Review, ReviewItem
from app.models.user import User
from app.schemas.review import ReviewCreate, ReviewDetailResponse, ReviewResponse

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("")
async def create_review(
    request: ReviewCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """发起合规审查。

    **两种执行模式**：

    - `async_mode=false`（默认）：同步执行，接口阻塞直到审查完成。
      适合几百字的小方案演示，1.5 秒内返回完整结果。
    - `async_mode=true`：投递 Celery 任务，立即返回 task_id，
      前端轮询 `/reviews/tasks/{task_id}` 看进度。
      适合几百页的大文档 —— 同步会 HTTP 超时。

    **落库逻辑两条链路共用同一个函数**
    （`app.services.review_persistence.persist_review_result`），
    避免「同步版本修了 bug、异步版本忘了改」这种不一致。
    """
    from app.agents.orchestrator import run_review
    from app.services.review_persistence import persist_review_result

    # 取方案正文
    text = request.text
    if not text and request.document_id:
        doc = await db.get(Document, request.document_id)
        if doc and doc.parsed_content:
            text = doc.parsed_content

    if not text:
        return {
            "code": 40001,
            "message": "缺少方案内容：需提供 text 或已解析的 document_id",
            "data": None,
        }

    # ---------------------------------------------------------------- 异步
    if request.async_mode:
        from app.models.chat import AsyncTask
        from app.tasks.review_tasks import run_review_task

        task = run_review_task.apply_async(
            kwargs={
                "review_text": text,
                "title": request.title,
                "user_id": str(current_user.id),
                "project_id": str(request.project_id) if request.project_id else None,
                "document_id": str(request.document_id) if request.document_id else None,
                "review_type": request.review_type,
            }
        )

        db.add(
            AsyncTask(
                celery_task_id=task.id,
                task_type="review",
                status="pending",
                progress=0,
                current_stage="任务已提交，等待执行",
                params={"title": request.title, "chars": len(text)},
                created_by=current_user.id,
            )
        )
        await db.commit()

        return {
            "code": 0,
            "message": "任务已受理",
            "data": {
                "task_id": task.id,
                "status": "pending",
                "poll_url": f"/api/v1/reviews/tasks/{task.id}",
            },
        }

    # ---------------------------------------------------------------- 同步
    result = await run_review(text, user_id=str(current_user.id))

    review = await persist_review_result(
        db,
        result,
        title=request.title,
        user_id=current_user.id,
        project_id=request.project_id,
        document_id=request.document_id,
        review_type=request.review_type,
        status="completed",
    )

    return {
        "code": 0,
        "message": "success",
        "data": {
            "review_id": str(review.id),
            "is_hazard_project": result.get("is_hazard_project"),
            "is_over_scale": result.get("is_over_scale"),
            "hazard_categories": (result.get("hazard_result") or {}).get("hazard_categories"),
            "overall_score": (result.get("review_summary") or {}).get("overall_score"),
            "overall_conclusion": (result.get("review_summary") or {}).get(
                "overall_conclusion"
            ),
            "review_items": result.get("review_items", []),
            "expert_points": result.get("expert_points", []),
            "report": result.get("report", {}),
            "trace": result.get("trace", []),
            "total_tokens": result.get("total_tokens", 0),
            "cost_estimate": result.get("cost_estimate", 0),
        },
    }


@router.get("/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """查询异步审查任务的进度。

    前端轮询这个接口即可知道当前执行到哪个 Agent。

    顺带做**僵尸任务回收**：worker 没启动或中途崩溃时，任务会永远停在
    pending/running，前端就一直转圈没有下文。这里在轮询时按超时阈值
    判定为失败并落库，让用户看到明确原因而不是无限等待。
    """
    from app.models.chat import AsyncTask

    task = (
        await db.execute(
            select(AsyncTask).where(AsyncTask.celery_task_id == task_id)
        )
    ).scalar_one_or_none()
    if not task:
        return {"code": 40400, "message": "任务不存在", "data": None}

    await _reap_stale_task(db, task)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "task_id": task.celery_task_id,
            "status": task.status,
            "progress": task.progress,
            "current_stage": task.current_stage,
            "result": task.result,
            "error_message": task.error_message,
            "created_at": task.created_at,
            "finished_at": task.finished_at,
        },
    }


# 任务创建后超过该时长仍无终态，判定为僵尸（分钟）。
STALE_TASK_MINUTES = 30


async def _reap_stale_task(db: AsyncSession, task) -> bool:
    """僵尸任务回收：超时未完成的任务标记为 failed。返回是否发生回收。"""
    if task.status in ("completed", "failed"):
        return False

    now = datetime.now(timezone.utc)
    ref = task.updated_at or task.created_at
    if ref is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)

    if (now - ref).total_seconds() < STALE_TASK_MINUTES * 60:
        return False

    task.status = "failed"
    task.progress = 0
    task.current_stage = (
        f"任务超时（超过 {STALE_TASK_MINUTES} 分钟无进展），"
        "通常是 Celery worker 未启动或中途异常退出导致"
    )
    task.error_message = (
        "STALE_TASK_TIMEOUT: 任务长时间无状态更新。"
        f"最近更新时间 {ref.isoformat()}。"
        "请确认 worker 进程已启动（python scripts\\start.py --restart 会一并拉起）。"
    )
    task.finished_at = now
    await db.commit()
    logger.warning(f"回收僵尸任务 {task.celery_task_id}：{task.current_stage}")
    return True


@router.get("/tasks")
async def list_tasks(
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """最近的异步任务列表。"""
    from app.models.chat import AsyncTask

    tasks = (
        (
            await db.execute(
                select(AsyncTask)
                .where(AsyncTask.created_by == current_user.id)
                .order_by(AsyncTask.created_at.desc())
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    return {
        "code": 0,
        "message": "success",
        "data": {
            "items": [
                {
                    "task_id": t.celery_task_id,
                    "status": t.status,
                    "progress": t.progress,
                    "current_stage": t.current_stage,
                    "created_at": t.created_at,
                    "finished_at": t.finished_at,
                }
                for t in tasks
            ],
            "total": len(tasks),
        },
    }


@router.get("")
async def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    project_id: str | None = None,
    document_id: str | None = None,
    overall_conclusion: str | None = None,
    is_over_scale: str | None = None,
    keyword: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审查列表（分页 + 多维筛选）。

    **筛选条件必须同时作用于 total**：
    原来 total 是无条件 count 全表，前端筛选后分页总数对不上，
    会出现「筛出3 条却显示 47 条、翻页全是空」。
    """
    from sqlalchemy import func

    conds = [Review.is_deleted == False]  # noqa: E712
    if project_id:
        conds.append(Review.project_id == uuid.UUID(project_id))
    if document_id:
        conds.append(Review.document_id == uuid.UUID(document_id))
    if overall_conclusion:
        conds.append(Review.overall_conclusion == overall_conclusion)
    if is_over_scale in ("true", "1"):
        conds.append(Review.is_over_scale == True)  # noqa: E712
    elif is_over_scale in ("false", "0"):
        conds.append(Review.is_over_scale == False)  # noqa: E712
    if keyword:
        like = f"%{keyword}%"
        conds.append(Review.title.ilike(like))

    total = (
        await db.execute(select(func.count()).select_from(Review).where(*conds))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Review)
                .where(*conds)
                .order_by(Review.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "status": r.status,
            # 文档详情页要按 document_id 过滤出该文档的审查记录，
            # 所以列表必须带上这个字段
            "document_id": str(r.document_id) if r.document_id else None,
            "project_id": str(r.project_id) if r.project_id else None,
            "is_hazard_project": r.is_hazard_project,
            "is_over_scale": r.is_over_scale,
            "hazard_categories": r.hazard_categories,
            "overall_score": r.overall_score,
            "overall_conclusion": r.overall_conclusion,
            "cost_estimate": r.cost_estimate,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"code": 0, "message": "success", "data": {"items": items, "total": total, "page": page, "page_size": page_size}}


@router.get("/{review_id}")
async def get_review(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审查详情（含审查意见与Agent 轨迹）。

    前端审查工作台依赖此接口展示风险分级与执行过程。
    """
    review = await db.get(Review, uuid.UUID(review_id))
    if not review or review.is_deleted:
        return {"code": 40400, "message": "审查记录不存在", "data": None}

    items = (
        (
            await db.execute(
                select(ReviewItem)
                .where(ReviewItem.review_id == review.id)
                .order_by(ReviewItem.sort_order)
            )
        )
        .scalars()
        .all()
    )
    points = (
        (
            await db.execute(
                select(ExpertPoint)
                .where(ExpertPoint.review_id == review.id)
                .order_by(ExpertPoint.sort_order)
            )
        )
        .scalars()
        .all()
    )

    return {
        "code": 0,
        "message": "success",
        "data": {
            "id": str(review.id),
            "title": review.title,
            "status": review.status,
            "is_hazard_project": review.is_hazard_project,
            "is_over_scale": review.is_over_scale,
            "hazard_categories": review.hazard_categories,
            "hazard_params": review.hazard_params,
            "overall_score": review.overall_score,
            "overall_conclusion": review.overall_conclusion,
            "review_items": [
                {
                    "category": i.category,
                    "item_title": i.item_title,
                    "scheme_content": i.scheme_content,
                    "regulation_reference": i.regulation_reference,
                    "citation_verified": i.citation_verified,
                    "compliance_status": i.compliance_status,
                    "severity": i.severity,
                    "review_comment": i.review_comment,
                    "modification_suggestion": i.modification_suggestion,
                    "agent_source": i.agent_source,
                }
                for i in items
            ],
            "expert_points": [
                {
                    "point_title": p.point_title,
                    "point_content": p.point_content,
                    "risk_level": p.risk_level,
                    "related_regulation": p.related_regulation,
                }
                for p in points
            ],
            "agent_trace": review.agent_trace,
            "token_summary": review.token_summary,
            "cost_estimate": review.cost_estimate,
            "created_at": review.created_at.isoformat() if review.created_at else None,
        },
    }


@router.get("/{review_id}/trace")
async def get_review_trace(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """单独取 Agent 执行轨迹。

    独立接口便于前端轮询展示进度（审查运行中时反复请求此接口）。
    """
    review = await db.get(Review, uuid.UUID(review_id))
    if not review or review.is_deleted:
        return {"code": 40400, "message": "审查记录不存在", "data": None}
    return {
        "code": 0,
        "message": "success",
        "data": {
            "review_id": str(review.id),
            "status": review.status,
            "trace": review.agent_trace or [],
            "token_summary": review.token_summary or {},
            "cost_estimate": review.cost_estimate or 0,
        },
    }


@router.delete("/{review_id}")
async def delete_review(
    review_id: str,
    current_user: User = Depends(require_roles("admin", "supervisor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除审查记录（软删除）。"""
    review = await db.get(Review, uuid.UUID(review_id))
    if not review or review.is_deleted:
        return {"code": 40400, "message": "审查记录不存在", "data": None}
    review.is_deleted = True
    await db.commit()
    return {"code": 0, "message": "success", "data": None}


@router.get("/stats/overview")
async def review_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """审查统计（仪表盘用）。"""
    from sqlalchemy import func

    total = (
        await db.execute(select(func.count(Review.id)).where(Review.is_deleted == False))
    ).scalar() or 0
    hazard = (
        await db.execute(
            select(func.count(Review.id)).where(
                Review.is_deleted == False, Review.is_hazard_project == True  # noqa: E712
            )
        )
    ).scalar() or 0
    over = (
        await db.execute(
            select(func.count(Review.id)).where(
                Review.is_deleted == False, Review.is_over_scale == True  # noqa: E712
            )
        )
    ).scalar() or 0
    passed = (
        await db.execute(
            select(func.count(Review.id)).where(
                Review.is_deleted == False, Review.overall_conclusion == "pass"
            )
        )
    ).scalar() or 0

    return {
        "code": 0,
        "message": "success",
        "data": {
            "total_reviews": total,
            "hazard_reviews": hazard,
            "over_scale_reviews": over,
            "passed_reviews": passed,
            "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        },
    }

def _content_disposition(filename: str) -> str:
    """构造支持中文的 Content-Disposition。

    HTTP 响应头按 latin-1 编码，中文标题（如「深基坑开挖专项施工方案」）
    直接放进去会让 Starlette 在 `init_headers` 阶段抛
    `UnicodeEncodeError: 'latin-1' codec can't encode characters`，
    接口 500。

    RFC 5987 规定：`filename*=UTF-8''<百分号编码后的值>`
    同时给一个 ASCII 兜底名，兼容老客户端。
    """
    from urllib.parse import quote

    # 纯 ASCII 兜底名。中文全被剥掉后通常只剩分隔符（如 "_"），
    # 必须判空后用可读默认名，不能产出 "_.docx" 这种怪文件名。
    stem = Path(filename).stem
    ascii_stem = re.sub(
        r"[^A-Za-z0-9\-]+", "-", stem.encode("ascii", "ignore").decode()
    ).strip("-")
    if not ascii_stem:
        ascii_stem = "compliance-review-report"
    return (
        f"attachment; filename=\"{ascii_stem}{Path(filename).suffix}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )



# ---------------------------------------------------------------- 报告导出


def _build_report_payload(review: Review, items: list, points: list) -> dict:
    """把数据库记录组装成报告生成器需要的数据结构。"""
    return {
        "title": review.title,
        "report_number": None,
        "project_name": None,
        "is_hazard_project": review.is_hazard_project,
        "is_over_scale": review.is_over_scale,
        "hazard_categories": review.hazard_categories or [],
        "overall_score": review.overall_score,
        "overall_conclusion": review.overall_conclusion,
        "review_items": [
            {
                "category": i.category,
                "item_title": i.item_title,
                "review_comment": i.review_comment,
                "regulation_reference": i.regulation_reference,
                "citation_verified": bool(i.citation_verified),
                "compliance_status": i.compliance_status,
                "severity": i.severity,
                "modification_suggestion": i.modification_suggestion,
                "agent_source": i.agent_source,
                "refused": bool(i.category == "needs_confirmation" and not i.citation_verified),
                "refuse_reason": (
                    "知识库中未找到该维度的直接法规依据，按证据闸门拒绝出具判定"
                    if i.category == "needs_confirmation" and not i.citation_verified
                    else None
                ),
            }
            for i in items
        ],
        "expert_points": [
            {
                "point_title": p.point_title,
                "point_content": p.point_content,
                "risk_level": p.risk_level,
                "related_regulation": p.related_regulation,
            }
            for p in points
        ],
        "agent_trace": review.agent_trace or [],
        "total_tokens": (review.token_summary or {}).get("total_used", 0),
        "cost_estimate": review.cost_estimate or 0,
    }


async def _load_review_bundle(db, review_id: str):
    review = await db.get(Review, uuid.UUID(review_id))
    if not review or review.is_deleted:
        return None, [], []
    items = (
        (
            await db.execute(
                select(ReviewItem)
                .where(ReviewItem.review_id == review.id)
                .order_by(ReviewItem.sort_order)
            )
        )
        .scalars()
        .all()
    )
    points = (
        (
            await db.execute(
                select(ExpertPoint)
                .where(ExpertPoint.review_id == review.id)
                .order_by(ExpertPoint.sort_order)
            )
        )
        .scalars()
        .all()
    )
    return review, list(items), list(points)


@router.get("/{review_id}/report.docx")
async def export_report_docx(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出 Word 审查报告（直接下载）。"""
    from fastapi.responses import Response

    review, items, points = await _load_review_bundle(db, review_id)
    if review is None:
        return {"code": 40400, "message": "审查记录不存在", "data": None}

    from app.services.report_service import generate_docx_bytes

    payload = _build_report_payload(review, items, points)
    content = generate_docx_bytes(payload)
    await _record_report_export(db, review, current_user, "word")

    filename = f"{review.title[:40]}_合规审查报告.docx"
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={
            # HTTP header 只能放 latin-1 字符，中文文件名必须按 RFC 5987
            # 做百分号编码，否则 Starlette 在 init_headers 阶段就抛
            # UnicodeEncodeError，接口直接 500。
            "Content-Disposition": _content_disposition(filename),
        },
    )


@router.get("/{review_id}/report.preview")
async def preview_report(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """报告内容预览（返回结构化数据，前端用于打印预览页）。"""
    review, items, points = await _load_review_bundle(db, review_id)
    if review is None:
        return {"code": 40400, "message": "审查记录不存在", "data": None}

    payload = _build_report_payload(review, items, points)
    return {"code": 0, "message": "success", "data": payload}


@router.get("/{review_id}/report.pdf")
async def export_report_pdf(
    review_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出 PDF 审查报告。

    与 docx 共用同一份 payload（_build_report_payload），
    保证两种格式的结论、拒答披露完全一致——
    不允许出现「Word 版说拒答 3 项，PDF 版没提」这种不一致，
    那会让审查结论失去可信度。
    """
    from fastapi.responses import Response

    review, items, points = await _load_review_bundle(db, review_id)
    if review is None:
        return {"code": 40400, "message": "审查记录不存在", "data": None}

    from app.services.report_pdf import generate_pdf_bytes

    payload = _build_report_payload(review, items, points)
    content = generate_pdf_bytes(payload)
    await _record_report_export(db, review, current_user, "pdf")

    filename = f"{review.title[:40]}_合规审查报告.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            # HTTP header 只能放 latin-1 字符，中文文件名必须按 RFC 5987
            # 做百分号编码，否则 Starlette 在 init_headers 阶段就抛
            # UnicodeEncodeError，接口直接 500。
            "Content-Disposition": _content_disposition(filename),
        },
    )


async def _record_report_export(
    db: AsyncSession, review: Review, user: User, fmt: str
) -> None:
    """记录一次报告导出。

    报告本身是按需实时生成的（内容永远来自最新审查数据），
    但**导出行为要留痕**：合规审查场景下「谁在什么时候导出了
    哪份报告」是审计要求，不能只存在于 HTTP 访问日志里。

    落库失败不影响文件下载——报告已经生成成功，不能因为
    写审计记录失败而让用户拿不到文件。
    """
    from app.models.review import Report

    try:
        report = (
            await db.execute(select(Report).where(Report.review_id == review.id))
        ).scalar_one_or_none()

        if report is None:
            report = Report(
                review_id=review.id,
                title=f"{review.title} 合规审查报告",
                report_number=f"HG-{review.id.hex[:12].upper()}",
            )
            db.add(report)

        now = datetime.now(timezone.utc)
        if fmt == "word":
            report.export_status = "word_exported"
        elif fmt == "pdf":
            # 两种都导出过时以 pdf 为准（PDF 是对外正式件）
            report.export_status = "pdf_exported"
        report.generated_by = user.id
        report.generated_at = now

        if report.summary is None:
            report.summary = (review.overall_conclusion or "")[:2000]

        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.warning(f"记录报告导出失败（不影响下载）：{exc}")


@router.get("/stats/dashboard")
async def stats_dashboard(    days: int = Query(30, ge=7, le=180),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """统计分析页的综合数据。

    概览接口只有 5 个计数，做统计分析页不够用。
    这里补齐四个维度：
      - 结论分布（通过/修改后/不通过）
      - 危大类别分布
      - 近 N 天审查趋势
      - 成本与耗时（Agent 维度）
    """
    from collections import Counter
    from datetime import timedelta

    rows = (
        (
            await db.execute(
                select(Review).where(Review.is_deleted == False)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )

    # 结论分布
    conclusion_dist = Counter(
        (r.overall_conclusion or "pending") for r in rows
    )

    # 危大类别分布（一条审查可能命中多个类别）
    category_dist: Counter = Counter()
    for r in rows:
        for c in (r.hazard_categories or []):
            category_dist[c] += 1

    # 趋势：按天聚合
    since = datetime.now(timezone.utc) - timedelta(days=days)
    daily: Counter = Counter()
    for r in rows:
        if r.created_at and r.created_at >= since:
            daily[r.created_at.date().isoformat()] += 1

    # 成本与耗时
    total_cost = sum(r.cost_estimate or 0 for r in rows)
    total_tokens = 0
    for r in rows:
        if r.token_summary:
            total_tokens += r.token_summary.get("total_used", 0) or 0

    durations = []
    for r in rows:
        if r.started_at and r.completed_at:
            durations.append((r.completed_at - r.started_at).total_seconds())

    # 评分分布
    scores = [r.overall_score for r in rows if r.overall_score is not None]

    return {
        "code": 0,
        "message": "success",
        "data": {
            "total": len(rows),
            "conclusion_dist": [
                {"key": k, "count": v} for k, v in conclusion_dist.most_common()
            ],
            "category_dist": [
                {"key": k, "count": v} for k, v in category_dist.most_common()
            ],
            "daily": [{"date": d, "count": daily[d]} for d in sorted(daily)],
            "cost": {
                "total": round(total_cost, 4),
                "total_tokens": total_tokens,
                "avg_cost": round(total_cost / len(rows), 5) if rows else 0,
                "avg_duration": round(sum(durations) / len(durations), 2)
                if durations
                else 0,
                "max_duration": round(max(durations), 2) if durations else 0,
            },
            "score": {
                "count": len(scores),
                "avg": round(sum(scores) / len(scores), 1) if scores else 0,
                "min": min(scores) if scores else 0,
                "max": max(scores) if scores else 0,
            },
        },
    }
