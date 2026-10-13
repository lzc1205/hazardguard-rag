"""项目管理 API。

补齐需求文档 7.4.3 / 7.4.4 所需的后端能力。

**为什么项目这层必须有**

`reviews.project_id` 与 `documents.project_id` 都指向项目，
但之前只有外键没有接口，导致：
- 前端做不出「项目 → 文档 → 审查」的层级导航
- 审查记录无法按项目筛选
- `projects` 表永远是 0 行（实测确认）

项目是审查业务的归属单位，没有它整个数据模型是悬空的。
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_db
from app.models.project import Document, Project
from app.models.review import Review
from app.models.user import User
from app.schemas.review import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])

# 项目状态 → 中文标签
STATUS_LABEL = {
    "draft": "草稿",
    "in_review": "审查中",
    "reviewed": "已完成",
    "archived": "已归档",
}


async def _project_stats(db: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    """统计项目下的文档数与审查数。

    前端列表页要显示这两个数字，用两次 count 查询即可——
    比 join + group by 简单，也更容易看出瓶颈。
    """
    doc_count = (
        await db.execute(
            select(func.count())
            .select_from(Document)
            .where(
                Document.project_id == project_id,
                Document.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    review_count = (
        await db.execute(
            select(func.count())
            .select_from(Review)
            .where(
                Review.project_id == project_id,
                Review.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    hazard_count = (
        await db.execute(
            select(func.count())
            .select_from(Review)
            .where(
                Review.project_id == project_id,
                Review.is_over_scale == True,  # noqa: E712
                Review.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    return {
        "document_count": doc_count,
        "review_count": review_count,
        "over_scale_count": hazard_count,
    }


@router.post("")
async def create_project(
    payload: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """新建项目。

    `projects.code` 上有唯一约束，直接插入重复编号会抛
    UniqueViolation 变成 500 —— 用户看到的是「服务器错误」，
    完全不知道是自己的编号重复了。
    所以先查一次并返回明确的中文提示。
    """
    if payload.code:
        dup = (
            await db.execute(
                select(Project).where(
                    Project.code == payload.code,
                    Project.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if dup:
            return {
                "code": 40901,
                "message": f"项目编号「{payload.code}」已存在（{dup.name}），请换一个",
                "data": None,
            }

    project = Project(
        **payload.model_dump(exclude_none=True),
        owner_id=current_user.id,
        status="draft",
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return {
        "code": 0,
        "message": "success",
        "data": ProjectResponse.model_validate(project).model_dump(mode="json"),
    }


@router.get("")
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    keyword: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """项目列表（含文档数/审查数统计）。"""
    cond = [Project.is_deleted == False]  # noqa: E712
    if status:
        cond.append(Project.status == status)
    if keyword:
        like = f"%{keyword}%"
        cond.append(
            (Project.name.ilike(like))
            | (Project.code.ilike(like))
            | (Project.construction_unit.ilike(like))
        )

    total = (
        await db.execute(select(func.count()).select_from(Project).where(*cond))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Project)
                .where(*cond)
                .order_by(Project.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    items: list[dict[str, Any]] = []
    for p in rows:
        data = ProjectResponse.model_validate(p).model_dump(mode="json")
        data["status_label"] = STATUS_LABEL.get(p.status, p.status)
        data.update(await _project_stats(db, p.id))
        items.append(data)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    }


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """项目详情。"""
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return {"code": 40001, "message": "项目 ID 格式错误", "data": None}

    project = await db.get(Project, pid)
    if not project or project.is_deleted:
        return {"code": 40400, "message": "项目不存在", "data": None}

    data = ProjectResponse.model_validate(project).model_dump(mode="json")
    data["status_label"] = STATUS_LABEL.get(project.status, project.status)
    data.update(await _project_stats(db, project.id))
    return {"code": 0, "message": "success", "data": data}


@router.get("/{project_id}/documents")
async def list_project_documents(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """项目下的文档列表（项目详情页用）。"""
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return {"code": 40001, "message": "项目 ID 格式错误", "data": None}

    rows = (
        (
            await db.execute(
                select(Document)
                .where(Document.project_id == pid, Document.is_deleted == False)  # noqa: E712
                .order_by(Document.created_at.desc())
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
                    "id": str(d.id),
                    "title": d.title,
                    "file_name": d.file_name,
                    "file_type": d.file_type,
                    "file_size": d.file_size,
                    "parse_status": d.parse_status,
                    "page_count": d.page_count,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in rows
            ],
            "total": len(rows),
        },
    }


@router.get("/{project_id}/reviews")
async def list_project_reviews(
    project_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """项目下的审查记录（项目详情页用）。"""
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return {"code": 40001, "message": "项目 ID 格式错误", "data": None}

    rows = (
        (
            await db.execute(
                select(Review)
                .where(Review.project_id == pid, Review.is_deleted == False)  # noqa: E712
                .order_by(Review.created_at.desc())
                .limit(limit)
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
                    "id": str(r.id),
                    "title": r.title,
                    "overall_score": r.overall_score,
                    "overall_conclusion": r.overall_conclusion,
                    "is_over_scale": r.is_over_scale,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": len(rows),
        },
    }


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """更新项目。"""
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return {"code": 40001, "message": "项目 ID 格式错误", "data": None}

    project = await db.get(Project, pid)
    if not project or project.is_deleted:
        return {"code": 40400, "message": "项目不存在", "data": None}

    # 改编号时也要查重——同样会撞唯一约束变 500
    new_code = payload.code
    if new_code and new_code != project.code:
        dup = (
            await db.execute(
                select(Project).where(
                    Project.code == new_code,
                    Project.id != pid,
                    Project.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if dup:
            return {
                "code": 40901,
                "message": f"项目编号「{new_code}」已存在（{dup.name}），请换一个",
                "data": None,
            }

    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(project, k, v)
    await db.commit()
    await db.refresh(project)
    return {
        "code": 0,
        "message": "success",
        "data": ProjectResponse.model_validate(project).model_dump(mode="json"),
    }


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除项目（软删除）。

    项目下的文档与审查记录会随之级联删除，
    所以前端必须二次确认——
    这在合规审查系统里是不可逆的数据损失。
    """
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return {"code": 40001, "message": "项目 ID 格式错误", "data": None}

    project = await db.get(Project, pid)
    if not project or project.is_deleted:
        return {"code": 40400, "message": "项目不存在", "data": None}

    stats = await _project_stats(db, pid)
    project.is_deleted = True
    await db.commit()

    return {
        "code": 0,
        "message": "success",
        "data": {"deleted": True, **stats},
    }