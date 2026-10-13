"""智能问答与向量库 API。

对应 JD「Agent 架构 37%」+「Function Calling 43%」+「向量数据库 21%」。

三种问答模式并存，体现不同的 Agent 编排方式：
- **react**  —— ReAct 循环，模型自主决定调用哪些工具（适合问法多变的问题）
- **function_calling** —— 原生 tool_calls，结构化更可靠（适合需要多工具协同）
- **direct**  —— 单次检索 + 生成，最快（适合简单明确的问题）
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["chat"])


class ChatSend(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    mode: str = Field("react", pattern="^(react|function_calling|direct)$")


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    refused: bool = False
    mode: str = "react"
    steps: list[dict] = []
    tool_calls: list[dict] = []
    tokens: int = 0
    latency_ms: int = 0


def _detect_refusal(answer: str, sources: list) -> tuple[bool, str | None]:
    """判断回答是否为拒答。

    **不能只靠模型自述**——它可能用肯定句包装一个没依据的回答。
    这里用两条硬信号：答案含拒答话术，或一个依据都没引用到。
    """
    refusal_markers = [
        "未收录", "无法给出", "未检索到", "没有找到",
        "不属于工程法规", "建议咨询", "无法判定",
    ]
    if any(m in answer for m in refusal_markers):
        return True, "知识库中未检索到相关条款，按证据闸门拒绝出具结论"

    if sources and not any(s.get("citation") for s in sources):
        return True, "检索结果无法定位到具体条款"

    return False, None


@router.post("/ask")
async def ask(
    request: ChatSend,
    current_user: User = Depends(get_current_user),
) -> dict:
    """智能问答。

    三种模式见模块 docstring。默认 react——问法多变时它最稳。
    """
    from app.agents.langchain_layer import call_with_tools, default_tools_schema
    from app.agents.react import build_qa_agent
    from app.services import retrieval_service as svc

    q = request.content
    mode = request.mode
    sources: list[dict] = []
    steps: list[dict] = []
    calls: list[dict] = []
    tokens = 0

    if mode == "react":
        agent = build_qa_agent()
        result = await agent.run(q)
        if not result["success"]:
            return {
                "code": 50300,
                "message": result.get("reason") or "问答失败",
                "data": None,
            }
        answer = result["answer"]
        steps = result.get("steps", [])
        tokens = result.get("tokens", 0)
        sources = [
            {"step": s["step"], "action": s["action"], "observation": s["observation"][:300]}
            for s in steps
            if s.get("action") == "search_regulation"
        ]

    elif mode == "function_calling":
        result = await call_with_tools(q, default_tools_schema())
        if not result["success"]:
            return {"code": 50300, "message": result.get("reason") or "问答失败", "data": None}
        answer = result["answer"]
        calls = result.get("tool_calls", [])
        tokens = result.get("tokens", 0)
        sources = [{"tool": c["name"], "args": c["args"]} for c in calls]

    else:  # direct
        hits = svc.search(query=q, top_k=5)
        sources = [
            {
                "clause_no": h["clause_no"],
                "regulation_code": h["regulation_code"],
                "citation": h["citation"],
                "content": h["content"][:300],
            }
            for h in hits
        ]
        if not hits:
            answer = "知识库中未收录相关条款，无法给出可靠依据。建议咨询专业工程师或查阅现行规范原文。"
        else:
            context = "\n\n".join(
                f"{h['citation']}\n{h['content']}" for h in hits[:3]
            )
            from app.agents.langchain_layer import build_qa_chain

            chain = build_qa_chain()
            answer = await chain.ainvoke({"question": q, "context": context})

    refused, reason = _detect_refusal(answer, sources)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": answer,
            "sources": sources,
            "refused": refused,
            "refuse_reason": reason,
            "mode": mode,
            "steps": steps,
            "tool_calls": calls,
            "tokens": tokens,
        },
    }


# ---------------------------------------------------------------- 向量库管理


@router.get("/vector/stats")
async def vector_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """向量库统计（覆盖率、模型分布、维度）。"""
    from app.rag.vector_store import PgVectorStore

    stats = await PgVectorStore(db).stats()
    return {"code": 0, "message": "success", "data": stats}


@router.post("/vector/reindex")
async def reindex(
    current_user: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """重建向量索引（换 Embedding 模型后必须执行）。"""
    from app.rag.vector_store import PgVectorStore, index_seed_knowledge

    result = await index_seed_knowledge(db)
    stats = await PgVectorStore(db).stats()
    return {"code": 0, "message": "success", "data": {**result, **stats}}


@router.post("/vector/search")
async def vector_search(
    request: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """PGVector + BM25 混合检索（对照纯 BM25 看差异）。"""
    from app.services.retrieval_service import hybrid_search_async

    q = request.get("query", "")
    if not q:
        return {"code": 40001, "message": "缺少 query", "data": None}

    results = await hybrid_search_async(
        q, db, top_k=request.get("top_k", 8), category=request.get("category")
    )
    return {"code": 0, "message": "success", "data": {"items": results, "total": len(results)}}
