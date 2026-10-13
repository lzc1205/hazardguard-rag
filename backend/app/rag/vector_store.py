"""PGVector 向量存储层。

对应 JD「向量数据库 21%」与需求文档 5.4 节。

**为什么要落库而不是只在内存里算**：
内存向量随进程生死，重启即丢，且无法多进程共享。
合规审查的知识库是**要长期维护的资产**——法规会更新、条款会废止，
这些都必须持久化，否则每次重启都要重新向量化一遍。

**HNSW 索引的取舍**：
- HNSW：召回率高、查询快，512 维下的平衡点
- IVFFlat：查询更快但需训练，召回率略低
- 数据量小于百万时两者差距不明显，但 HNSW 免训练，省一道工序

当前数据量仅 28 条，索引带来的收益有限，但**结构先立好**——
数据涨到十万级时无需改表。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeEntry

VECTOR_DIM = 512


class PgVectorStore:
    """PGVector 向量存储与检索。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------ 写入

    async def upsert_embedding(
        self,
        chunk_id: uuid.UUID,
        embedding: list[float],
        model: str,
    ) -> None:
        """写入/更新单条向量。

        用 SQL 直写而非 ORM 属性赋值：JSONB→vector 的类型转换
        走 ORM 时容易踩坑，直接 CAST 更可控。
        """
        if len(embedding) != VECTOR_DIM:
            raise ValueError(
                f"向量维度不匹配：期望 {VECTOR_DIM}，实际 {len(embedding)}。"
                f"换 Embedding 模型后必须全量重建，不能混存。"
            )

        await self.session.execute(
            text(
                """
                UPDATE knowledge_chunks
                SET embedding = CAST(:vec AS vector),
                    embedding_model = :model,
                    updated_at = now()
                WHERE id = :id AND is_deleted = false
                """
            ),
            {"vec": str(embedding), "model": model, "id": str(chunk_id)},
        )

    async def index_entry(self, entry_id: uuid.UUID, embedding: list[float]) -> int:
        """为某个条目的所有分块写入向量，返回写入条数。"""
        chunks = (
            (
                await self.session.execute(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.entry_id == entry_id,
                        KnowledgeChunk.is_deleted == False,
                    )
                )
            )
            .scalars()
            .all()
        )
        for c in chunks:
            await self.upsert_embedding(c.id, embedding, "char-ngram-v1")
        await self.session.commit()
        return len(chunks)

    async def bulk_index(
        self, items: list[tuple[uuid.UUID, list[float]]], model: str = "char-ngram-v1"
    ) -> int:
        """批量写入向量。"""
        count = 0
        for chunk_id, vec in items:
            await self.upsert_embedding(chunk_id, vec, model)
            count += 1
        await self.session.commit()
        return count

    # ------------------------------------------------------------ 检索

    async def similarity_search(
        self,
        embedding: list[float],
        top_k: int = 10,
        category: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """余弦相似度检索。

        用 `<=>` 余弦距离算子，转成相似度 = 1 - distance。
        走 HNSW 索引而非全表扫——数据量大了之后差别是数量级的。
        """
        if len(embedding) != VECTOR_DIM:
            raise ValueError(f"向量维度不匹配：期望 {VECTOR_DIM}")

        conditions = [
            "c.embedding IS NOT NULL",
            "c.is_deleted = false",
            "e.is_deleted = false",
        ]
        params: dict[str, Any] = {
            "vec": str(embedding),
            "limit": top_k,
        }

        if category:
            conditions.append("e.category = :category")
            params["category"] = category

        if min_similarity > 0:
            conditions.append("(1 - (c.embedding <=> CAST(:vec AS vector))) >= :min_sim")
            params["min_sim"] = min_similarity

        sql = f"""
            SELECT
                c.id::text           AS chunk_id,
                c.content,
                c.chunk_index,
                e.id::text           AS entry_id,
                e.title,
                e.regulation_name,
                e.regulation_code,
                e.clause_number,
                e.category,
                e.is_mandatory,
                1 - (c.embedding <=> CAST(:vec AS vector)) AS similarity
            FROM knowledge_chunks c
            JOIN knowledge_entries e ON e.id = c.entry_id
            WHERE {' AND '.join(conditions)}
            ORDER BY c.embedding <=> CAST(:vec AS vector)
            LIMIT :limit
        """

        rows = (
            (await self.session.execute(text(sql), params)).mappings().all()
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 维护

    async def clear_vectors(self, model: str | None = None) -> int:
        """清空向量（换 Embedding 模型前必须执行）。"""
        sql = "UPDATE knowledge_chunks SET embedding = NULL, embedding_model = NULL WHERE is_deleted = false"
        params: dict[str, Any] = {}
        if model:
            sql += " AND embedding_model = :model"
            params["model"] = model
        result = await self.session.execute(text(sql), params)
        await self.session.commit()
        return result.rowcount

    async def stats(self) -> dict[str, Any]:
        """向量库统计。"""
        row = (
            await self.session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS indexed,
                        COUNT(*) AS total,
                        COUNT(DISTINCT embedding_model) AS model_count
                    FROM knowledge_chunks
                    WHERE is_deleted = false
                    """
                )
            )
        ).mappings().one()

        models = (
            await self.session.execute(
                text(
                    """
                    SELECT embedding_model, COUNT(*) AS n
                    FROM knowledge_chunks
                    WHERE embedding_model IS NOT NULL AND is_deleted = false
                    GROUP BY embedding_model
                    """
                )
            )
        ).mappings().all()

        indexed = row["indexed"] or 0
        total = row["total"] or 0
        return {
            "total_chunks": total,
            "indexed_chunks": indexed,
            "coverage": round(indexed / total * 100, 1) if total else 0.0,
            "models": {m["embedding_model"]: m["n"] for m in models},
            "dimension": VECTOR_DIM,
        }


# ---------------------------------------------------------------- 便捷函数


async def index_seed_knowledge(session: AsyncSession) -> dict[str, Any]:
    """把种子法规全量向量化入库。

    这是「知识库入库流水线」的最后一环：解析 → 分块 → 向量化 → 入库。
    """
    from app.data.seed import read_seed_data
    from app.rag.chunker import TextChunk
    from app.config import settings
    from app.rag.embedding import EmbeddingFactory
    from app.rag.retriever import DOMAIN_SCENARIOS

    data = read_seed_data()
    entries = data["entries"]

    # 1. 确保条目与分块存在
    store = PgVectorStore(session)
    created_entries = 0

    for item in entries:
        exists = (
            await session.execute(
                select(KnowledgeEntry.id).where(
                    KnowledgeEntry.regulation_code == item.get("regulation_code"),
                    KnowledgeEntry.clause_number == item.get("clause_number"),
                    KnowledgeEntry.is_deleted == False,
                )
            )
        ).scalar_one_or_none()

        if exists:
            continue

        entry = KnowledgeEntry(
            title=item["title"],
            content=item["content"],
            entry_type=item.get("entry_type", "regulation"),
            category=item.get("category"),
            regulation_name=item.get("regulation_name"),
            regulation_code=item.get("regulation_code"),
            clause_number=item.get("clause_number"),
            page_number=item.get("page_number"),
            is_mandatory=item.get("is_mandatory", False),
            tags=item.get("tags"),
        )
        session.add(entry)
        await session.flush()

        session.add(
            KnowledgeChunk(
                entry_id=entry.id,
                chunk_index=0,
                content=item["content"],
                token_count=len(item["content"]),
            )
        )
        created_entries += 1

    await session.commit()

    # 2. 批量向量化
    chunks = (
        (await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.is_deleted == False)))
        .scalars()
        .all()
    )
    if not chunks:
        return {"entries": 0, "chunks": 0, "message": "无数据"}

    # 走工厂读 EMBEDDING_PROVIDER 配置；char 模式下显式传维度，
    # 因为 VECTOR_DIM 是本表 VECTOR 列的定义，必须一致
    emb = EmbeddingFactory.create(dimension=VECTOR_DIM)
    emb.fit([c.content for c in chunks])
    vectors = emb.embed([c.content for c in chunks])

    indexed = await store.bulk_index(
        [(c.id, v) for c, v in zip(chunks, vectors)],
        model=f"{settings.EMBEDDING_PROVIDER}-v1",
    )

    return {
        "entries_created": created_entries,
        "chunks_indexed": indexed,
        "dimension": VECTOR_DIM,
    }