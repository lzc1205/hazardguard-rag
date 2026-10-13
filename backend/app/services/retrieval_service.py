"""检索服务层。

把 RAG 检索逻辑封装成可复用的服务，供MCP 工具与 Agent 共用——
避免 MCP 层和 Agent 层各写一套检索，导致行为不一致。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.config import settings

# 本文件位于 app/services/，故向上一级到 app/，再进入 data/seed/
SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "seed" / "knowledge_entries.json"

_lock = threading.Lock()
_indexed = False
_bm25 = None
_chunks: list = []
_retriever = None


def _load_entries() -> list[dict[str, Any]]:
    if not SEED_FILE.exists():
        return []
    return json.loads(SEED_FILE.read_text(encoding="utf-8")).get("entries", [])


def ensure_indexed() -> bool:
    """构建内存索引（懒加载，进程内只建一次）。

    返回 False 表示没有可用数据源——此时检索应明确返回空，
    而不是抛异常让上层误判为系统故障。
    """
    global _bm25, _chunks, _retriever, _indexed

    if _indexed:
        return _retriever is not None

    with _lock:
        if _indexed:
            return _retriever is not None

        entries = _load_entries()
        if not entries:
            _indexed = True
            return False

        from app.rag.chunker import TextChunk
        from app.rag.retriever import BM25Index, HybridRetriever

        _chunks = [
            TextChunk(
                content=e["content"],
                doc_id=e.get("regulation_code") or "unknown",
                doc_name=e.get("regulation_name") or "法规",
                version=e.get("regulation_code"),
                clause_no=e.get("clause_number"),
                section=None,
                page=e.get("page_number"),
                is_mandatory=e.get("is_mandatory", False),
                extra={"category": e.get("category"), "tags": e.get("tags", [])},
            )
            for e in entries
        ]
        _bm25 = BM25Index().build(_chunks)
        _retriever = HybridRetriever(_bm25, _chunks)
        _indexed = True
        return True


def search(
    query: str,
    top_k: int | None = None,
    category: str | None = None,
    clause_no: str | None = None,
    only_active: bool = True,
) -> list[dict]:
    """检索法规条文，返回可直接序列化的 dict 列表。"""
    from app.rag.retriever import rule_based_boost

    if not ensure_indexed():
        return []

    top_k = top_k or settings.RETRIEVER_TOP_K

    # 召回阶段超额取数：规则加权只能重排已进入候选池的文档。
    # 只取 top_k 的话，排在第 7 名的目标条款会被直接丢弃，加权再高也无效。
    pool_size = max(top_k * settings.RETRIEVER_POOL_MULTIPLIER, 30)
    items = _retriever.retrieve(query, top_k=pool_size)
    items = rule_based_boost(query, items)

    results = []
    for item in items:
        c = item.chunk
        if clause_no and c.clause_no and clause_no not in c.clause_no:
            continue
        if category and c.extra.get("category") != category:
            continue
        results.append(
            {
                "clause_no": c.clause_no,
                "regulation_name": c.doc_name,
                "regulation_code": c.version,
                "category": c.extra.get("category"),
                "page_number": c.page,
                "is_mandatory": c.is_mandatory,
                "content": c.content,
                "citation": c.citation,
                "score": round(item.final_score, 4),
                "tags": c.extra.get("tags", []),
            }
        )
        if len(results) >= top_k:
            break
    return results


def get_clause_detail(regulation_code: str, clause_no: str) -> dict | None:
    """按规范号 + 条款号取完整原文。"""
    if not ensure_indexed():
        return None
    for c in _chunks:
        if c.version == regulation_code and c.clause_no == clause_no:
            return {
                "clause_no": c.clause_no,
                "regulation_name": c.doc_name,
                "regulation_code": c.version,
                "page_number": c.page,
                "is_mandatory": c.is_mandatory,
                "content": c.content,
                "citation": c.citation,
            }
    return None


def check_version_validity(regulation_code: str) -> dict:
    """校验规范是否现行有效。

    法规废止后必须能识别，否则会拿旧规范做审查，结论直接不可用。
    """
    if not ensure_indexed():
        return {"regulation_code": regulation_code, "is_active": False, "reason": "知识库未就绪"}

    related = [c for c in _chunks if c.version == regulation_code]
    if not related:
        return {
            "regulation_code": regulation_code,
            "is_active": False,
            "reason": "该规范未收录进知识库，无法确认现行状态",
            "clause_count": 0,
        }
    return {
        "regulation_code": regulation_code,
        "is_active": True,
        "reason": "该规范在知识库中标记为现行有效",
        "clause_count": len(related),
        "mandatory_count": sum(1 for c in related if c.is_mandatory),
    }


def list_hazard_categories() -> list[dict]:
    """返回七大危大工程类别及其判定阈值（文档 8.1 节）。"""
    from app.models.enums import HAZARD_CATEGORIES

    thresholds = {
        "foundation_pit": "开挖深度 ≥3m（不含软岩土层中的微型基坑）",
        "formwork": "各类工具式模板工程：搭设高度 ≥5m 或跨度 ≥10m 或总荷载 ≥10kN/m2",
        "lifting": "非常规起重设备、方法，单件起吊重量 ≥10kN",
        "scaffold": "落地式钢管脚手架搭设高度 ≥24m",
        "demolition": "可能影响行人、交通、电力设施或其他建（构）筑物安全的拆除工程",
        "underground": "采用矿山法、盾构法、顶管法施工的隧道、洞室工程",
        "other": "其他经辨识属于危大工程的专项施工",
    }
    over_scale = {
        "foundation_pit": "开挖深度 ≥5m",
        "formwork": "搭设高度 ≥8m（混凝土模板支撑工程）",
        "lifting": "起重量 ≥300kN 的起重设备安装",
        "scaffold": "落地式钢管脚手架搭设高度 ≥50m，或高度 ≥8m 且跨度 ≥18m",
        "demolition": "文物保护建筑、优秀历史建筑或历史文化风貌区影响范围内的拆除",
        "underground": "采用矿山法的软弱围岩或不良地质隧道",
        "other": "各省市细则规定的其他情形",
    }
    return [
        {
            "key": k,
            "name": v["name"],
            "icon": v["icon"],
            "hazard_threshold": thresholds.get(k, ""),
            "over_scale_threshold": over_scale.get(k, ""),
        }
        for k, v in HAZARD_CATEGORIES.items()
    ]

# ---------------------------------------------------------------- 混合检索（PGVector）


async def hybrid_search_async(
    query: str,
    session,
    top_k: int = 8,
    category: str | None = None,
    vector_weight: float = 0.5,
) -> list[dict]:
    """BM25 + PGVector 向量检索的异步版本。

    **为什么不能只用向量**：实测「深基坑多少米需要专家论证」这类查询，
    纯向量会召回第4/12/13 条这类泛化的论证程序条款，
    因为「专家论证」四个字在多条款里重复出现，向量把它当成了强信号。
    BM25 靠关键词精确匹配 + 领域加权能纠正这一点——两路互补才可靠。
    """
    from sqlalchemy import select

    from app.data.seed import load_seed_chunks
    from app.models.knowledge import KnowledgeChunk
    from app.rag.embedding import EmbeddingFactory
    from app.rag.retriever import rule_based_boost
    from app.rag.vector_store import VECTOR_DIM, PgVectorStore

    # 1) 内存 BM25 路（现有实现）
    bm25_results = search(query, top_k=top_k, category=category)
    bm25_scores = {r["citation"]: i for i, r in enumerate(bm25_results)}

    # 2) PGVector 路
    chunks = (
        (
            await session.execute(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.is_deleted == False,
                    KnowledgeChunk.embedding.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not chunks:
        return bm25_results

    emb = EmbeddingFactory.create(dimension=VECTOR_DIM)
    emb.fit([c.content for c in chunks])
    qv = emb.embed_query(query)

    store = PgVectorStore(session)
    vector_hits = await store.similarity_search(qv, top_k=top_k, category=category)

    # 3) RRF 融合两路排名
    #
    # key 必须统一成「规范号+条款号」——之前用 citation（含中文书名号）
    # 做 key，导致同一条款在两路里被当成两条，输出出现重复项。
    def norm(code: str | None, clause: str | None) -> str:
        return f"{code or ''}::{clause or ''}"

    vector_scores = {norm(h.get("regulation_code"), h.get("clause_number")): i
                     for i, h in enumerate(vector_hits)}

    merged: dict[str, dict] = {}
    for r in bm25_results:
        key = norm(r.get("regulation_code"), r.get("clause_no"))
        merged[key] = {**r, "bm25_rank": bm25_scores.get(r["citation"], 999)}

    for h in vector_hits:
        key = norm(h.get("regulation_code"), h.get("clause_number"))
        if key in merged:
            # 向量路补充相似度，不覆盖 BM25 路的内容
            merged[key]["vector_rank"] = vector_scores.get(key, 999)
            merged[key]["vector_similarity"] = h.get("similarity", 0.0)
        else:
            # 向量独有的命中，构造一条结果
            merged[key] = {
                "clause_no": h.get("clause_number"),
                "regulation_name": h.get("regulation_name"),
                "regulation_code": h.get("regulation_code"),
                "category": h.get("category"),
                "page_number": None,
                "is_mandatory": h.get("is_mandatory"),
                "content": h.get("content"),
                "citation": (
                    f"《{h.get('regulation_name')}》{h.get('regulation_code')} "
                    f"{h.get('clause_number')}"
                ),
                "score": 0.0,
                "tags": [],
                "bm25_rank": 999,
                "vector_rank": vector_scores.get(key, 999),
                "vector_similarity": h.get("similarity", 0.0),
            }

    items = list(merged.values())
    for it in items:
        rr = 0.0
        if it.get("bm25_rank", 999) < 999:
            rr += vector_weight / (60 + it["bm25_rank"] + 1)
        if it.get("vector_rank", 999) < 999:
            rr += (1 - vector_weight) / (60 + it["vector_rank"] + 1)
        it["score"] = rr

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:top_k]
