"""检索与知识库 API。

检索接口对外暴露 RAG 能力，是前端「智能问答」与「法规速查」页面的后端。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.database import get_db
from app.models.knowledge import KnowledgeEntry
from app.models.user import User
from app.schemas.review import KnowledgeEntryCreate, SearchQuery

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------- 检索


@router.post("/search")
async def search_regulations(
    request: SearchQuery,
    current_user: User = Depends(get_current_user),
) -> dict:
    """混合检索法规条文（BM25 + 向量双路 + RRF 融合）。

    返回结果均带规范名称、条款号与原文，可直接作为审查依据引用。
    """
    from app.services import retrieval_service as svc

    results = svc.search(
        query=request.query,
        top_k=request.top_k,
        category=request.category,
    )
    if request.only_mandatory:
        results = [r for r in results if r["is_mandatory"]]

    return {"code": 0, "message": "success", "data": {"items": results, "total": len(results)}}


@router.get("/categories")
async def list_categories(current_user: User = Depends(get_current_user)) -> dict:
    """七大危大工程类别及其判定阈值。"""
    from app.services import retrieval_service as svc

    return {"code": 0, "message": "success", "data": svc.list_hazard_categories()}


@router.get("/regulations/{regulation_code}/validity")
async def check_validity(
    regulation_code: str, current_user: User = Depends(get_current_user)
) -> dict:
    """校验规范是否现行有效。"""
    from app.services import retrieval_service as svc

    return {"code": 0, "message": "success", "data": svc.check_version_validity(regulation_code)}


@router.get("/regulations/{regulation_code}/clauses")
async def list_clauses(
    regulation_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按规范编号列出全部条款。"""
    from app.services import retrieval_service as svc

    results = svc.search(query=regulation_code, top_k=20)
    hits = [r for r in results if r.get("regulation_code") == regulation_code]
    return {"code": 0, "message": "success", "data": {"items": hits, "total": len(hits)}}


# ---------------------------------------------------------------- 知识库管理


@router.get("/entries")
async def list_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = None,
    keyword: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """知识库条目列表。"""
    query = select(KnowledgeEntry).where(KnowledgeEntry.is_deleted == False)
    if category:
        query = query.where(KnowledgeEntry.category == category)
    if keyword:
        query = query.where(KnowledgeEntry.title.ilike(f"%{keyword}%"))

    rows = (
        (
            await db.execute(
                query.order_by(KnowledgeEntry.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    total = len(
        (
            await db.execute(
                select(KnowledgeEntry.id).where(KnowledgeEntry.is_deleted == False)
            )
        )
        .scalars()
        .all()
    )

    items = [
        {
            "id": str(e.id),
            "title": e.title,
            "entry_type": e.entry_type,
            "category": e.category,
            "regulation_name": e.regulation_name,
            "regulation_code": e.regulation_code,
            "clause_number": e.clause_number,
            "is_mandatory": e.is_mandatory,
            "is_active": e.is_active,
            "tags": e.tags or [],
        }
        for e in rows
    ]
    return {
        "code": 0,
        "message": "success",
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
    }


@router.post("/entries")
async def create_entry(
    request: KnowledgeEntryCreate,
    current_user: User = Depends(require_roles("admin", "supervisor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """新增知识库条目。"""
    entry = KnowledgeEntry(
        title=request.title,
        content=request.content,
        entry_type=request.entry_type,
        category=request.category,
        regulation_name=request.regulation_name,
        regulation_code=request.regulation_code,
        clause_number=request.clause_number,
        page_number=request.page_number,
        is_mandatory=request.is_mandatory,
        tags=request.tags,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return {"code": 0, "message": "success", "data": {"id": str(entry.id), "title": entry.title}}


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: str,
    current_user: User = Depends(require_roles("admin", "supervisor")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除知识库条目（软删除）。"""
    import uuid

    entry = await db.get(KnowledgeEntry, uuid.UUID(entry_id))
    if not entry or entry.is_deleted:
        return {"code": 40400, "message": "条目不存在", "data": None}
    entry.is_deleted = True
    await db.commit()
    return {"code": 0, "message": "success", "data": None}


@router.post("/reload")
async def reload_index(current_user: User = Depends(require_roles("admin"))) -> dict:
    """重新加载检索索引。

    知识库更新后需调用，否则新增内容不会被检索到。
    """
    import app.services.retrieval_service as svc

    svc._indexed = False
    svc._bm25 = svc._chunks = svc._retriever = None
    ok = svc.ensure_indexed()
    return {
        "code": 0,
        "message": "success",
        "data": {"reloaded": ok, "chunk_count": len(svc._chunks)},
    }


# ---------------------------------------------------------------- 评测


@router.post("/eval/run")
async def run_evaluation(
    request: dict,
    current_user: User = Depends(require_roles("admin")),
) -> dict:
    """跑一次评测并返回指标（JD 评测体系 34% 的对外暴露）。

    需 admin 权限：评测会消耗 CPU 与（接入 LLM 后）token，
    不应允许普通用户反复触发。
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from eval.runner import load_dataset, run_eval, save_report
    from eval.runner import MockRetriever, RealRetriever

    retriever_type = request.get("retriever", "real")
    tag = request.get("tag", "api_run")

    try:
        dataset = load_dataset("regression_set")
        if retriever_type == "real":
            from app.data.seed import load_seed_chunks

            retriever = RealRetriever(load_seed_chunks())
        else:
            retriever = MockRetriever(dataset["cases"])

        report = run_eval(dataset, retriever)
        save_report(report, tag)
        return {"code": 0, "message": "success", "data": report.to_dict()}
    except Exception as e:  # noqa: BLE001
        return {"code": 50000, "message": f"评测执行失败: {type(e).__name__}", "data": None}

# ---------------------------------------------------------------- Prompt 管理


@router.get("/prompts")
async def list_prompts_api(current_user: User = Depends(get_current_user)) -> dict:
    """列出所有 Prompt 及其元信息。"""
    from app.agents.prompts.templates import list_prompts

    return {"code": 0, "message": "success", "data": list_prompts()}


@router.get("/prompts/versions")
async def list_prompt_versions(
    name: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Prompt 版本记录（含评测分数）。"""
    from app.agents.prompt_manager import list_versions

    versions = await list_versions(db, name)
    return {"code": 0, "message": "success", "data": {"items": versions, "total": len(versions)}}


@router.post("/prompts/sync")
async def sync_prompts(
    force: bool = False,
    current_user: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把代码中的 Prompt 同步到数据库。

    force=true 时覆盖已存在版本的模板内容。
    """
    from app.agents.prompt_manager import sync_prompts_to_db

    result = await sync_prompts_to_db(db, force=force)
    return {"code": 0, "message": "success", "data": result}


@router.post("/prompts/{name}/activate")
async def activate_prompt_version(
    name: str,
    version: str,
    current_user: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """启用指定 Prompt 版本（灰度切换的基础）。"""
    from app.agents.prompt_manager import activate_version

    try:
        result = await activate_version(db, name, version)
        return {"code": 0, "message": "success", "data": result}
    except ValueError as e:
        return {"code": 40400, "message": str(e), "data": None}


@router.get("/llm/status")
async def llm_status(current_user: User = Depends(get_current_user)) -> dict:
    """LLM 配置状态。

    前端据此提示用户「当前为规则模式，配置 API Key 后可启用 LLM 说理」——
    否则用户会以为审查说理本来就是模板拼的。
    """
    from app.llm import get_llm

    llm = get_llm()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "available": llm.available,
            "model": llm.model,
            "base_url": llm.base_url,
            "mode": "llm" if llm.available else "rule_only",
            "note": (
                "LLM 已启用，审查意见将基于法规依据生成说理"
                if llm.available
                else "未配置 API Key，当前使用规则模式：结论由规则判定，说理使用模板文本"
            ),
        },
    }
